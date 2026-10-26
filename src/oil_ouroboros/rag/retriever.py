"""
Поиск по базе отчётов: многоязычные запросы → гибрид Qdrant (dense+sparse, RRF) → реранкер → приоритет выпуска.

Формула итоговой оценки фрагмента (каждый член виден в трейсе и на карте):
    final = rerank_score (0..1)  +  latest_edition_bonus · [фрагмент из последнего выпуска серии]
Без реранкера rerank_score заменяется нормированной RRF-оценкой. Надбавка не применяется, если пользователь
сам назвал выпуск (например, «в сентябрьском STEO»): тогда выпуск — жёсткий фильтр.

Запросы: исходный вопрос + переписанные узлом plan (английская формулировка обязательна: корпус англоязычный, а
вопрос обычно на русском; исходный и переведённый запросы вместе находят больше, чем каждый по отдельности). Все
запросы уходят в Qdrant одной пачкой; RRF-оценки фрагмента по запросам складываются (RRF: Cormack и др., 2009).
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

from .. import catalog
from ..config import get_settings
from ..reports import editions
from . import vectors

log = logging.getLogger(__name__)


@dataclass
class Hit:
    chunk_id: str
    org: str
    report: str
    edition: str
    published: str
    page: int | None
    section: str
    kind: str
    text: str
    url: str
    rrf: float
    rrf_rank: int
    rerank: float | None
    bonus: float
    final: float
    matched_queries: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        y, m = self.edition.split("-")
        loc = f"с. {self.page}" if self.page else f"раздел «{self.section[:50]}»"
        return f"Отчёт {self.org} {self.report}, {editions.MONTHS_RU.get(m, m)} {y}, {loc}"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label
        return d


@dataclass
class RetrievalResult:
    hits: list[Hit]  # top_k лучших, при сравнении агентств — с квотой каждому
    queries: list[str]
    filters: dict
    latest: dict[str, str]
    reranker: str
    embedder: str
    timings_ms: dict[str, int]
    index_size: int


def _balanced(ranked: list[Hit], orgs: list[str], limit: int) -> list[Hit]:
    """Делит limit мест между агентствами: каждому — до ceil(limit/n) его лучших, остаток — по общей оценке.

    Нужно при сравнении агентств: без квоты серия с большим числом фрагментов (OPEC MOMR) вытесняет
    остальные (IEA OMR). При одном агентстве (или без фильтра) возвращает ranked[:limit].
    """
    if len(orgs) < 2:
        return ranked[:limit]
    quota = -(-limit // len(orgs))
    count = dict.fromkeys(orgs, 0)
    picked: list[Hit] = []
    for h in ranked:
        if count.get(h.org, quota) < quota:
            count[h.org] = count.get(h.org, 0) + 1
            picked.append(h)
    picked_ids = {id(h) for h in picked}
    picked += [h for h in ranked if id(h) not in picked_ids]
    keep = {id(h) for h in picked[:limit]}
    return [h for h in ranked if id(h) in keep]


def retrieve(
    queries: list[str],
    orgs: list[str] | None = None,
    edition_filter: list[str] | None = None,
    top_k: int | None = None,
    rerank_query: str | None = None,
) -> RetrievalResult:
    s = get_settings()
    cr = catalog.get().retrieval
    top_k = top_k or cr.top_k
    t0 = time.monotonic()
    from ..reports.queue import pending

    # выпуск в очереди на индекс приоритет не забирает
    latest = {k: d.edition for k, d in editions.latest(editions.load(), set(pending())).items()}
    emb = vectors.get_embedder()
    queries = [q for i, q in enumerate(queries) if q and q.strip() and q not in queries[:i]]
    filt = {}
    if orgs:
        filt["org"] = list(dict.fromkeys(o.upper() for o in orgs))
    if edition_filter:
        filt["edition"] = edition_filter
    vecs = emb.embed(queries, is_query=True)
    t1 = time.monotonic()
    # Несколько агентств: отдельный поиск по каждому (кандидаты делятся квотой в _balanced)
    orgs_filt = filt.get("org", [])
    scopes = [{**filt, "org": [o]} for o in orgs_filt] if len(orgs_filt) > 1 else [filt]
    per_scope = max(1, cr.rerank_candidates // len(scopes))
    merged: dict[str, Hit] = {}
    jobs = [(q, v, sc) for sc in scopes for q, v in zip(queries, vecs, strict=True)]
    found = vectors.hybrid_queries(s.qdrant_collection, [(v, sc) for _q, v, sc in jobs], per_scope, cr.prefetch)
    for (q, _v, _sc), points in zip(jobs, found, strict=True):
        for rank, (p, score) in enumerate(points, start=1):
            cid = p["chunk_id"]
            if cid in merged:
                h = merged[cid]
                h.rrf += score  # RRF по запросам — сумма: фрагмент, найденный несколькими формулировками, выше
                h.rrf_rank = min(h.rrf_rank, rank)
                h.matched_queries.append(q)
                continue
            merged[cid] = Hit(
                chunk_id=cid,
                org=p["org"],
                report=p["report"],
                edition=p["edition"],
                published=p.get("published", ""),
                page=p.get("page"),
                section=p.get("section", ""),
                kind=p.get("kind", "text"),
                text=p["text"],
                url=p.get("url", ""),
                rrf=score,
                rrf_rank=rank,
                rerank=None,
                bonus=0.0,
                final=0.0,
                matched_queries=[q],
            )
    t2 = time.monotonic()
    top_all = max((h.rrf for h in merged.values()), default=1.0) or 1.0

    def pre(h: Hit) -> float:  # надбавка последнему выпуску — уже при отборе кандидатов: иначе его фрагмент чуть ниже
        # по RRF не доходит до отбора, и ответ строится по прошлому выпуску
        return h.rrf / top_all + (
            cr.latest_edition_bonus if not edition_filter and latest.get(f"{h.org} {h.report}") == h.edition else 0.0
        )

    cands = _balanced(sorted(merged.values(), key=lambda h: (-pre(h), h.rrf_rank)), orgs_filt, cr.rerank_candidates)
    rr = vectors.get_reranker()
    scores = rr.score(rerank_query or queries[0], [f"{h.org} {h.report} {h.edition}: {h.text}" for h in cands])
    top_rrf = max((h.rrf for h in cands), default=1.0) or 1.0
    for i, h in enumerate(cands):
        h.rerank = scores[i] if scores else None
        base = h.rerank if h.rerank is not None else h.rrf / top_rrf
        if not edition_filter and latest.get(f"{h.org} {h.report}") == h.edition:
            h.bonus = cr.latest_edition_bonus
        h.final = round(base + h.bonus, 4)
    t3 = time.monotonic()
    ranked = sorted(cands, key=lambda h: -h.final)
    return RetrievalResult(
        hits=_balanced(ranked, orgs_filt, top_k),
        queries=queries,
        filters=filt,
        latest=latest,
        reranker=rr.name,
        embedder=emb.name,
        timings_ms={
            "embed": int((t1 - t0) * 1000),
            "search": int((t2 - t1) * 1000),
            "rerank": int((t3 - t2) * 1000),
        },
        index_size=vectors.count(s.qdrant_collection),
    )

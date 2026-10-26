"""
Субагент «исследование» (LangGraph): отчёты → отбор и достаточность → веб по политике → пакет доказательств.

Это инструмент `research` агента (MCP) и единственное место, где выполняется правило приоритета
источников. Правило — КОД (route_after_grade + config/current/web.yaml), а не предпочтение модели: решение
аудируемо, версионируется отпечатком каталога и не поддаётся «авторитетному виду» найденной страницы.

    plan ─► retrieve ─► select_grade ─┬─► web ─► pack
                                      └────────► pack

- plan (LLM субагента): запросы к отчётам RU + EN, агентства и выпуски, нужна ли свежесть, запросы для веба;
- retrieve (код): гибрид BGE-M3 dense + sparse в Qdrant → RRF → надбавка последнему выпуску → кандидаты;
- select_grade (LLM субагента): listwise-отбор полезных фрагментов (вместо cross-encoder на CPU) и оценка
  «достаточно ли отчётов» (sufficient context) — один вызов;
- route (код): веб нужен, если вопрос требует свежести, отчётов недостаточно или есть разрыв по датам;
- web (код): сначала домены уровней 0–1, затем открытый поиск без deny-списка; жёлтая пресса отсекается;
- pack (код): доказательства → журнал (устойчивые R#/W#), пакет P#, решения, текст для агента.
"""

from __future__ import annotations

import functools
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from .. import catalog
from ..evidence import ledger
from ..llm import core as llm
from ..rag import retriever
from ..reports import editions
from ..web import search as web
from ..web.credibility import TIER_TITLES
from . import kit
from .kit import decision


class ResearchState(TypedDict, total=False):
    run_id: str
    question: str
    focus: str
    today: str
    mode: str  # research | reports (без веба: инструмент search_reports)
    given_queries: list[str]
    given_orgs: list[str]
    given_editions: list[str]
    plan: dict
    retrieval: dict
    candidates: list[dict]
    grade: dict
    reports: list[dict]
    web_reasons: list[str]
    web_prefetch: str  # ключ фонового веб-поиска, начатого после плана (needs_freshness)
    web: dict
    web_docs: list[dict]
    decisions: list[dict]
    pack_id: str
    result: str
    path: list[str]


class Plan(BaseModel):
    in_scope: bool = True
    needs_freshness: bool = False
    freshness_reason: str = ""
    queries: list[str] = Field(default_factory=list)
    web_queries: list[str] = Field(default_factory=list)
    orgs: list[str] = Field(default_factory=list)
    editions: list[str] = Field(default_factory=list)


class Grade(BaseModel):
    selected: list[int] = Field(default_factory=list)
    verdict: Literal["sufficient", "partial", "insufficient"] = "insufficient"
    missing: list[str] = Field(default_factory=list)
    freshness_gap: bool = False
    reason: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _norm(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("selected", mode="before")
    @classmethod
    def _ints(cls, v: object) -> object:
        if isinstance(v, list):
            out = []
            for x in v:
                try:
                    out.append(int(str(x).strip().lstrip("#[").rstrip("]")))
                except ValueError:
                    continue
            return out
        return v


def _latest_text() -> str:
    latest = editions.latest(editions.load())
    return ", ".join(f"{k} — {d.edition} (опубликован {d.published})" for k, d in sorted(latest.items())) or "—"


# ---------------------------------------------------------------------------------------------------------------
# узлы
# ---------------------------------------------------------------------------------------------------------------


def plan(state: ResearchState) -> dict:
    """Модель субагента составляет план: 1–4 запроса к отчётам (на английском и русском — корпус англоязычный),
    агентства и выпуски, нужна ли свежесть. План — JSON по схеме Plan; исходный вопрос всегда добавляется к запросам.
    Если свежесть нужна, веб-поиск стартует сразу, параллельно с поиском по отчётам. У search_reports запросы даёт
    агент — тогда план без LLM."""
    q = state["question"]
    if state.get("given_queries"):  # search_reports: запросы дал агент — план без LLM
        p = Plan(
            queries=list(state["given_queries"]),
            orgs=list(state.get("given_orgs") or []),
            editions=list(state.get("given_editions") or []),
        )
        if q and q not in p.queries:
            p.queries.append(q)
        return {"plan": p.model_dump(), "decisions": [decision("plan", "queries", "given", ["agent_queries"])]}
    c = catalog.get()
    orgs = editions.orgs()
    msgs = [
        SystemMessage(
            content=c.prompt("research_plan", today=state["today"], latest=_latest_text(), orgs=", ".join(orgs) or "—")
        ),
        HumanMessage(content=f"Вопрос: {q}" + (f"\nУточнение агента: {state['focus']}" if state.get("focus") else "")),
    ]
    p: Plan = llm.call_json("subagent", msgs, Plan, purpose="research_plan")
    p.queries = [x for x in p.queries if x.strip()][: c.research.max_queries]
    if q not in p.queries:
        p.queries.append(q)  # исходная формулировка всегда участвует в поиске
    p.web_queries = [x for x in p.web_queries if x.strip()][: c.research.max_web_queries] or p.queries[
        : c.research.max_web_queries
    ]
    p.orgs = [o.upper() for o in p.orgs if o.upper() in orgs]  # агентства базы — из каталога отчётов
    codes = ["in_scope" if p.in_scope else "out_of_scope"] + (["needs_freshness"] if p.needs_freshness else [])
    out = {
        "plan": p.model_dump(),
        "decisions": [
            decision(
                "plan",
                "queries",
                f"{len(p.queries)} запросов",
                codes,
                rationale=p.freshness_reason,
                confidence_method="llm_classification",
            )
        ],
    }
    if state.get("mode") != "reports" and c.web.policy.needs_freshness and p.needs_freshness:
        # веб по политике будет при любой оценке отчётов (needs_freshness): поиск стартует сейчас, параллельно с
        # поиском и отбором по отчётам, узел web лишь забирает результат
        token = f"{state.get('run_id', '')}:{time.monotonic_ns()}"
        for k in list(_PREFETCH)[:-50]:  # прогон, сорвавшийся до узла web, не забрал свой результат
            _PREFETCH.pop(k, None)
        _PREFETCH[token] = _POOL.submit(web.search, *_web_args(out["plan"], q))
        out["web_prefetch"] = token
    return out


_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="web-prefetch")
_PREFETCH: dict[str, Future] = {}  # в состоянии графа — только ключ (оно сериализуется), сам поиск — здесь


def _web_args(p: dict, question: str) -> tuple[list[str], str]:
    return p.get("web_queries") or p.get("queries") or [question], "week" if p.get("needs_freshness") else "month"


def retrieve(state: ResearchState) -> dict:
    """Код: все запросы плана одной пачкой в Qdrant — dense и sparse векторы BGE-M3, слияние RRF, оценки фрагмента по
    запросам складываются; последний выпуск серии получает надбавку, каждое агентство из плана — свою долю мест.
    Результат — кандидаты для отбора (без LLM: быстро и воспроизводимо)."""
    p = state["plan"]
    c = catalog.get()
    res = retriever.retrieve(
        p["queries"],
        p.get("orgs") or None,
        p.get("editions") or None,
        top_k=c.research.select_candidates,
        rerank_query=state["question"],
    )
    cands = [h.as_dict() for h in res.hits]  # уже с квотой агентств при сравнении
    retrieval = {
        "queries": res.queries,
        "filters": res.filters,
        "latest": res.latest,
        "timings_ms": res.timings_ms,
        "index_size": res.index_size,
        "embedder": res.embedder,
        "reranker": res.reranker,
    }
    return {"retrieval": retrieval, "candidates": cands}


def _cand_block(cands: list[dict], limit: int) -> str:
    rows = []
    for i, h in enumerate(cands, start=1):
        text = h["text"] if len(h["text"]) <= limit else h["text"][:limit] + "…"
        # в метке фрагмента PDF — страница; раздел (у таблицы — её заголовок) отбору тоже нужен
        section = f" · {h['section'][:120]}" if h.get("page") and h.get("section") else ""
        rows.append(f"#{i} {h['label']}{section} (опубликован {h.get('published') or '—'})\n{text}")
    return "\n\n".join(rows)


def with_every_org(picked: list[int], cands: list[dict], orgs: list[str], top_k: int) -> tuple[list[int], list[str]]:
    """Вопрос сравнивает агентства — в отборе есть каждое: отбор LLM нередко останавливается на одном (R09 — в 2 из 3
    повторов), хотя промпт просит брать от каждого. Пропущенному агентству — его лучший по поиску фрагмент; если мест
    нет, он заменяет последний выбранный фрагмент агентства, у которого их несколько."""
    added = []
    for org in dict.fromkeys(o.upper() for o in orgs) if len(orgs) > 1 and picked else ():
        best = next((i for i, h in enumerate(cands, 1) if h["org"] == org), None)
        if not best or any(cands[i - 1]["org"] == org for i in picked):
            continue
        if len(picked) >= top_k:
            n = Counter(cands[i - 1]["org"] for i in picked)
            drop = next((i for i in reversed(picked) if n[cands[i - 1]["org"]] > 1), None)
            if drop is None:
                continue
            picked = [i for i in picked if i != drop]
        picked = picked + [best]
        added.append(org)
    return picked, added


def select_grade(state: ResearchState) -> dict:
    """Один вызов модели субагента: отобрать до top_k полезных фрагментов из кандидатов (listwise — вместо
    cross-encoder, который на CPU занимает 24–130 с) и оценить, хватает ли отчётов: sufficient / partial /
    insufficient, есть ли разрыв по датам. По этой оценке маршрутизатор решает, нужен ли веб."""
    cands = state.get("candidates") or []
    c = catalog.get()
    if not cands:
        g = Grade(verdict="insufficient", reason="в базе отчётов ничего не найдено", missing=["данные отчётов"])
    else:
        seen = sorted({f"{h['org']} {h['report']} {h['edition']}: {h.get('published') or '—'}" for h in cands})
        msgs = [
            SystemMessage(
                content=c.prompt(
                    "select_grade", today=state["today"], editions="; ".join(seen), top_k=c.retrieval.top_k
                )
            ),
            HumanMessage(
                content=f"Вопрос: {state['question']}\n\n"
                f"Фрагменты:\n{_cand_block(cands, c.research.select_text_chars)}"
            ),
        ]
        g = llm.call_json("subagent", msgs, Grade, purpose="select_grade")
    picked = [i for i in dict.fromkeys(g.selected) if 1 <= i <= len(cands)][: c.retrieval.top_k]
    if not picked and cands and g.verdict != "insufficient":
        # модель забыла номера — берём лучшие по поиску
        picked = list(range(1, min(len(cands), c.retrieval.top_k) + 1))
    picked, added = with_every_org(picked, cands, (state.get("plan") or {}).get("orgs") or [], c.retrieval.top_k)
    reports = [cands[i - 1] for i in picked]
    d = decision(
        "select_grade",
        "report_selection",
        ", ".join(f"#{i}" for i in picked) or "—",
        [f"verdict={g.verdict}"] + (["freshness_gap"] if g.freshness_gap else []) + [f"added_org={o}" for o in added],
        options=[
            {
                "option": h["label"],
                "chunk_id": h["chunk_id"],
                "rrf": round(h["rrf"], 4),
                "bonus": h["bonus"],
                "selected": (i + 1) in picked,
            }
            for i, h in enumerate(cands)
        ],
        rationale=g.reason,
        policy=f"listwise LLM, top_k={c.retrieval.top_k}",
        confidence_method="llm_sufficiency_autorater",
    )
    return {"grade": g.model_dump(), "reports": reports, "decisions": list(state.get("decisions", [])) + [d]}


def web_reasons(state: ResearchState) -> list[str]:
    """Политика эскалации в веб (config/current/web.yaml). Пустой список — веб не нужен."""
    if state.get("mode") == "reports":
        return []
    p, g = state.get("plan", {}), state.get("grade", {})
    pol = catalog.get().web.policy
    reasons = []
    if pol.needs_freshness and p.get("needs_freshness"):
        reasons.append("needs_freshness")
    if g.get("verdict") in pol.sufficiency:
        reasons.append(f"sufficiency={g.get('verdict')}")
    if pol.freshness_gap and g.get("freshness_gap"):
        reasons.append("freshness_gap")
    return reasons


def web_policy_text() -> str:
    c = catalog.get()
    p = c.web.policy
    conds = [
        x
        for x, on in (
            ("needs_freshness", p.needs_freshness),
            (f"sufficiency∈{{{','.join(p.sufficiency)}}}", bool(p.sufficiency)),
            ("freshness_gap", p.freshness_gap),
        )
        if on
    ]
    return f"веб, если: {' ∨ '.join(conds) or 'никогда'} ({c.ref})"


def route_after_grade(state: ResearchState) -> str:
    return "web" if web_reasons(state) else "pack"


def web_node(state: ResearchState) -> dict:
    """Код: веб-поиск по правилу приоритета — сначала домены уровней доверия 0–1, затем открытый поиск без deny-списка;
    жёлтая пресса и лимит на домен отсекаются до контекста. Если поиск уже начат после плана — забирает результат."""
    queries, fresh = _web_args(state.get("plan", {}), state["question"])
    fut = _PREFETCH.pop(state.get("web_prefetch") or "", None)
    try:
        res = fut.result() if fut is not None else web.search(queries, freshness=fresh)
    except Exception:  # noqa: BLE001 — сбой фонового поиска: обычный поиск
        res = web.search(queries, freshness=fresh)
    return {"web": res.summary(fresh), "web_docs": [x.as_dict() for x in res.docs], "web_reasons": web_reasons(state)}


def pack(state: ResearchState) -> dict:
    """Код: каждое доказательство — в журнал под устойчивым id (R# — отчёт, W# — веб), решения узлов — в пакет P#,
    агенту — компактный текст: фрагменты с метками, оценка достаточности, решение о вебе и его причины."""
    c = catalog.get()
    decisions = list(state.get("decisions", []))
    ids_r, ids_w = [], []
    reports = []
    for h in state.get("reports") or []:
        eid = ledger.put(
            "report",
            ledger.key_of("report", h["chunk_id"]),
            h["label"],
            h["text"],
            tier=0,
            org=h["org"],
            report=h["report"],
            edition=h["edition"],
            published=h.get("published", ""),
            page=h.get("page"),
            section=h.get("section", ""),
            url=h.get("url", ""),
            chunk_id=h["chunk_id"],
        )
        ids_r.append(eid)
        reports.append({**h, "id": eid})
    web_docs = []
    for d in state.get("web_docs") or []:
        eid = ledger.put_web(d)
        ids_w.append(eid)
        web_docs.append({**d, "id": eid})
    reasons = web_reasons(state)
    if state.get("mode") != "reports":
        decisions.append(
            decision(
                "route",
                "source_priority",
                "web" if reasons else "reports_only",
                reasons or ["sufficiency=sufficient", "no_freshness_need"],
                evidence_ids=ids_r,
                rationale=state.get("grade", {}).get("reason", ""),
                policy=web_policy_text(),
            )
        )
    if state.get("web"):
        w = state["web"]
        decisions.append(
            decision(
                "web",
                "web_source_filter",
                ", ".join(ids_w) or "—",
                [f"provider={w.get('provider')}", f"freshness={w.get('freshness')}"]
                + (["error"] if w.get("error") else []),
                evidence_ids=ids_w,
                rationale=w.get("error", ""),
                options=[{"option": x["label"], "tier": x["tier"], "kept": True} for x in web_docs]
                + [
                    {"option": x["url"], "tier": x["tier"], "reason": x["reason"], "kept": False}
                    for x in w.get("rejected", [])
                ],
                policy=f"source_tiers {w.get('policy_version')}",
            )
        )
    pid = ledger.new_pack(
        state.get("mode") == "reports" and "search_reports" or "research",
        ids_r + ids_w,
        run_id=state.get("run_id", ""),
        question=state["question"],
        decisions=decisions,
        catalog=c.ref,
        grade=state.get("grade", {}),
        web_reasons=reasons,
    )
    return {
        "pack_id": pid,
        "reports": reports,
        "web_docs": web_docs,
        "decisions": decisions,
        "result": render(state, pid, reports, web_docs, reasons),
    }


# ---------------------------------------------------------------------------------------------------------------
# текст для агента: компактно, без JSON-экранирования (модель читает текст лучше, чем JSON со строками)
# ---------------------------------------------------------------------------------------------------------------


RESULT_CHUNK_CHARS = 2000  # сколько символов фрагмента отчёта видит агент (фрагмент ~450 токенов — обычно целиком)
RESULT_WEB_CHARS = 800  # сколько символов веб-выдачи на источник


def render(state: ResearchState, pid: str, reports: list[dict], web_docs: list[dict], reasons: list[str]) -> str:
    g = state.get("grade", {})
    p = state.get("plan", {})
    out = [f"Пакет {pid} · trace {state.get('run_id', '')} · каталог {catalog.get().ref}"]
    if not p.get("in_scope", True):
        out.append("Внимание: субагент счёл вопрос вне компетенции аналитика (нефть, газ, энергетические рынки).")
    out.append(
        f"Достаточность отчётов: {g.get('verdict', '—')} — {g.get('reason', '')}"
        + (f" Не хватает: {'; '.join(g.get('missing', []))}." if g.get("missing") else "")
    )
    if state.get("mode") != "reports":
        out.append(
            "Решение о вебе: "
            + (
                f"поиск выполнен ({', '.join(reasons)})"
                if reasons
                else "не нужен — отчётов достаточно, свежесть не требуется"
            )
            + f". Политика: {web_policy_text()}."
        )
    out.append("\n## Отчёты агентств (приоритетный, верифицированный источник)")
    if reports:
        for h in reports:
            text = (
                h["text"]
                if len(h["text"]) <= RESULT_CHUNK_CHARS
                else h["text"][:RESULT_CHUNK_CHARS] + "… (полный текст — в журнале доказательств)"
            )
            out.append(f"[{h['id']}] {h['label']} (опубликован {h.get('published') or '—'})\n{text}")
    else:
        out.append("— подходящих фрагментов нет —")
    if state.get("web") is not None and state.get("mode") != "reports":
        w = state["web"]
        err = f" (сбой поиска: {w['error']})" if w.get("error") else ""
        out.append(
            f"\n## Веб (дополнение и свежие данные; провайдер {w.get('provider')}, свежесть {w.get('freshness')}){err}"
        )
        for d in web_docs:
            tier = d.get("tier")
            out.append(
                f"[{d['id']}] {d['label']} — {TIER_TITLES.get(tier, '')}\n"
                f"{d['title']}. {d['content'][:RESULT_WEB_CHARS]}"
            )
        if not web_docs:
            out.append("— подходящих источников нет —")
        if w.get("rejected"):
            out.append(
                f"Отсеяно фильтром источников: {len(w['rejected'])} (жёлтая пресса, deny-список, неизвестный уровень)."
            )
    out.append(
        "\nЦитируй id в квадратных скобках в конце предложения с цифрой, например «…на 1,3 млн б/с [R12].». "
        "Черновик со ссылками проверь verify_answer."
    )
    return "\n\n".join(out)


def summarize(node: str, out: dict) -> dict:
    if node == "plan":
        p = out.get("plan", {})
        return {k: p.get(k) for k in ("in_scope", "needs_freshness", "queries", "orgs", "editions")}
    if node == "retrieve":
        r = out.get("retrieval", {})
        return {
            "candidates": len(out.get("candidates", [])),
            "timings_ms": r.get("timings_ms"),
            "filters": r.get("filters"),
        }
    if node == "select_grade":
        g = out.get("grade", {})
        return {"verdict": g.get("verdict"), "selected": g.get("selected"), "missing": g.get("missing")}
    if node == "web":
        w = out.get("web", {})
        return {
            "provider": w.get("provider"),
            "kept": len(out.get("web_docs", [])),
            "rejected": len(w.get("rejected", [])),
            "error": w.get("error"),
        }
    if node == "pack":
        return {
            "pack": out.get("pack_id"),
            "reports": [h["id"] for h in out.get("reports", [])],
            "web": [d["id"] for d in out.get("web_docs", [])],
        }
    return {}


NODES = [
    kit.Node(
        "plan",
        plan,
        "План поиска: запросы RU+EN, агентства, свежесть",
        "llm",
        ("research.max_queries", "research.max_web_queries", "llm.subagent.model"),
    ),
    kit.Node(
        "retrieve",
        retrieve,
        "Гибридный поиск по отчётам (BGE-M3 dense+sparse, Qdrant, RRF)",
        "db",
        ("research.select_candidates", "retrieval.prefetch", "retrieval.latest_edition_bonus"),
    ),
    kit.Node(
        "select_grade",
        select_grade,
        "Отбор фрагментов и оценка достаточности отчётов",
        "llm",
        ("retrieval.top_k", "research.select_text_chars", "llm.subagent.model"),
    ),
    kit.Node(
        "web",
        web_node,
        "Веб-поиск по уровням доверия, фильтр жёлтой прессы",
        "web",
        ("web.max_results", "research.max_web_queries"),
    ),
    kit.Node("pack", pack, "Пакет доказательств: журнал R#/W#, решения, текст для агента"),
]
EDGES = [
    ("plan", "retrieve"),
    ("retrieve", "select_grade"),
    ("select_grade", "web"),
    ("select_grade", "pack"),
    ("web", "pack"),
]
ROUTERS = {"select_grade": route_after_grade}
# правило приоритета источников (Р4) — подписи ветвлений на карте консоли; само правило — web_reasons и web.yaml
EDGE_LABELS = {
    ("select_grade", "web"): "нужен веб: свежесть, отчётов мало или разрыв по датам (web.policy)",
    ("select_grade", "pack"): "отчётов достаточно — веб не нужен",
}


@functools.cache
def graph():
    return kit.build(ResearchState, NODES, EDGES, ROUTERS, summarize)


def run(
    question: str,
    focus: str = "",
    mode: str = "research",
    queries: list[str] | None = None,
    orgs: list[str] | None = None,
    editions: list[str] | None = None,
    tool: str = "research",
) -> kit.ToolRun:
    inputs: ResearchState = {
        "question": question,
        "focus": focus,
        "today": time.strftime("%Y-%m-%d"),
        "mode": mode,
        "given_queries": queries or [],
        "given_orgs": orgs or [],
        "given_editions": editions or [],
        "decisions": [],
    }
    args = {"question": question, "focus": focus, "queries": queries, "orgs": orgs, "editions": editions}
    return kit.run_graph(tool, graph(), dict(inputs), {k: v for k, v in args.items() if v}, question=question)

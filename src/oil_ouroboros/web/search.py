"""
Веб-поиск для свежих данных (новости, текущие котировки, заявления регуляторов) с фильтром источников.

Провайдеры за общим интерфейсом: **Tavily** (основной: allow/deny-фильтры доменов, тема news, диапазон дат),
**Brave Search API** (резерв, лидер независимого бенчмарка качества AIMultiple), **DuckDuckGo/ddgs** (без ключа,
неофициальный, с лимитами — офлайн-разработка). Google CSE не используется: закрыт для новых клиентов до 01.01.2027.

Стратегия (по уровням доверия, «сначала надёжные»):
  проход 1 — только домены уровней 0–1 (include_domains), свежесть по запросу;
  проход 2 — если хороших результатов < 3: открытый поиск с exclude_domains = deny-список;
  затем каждый результат оценивается credibility.assess(): deny отбрасывается с кодом причины, остальные
  сортируются по (уровень, релевантность провайдера), не больше 2 результатов с одного домена.

Устойчивость: если у провайдера кончился лимит или ключ недействителен (HTTP 401/402/403/429/432), поиск
переходит к следующему в цепочке (Brave, если есть ключ; затем DuckDuckGo, если WEB_FALLBACK=ddgs) и пишет это
в проход («tavily→ddgs: лимит»). Провайдер с исчерпанным лимитом пропускается 30 минут.

Кэш выдачи: одинаковый запрос → одна и та же выдача в пределах окна WEB_CACHE_HOURS (по умолчанию 6 ч; 0 — без
кэша) или постоянного каталога WEB_CACHE_DIR. Агент зовёт инструменты по MCP без признака «это eval», поэтому кэш
общий: повторы одного вопроса (eval, демо, тестировщики) видят один веб и не сжигают лимит поискового API.

Бюджет времени (WEB_TIME_BUDGET_S): новые проходы не начинаются, если поиск уже занял бюджет, — инструмент
research укладывается в таймаут MCP-вызова агента.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from .. import catalog, net
from ..config import get_settings
from .credibility import assess, policy

log = logging.getLogger(__name__)


@dataclass
class WebDoc:
    url: str
    title: str
    content: str
    published: str
    provider: str
    provider_score: float
    domain: str = ""
    tier: int | None = None
    reason: str = ""
    pc1: float | None = None
    state_affiliated: bool = False
    query: str = ""

    @property
    def label(self) -> str:
        src = self.domain
        day = norm_date(self.published)
        date = f", {day}" if day else ""
        mark = ", гос. СМИ" if self.state_affiliated else ""
        return f"Источник: {src}, web{date}, уровень {self.tier}{mark}"

    def as_dict(self) -> dict:
        d = asdict(self)
        d["label"] = self.label
        return d


def _bare(url: str) -> str:
    return url.split("://", 1)[-1].removeprefix("www.").lower()


def norm_url(url: str) -> str:
    """Адрес для сравнения: без схемы, www, запроса, якоря и хвостового «/»."""
    return _bare(url.split("#", 1)[0].split("?", 1)[0]).rstrip("/")


def report_urls_in_base() -> frozenset[str]:
    """Точные адреса выпусков, которые уже есть в базе отчётов: их цитируют как [Отчёт …], а не как веб.

    Только адреса самих выпусков — шаблоны ссылок серии для их месяцев и ссылка скачивания, если она не общая
    (страница серии, «последний выпуск»). Страницы серии и выпуски, которых в базе ещё нет, из веба проходят: новый
    OMR или STEO агент должен увидеть до того, как наблюдатель его скачает."""
    from ..reports import editions, watch

    series = {(s.org, s.report): s for s in catalog.get().reports.sources}
    out: set[str] = set()
    for d in editions.load():
        s = series.get((d.org, d.report))
        if not d.path.is_file() or s is None:
            continue
        out |= {norm_url(u.format(**watch.edition_subst(d.edition))) for u in s.urls}
        if d.download.startswith("http") and norm_url(d.download) not in {norm_url(s.page), norm_url(s.latest_url)}:
            out.add(norm_url(d.download))
    return frozenset(out)


def norm_date(raw: str) -> str:
    """Дата публикации → ГГГГ-ММ-ДД (ISO, RFC 2822 «Fri, 16 Oct 2026 …», «2026-10-16T…»); не распознана — пусто."""
    import re
    from email.utils import parsedate_to_datetime

    raw = (raw or "").strip()
    if not raw:
        return ""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    try:
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return ""


@dataclass
class WebResult:
    docs: list[WebDoc]
    rejected: list[WebDoc]
    provider: str
    passes: list[dict] = field(default_factory=list)
    policy_version: str = ""
    error: str = ""
    ms: int = 0

    def summary(self, freshness: str) -> dict:
        """Итог поиска для трейса и решения в пакете: провайдер, проходы, отсеянное с причиной."""
        return {
            "provider": self.provider,
            "passes": self.passes,
            "rejected": [x.as_dict() for x in self.rejected],
            "policy_version": self.policy_version,
            "error": self.error,
            "ms": self.ms,
            "freshness": freshness,
        }


def _client() -> httpx.Client:
    return net.outbound(30.0)


def _tavily(
    query: str, include: list[str] | None, exclude: list[str] | None, fresh: str | None, n: int
) -> list[WebDoc]:
    s = get_settings()
    body: dict = {
        "query": query,
        "search_depth": "basic",
        "max_results": n,
        "topic": "news" if fresh else "general",
        "include_answer": False,
    }
    if include:
        body["include_domains"] = include[:300]
    if exclude:
        body["exclude_domains"] = exclude[:150]
    if fresh:
        body["time_range"] = fresh  # day | week | month | year
    with _client() as c:
        r = c.post("https://api.tavily.com/search", json=body, headers={"Authorization": f"Bearer {s.tavily_api_key}"})
        r.raise_for_status()
        data = r.json()
    return [
        WebDoc(
            url=x.get("url", ""),
            title=x.get("title", ""),
            content=(x.get("content") or "")[:1500],
            published=x.get("published_date") or "",
            provider="tavily",
            provider_score=float(x.get("score") or 0.0),
            query=query,
        )
        for x in data.get("results", [])
    ]


def _brave(
    query: str, include: list[str] | None, exclude: list[str] | None, fresh: str | None, n: int
) -> list[WebDoc]:
    s = get_settings()
    q = query
    if include:
        q += " (" + " OR ".join(f"site:{d}" for d in _site_operators(include)) + ")"
    params = {"q": q, "count": n}
    if fresh:
        params["freshness"] = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}.get(fresh, "pm")
    with _client() as c:
        r = c.get(
            "https://api.search.brave.com/res/v1/web/search",
            params=params,
            headers={"X-Subscription-Token": s.brave_api_key, "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
    res = data.get("web", {}).get("results", [])
    return [
        WebDoc(
            url=x.get("url", ""),
            title=x.get("title", ""),
            content=(x.get("description") or "")[:1500],
            published=x.get("page_age") or x.get("age") or "",
            provider="brave",
            provider_score=1.0 - i / max(len(res), 1),
            query=query,
        )
        for i, x in enumerate(res)
    ]


def _site_operators(include: list[str]) -> list[str]:
    """Провайдеры без списка доменов (Brave, DuckDuckGo): доверенный проход — операторы site: по ключевым доменам
    из политики (source_tiers.yaml → site_operators, по важности), а не первые домены по алфавиту."""
    return list(policy().site_operators) or include[:12]


def _ddgs(query: str, include: list[str] | None, exclude: list[str] | None, fresh: str | None, n: int) -> list[WebDoc]:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    s = get_settings()
    if include:
        query = f"{query} (" + " OR ".join(f"site:{d}" for d in _site_operators(include)) + ")"
    try:
        return _ddgs_rows(DDGS, s, query, fresh, n)
    except DDGSException as exc:  # пустая выдача у ddgs — исключение, а для поиска это обычный исход
        if "no results" in str(exc).lower():
            return []
        raise


def _ddgs_rows(DDGS, s, query: str, fresh: str | None, n: int) -> list[WebDoc]:  # noqa: N803
    with DDGS(proxy=s.outbound_proxy or None, timeout=20) as d:
        if fresh:
            timelimit = {"day": "d", "week": "w", "month": "m", "year": "y"}.get(fresh, "m")
            rows = d.news(query, max_results=n, timelimit=timelimit)
            return [
                WebDoc(
                    x.get("url", ""),
                    x.get("title", ""),
                    (x.get("body") or "")[:1500],
                    x.get("date", ""),
                    "ddgs",
                    1.0 - i / max(len(rows), 1),
                    query=query,
                )
                for i, x in enumerate(rows)
            ]
        rows = d.text(query, max_results=n)
        return [
            WebDoc(
                x.get("href", ""),
                x.get("title", ""),
                (x.get("body") or "")[:1500],
                "",
                "ddgs",
                1.0 - i / max(len(rows), 1),
                query=query,
            )
            for i, x in enumerate(rows)
        ]


PROVIDERS = {"tavily": _tavily, "brave": _brave, "ddgs": _ddgs}
QUOTA_CODES = (401, 402, 403, 429, 432)  # ключ недействителен или лимит исчерпан (432 — лимит тарифа Tavily)
_down: dict[str, tuple[float, str]] = {}  # провайдер → (до какого времени не звать, причина)


def chain() -> list[str]:
    """Основной провайдер и резервные: при исчерпанном лимите основного поиск не пропадает целиком."""
    s = get_settings()
    first = active_provider()
    if first == "none":
        return ["none"]
    out = [first]
    if s.brave_api_key and "brave" not in out:
        out.append("brave")
    if s.web_fallback == "ddgs" and "ddgs" not in out:
        out.append("ddgs")
    return out


def _cache_dir() -> str:
    """Каталог кэша: постоянный WEB_CACHE_DIR или окно WEB_CACHE_HOURS в var/webcache."""
    s = get_settings()
    if s.web_cache_dir:
        return str(s.web_cache_dir)
    if s.web_cache_hours <= 0:
        return ""
    bucket = int(time.time() // (s.web_cache_hours * 3600))
    return str(s.var_dir / "webcache" / f"w{int(s.web_cache_hours)}h-{bucket}")


def _cache_file(name: str, q: str, include, exclude, fresh, n: int) -> Path | None:
    d = _cache_dir()
    if not d:
        return None
    key = json.dumps([name, q, include or [], exclude or [], fresh, n], ensure_ascii=False)
    return Path(d) / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}.json"


def _call(name: str, q: str, include, exclude, fresh, n: int) -> tuple[list[WebDoc], str]:
    """Один запрос к провайдеру через кэш: (документы, откуда — live | cache)."""
    f = _cache_file(name, q, include, exclude, fresh, n)
    if f is not None and f.is_file():
        try:
            return [WebDoc(**x) for x in json.loads(f.read_text(encoding="utf-8"))], "cache"
        except (OSError, ValueError, TypeError):
            pass
    docs = PROVIDERS[name](q, include, exclude, fresh, n)
    if f is not None:
        try:  # каталог может быть только для чтения (оценка кандидата): тогда просто не сохраняем
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps([asdict(d) for d in docs], ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
        except OSError:
            pass
    return docs, "live"


def _search_one(
    names: list[str], q: str, include, exclude, fresh, n: int, passes: list[dict], mode: str
) -> list[WebDoc]:
    """Запрос по цепочке провайдеров: следующий — только если у текущего кончился лимит или ключ."""
    tried: list[str] = []
    for name in names:
        until, why = _down.get(name, (0.0, ""))
        if until > time.time():
            tried.append(f"{name}: {why}")
            continue
        try:
            docs, src = _call(name, q, include, exclude, fresh, n)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code not in QUOTA_CODES:
                raise
            why = "лимит исчерпан" if code in (402, 429, 432) else f"ключ отклонён (HTTP {code})"
            _down[name] = (time.time() + 1800, why)
            log.warning("веб-поиск: %s — %s, переход к следующему провайдеру", name, why)
            tried.append(f"{name}: {why}")
            continue
        p = {"query": q, "mode": mode, "results": len(docs), "provider": name, "source": src}
        if tried:
            p["fallback"] = "; ".join(tried)
        passes.append(p)
        return docs
    raise RuntimeError("все провайдеры недоступны: " + "; ".join(tried))


def active_provider() -> str:
    s = get_settings()
    if s.web_provider != "auto":
        return s.web_provider
    if s.tavily_api_key:
        return "tavily"
    if s.brave_api_key:
        return "brave"
    return "ddgs"


def search(queries: list[str], freshness: str | None = "month", max_results: int | None = None) -> WebResult:
    """Запросов за вызов — не больше research.max_web_queries (каталог): каждый — доверенный проход и при нужде
    открытый, запросы идут параллельно."""
    c = catalog.get()
    n = max_results or c.web.max_results
    queries = [q for q in queries if q and q.strip()][: c.research.max_web_queries]
    names = chain()
    name = names[0]
    pol = policy()
    if name == "none":
        return WebResult([], [], name, policy_version=pol.version, error="веб-поиск выключен (WEB_PROVIDER=none)")
    trusted = sorted(d for d, t in pol.tiers.items() if t <= 1)
    deny = sorted(pol.deny)
    t0 = time.monotonic()
    raw: list[WebDoc] = []
    passes: list[dict] = []
    error = ""
    budget = get_settings().web_time_budget_s

    def one(q: str) -> tuple[list[WebDoc], list[dict], str]:
        """Запрос: сначала домены уровней 0–1, открытый поиск — если надёжных мало и бюджет времени не исчерпан."""
        local: list[dict] = []
        try:
            got = _search_one(names, q, trusted, None, freshness, n, local, "trusted_tier0_1")
            out = list(got)
            good = [d for d in got if assess(d.url).tier in (0, 1)]
            if len(good) < 3:
                if time.monotonic() - t0 <= budget:
                    out.extend(_search_one(names, q, None, deny, freshness, n, local, "open_exclude_deny"))
                else:
                    local.append(
                        {"query": q, "mode": "skipped", "note": f"бюджет времени веб-поиска {budget:.0f} с исчерпан"}
                    )
            if not out and freshness in ("day", "week") and time.monotonic() - t0 <= budget:
                # за неделю ничего: последние новости за месяц (дату каждой агент видит в метке и оговорит)
                out.extend(_search_one(names, q, None, deny, "month", n, local, "open_month_fallback"))
            return out, local, ""
        except Exception as exc:  # noqa: BLE001 — сбой веба не роняет ответ: агент сообщит об этом явно
            err = f"{name}: {type(exc).__name__}: {exc}"[:300]
            log.warning("веб-поиск: %s", err)
            local.append({"query": q, "mode": "error", "error": err})
            return [], local, err

    # запросы — параллельно: у DuckDuckGo через прокси один запрос идёт 5–20 с, по очереди веб съедал полминуты
    with ThreadPoolExecutor(max_workers=max(len(queries), 1)) as ex:
        for got, local, err in ex.map(one, queries):
            raw.extend(got)
            passes.extend(local)
            error = error or err
    seen: set[str] = set()
    per_domain: dict[str, int] = {}
    kept: list[WebDoc] = []
    rejected: list[WebDoc] = []
    in_base = report_urls_in_base()
    for d in raw:
        v = assess(d.url)
        d.domain, d.tier, d.reason, d.pc1, d.state_affiliated = v.domain, v.tier, v.reason, v.pc1, v.state_affiliated
        key = d.url.split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        if norm_url(key) in in_base:  # выпуск из базы отчётов: цитируется как [Отчёт …], а не как веб
            d.reason += "|report_in_base"
            rejected.append(d)
            continue
        if not v.allowed:
            rejected.append(d)
            continue
        kept.append(d)
    kept.sort(key=lambda d: (d.tier, -d.provider_score))
    final: list[WebDoc] = []
    for d in kept:
        if per_domain.get(d.domain, 0) >= 2:
            d.reason += "|domain_cap"
            rejected.append(d)
            continue
        per_domain[d.domain] = per_domain.get(d.domain, 0) + 1
        final.append(d)
    for d in final[n:]:
        d.reason += "|limit"
        rejected.append(d)
    used = sorted({p["provider"] for p in passes if p.get("provider")}, key=names.index)
    return WebResult(
        docs=final[:n],
        rejected=rejected,
        provider="→".join(used) if used else name,  # «tavily→ddgs» — основной отказал, ответ из резервного
        passes=passes,
        policy_version=pol.version,
        error=error,
        ms=int((time.monotonic() - t0) * 1000),
    )

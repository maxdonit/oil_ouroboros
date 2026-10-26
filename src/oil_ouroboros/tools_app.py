"""
HTTP-сервис инструментов (контейнер tools): MCP для Ouroboros (/mcp, официальный MCP SDK, без сессий, Bearer)
+ API для консоли (трейсы инструментов,
журнал доказательств, каталог параметров, схемы графов, прямой вызов прогноза).

Консоль и агент — разные клиенты: агенту — только MCP по токену; консоли — /api/* во внутренней сети (наружу порт
tools не публикуется).
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.transport_security import TransportSecuritySettings

from . import __version__, catalog, freeze
from .config import get_settings
from .evidence import ledger
from .forecast import service as fc
from .graphs import calc, kit, research, runs, search_web, verify_answer
from .mcp.server import server
from .rag import vectors
from .reports import editions

log = logging.getLogger(__name__)


# MCP: без сессий и потоков — клиент Ouroboros открывает новую сессию на каждый вызов; сеть внутренняя, вход по токену,
# поэтому проверка Host (защита от DNS rebinding для localhost) не нужна
_mcp_app = server().streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    max_request_body_size=1_000_000,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _warmup()
    async with server().session_manager.run():
        yield


app = FastAPI(
    title="oil-ouroboros tools",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=_lifespan,
)
_warm: dict = {"status": "pending"}


def _warmup() -> None:
    """Прогрев в фоне: кодировщик вопроса и бэктесты прогноза — первый вопрос не ждёт их (холодный бэктест ~45 с)."""

    def work() -> None:
        t0 = time.monotonic()
        try:
            vectors.get_embedder().embed(["warmup"], is_query=True)
            for b in ("brent", "wti"):
                fc.forecast(b, 3)
            _warm.update(status="ready", seconds=round(time.monotonic() - t0, 1))
        except Exception as exc:  # noqa: BLE001
            _warm.update(status="error", error=str(exc)[:300])
            log.warning("прогрев: %s", exc)

    threading.Thread(target=work, daemon=True).start()
    from .forecast import data as prices
    from .reports import watch

    def warm_forecasts() -> None:  # новая дата цен — новый ключ кэша бэктеста: прогреть, чтобы вопрос не ждал ~45 с
        for b in ("brent", "wti"):
            fc.forecast(b, 3)

    prices.start_refresh_scheduler(on_update=warm_forecasts)
    watch.start_scheduler()  # первая проверка выпусков — вскоре после старта, дальше по расписанию


def _same_token(presented: str, expected: str) -> bool:
    """Сверка токена за постоянное время (длина и содержимое не утекают через время ответа)."""
    return bool(expected) and hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def _presented(request: Request) -> str:
    """«Bearer <токен>» или токен как есть (так шлёт клиент Ouroboros)."""
    hdr = request.headers.get("authorization", "")
    scheme, _, value = hdr.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else hdr.strip()


@app.middleware("http")
async def api_token(request: Request, call_next):
    """Служебное API (/api/*) — только консоли; Ouroboros в той же сети ходит только в /mcp со своим токеном."""
    s, path = get_settings(), request.url.path
    if path.startswith("/mcp"):
        if not s.mcp_token:
            return JSONResponse({"error": "MCP выключен: задайте MCP_TOKEN"}, status_code=404)
        if not _same_token(_presented(request), s.mcp_token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    elif s.tools_api_token and path.startswith("/api/") and path != "/api/health":
        if not _same_token(_presented(request), s.tools_api_token):
            return JSONResponse({"error": "нужен токен консоли (TOOLS_API_TOKEN)"}, status_code=401)
    return await call_next(request)


@app.get("/api/health")
def health() -> Any:
    """ok — всё готово; degraded — работает, но база отчётов ещё строится; 503 — каталог невалиден (инструменты
    вели бы себя не так, как описано), поэтому Ouroboros с такими инструментами не стартует."""
    s = get_settings()
    try:
        idx: int | str = vectors.count(s.qdrant_collection)
    except Exception as exc:  # noqa: BLE001
        idx = f"ошибка: {exc}"
    from .web.search import active_provider

    cat = catalog.health()
    ok = cat["ok"] and isinstance(idx, int) and idx > 0
    out = {
        "status": "ok" if ok else "degraded" if cat["ok"] else "invalid_catalog",
        "version": __version__,
        "catalog": cat,
        "index_chunks": idx,
        "warmup": _warm,
        "web_provider": active_provider(),
        "mcp": bool(s.mcp_token),
        "evidence": ledger.stats(),
        "reranker": s.reranker,
        "embedder": s.embedder,
        "prices": _prices_status(),
    }
    return out if cat["ok"] else JSONResponse(out, status_code=503)


def _prices_status() -> dict:
    from .forecast import data as prices

    st = prices.refresh_status()
    return {
        "as_of": st.get("as_of"),
        "last_refresh": st.get("last_run"),
        "next_refresh": st.get("next"),
        "result": st.get("result"),
        "skipped": st.get("skipped"),
    }


# --------------------------------------------------------------------------------------------- консоль


@app.get("/api/runs/{run_id}")
def run(run_id: str) -> dict:
    r = runs.get_run(run_id)
    if not r:
        raise HTTPException(404, "нет прогона")
    return r


@app.get("/api/evidence")
def evidence(ids: str) -> dict:
    return ledger.get([x.strip() for x in ids.split(",") if x.strip()])


@app.post("/api/packs")
def pack_of_dialog(payload: dict = Body(...)) -> dict:
    """Пакет диалога (консоль): доказательства прошлых реплик разговора. Агент передаёт его в verify_answer вместе с
    пакетами хода — ссылки из истории разговора тогда свои, а не чужие."""
    ids = list(ledger.get([str(x) for x in payload.get("ids") or []]))  # только то, что есть в журнале
    if not ids:
        raise HTTPException(400, "нет доказательств для пакета")
    return {
        "pack_id": ledger.new_pack("dialog", ids, question=str(payload.get("question") or "")[:500]),
        "ids": len(ids),
    }


@app.post("/api/finalize")
def finalize(payload: dict = Body(...)) -> dict:
    """Финал хода консоли: какой текст увидит пользователь — ответ агента, перепроверенный по доказательствам именно
    этого хода (прогоны его инструментов и история разговора), с метками источников и строкой проверки от кода
    (graphs/verify_answer.finalize)."""
    return verify_answer.finalize(
        str(payload.get("answer") or ""),
        str(payload.get("question") or ""),
        [str(x) for x in payload.get("traces") or []],
        [str(x) for x in payload.get("history_ids") or []],
    )


@app.post("/api/eval_lock")
def eval_lock(payload: dict = Body(...)) -> dict:
    """Замер eval через агента (консоль): данные сервиса инструментов заморожены, пока он идёт (freeze.py). Консоль и
    инструменты — разные тома var, поэтому блокировку ставит этот вызов, а не файл консоли."""
    if payload.get("active"):
        freeze.hold(str(payload.get("eval_id") or ""))
    else:
        freeze.release()
    return {"locked": freeze.held()}


@app.get("/api/catalog")
def catalog_view(texts: bool = False) -> dict:
    return catalog.describe(texts=texts)


# графы инструментов: какой граф отвечает за какие инструменты MCP (тест сверяет с mcp/server.TOOLS)
GRAPHS = [
    ("research", "Исследование: отчёты → достаточность → веб по правилу", ("research", "search_reports"), research),
    ("search_web", "Точечный веб-поиск после отчётов", ("search_web",), search_web),
    (
        "calc",
        "Расчётный модуль: прогноз, сценарий, история цен, котировки",
        ("forecast_price", "supply_shock_scenario", "price_history", "price_quote"),
        calc,
    ),
    ("verify_answer", "Проверка ответа: каждая цифра ↔ источник", ("verify_answer",), verify_answer),
]


@app.get("/api/graphs")
async def graphs() -> dict:
    """Графы инструментов из кода (узлы, вид, параметры каталога с текущими значениями, ветвления) и схемы
    инструментов MCP — для вкладки консоли «Архитектура»."""
    return {
        "graphs": [
            {
                "id": gid,
                "title": title,
                "tools": list(tools),
                "file": f"src/oil_ouroboros/graphs/{gid}.py",
                **kit.describe(m.NODES, m.EDGES, getattr(m, "ROUTERS", None), getattr(m, "EDGE_LABELS", None)),
            }
            for gid, title, tools, m in GRAPHS
        ],
        "mcp_tools": [
            {"name": t.name, "description": t.description, "schema": t.input_schema}
            for t in await server().list_tools()
        ],
    }


@app.get("/api/reports")
def reports() -> list[dict]:
    return [d.__dict__ for d in editions.load()]


@app.get("/api/sources")
def sources_view() -> dict:
    """Источники агента для вкладки «Источники»: отчёты агентств и наблюдатель выпусков, веб-поиск и политика доверия
    к доменам, цены EIA расчётного модуля, котировки сортов (без запроса к бирже — последние сохранённые)."""
    from .forecast import data as prices
    from .forecast import quotes
    from .reports import watch
    from .web.credibility import TIER_TITLES, policy
    from .web.search import active_provider

    c, s = catalog.get(), get_settings()
    w = watch.status()
    series = []
    for key, meta in prices.SERIES.items():
        row = {
            "benchmark": key,
            "title": meta["title"],
            "eia_id": meta["eia_id"],
            "url": prices.EIA_XLS_URL.format(**meta),
            "unit": meta["unit"],
        }
        try:
            p = prices.load(key)
            row |= {
                "as_of": str(p.as_of),
                "last_price": p.last_price,
                "snapshot": not Path(p.source_file).is_relative_to(s.var_dir),
            }
        except (OSError, ValueError) as exc:
            row["error"] = str(exc)
        series.append(row)
    keep = ("series", "catalog", "last", "pending", "every_hours", "next_run", "auto_index", "running", "statuses")
    return {
        "reports": {k: w[k] for k in keep} | {"journal": w["journal"][:20]},
        "web": {
            "provider": active_provider(),
            "max_results": c.web.max_results,
            "min_tier_for_numbers": c.web.min_tier_for_numbers,
            "policy": c.web.policy.model_dump(),
            "tiers": c.source_tiers.model_dump(),
            "titles": TIER_TITLES,
            "rated_domains": len(policy().pc1),
            "used": ledger.web_domains(),
        },
        "prices": {"series": series, "every_hours": s.prices_refresh_hours} | _prices_status(),
        "quote": quotes.status(),
    }


@app.get("/api/sources/domain")
def source_domain(url: str) -> dict:
    """Уровень доверия к адресу или домену — по тем же правилам, что и фильтр веб-поиска."""
    from .web.credibility import REASON_TITLES, TIER_TITLES, assess

    url = url.strip()
    v = assess(url if "://" in url else f"https://{url}")
    return {
        "domain": v.domain,
        "tier": v.tier,
        "allowed": v.allowed,
        "title": TIER_TITLES.get(v.tier, "отклонён"),
        "reason": REASON_TITLES.get(v.reason, v.reason),
        "pc1": v.pc1,
        "state_affiliated": v.state_affiliated,
    }


@app.get("/api/forecast")
def forecast(benchmark: str = "brent", horizon: int = 3, method: str = "auto") -> dict:
    return fc.forecast(benchmark, horizon, method).as_dict()


@app.get("/api/forecast/history")
def forecast_history(benchmark: str = "brent", start: str = "2024-01-01", freq: str = "M") -> dict:
    return fc.history(benchmark, start, freq)


@app.get("/api/forecast/scenario")
def forecast_scenario(
    delta_mbd: float, benchmark: str = "brent", horizon: int = 3, compliance: float = 1.0, offset: float = 0.0
) -> dict:
    return fc.shock(delta_mbd, benchmark, horizon, compliance, offset)


app.mount("/", _mcp_app)  # после всех маршрутов /api/*: приложение SDK отвечает только на /mcp


def main() -> None:
    import uvicorn

    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(app, host=s.api_host, port=s.api_port, log_level="warning")


if __name__ == "__main__":
    main()

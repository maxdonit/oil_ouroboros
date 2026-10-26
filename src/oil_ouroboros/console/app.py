"""
Консоль (контейнер console): UI для тестирования агента и мониторинг. Только наблюдает: агент — Ouroboros, его
инструменты — сервис tools; консоль задаёт вопросы через API задач Ouroboros, собирает ход из журналов и показывает
его, считает метрики, гоняет eval. Ни память агента, ни параметры инструментов консоль не меняет.

Вкладки: Чат · Демо (записанные ходы по шагам) · Ходы (трейс) · Мониторинг · Качество (eval) · Параметры · Прогноз ·
Источники · Архитектура.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
import uuid

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__, agent, net
from ..config import ROOT, describe_settings, get_settings
from . import demo, deployment, fingerprint, turns

log = logging.getLogger(__name__)
UI_DIR = ROOT / "ui"
app = FastAPI(
    title="oil-ouroboros console", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json"
)

# --------------------------------------------------------------------------------------------- без входа
# Паролей и ролей нет: все эндпоинты открыты. Простой режим (чат и прогноз) и режим администратора (все вкладки)
# выбирает интерфейс (ui/main.js), сервер о них не знает.


@app.middleware("http")
async def no_cache_ui(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/ui/") or path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


# --------------------------------------------------------------------------------------------- служебное


@app.get("/api/health")
def health() -> dict:
    out: dict = {"console": "ok", "version": __version__}
    for name, fn in (
        ("tools", lambda: net.tools_api("/api/health")),
        ("gateway", lambda: net.gateway_api("/health")),
        ("ouroboros", lambda: agent.Ouroboros().health()),
    ):
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001
            out[name] = {"status": "unavailable", "error": str(exc)[:200]}
    t, g, o = out["tools"], out["gateway"], out["ouroboros"]
    out["status"] = (
        "ok"
        if t.get("status") == "ok"
        and g.get("status") == "ok"
        and o.get("status") in ("ok", "healthy", True, None)
        and "error" not in o
        else "degraded"
    )
    return out


# --------------------------------------------------------------------------------------------- чат

_live: dict[str, dict] = {}  # turn_id → прогресс хода (пока он идёт)
_live_lock = threading.Lock()


def _run_turn(turn_id: str, conversation_id: str, question: str) -> None:
    started = time.time()

    def progress(ev: dict) -> None:
        with _live_lock:
            cur = _live.setdefault(turn_id, {})
            cur.update({k: v for k, v in ev.items() if k != "stage"}, stage=ev.get("stage"), updated=time.time())

    try:
        fp = fingerprint.current()
        hist = turns.history(conversation_id)
        hist_ids = [i for h in hist for i in h.get("ids", [])]
        raw = agent.ask(
            question,
            history=hist,
            conversation_id=conversation_id,
            turn_id=turn_id,
            on_progress=progress,
            dialog_pack=turns.dialog_pack(hist_ids, question),
        )
        turn = turns.assemble(raw, fp, history_ids=hist_ids)
    except Exception as exc:  # noqa: BLE001 — ход с ошибкой тоже сохраняется: его видно в мониторинге
        log.exception("ход %s", turn_id)
        turn = {
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "question": question,
            "status": "error",
            "answer": "",
            "error": f"{type(exc).__name__}: {exc}"[:500],
            "ms": int((time.time() - started) * 1000),
            "tool_calls": [],
            "llm_rounds": [],
            "checks": {"answered": False},
        }
    turns.save(turn, source="chat", started=started)
    with _live_lock:
        _live.pop(turn_id, None)


@app.post("/api/chat")
def chat(payload: dict = Body(...)) -> dict:
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "пустой вопрос")
    # разговор — по его id (логинов нет): прежние id вида c-<12 hex>-<10 hex> продолжаются как есть
    conversation_id = str(payload.get("conversation_id") or "") or f"c-{uuid.uuid4().hex[:10]}"
    turn_id = uuid.uuid4().hex[:12]
    with _live_lock:
        _live[turn_id] = {
            "stage": "queued",
            "question": question,
            "conversation_id": conversation_id,
            "started": time.time(),
        }
    threading.Thread(target=_run_turn, args=(turn_id, conversation_id, question), daemon=True).start()
    return {"turn_id": turn_id, "conversation_id": conversation_id}


@app.get("/api/chat/{turn_id}")
def chat_turn(turn_id: str) -> dict:
    with _live_lock:
        live = dict(_live.get(turn_id) or {})
    if live:
        return {"done": False, **live, "elapsed_s": round(time.time() - live.get("started", time.time()), 1)}
    t = turns.get(turn_id)
    if not t:
        raise HTTPException(404, "нет хода")
    return {"done": True, "turn": t}


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str) -> list[dict]:
    return [turns.get(r["turn_id"]) for r in reversed(turns.listing(50, conversation=conversation_id))]


# --------------------------------------------------------------------------------------------- ходы и мониторинг


@app.get("/api/turns")
def turn_list(limit: int = 100, source: str = "") -> list[dict]:
    return turns.listing(limit, source)


@app.get("/api/turns/{turn_id}")
def turn_detail(turn_id: str) -> dict:
    t = turns.get(turn_id)
    if not t:
        raise HTTPException(404, "нет хода")
    return t


@app.get("/api/tool_runs/{trace}")
def tool_run(trace: str) -> dict:
    """Полный прогон инструмента (узлы графа, вызовы LLM субагентов с промптами, решения) — из сервиса tools."""
    return net.tools_api(f"/api/runs/{trace}")


@app.get("/api/llm")
def llm_calls(since: float = 0.0, caller: str = "", limit: int = 300) -> dict:
    """Журнал LLM-шлюза: все вызовы системы — агент (Ouroboros) и субагенты."""
    return net.gateway_api("/ledger", {"since": since, "caller": caller, "limit": limit})


@app.get("/api/llm/{call_id}")
def llm_call(call_id: str) -> dict:
    """Тело вызова LLM: что именно агент отправил модели и что получил (раунд Ouroboros целиком)."""
    return net.gateway_api(f"/calls/{call_id}")


@app.get("/api/metrics")
def metrics(hours: float = 24 * 7, source: str = "chat") -> dict:
    since = time.time() - hours * 3600 if hours else 0.0
    out = turns.metrics(since, "" if source in ("", "all") else source)  # all — ходы всех источников
    try:
        g = net.gateway_api("/health")
        out["budget"] = {
            "spent_usd": g.get("spent_usd"),
            "budget_usd": g.get("budget_usd"),
            "by_caller": g.get("by_caller"),
            "refused": g.get("refused"),
        }
    except Exception as exc:  # noqa: BLE001
        out["budget"] = {"error": str(exc)[:200]}
    return out


# --------------------------------------------------------------------------------------------- качество (eval)

_eval_job: dict = {"active": False}
_eval_lock = threading.Lock()


@app.get("/api/evals")
def evals_list() -> list[dict]:
    from ..evals import runner

    return runner.list_runs()


@app.get("/api/evals/status")
def evals_status() -> dict:
    return dict(_eval_job)


@app.post("/api/evals/run")
def evals_run(payload: dict = Body(default={})) -> dict:
    from ..evals import runner

    only = payload.get("only") or None
    label = str(payload.get("label") or "")
    repeats = int(payload.get("repeats") or 1)
    with _eval_lock:  # флаг ставится до старта потока: двойной POST не запустит два платных прогона
        if _eval_job.get("active"):
            raise HTTPException(409, "eval уже идёт")
        _eval_job.update(active=True, started=time.time(), label=label, only=only, error="", eval_id="")

    def work() -> None:
        try:
            rec = runner.run(ROOT / "evals" / "datasets" / "dev.jsonl", repeats, label, only)
            _eval_job["eval_id"] = rec["eval_id"]
        except Exception as exc:  # noqa: BLE001
            _eval_job["error"] = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            _eval_job["active"] = False

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@app.get("/api/evals/compare")
def evals_compare(base: str, cand: str) -> dict:
    from ..evals import runner

    return runner.compare(runner.load(base), runner.load(cand))


@app.get("/api/evals/{eval_id}")
def eval_get(eval_id: str) -> dict:
    from ..evals import runner

    try:
        return runner.load(eval_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, "нет прогона") from exc


# --------------------------------------------------------------------------------------------- параметры


@app.get("/api/params")
def params() -> dict:
    """Всё, что определяет поведение агента, с версиями: регламент и память Ouroboros, его настройки, каталог
    параметров инструментов, окружение. Основа для будущего самоулучшения: что можно менять и что изменилось."""
    fp = fingerprint.current(max_age_s=0)
    try:
        cat = net.tools_api("/api/catalog")
    except Exception as exc:  # noqa: BLE001
        cat = {"error": str(exc)[:200]}
    return {
        "fingerprint": fp,
        "tools_catalog": cat,
        "journals": fingerprint.journals(),
        "console_settings": describe_settings(),
        "disabled_tools": agent.disabled_tools(),
        "turn_rules": agent.TURN_RULES,
    }


@app.get("/api/params/memory")
def params_memory(file: str) -> dict:
    try:
        return {"file": file, "text": fingerprint.memory_text(file)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# --------------------------------------------------------------------------------------------- прогноз, отчёты, карта


@app.get("/api/forecast")
def forecast(benchmark: str = "brent", horizon: int = 3, method: str = "auto") -> dict:
    return net.tools_api("/api/forecast", {"benchmark": benchmark, "horizon": horizon, "method": method})


@app.get("/api/forecast/history")
def forecast_history(benchmark: str = "brent", start: str = "2024-01-01", freq: str = "M") -> dict:
    return net.tools_api("/api/forecast/history", {"benchmark": benchmark, "start": start, "freq": freq})


@app.get("/api/forecast/scenario")
def forecast_scenario(
    delta_mbd: float, benchmark: str = "brent", horizon: int = 3, compliance: float = 1.0, offset: float = 0.0
) -> dict:
    return net.tools_api(
        "/api/forecast/scenario",
        {
            "delta_mbd": delta_mbd,
            "benchmark": benchmark,
            "horizon": horizon,
            "compliance": compliance,
            "offset": offset,
        },
    )


@app.get("/api/sources")
def sources() -> dict:
    return net.tools_api("/api/sources")


@app.get("/api/sources/domain")
def source_domain(url: str) -> dict:
    return net.tools_api("/api/sources/domain", {"url": url})


@functools.lru_cache(maxsize=1)
def _replay() -> dict:
    return demo.replay()


@app.get("/api/demo")
def demo_list() -> dict:
    """Записанные демо-ходы (data/demo/turns.json) для проигрывателя: работают без агента и без LLM."""
    rec = _replay()
    return {
        "recorded": rec.get("recorded", ""),
        "turns": [
            {
                "i": i,
                "title": t.get("title", ""),
                "question": t.get("question", ""),
                "conversation_id": t.get("conversation_id", ""),
                "ms": t.get("ms"),
                "cost_usd": t.get("cost_usd"),
                "rounds": t.get("rounds"),
                "tools": [c["tool"] for c in t.get("tool_calls", []) if c.get("tool") != "finish_task"],
                "agent_version": (t.get("fingerprint") or {}).get("agent_version", ""),
            }
            for i, t in enumerate(rec.get("turns", []))
        ],
    }


@app.get("/api/demo/{i}")
def demo_turn(i: int) -> dict:
    items = _replay().get("turns", [])
    if not 0 <= i < len(items):
        raise HTTPException(404, "нет такого демо-хода")
    return items[i]


@app.get("/api/about")
def about() -> dict:
    """Вкладка «Архитектура»: развёртывание из docker-compose.yml, MCP-инструменты, правила хода (графы инструментов —
    отдельно, /api/graphs)."""
    try:
        graphs = net.tools_api("/api/graphs")
    except Exception as exc:  # noqa: BLE001
        graphs = {"error": str(exc)[:200]}
    return {"deployment": deployment.describe(), "graphs": graphs, "turn_rules": agent.TURN_RULES}


ROOT_DOCS = {"_root/README.md": "README.md", "_root/config/README.md": "config/README.md"}


@app.get("/docs/{path:path}")
def docs(path: str) -> Response:
    if path in ROOT_DOCS:
        return FileResponse(ROOT / ROOT_DOCS[path], media_type="text/markdown; charset=utf-8")
    base = (ROOT / "docs").resolve()
    f = (base / path).resolve()
    if not f.is_relative_to(base) or not f.is_file():
        raise HTTPException(404, "нет документа")
    return FileResponse(f, media_type="text/markdown; charset=utf-8" if f.suffix == ".md" else None)


if UI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")


@app.get("/")
def index() -> Response:
    f = UI_DIR / "index.html"
    return FileResponse(f) if f.is_file() else JSONResponse({"error": "консоль не найдена"}, 404)


def main() -> None:
    import uvicorn

    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(app, host=s.api_host, port=s.api_port, log_level="warning")


if __name__ == "__main__":
    main()

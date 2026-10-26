"""
Сервис кодировщика (контейнер encoder): BGE-M3 (dense + sparse) и, если включён (RERANKER=bge-reranker-v2-m3),
реранкер bge-reranker-v2-m3 в одном процессе; сервис инструментов и загрузка отчётов обращаются к нему по HTTP
(ENCODER_URL).

Зачем отдельный контейнер: модели (~2.2 ГБ BGE-M3, ещё ~2.3 ГБ реранкер) грузятся один раз и не утяжеляют образ
инструментов (torch только здесь). Реранкер на CPU — от десятков секунд до двух минут на вопрос, поэтому по
умолчанию он выключен (RERANKER=none: отбор фрагментов делает LLM в узле select_grade); с GPU — включается одной
настройкой RERANKER=bge-reranker-v2-m3.

    python -m oil_ouroboros.rag.encoder_service        # порт 8020
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI

from . import vectors
from .vectors import BGEM3Embedder, BGEReranker

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _load()  # модели и автозагрузка — в фоновом потоке: сервис отвечает сразу
    yield


app = FastAPI(title="oil-ouroboros encoder", docs_url=None, lifespan=_lifespan)
_models: dict = {}
_lock = threading.Lock()


def _expected() -> list[str]:
    from ..config import get_settings

    return ["emb", "rr"] if get_settings().reranker.startswith("bge") else ["emb"]


def _get(name: str):
    with _lock:
        if name not in _models:
            from ..config import get_settings

            s = get_settings()
            _models[name] = BGEM3Embedder(s.embed_model) if name == "emb" else BGEReranker(s.rerank_model)
            vectors.share_model(name, _models[name])  # загрузка отчётов в этом процессе — той же копией модели
        return _models[name]


_load_error: dict = {"error": "", "attempts": 0}


def _load() -> None:
    """Модели — в фоне при старте (первая загрузка ~4.8 ГБ с HuggingFace); затем, если AUTO_INGEST, — недостающие
    в индексе выпуски."""

    def work() -> None:
        while True:  # первая загрузка моделей (~4.8 ГБ с HuggingFace) может сорваться — повтор, ошибка видна в /health
            _load_error["attempts"] += 1
            try:
                _get("emb").embed(["warmup"], is_query=True)
                if "rr" in _expected():
                    _get("rr").score("warmup", ["warmup"])
                _load_error["error"] = ""
                break
            except Exception as exc:  # noqa: BLE001
                _load_error["error"] = f"{type(exc).__name__}: {exc}"[:500]
                log.exception("загрузка моделей — повтор через минуту")
                time.sleep(60)
        log.info("модели загружены: %s", ", ".join(_expected()))
        if os.environ.get("AUTO_INGEST", "true").lower() == "true":
            # загрузка документов, которых нет в индексе (или их файл изменился) — возобновляемо: после перезапуска
            # контейнера уже проиндексированные документы заново не векторизуются. Чужая (частичная) загрузка — ждём её
            while todo := _missing_docs():
                if not _ingest["active"] and _start_ingest(recreate=False, only=todo, trigger="auto"):
                    log.info("в индексе нет %d документов (%s) — загрузка в фоне", len(todo), ", ".join(todo))
                while _ingest["active"]:
                    time.sleep(5)
                if _ingest.get("error"):
                    log.warning("загрузка отчётов: %s — повтор через минуту", _ingest["error"])
                    time.sleep(60)

    threading.Thread(target=work, daemon=True).start()


# --------------------------------------------------------------------------------------------- загрузка отчётов
# Разбор PDF (Docling) и векторы — здесь, где уже есть модели: сервису инструментов не нужен torch.

_ingest: dict = {"active": False, "last": None, "error": "", "started": None, "trigger": ""}
_ingest_lock = threading.Lock()


def _missing_docs() -> list[str]:
    """Документы каталога с файлом, которых нет в отметках индекса или чей файл изменился (sha256)."""
    from ..config import get_settings
    from ..reports import editions
    from .ingest import file_sha, index_state

    s = get_settings()
    try:
        if vectors.count(s.qdrant_collection) == 0:
            return [m.doc_id for m in editions.load() if m.path.is_file()]
    except Exception:  # noqa: BLE001 — коллекции ещё нет
        return [m.doc_id for m in editions.load() if m.path.is_file()]
    done = (index_state().get(s.qdrant_collection) or {}).get("docs") or {}
    out = []
    for m in editions.load():
        if not m.path.is_file():
            continue
        rec = done.get(m.doc_id)
        if not rec or (rec.get("sha256") and rec["sha256"] != file_sha(m.path)):
            out.append(m.doc_id)
    return out


def _start_ingest(recreate: bool, only: list[str] | None, trigger: str, parser: str = "auto") -> bool:
    with _ingest_lock:  # флаг ставится до старта потока: два запроса подряд не запустят две загрузки
        if _ingest["active"]:
            return False
        _ingest.update(active=True, started=time.time(), trigger=trigger, error="")

    def work() -> None:
        from . import ingest

        try:
            _ingest["last"] = ingest.run(recreate, parser, only)
        except Exception as exc:  # noqa: BLE001
            _ingest["error"] = f"{type(exc).__name__}: {exc}"[:500]
            log.exception("загрузка отчётов")
        finally:
            _ingest["active"] = False

    threading.Thread(target=work, daemon=True).start()
    return True


@app.post("/ingest")
def ingest_start(payload: dict = Body(default={})) -> dict:
    started = _start_ingest(
        bool(payload.get("recreate")),
        payload.get("only"),
        str(payload.get("trigger") or "api"),
        str(payload.get("parser") or "auto"),
    )
    return {"started": started, **_ingest}


@app.get("/ingest")
def ingest_status() -> dict:
    return {**_ingest, "complete": not _missing_docs()}


@app.get("/health")
def health():
    """Загрузка моделей идёт — 200 (status loading); загрузка сорвалась — 503 с ошибкой (контейнер unhealthy)."""
    import torch
    from fastapi.responses import JSONResponse

    ready = all(m in _models for m in _expected())
    body = {
        "status": "ok" if ready else ("error" if _load_error["error"] else "loading"),
        "loaded": sorted(_models),
        "expected": _expected(),
        "load_error": _load_error["error"],
        "load_attempts": _load_error["attempts"],
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
    }
    return JSONResponse(body, status_code=503 if body["status"] == "error" else 200)


@app.post("/embed")
def embed(payload: dict = Body(...)) -> dict:
    t0 = time.monotonic()
    vecs = _get("emb").embed(list(payload["texts"]), is_query=bool(payload.get("is_query")))
    return {
        "vectors": [{"dense": v.dense, "sparse_idx": v.sparse_idx, "sparse_val": v.sparse_val} for v in vecs],
        "ms": int((time.monotonic() - t0) * 1000),
    }


@app.post("/rerank")
def rerank(payload: dict = Body(...)) -> dict:
    t0 = time.monotonic()
    scores = _get("rr").score(str(payload["query"]), list(payload["passages"]), payload.get("max_length"))
    return {"scores": scores, "ms": int((time.monotonic() - t0) * 1000)}


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("ENCODER_PORT", "8020")))


if __name__ == "__main__":
    main()

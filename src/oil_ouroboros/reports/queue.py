"""
Очередь индексации: выпуски, которые есть файлом, но ещё не в индексе (или в индексе другая версия файла). Наблюдатель
и ручная загрузка добавляют выпуск в очередь, индексирует его кодировщик (encoder). Пока идёт замер eval (freeze.py)
или кодировщик сам строит базу, выпуск ждёт — наблюдатель повторяет раз в 10 минут.
"""

from __future__ import annotations

import time

from .. import freeze, net
from ..config import get_settings
from . import editions, journal


def _encoder_auto_busy() -> str:
    """Кодировщик строит базу сам (AUTO_INGEST при старте): ждать его здесь — держать блокировку выпусков до часа
    (ручная загрузка всё это время отклоняется). Его цикл после каждой загрузки сверяет каталог
    и берёт новые выпуски."""
    url = get_settings().encoder_url
    if not url:
        return ""
    try:
        with net.internal(10) as c:
            st = c.get(url.rstrip("/") + "/ingest").json()
    except Exception:  # noqa: BLE001 — кодировщик недоступен: обычный путь (там ожидание и повторы)
        return ""
    if st.get("active") and st.get("trigger") == "auto":
        return "кодировщик строит базу: новые выпуски он возьмёт сам после текущей загрузки"
    return ""


def pending() -> list[str]:
    """Выпуски наблюдателя, которые есть файлом, но не в основном индексе — или в индексе другая версия файла."""
    from ..rag.ingest import index_state

    s = get_settings()
    done = (index_state().get(s.qdrant_collection) or {}).get("docs") or {}
    return [
        m.doc_id
        for m in editions.load()
        if m.source in ("watch", "upload")
        and m.path.is_file()
        and (
            m.doc_id not in done
            or (m.sha256 and done[m.doc_id].get("sha256") and done[m.doc_id]["sha256"] != m.sha256)
        )
    ]


def index_pending(run: str = "", trigger: str = "") -> dict:
    todo = pending()
    if not todo:
        return {"indexed": [], "queued": []}
    busy = freeze.reason() or _encoder_auto_busy()
    if busy:
        for d in todo:
            journal.add(
                {"run": run, "action": "index", "doc_id": d, "status": "queued", "note": busy, "trigger": trigger}
            )
        return {"indexed": [], "queued": todo, "note": busy}
    from ..rag import ingest

    t0 = time.monotonic()
    rep: dict = {"busy": True}
    for _ in range(5):  # кодировщик был занят чужой загрузкой — после неё индексируется только то, что ещё не в базе
        rep = ingest.run_via_encoder(only=todo) if get_settings().encoder_url else ingest.run(only=todo)
        if not rep.get("busy"):
            break
        todo = pending()
        if not todo:
            return {"indexed": [], "queued": [], "note": "выпуски проиндексировала другая загрузка"}
    for x in rep.get("documents", []):
        journal.add(
            {
                "run": run,
                "action": "index",
                "doc_id": x.get("doc_id"),
                "status": "indexed" if x.get("status") == "ok" else "error",
                "chunks": x.get("chunks"),
                "note": x.get("status")
                if x.get("status") != "ok"
                else f"{x.get('chunks')} фрагментов, разбор {x.get('parse_s')} с",
                "trigger": trigger,
            }
        )
    return {
        "indexed": todo,
        "queued": [],
        "index_size": rep.get("index_size"),
        "seconds": round(time.monotonic() - t0, 1),
    }

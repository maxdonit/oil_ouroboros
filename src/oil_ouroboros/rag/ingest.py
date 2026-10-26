"""
Конвейер загрузки базы отчётов: каталог → разбор (Docling | pypdf | HTML) → чанки → векторы → Qdrant.

Разобранные документы кэшируются в var/parsed/<doc_id>.json: смена модели эмбеддингов или параметров индекса не
требует повторного (самого дорогого) разбора PDF. Запуск: `oilo ingest [--recreate] [--parser pypdf]`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict

from ..catalog import get as behavior
from ..config import get_settings
from ..reports import editions
from . import vectors
from .chunks import Chunk, chunk_elements
from .parse import Element, parse_html, parse_pdf

log = logging.getLogger(__name__)


def parse_document(meta, parser: str = "auto", force: bool = False) -> tuple[list[Element], str]:
    s = get_settings()
    cache = s.var_dir / "parsed" / f"{meta.doc_id}.json"
    src = meta.path
    if cache.is_file() and not force and cache.stat().st_mtime >= src.stat().st_mtime:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if parser in ("auto", d["parser"]):
            return [Element(**e) for e in d["elements"]], d["parser"]
    if src.suffix.lower() == ".pdf":
        elements, used = parse_pdf(src, parser)
    else:
        elements, used = parse_html(src), "html"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"parser": used, "elements": [asdict(e) for e in elements]}, ensure_ascii=False), encoding="utf-8"
    )
    return elements, used


def index_state() -> dict:
    """Когда и что менялось в индексах: {collection: {changed_at, docs: {doc_id: {chunks, at}}}}.

    Лежит в общем каталоге выпусков: по нему кодировщик находит выпуски, которых нет в индексе (или чей файл
    изменился), а версия агента — состав базы отчётов."""
    f = editions.auto_dir() / "index_state.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def file_sha(path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


class _IngestLock:
    """Межпроцессная блокировка загрузки (первая загрузка контейнера, наблюдатель, CLI): одна за раз."""

    def __enter__(self):
        self.f = (get_settings().var_dir / "ingest.lock").open("w")
        try:
            import fcntl

            fcntl.flock(self.f, fcntl.LOCK_EX)  # ждём, пока закончится другая загрузка
        except ImportError:  # Windows (разработка)
            pass
        return self

    def __exit__(self, *exc):
        self.f.close()


def _stamp(collection: str, info: dict | None, reset: bool = False) -> None:
    st = index_state()
    c = st.setdefault(collection, {"changed_at": 0.0, "docs": {}})
    if reset:
        c["docs"] = {}
    c["changed_at"] = time.time()
    if info:
        c["docs"][info["doc_id"]] = {"chunks": info["chunks"], "at": c["changed_at"], "sha256": info.get("sha256", "")}
    f = editions.auto_dir() / "index_state.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def run(recreate: bool = False, parser: str = "auto", only: list[str] | None = None) -> dict:
    with _IngestLock():
        return _run(recreate, parser, only)


def _run(recreate: bool, parser: str, only: list[str] | None) -> dict:
    s = get_settings()
    catalog = editions.load()
    emb = vectors.get_embedder()
    vectors.ensure_collection(s.qdrant_collection, emb.dim, recreate=recreate)
    if recreate:
        _stamp(s.qdrant_collection, None, reset=True)
    report: dict = {"embedder": emb.name, "collection": s.qdrant_collection, "documents": []}
    for meta in catalog:
        src = meta.path
        if only and meta.doc_id not in only:
            continue
        if not src.is_file():
            report["documents"].append({"doc_id": meta.doc_id, "status": "нет файла"})
            continue
        t0 = time.monotonic()
        elements, used = parse_document(meta, parser)
        chunks: list[Chunk] = chunk_elements(meta, elements, behavior().indexing.chunk_tokens)
        t1 = time.monotonic()
        if not recreate:
            vectors.delete_doc(s.qdrant_collection, meta.doc_id)
        for i in range(0, len(chunks), 32):
            batch = chunks[i : i + 32]
            vecs = emb.embed([c.embed_text for c in batch])
            vectors.upsert(s.qdrant_collection, [c.payload() for c in batch], vecs)
        t2 = time.monotonic()
        kinds: dict[str, int] = {}
        for c in chunks:
            kinds[c.kind] = kinds.get(c.kind, 0) + 1
        info = {
            "doc_id": meta.doc_id,
            "status": "ok",
            "parser": used,
            "elements": len(elements),
            "chunks": len(chunks),
            "kinds": kinds,
            "pages": len({c.page for c in chunks if c.page}),
            "parse_s": round(t1 - t0, 1),
            "embed_s": round(t2 - t1, 1),
            "sha256": file_sha(src),
        }
        log.info("%s", info)
        report["documents"].append(info)
        _stamp(s.qdrant_collection, info)
    report["index_size"] = vectors.count(s.qdrant_collection)
    return report


def run_via_encoder(recreate: bool = False, only: list[str] | None = None, timeout_s: float = 3600) -> dict:
    """Загрузка в контейнере кодировщика (там Docling и модели): запустить и дождаться именно своей загрузки.
    Если кодировщик занят другой загрузкой (например, первой при старте), сначала ждём её окончания."""
    from .. import net

    url = get_settings().encoder_url.rstrip("/")
    t0 = time.monotonic()
    with net.internal(60) as c:
        while time.monotonic() - t0 < timeout_s:
            r = c.post(url + "/ingest", json={"recreate": recreate, "only": only, "trigger": "tools"}).json()
            started = bool(r.get("started"))
            while time.monotonic() - t0 < timeout_s:
                time.sleep(3)
                st = c.get(url + "/ingest").json()
                if not st.get("active"):
                    break
            if not started:
                # кодировщик был занят другой загрузкой; она могла взять и наши документы — список пересчитает
                # вызывающий (watch.index_pending), повтор того же списка заново векторизовал бы готовые документы
                log.info("кодировщик был занят другой загрузкой — она закончилась, список выпусков пересчитывается")
                return {"busy": True}
            if st.get("error"):
                raise RuntimeError(f"загрузка в кодировщике: {st['error']}")
            return st.get("last") or {}
    raise TimeoutError("загрузка отчётов не закончилась вовремя")

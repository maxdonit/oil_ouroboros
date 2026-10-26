"""
Журнал доказательств: всё, что инструменты отдали агенту, — фрагменты отчётов [R#], веб-источники [W#], расчёты [F#].

Почему отдельный журнал, а не номера внутри одного вызова:
- Ouroboros вызывает инструменты по MCP без идентификатора диалога: два поиска в одном ответе выдали бы по «R1»;
- идентификаторы сквозные и устойчивые: один и тот же фрагмент (chunk_id), страница (URL + текст) или расчёт
  (инструмент + аргументы + дата данных) с тем же текстом всегда получает тот же номер — цитаты не «переезжают»;
  тот же ключ с другим текстом (переразбор отчёта, другой каталог) — новый номер: под прежним id остаётся ровно то,
  что агент тогда видел;
- проверка ответа (verify_answer) берёт тексты источников ИЗ ЖУРНАЛА, а не из ответа агента: подменить цифру в
  источнике агент не может;
- пакет (P#) — один ответ инструмента: какие доказательства, решения и параметры его породили. По пакетам проверка
  находит «соседей» цитаты (для пере-сопоставления цифр без ссылки), а консоль — объяснение хода.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..config import get_settings

PREFIX = {"report": "R", "web": "W", "tool": "F"}
PACK_MAX_AGE_S = 1200  # пакет старше 20 минут — это другой ход (или другой пользователь)

SCHEMA = """
create table if not exists evidence (
  id text primary key, kind text not null, n integer not null, key text unique not null, label text not null,
  text text not null, tier integer, meta text not null, created real not null
);
create index if not exists evidence_kind_n on evidence(kind, n);
create table if not exists packs (
  id text primary key, n integer not null, created real not null, tool text not null, run_id text, question text,
  ids text not null, decisions text not null, meta text not null
);
create table if not exists pack_items (pack text not null, item text not null, primary key (pack, item));
create index if not exists pack_items_item on pack_items(item);
"""

_lock = threading.Lock()


def _path() -> Path:
    return get_settings().var_dir / "evidence.sqlite"


def _db() -> sqlite3.Connection:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=30, isolation_level=None)  # транзакции — явно (BEGIN IMMEDIATE)
    c.row_factory = sqlite3.Row
    c.execute("pragma journal_mode=wal")
    c.executescript(SCHEMA)
    return c


def key_of(kind: str, *parts: Any) -> str:
    raw = json.dumps([kind, *parts], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def put(kind: str, key: str, label: str, text: str, tier: int | None = None, **meta: Any) -> str:
    """Зарегистрировать доказательство: тот же key с тем же текстом — прежний id, с другим текстом — новый."""
    with _lock:
        c = _db()
        try:
            c.execute("begin immediate")  # номер выдаётся под блокировкой записи: два процесса не возьмут один n
            row = c.execute("select id, text from evidence where key = ?", (key,)).fetchone()
            if row and row["text"] != text:  # текст под ключом изменился: прежний id остаётся за прежним текстом
                key = key_of(kind, key, text)
                row = c.execute("select id, text from evidence where key = ?", (key,)).fetchone()
            if row:
                c.execute("commit")
                return row["id"]
            n = (c.execute("select coalesce(max(n), 0) from evidence where kind = ?", (kind,)).fetchone()[0] or 0) + 1
            eid = f"{PREFIX[kind]}{n}"
            c.execute(
                "insert into evidence values (?,?,?,?,?,?,?,?,?)",
                (eid, kind, n, key, label, text, tier, json.dumps(meta, ensure_ascii=False, default=str), time.time()),
            )
            c.execute("commit")
            return eid
        except Exception:
            c.execute("rollback")
            raise
        finally:
            c.close()


def put_web(doc: dict) -> str:
    """Веб-документ поиска (web/search.WebDoc.as_dict) → устойчивый W# (тот же URL и текст — тот же id)."""
    text = f"{doc['title']}. {doc['content']}"
    return put(
        "web",
        key_of("web", doc["url"], text),
        doc["label"],
        text,
        tier=doc.get("tier"),
        url=doc["url"],
        domain=doc.get("domain", ""),
        published=doc.get("published", ""),
        state_affiliated=doc.get("state_affiliated", False),
        provider=doc.get("provider", ""),
    )


def get(ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    c = _db()
    try:
        rows = c.execute(f"select * from evidence where id in ({','.join('?' * len(ids))})", ids).fetchall()
    finally:
        c.close()
    out = {}
    for r in rows:
        meta = json.loads(r["meta"])
        out[r["id"]] = {
            "id": r["id"],
            "kind": r["kind"],
            "label": r["label"],
            "text": r["text"],
            "tier": r["tier"],
            **meta,
        }
    return {i: out[i] for i in ids if i in out}


def new_pack(
    tool: str, ids: list[str], run_id: str = "", question: str = "", decisions: list | None = None, **meta: Any
) -> str:
    with _lock:
        c = _db()
        try:
            c.execute("begin immediate")
            n = (c.execute("select coalesce(max(n), 0) from packs").fetchone()[0] or 0) + 1
            pid = f"P{n}"
            c.execute(
                "insert into packs values (?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    n,
                    time.time(),
                    tool,
                    run_id,
                    question,
                    json.dumps(ids),
                    json.dumps(decisions or [], ensure_ascii=False, default=str),
                    json.dumps(meta, ensure_ascii=False, default=str),
                ),
            )
            c.executemany("insert or ignore into pack_items values (?, ?)", [(pid, i) for i in ids])
            c.execute("commit")
            return pid
        except Exception:
            c.execute("rollback")
            raise
        finally:
            c.close()


def pack(pack_id: str) -> dict[str, Any] | None:
    c = _db()
    try:
        r = c.execute("select * from packs where id = ?", (pack_id,)).fetchone()
    finally:
        c.close()
    if not r:
        return None
    return {
        "id": r["id"],
        "created": r["created"],
        "tool": r["tool"],
        "run_id": r["run_id"],
        "question": r["question"],
        "ids": json.loads(r["ids"]),
        "decisions": json.loads(r["decisions"]),
        **json.loads(r["meta"]),
    }


def neighbours(ids: Iterable[str], max_age_s: float = 6 * 3600) -> list[str]:
    """Доказательства из последнего (свежего, max_age_s) пакета каждой цитаты — приближение «доказательств хода».

    id глобальны (один фрагмент или расчёт — один R#/F# во всех ходах), поэтому пакет с той же цитатой бывает и у
    соседнего разговора. Последний пакет цитаты — почти всегда пакет этого хода: так проверка в ходе агента совпадает
    с финальной проверкой консоли, которая знает доказательства хода точно."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return []
    since = time.time() - max_age_s
    c = _db()
    try:
        packs = []
        for i in ids:
            row = c.execute(
                "select pi.pack from pack_items pi join packs p on p.id = pi.pack "
                "where pi.item = ? and p.created >= ? order by p.created desc limit 1",
                (i, since),
            ).fetchone()
            if row and row[0] not in packs:
                packs.append(row[0])
        if not packs:
            return []
        rows = c.execute(f"select item from pack_items where pack in ({','.join('?' * len(packs))})", packs).fetchall()
    finally:
        c.close()
    return list(dict.fromkeys(r[0] for r in rows))


def items_of_runs(run_ids: Iterable[str]) -> list[str]:
    """Все доказательства пакетов, созданных данными прогонами инструментов (trace) — доказательства одного хода."""
    run_ids = [r for r in dict.fromkeys(run_ids) if r]
    if not run_ids:
        return []
    c = _db()
    try:
        rows = c.execute(
            "select pi.item from pack_items pi join packs p on p.id = pi.pack "
            f"where p.run_id in ({','.join('?' * len(run_ids))})",
            run_ids,
        ).fetchall()
    finally:
        c.close()
    return list(dict.fromkeys(r[0] for r in rows))


def items_of_packs(pack_ids: Iterable[str], max_age_s: float) -> tuple[list[str], list[str], list[str]]:
    """Доказательства перечисленных пакетов P# — доказательства хода по списку агента: (id, принятые пакеты,
    отклонённые с причиной). Пакет старше max_age_s — из другого хода: его доказательства не засчитываются."""
    pids = [p for p in dict.fromkeys(pack_ids) if p]
    if not pids:
        return [], [], []
    since = time.time() - max_age_s
    c = _db()
    try:
        created = {
            r["id"]: r["created"]
            for r in c.execute(f"select id, created from packs where id in ({','.join('?' * len(pids))})", pids)
        }
        ok = [p for p in pids if created.get(p, 0) >= since]
        rows = (
            c.execute(f"select item from pack_items where pack in ({','.join('?' * len(ok))})", ok).fetchall()
            if ok
            else []
        )
    finally:
        c.close()
    skipped = [
        f"{p} ({'нет такого пакета' if p not in created else f'старше {int(max_age_s // 60)} мин'})"
        for p in pids
        if p not in ok
    ]
    return list(dict.fromkeys(r[0] for r in rows)), ok, skipped


def web_domains(limit: int = 40) -> list[dict[str, Any]]:
    """Веб-домены, чьи фрагменты попадали в доказательства ответов: уровень доверия, сколько фрагментов,
    когда последний."""
    c = _db()
    try:
        rows = c.execute(
            "select json_extract(meta, '$.domain') domain, tier, count(*) n, max(created) last from evidence"
            " where kind = 'web' group by domain, tier order by n desc, last desc limit ?",
            (limit,),
        ).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


def stats() -> dict[str, int]:
    c = _db()
    try:
        out = {r["kind"]: r["n"] for r in c.execute("select kind, count(*) n from evidence group by kind")}
        out["packs"] = c.execute("select count(*) from packs").fetchone()[0]
    finally:
        c.close()
    return out

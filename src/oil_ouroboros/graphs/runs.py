"""
Журнал прогонов инструментов (SQLite, var/runs.sqlite): каждый вызов инструмента —
запись run (thread_id — имя инструмента) + поток событий (узлы, решения, вызовы LLM с промптами и
ответами, токены, стоимость, длительности) + итоговое состояние. Из него читают трейсы консоли и eval.

Журналы хранятся не меньше 6 месяцев (ориентир EU AI Act ст. 26(6) для развёртывателей) — очистка вручную.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from ..config import get_settings

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
create table if not exists runs (
  run_id text primary key, thread_id text, question text, args text,
  started real, finished real, ms integer, status text, answer text, usage text, error text, state text,
  source text
);
create table if not exists events (
  run_id text, seq integer, ts real, type text, node text, payload text, primary key (run_id, seq)
);
create index if not exists ix_runs_started on runs(started);
"""


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(str(get_settings().runs_db), check_same_thread=False)
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def _json(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=_default)


def _default(o: Any) -> Any:
    try:
        from langchain_core.messages import BaseMessage, messages_to_dict

        if isinstance(o, BaseMessage):
            return messages_to_dict([o])[0]
    except Exception:  # noqa: BLE001
        pass
    return str(o)


def start_run(run_id: str, thread_id: str, question: str, args: dict, source: str) -> None:
    c = conn()
    with _lock:
        c.execute(
            "insert or replace into runs(run_id, thread_id, question, args, started, status, source) "
            "values (?,?,?,?,?,?,?)",
            (run_id, thread_id, question, _json(args), time.time(), "running", source),
        )
        c.commit()


def finish_run(run_id: str, answer: str, state: dict, events: list[dict], usage: dict, ms: int, error: str) -> None:
    c = conn()
    with _lock:
        c.executemany(
            "insert or replace into events(run_id, seq, ts, type, node, payload) values (?,?,?,?,?,?)",
            [
                (run_id, i, e.get("ts", 0), e.get("type", ""), e.get("node", ""), _json(e))
                for i, e in enumerate(events)
            ],
        )
        c.execute(
            "update runs set finished=?, ms=?, status=?, answer=?, usage=?, error=?, state=? where run_id=?",
            (
                time.time(),
                ms,
                "error" if error else "done",
                answer,
                _json(usage),
                error,
                _json(state),
                run_id,
            ),
        )
        c.commit()


def recent_args(source: str, since: float) -> list[dict]:
    """Аргументы прогонов источника source (например, «mcp:verify_answer»), начатых не раньше since, новые первыми."""
    c = conn()
    with _lock:
        rows = c.execute(
            "select args from runs where started >= ? and source = ? order by started desc", (since, source)
        ).fetchall()
    return [json.loads(r[0] or "{}") for r in rows]


def get_run(run_id: str) -> dict | None:
    c = conn()
    with _lock:
        r = c.execute(
            "select run_id, thread_id, question, args, started, finished, ms, status, answer, usage, "
            "error, state, source from runs where run_id=?",
            (run_id,),
        ).fetchone()
        events = (
            [json.loads(e[0]) for e in c.execute("select payload from events where run_id=? order by seq", (run_id,))]
            if r
            else []
        )
    if not r:
        return None
    keys = (
        "run_id",
        "thread_id",
        "question",
        "args",
        "started",
        "finished",
        "ms",
        "status",
        "answer",
        "usage",
        "error",
        "state",
        "source",
    )
    d = dict(zip(keys, r, strict=True))
    for k in ("args", "usage", "state"):
        d[k] = json.loads(d[k] or "{}")
    d["events"] = events
    return d

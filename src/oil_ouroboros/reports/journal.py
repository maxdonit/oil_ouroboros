"""
Журнал наблюдателя выпусков: каждый шаг — строка <каталог выпусков>/journal.jsonl, итог последней проверки сайтов —
state.json. Читают вкладка «Источники», list_reports и `oilo reports`.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import editions


def _dir() -> Path:
    d = editions.auto_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def now() -> str:
    """Время со смещением пояса: консоль показывает его в поясе браузера."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def add(rec: dict) -> None:
    rec = {"ts": now(), **rec}
    with (_dir() / "journal.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read(limit: int = 100) -> list[dict]:
    f = _dir() / "journal.jsonl"
    if not f.is_file():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[::-1]


def last_check() -> dict:
    """Итог последней проверки сайтов агентств (finished, items); {} — проверок не было."""
    try:
        return json.loads((_dir() / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_check(summary: dict) -> None:
    (_dir() / "state.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

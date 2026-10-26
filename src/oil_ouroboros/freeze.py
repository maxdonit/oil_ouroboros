"""
Заморозка данных на время замера eval: пока лежит var/eval.lock, новые выпуски отчётов не добавляются в индекс, а цены
EIA не обновляются — все кейсы прогона идут на одних данных, и эффект версии агента не смешивается с эффектом новых
данных. Новый выпуск ждёт в очереди и индексируется после прогона (reports/queue.py).

Блокировку ставит прогон eval: замер поиска — в своём процессе (frozen), замер через агента — вызовом
POST /api/eval_lock сервиса инструментов (у консоли свой том var). Оборванный прогон не держит данные вечно:
блокировка старше LOCK_MAX_S не действует.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

LOCK_MAX_S = 3 * 3600  # дольше полный прогон eval не идёт


def _lock() -> Path:
    return get_settings().var_dir / "eval.lock"


def reason() -> str:
    """Почему данные сейчас менять нельзя ('' — можно)."""
    lock = _lock()
    if not lock.is_file() or time.time() - lock.stat().st_mtime > LOCK_MAX_S:
        return ""
    return "идёт замер eval: данные обновятся после"


def hold(eval_id: str, kind: str = "agent") -> None:
    _lock().write_text(json.dumps({"eval_id": eval_id, "kind": kind, "at": time.time()}), encoding="utf-8")


def release() -> None:
    _lock().unlink(missing_ok=True)


def held() -> bool:
    """Данные заморожены: блокировка есть и не просрочена (то же правило, что у reason())."""
    return bool(reason())


@contextmanager
def frozen(eval_id: str, kind: str) -> Iterator[None]:
    """Данные заморожены на время блока. Если блокировку уже держит другой замер, она его — снимает он же."""
    own = not held()
    if own:
        hold(eval_id, kind)
    try:
        yield
    finally:
        if own:
            release()

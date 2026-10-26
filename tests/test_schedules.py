"""Засев расписаний Ouroboros: создаёт один раз, правку и удаление владельцем не откатывает,
новую версию — доставляет."""

from __future__ import annotations

import json
import re
from zoneinfo import ZoneInfo

import pytest

from oil_ouroboros import schedules


class FakeOuroboros:
    """Таблица расписаний как у Ouroboros: строка = поля автора + служебные поля рантайма."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.posts = 0

    def get(self, path: str) -> dict:
        assert path == "/api/schedules"
        return {"schema_version": 1, "tasks": [dict(r) for r in self.rows.values()]}

    def post(self, path: str, body: dict) -> dict:
        assert path == "/api/schedules" and body.get("reason")
        self.posts += 1
        assert "builtin_tools" not in body  # служебное поле описания в Ouroboros не уходит
        row = {k: body[k] for k in ("id", "name", "description", "enabled", "timezone", "trigger")}
        row["task"] = dict(body["task"])
        row |= {"status": "active", "next_run_at": "2026-10-23T06:00:00+00:00", "runs": []}
        self.rows[row["id"]] = row
        return {"ok": True, "schedule": {**row, "audit": "recorded"}}


SPEC = {
    "id": "brief",
    "name": "Сводка",
    "description": "d",
    "enabled": True,
    "timezone": "Europe/Moscow",
    "trigger": {"type": "cron", "expr": "0 9 * * *"},
    "task": {"type": "task", "chat_id": 0, "text": "v1"},
}


@pytest.fixture
def spec(monkeypatch):
    cur = {"spec": json.loads(json.dumps(SPEC))}
    monkeypatch.setattr(schedules, "specs", lambda: [cur["spec"]])
    return cur


def _actions(api, state, force=False):
    return [r["action"] for r in schedules.seed(api, state, force)]


def test_seed_creates_once_then_leaves_alone(spec):
    api, state = FakeOuroboros(), {}
    assert _actions(api, state) == ["создано"]
    api.rows["brief"]["runs"].append({"at": "2026-10-23T06:00:05+00:00"})  # история запусков — не правка владельца
    assert _actions(api, state) == ["без изменений"] and api.posts == 1


def test_new_repo_version_updates_untouched_schedule(spec):
    api, state = FakeOuroboros(), {}
    schedules.seed(api, state)
    spec["spec"]["task"]["text"] = "v2"
    assert _actions(api, state) == ["обновлено до версии из репозитория"]
    assert api.rows["brief"]["task"]["text"] == "v2"


def test_owner_edit_and_delete_are_kept_unless_forced(spec):
    api, state = FakeOuroboros(), {}
    schedules.seed(api, state)
    api.rows["brief"]["enabled"] = False  # владелец выключил в UI
    spec["spec"]["task"]["text"] = "v2"
    assert _actions(api, state) == ["оставлено: изменено владельцем"] and api.rows["brief"]["task"]["text"] == "v1"
    del api.rows["brief"]  # владелец удалил
    assert _actions(api, state) == ["оставлено: удалено владельцем"] and "brief" not in api.rows
    assert _actions(api, state, force=True) == ["создано"] and api.rows["brief"]["enabled"] is True


def test_schedule_task_gets_console_toolset_plus_its_builtins():
    """Без схем ~140 встроенных инструментов контекст задачи вдвое меньше; заметка владельцу остаётся доступной."""
    from oil_ouroboros.agent import disabled_tools

    body = schedules.payload({**SPEC, "builtin_tools": ["schedule_followup"]})
    off = body["task"]["metadata"]["disabled_tools"]
    assert "builtin_tools" not in body and "schedule_followup" not in off and "finish_task" not in off
    assert set(off) == set(disabled_tools()) - {"schedule_followup"} and "browse_page" in off


def test_repo_schedules_are_valid_for_ouroboros():
    reserved = {"memory_mode", "workspace_root", "parent_task_id", "client_surface", "task_id", "session_id"}
    found = [schedules.payload(s) for s in schedules.specs()]
    assert [s["id"] for s in found] == ["oil-morning-brief"]
    for s in found:
        assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", s["id"])
        assert s["trigger"]["type"] == "cron" and len(s["trigger"]["expr"].split()) == 5
        ZoneInfo(s["timezone"])
        assert (
            s["task"]["type"] == "task" and not reserved & set(s["task"]) and not reserved & set(s["task"]["metadata"])
        )
    brief = found[0]
    assert brief["trigger"]["expr"] == "0 9 * * *" and brief["timezone"] == "Europe/Moscow"
    text = brief["task"]["text"]
    # доставка: ход скрыт (chat_id 0), владельцу — заметка с проверенным текстом и метками источников
    assert brief["task"]["chat_id"] == 0 and "schedule_followup(notify=true" in text and "render=true" in text
    for tool in (
        "list_reports",
        "price_quote",
        'price_quote(benchmark="wti")',
        "price_history",
        "research",
        "verify_answer",
        "finish_task",
    ):
        assert tool in text

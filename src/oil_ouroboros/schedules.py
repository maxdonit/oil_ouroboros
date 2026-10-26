"""
Расписания Ouroboros из репозитория (ouroboros/schedules/*.json): засев после старта агента.

Запускается разовым сервисом `schedules`, когда Ouroboros готов (каждый `docker compose up`). Расписание — настройка
владельца, как ключи владельца в settings.json: засев его ставит, а дальше его ведёт владелец в UI Ouroboros
(Schedules).
- расписания нет и засев его не ставил — создаётся;
- владелец его не менял (поля автора те же, что после прошлого засева), а версия в репозитории новая — обновляется;
- владелец изменил, выключил или удалил — не трогается.
Учёт засева — в state/oilo_seed.json (ключ schedules_seeded), рядом с учётом памяти и настроек. `--force` пишет версии
из репозитория поверх правок владельца.

    python -m oil_ouroboros.schedules [--force] [--data /ouroboros-data]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from . import net
from .agent import disabled_tools
from .bootstrap import sha
from .config import ROOT, get_settings

SPEC_DIR = ROOT / "ouroboros" / "schedules"
AUTHORED = ("name", "description", "enabled", "timezone", "trigger")  # поля, которые пишет автор, а не Ouroboros


def specs() -> list[dict]:
    out = []
    for p in sorted(SPEC_DIR.glob("*.json")):
        spec = json.loads(p.read_text(encoding="utf-8"))
        spec.pop("_comment", None)
        out.append(spec)
    return out


def payload(spec: dict) -> dict:
    """Тело POST /api/schedules. Встроенные инструменты — как в ходе консоли (disabled_tools.txt) плюс те, что нужны
    расписанию (builtin_tools): без схем ~140 инструментов контекст задачи вдвое меньше. Задача по расписанию
    берёт disabled_tools из metadata шаблона."""
    keep = set(spec.get("builtin_tools") or [])
    task = dict(spec["task"])
    task["metadata"] = {
        **(task.get("metadata") or {}),
        "disabled_tools": [t for t in disabled_tools() if t not in keep],
    }
    return {**{k: v for k, v in spec.items() if k != "builtin_tools"}, "task": task}


def authored(row: dict) -> str:
    """sha полей автора расписания; историю запусков и служебные поля Ouroboros не учитывает."""
    task = row.get("task") or {}
    pick = {k: row.get(k) for k in AUTHORED} | {"text": task.get("text"), "chat_id": task.get("chat_id")}
    return sha(json.dumps(pick, sort_keys=True, ensure_ascii=False))


class Api:
    """API Ouroboros с сетевым паролем (как у консоли)."""

    def __init__(self, url: str, password: str) -> None:
        headers = {"X-Ouroboros-Password": password} if password else {}
        self.http = net.internal(30, base_url=url.rstrip("/"), headers=headers)

    def get(self, path: str) -> dict:
        r = self.http.get(path)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        r = self.http.post(path, json=body)
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(f"{path}: HTTP {r.status_code} {r.text[:300]}", request=r.request, response=r)
        return r.json()

    def wait_ready(self, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                if self.get("/api/state").get("supervisor_ready"):
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"Ouroboros не готов за {timeout_s:g} с")
            time.sleep(5)


def seed(api, state: dict, force: bool = False) -> list[dict]:
    # id → {"spec": sha версии из репозитория, "row": sha полей автора после засева}
    seeded = state.setdefault("schedules_seeded", {})
    rows = {r.get("id"): r for r in api.get("/api/schedules").get("tasks") or []}
    report = []
    for spec in map(payload, specs()):
        sid, spec_sha = spec["id"], sha(json.dumps(spec, sort_keys=True, ensure_ascii=False))
        row, prev = rows.get(sid), seeded.get(sid) or {}
        if not force:
            if row is None and prev:
                report.append({"id": sid, "action": "оставлено: удалено владельцем"})
                continue
            if row is not None and authored(row) != prev.get("row"):
                report.append({"id": sid, "action": "оставлено: изменено владельцем"})
                continue
            if row is not None and prev.get("spec") == spec_sha:
                report.append({"id": sid, "action": "без изменений"})
                continue
        stored = api.post("/api/schedules", {**spec, "reason": "засев oil-ouroboros (ouroboros/schedules)"})[
            "schedule"
        ]
        seeded[sid] = {"spec": spec_sha, "row": authored(stored)}
        report.append({"id": sid, "action": "создано" if row is None else "обновлено до версии из репозитория"})
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="schedules", description=__doc__.split("\n\n")[0])
    p.add_argument("--data", default=os.environ.get("OUROBOROS_DATA", "/ouroboros-data"))
    p.add_argument("--force", action="store_true", help="записать версии из репозитория поверх правок владельца")
    p.add_argument("--wait", type=float, default=600, help="сколько ждать готовности Ouroboros, с")
    a = p.parse_args(argv)
    s = get_settings()
    state_file = Path(a.data) / "state" / "oilo_seed.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    api = Api(s.ouroboros_url, s.ouroboros_password)
    try:
        api.wait_ready(a.wait)
        report = seed(api, state, a.force)
    except (httpx.HTTPError, TimeoutError) as exc:
        print(f"schedules: {exc}", file=sys.stderr)
        return 2
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in report:
        print(f"расписание: {r['id']:<24} {r['action']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Засев агента (Ouroboros) до его запуска: settings.json, identity.md, WORLD.md, knowledge/overview.md.

Запускается разовым сервисом `bootstrap` перед контейнером ouroboros при каждом `docker compose up`. Работа
идемпотентна:
- **settings.json** — два вида ключей шаблона (ouroboros/settings.template.json + .env):
  - интеграция (шлюз LLM и модели, MCP-инструменты, сетевой пароль, бюджет) — пишется при каждом запуске:
    конфигурация как код;
  - ключи владельца (`_owner_keys`: режимы, ревью, эволюция, «сознание», субагенты, усилие, лимиты, прочие модели) —
    засев их ставит, а правку
    в UI Ouroboros не откатывает. Новая версия засева заменяет значение, только если владелец его не менял (sha
    засева — в state/oilo_seed.json, как у памяти).
  Ключи, которые дописывает сам Ouroboros, сохраняются. `--reset` пишет файл заново.
- **TELEGRAM_BOT_TOKEN** из .env (если задан) — в settings.json, когда его там ещё нет: дальше токен ведёт
владелец в UI.
- **Память** (identity.md, WORLD.md, knowledge/overview.md) — пишется, если её нет. Новая версия засева заменяет
  файл, только если агент его не менял: sha последнего засева хранится в state/oilo_seed.json. Если агент улучшил
  регламент сам (knowledge_write, update_identity), его версия сохраняется — это и есть самоулучшение, и консоль
  показывает его как изменённую агентом версию.

    python -m oil_ouroboros.bootstrap [--reset] [--data /ouroboros-data]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from .config import ROOT

SEEDS = {  # файл засева в репозитории → путь в данных Ouroboros
    "ouroboros/memory/identity.md": "memory/identity.md",
    "ouroboros/memory/WORLD.md": "memory/WORLD.md",
    "ouroboros/memory/knowledge/overview.md": "memory/knowledge/overview.md",
}
SECRETS = ("OPENAI_COMPATIBLE_API_KEY", "OUROBOROS_NETWORK_PASSWORD", "MCP_SERVERS", "TELEGRAM_BOT_TOKEN")
OWNER = (1000, 1000)  # пользователь контейнера ouroboros (docker-compose.yml: user)


def sha(text: str | bytes) -> str:
    raw = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(raw).hexdigest()


def render_settings(env: dict[str, str]) -> dict:
    """Шаблон → словарь настроек: ${VAR} из окружения; обязательные секреты проверяются."""
    raw = (ROOT / "ouroboros" / "settings.template.json").read_text(encoding="utf-8")
    values = {**env, "NOW": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    values.setdefault("OUROBOROS_MODEL", "GigaChat-3-Ultra")  # LLM-профиль по умолчанию — deploy/llm/gigachat.env
    values.setdefault("OUROBOROS_MODEL_LIGHT", "GigaChat-2")
    values.setdefault("OUROBOROS_TOTAL_BUDGET", "4")
    values.setdefault("OUROBOROS_PER_TASK_COST_USD", "0.3")
    missing = sorted({m for m in re.findall(r"\$\{(\w+)\}", raw) if not values.get(m)})
    if missing:
        raise SystemExit(f"bootstrap: не заданы переменные окружения {', '.join(missing)} (.env)")
    text = re.sub(r"\$\{(\w+)\}", lambda m: values[m.group(1)], raw)
    data = json.loads(text)
    data.pop("_comment", None)
    data.pop("_owner_keys", None)
    return data


def owner_keys() -> frozenset[str]:
    """Ключи владельца из шаблона: их правку в UI Ouroboros засев не откатывает."""
    raw = (ROOT / "ouroboros" / "settings.template.json").read_text(encoding="utf-8")
    m = re.search(r'"_owner_keys"\s*:\s*\[(.*?)\]', raw, re.S)
    return frozenset(re.findall(r'"(\w+)"', m.group(1))) if m else frozenset()


def merge_settings(
    path: Path, rendered: dict, reset: bool, owner: frozenset[str] = frozenset(), seeded: dict | None = None
) -> tuple[dict, list[str], list[str], dict]:
    """→ (настройки, изменённые ключи, ключи владельца, оставленные как есть, значения засева ключей владельца).

    seeded — значения ключей владельца, которые засев записал в прошлый раз: отличие текущего значения от них —
    правка владельца в UI Ouroboros."""
    current: dict = {}
    if path.is_file() and not reset:
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            current = {}
    merged, changed, kept, now_seeded = dict(current), [], [], {}
    for k, v in rendered.items():
        if k == "OUROBOROS_ONBOARDING_COMPLETED_AT" and k in current:  # дата первого засева не «меняется» при запуске
            continue
        if k in owner and k in current:
            prev = (seeded or {}).get(k, current[k])
            if current[k] != prev:  # владелец поменял в UI Ouroboros — его значение
                kept.append(k)
                now_seeded[k] = prev
                continue
        if current.get(k) != v:
            changed.append(k)
        merged[k] = v
        if k in owner:
            now_seeded[k] = v
    return merged, changed, kept, now_seeded


def seed_memory(data: Path, state: dict, reset: bool) -> list[dict]:
    report = []
    seeded = state.setdefault("seeded", {})
    for src_rel, dst_rel in SEEDS.items():
        seed = (ROOT / src_rel).read_text(encoding="utf-8")
        dst = data / dst_rel
        new_sha = sha(seed)
        if not dst.is_file():
            action = "создан"
        else:
            cur = sha(dst.read_bytes())
            if cur == new_sha:
                seeded[dst_rel] = new_sha
                report.append({"file": dst_rel, "action": "без изменений", "sha": new_sha[:12]})
                continue
            if reset or cur == seeded.get(dst_rel):
                action = "обновлён до новой версии засева"
            else:
                report.append(
                    {"file": dst_rel, "action": "оставлен: изменён агентом", "sha": cur[:12], "seed_sha": new_sha[:12]}
                )
                continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(seed, encoding="utf-8")
        seeded[dst_rel] = new_sha
        report.append({"file": dst_rel, "action": action, "sha": new_sha[:12]})
    return report


def chown_tree(root: Path) -> None:
    if not hasattr(os, "chown") or os.geteuid() != 0:  # type: ignore[attr-defined]
        return
    for d, _dirs, files in os.walk(root):
        for name in [d, *(os.path.join(d, f) for f in files)]:
            try:
                os.chown(name, *OWNER)
            except OSError:
                pass


GITCONFIG = (
    "[user]\n\tname = Ouroboros (oil analyst)\n\temail = ouroboros@oil-ouroboros.local\n[safe]\n\tdirectory = *\n"
)


def prepare_home(home: Path | None, work: Path | None) -> None:
    """Домашний и рабочий каталоги агента: свои тома, владелец — пользователь контейнера (не root)."""
    for d in (home, work):
        if d is None:
            continue
        d.mkdir(parents=True, exist_ok=True)
        if d == home and not (d / ".gitconfig").is_file():
            (d / ".gitconfig").write_text(GITCONFIG, encoding="utf-8")  # /app (тело Ouroboros) принадлежит root
        chown_tree(d)


def run(data: Path, reset: bool = False, env: dict[str, str] | None = None) -> dict:
    env = dict(os.environ if env is None else env)
    home, work = env.get("OUROBOROS_HOME"), env.get("OUROBOROS_WORK")
    prepare_home(Path(home) if home else None, Path(work) if work else None)
    data.mkdir(parents=True, exist_ok=True)
    (data / "state").mkdir(exist_ok=True)
    state_file = data / "state" / "oilo_seed.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    rendered = render_settings(env)
    settings, changed, kept, seeded = merge_settings(
        data / "settings.json", rendered, reset, owner_keys(), None if reset else state.get("settings_seeded")
    )
    if env.get("TELEGRAM_BOT_TOKEN") and (reset or not settings.get("TELEGRAM_BOT_TOKEN")):
        settings["TELEGRAM_BOT_TOKEN"] = env["TELEGRAM_BOT_TOKEN"]  # дальше токен ведёт владелец (UI Ouroboros)
        changed.append("TELEGRAM_BOT_TOKEN")
    (data / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(data / "settings.json", 0o600)
    memory = seed_memory(data, state, reset)
    public = {k: settings.get(k) for k in rendered if k not in SECRETS}  # действующие ключи шаблона без секретов
    state.update(
        updated=time.time(),
        settings_public=public,
        settings_sha=sha(json.dumps(public, sort_keys=True)),
        settings_seeded=seeded,
    )
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    chown_tree(data)
    # консоль (другой пользователь) читает версию настроек; settings.json с секретами — 600
    os.chmod(state_file, 0o644)
    return {
        "settings_changed": [k for k in changed if k not in SECRETS]
        + [f"{k} (секрет)" for k in changed if k in SECRETS],
        "settings_kept": kept,
        "memory": memory,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bootstrap", description=__doc__.split("\n\n")[0])
    p.add_argument("--data", default=os.environ.get("OUROBOROS_DATA", "/ouroboros-data"))
    p.add_argument(
        "--reset", action="store_true", help="переписать settings.json и память агента версией из репозитория"
    )
    a = p.parse_args(argv)
    try:
        rep = run(Path(a.data), a.reset)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2
    print("settings.json: ключи засева изменены:", ", ".join(rep["settings_changed"]) or "—")
    print("settings.json: правка владельца сохранена:", ", ".join(rep["settings_kept"]) or "—")
    for m in rep["memory"]:
        print(f"память: {m['file']:<32} {m['action']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

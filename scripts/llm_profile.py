#!/usr/bin/env python3
"""
LLM-профиль стенда: какие модели у агента и субагентов и через кого они идут (только stdlib).

    python scripts/llm_profile.py              # текущий профиль и список профилей
    python scripts/llm_profile.py openrouter   # переключить .env на профиль
    python scripts/llm_profile.py gigachat     # вернуть профиль по умолчанию

Профиль — файл deploy/llm/<имя>.env: модели (OUROBOROS_MODEL, OUROBOROS_MODEL_LIGHT, LLM_MODEL_SUBAGENT) и, если нужен
отдельный сервис, REQUIRES_COMPOSE_PROFILE — он добавляется в COMPOSE_PROFILES, а профили других LLM оттуда
убираются (owner и прочие остаются). Меняется только .env; затем:
docker compose up -d && docker compose restart ouroboros
(на стенде: python scripts/deploy.py up && python scripts/deploy.py restart ouroboros).
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "deploy" / "llm"
GENERATED = {"gigachat": ("GPT2GIGA_API_KEY",)}  # внутренние токены профиля: создаются, если их нет
REQUIRED = {"gigachat": ("GIGACHAT_CREDENTIALS",), "openrouter": ("OPENROUTER_API_KEY",)}  # их вписывает человек


def read_profile(name: str) -> dict[str, str]:
    f = PROFILES / f"{name}.env"
    if not f.is_file():
        known = ", ".join(sorted(p.stem for p in PROFILES.glob("*.env")))
        sys.exit(f"нет профиля {name!r}; есть: {known}")
    out = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def compose_profiles() -> set[str]:
    """Профили compose, которые принадлежат LLM-профилям (их переключатель и добавляет, и убирает)."""
    return {read_profile(p.stem).get("REQUIRES_COMPOSE_PROFILE", "") for p in PROFILES.glob("*.env")} - {""}


def switch(env_text: str, name: str) -> tuple[str, list[str]]:
    """Текст .env → текст с профилем name; возвращает и список того, что нужно вписать человеку."""
    prof = read_profile(name)
    want = {k: v for k, v in prof.items() if k != "REQUIRES_COMPOSE_PROFILE"} | {"LLM_PROFILE": name}
    lines = env_text.splitlines()
    values = {m.group(1): m.group(2) for m in (re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", x) for x in lines) if m}
    for key in GENERATED.get(name, ()):
        if not values.get(key, "").strip():
            want[key] = secrets.token_hex(24)
    current = [x.strip() for x in values.get("COMPOSE_PROFILES", "").split(",") if x.strip()]
    keep = [x for x in current if x not in compose_profiles()]
    want["COMPOSE_PROFILES"] = ",".join(
        keep + ([prof["REQUIRES_COMPOSE_PROFILE"]] if prof.get("REQUIRES_COMPOSE_PROFILE") else [])
    )
    out, seen = [], set()
    for line in lines:
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
        if m and m.group(1) in want:
            line = f"{m.group(1)}={want[m.group(1)]}"
            seen.add(m.group(1))
        out.append(line)
    added = [k for k in want if k not in seen]
    if added:
        out += ["", f"# --- LLM-профиль ({name}): scripts/llm_profile.py"] + [f"{k}={want[k]}" for k in added]
    missing = [k for k in REQUIRED.get(name, ()) if not values.get(k, "").strip()]
    return "\n".join(out) + "\n", missing


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # консоль Windows (cp1251) не печатает «—»
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("profile", nargs="?", help="имя профиля из deploy/llm/")
    p.add_argument("--env", default=str(ROOT / ".env"), help="файл .env (по умолчанию — в корне проекта)")
    a = p.parse_args()
    env = Path(a.env)
    if not env.is_file():
        sys.exit(f"нет {env}: сначала python scripts/init_env.py")
    text = env.read_text(encoding="utf-8")
    if not a.profile:
        m = re.search(r"^LLM_PROFILE=(.*)$", text, re.M)
        print(f"текущий профиль: {(m.group(1).strip() if m else '') or 'не задан'}")
        print("профили:", ", ".join(sorted(x.stem for x in PROFILES.glob("*.env"))))
        return 0
    new, missing = switch(text, a.profile)
    env.write_text(new, encoding="utf-8", newline="\n")
    print(f"{env.name}: профиль {a.profile}")
    for k in missing:
        print(f"впишите {k} в {env.name} — без него профиль не заработает")
    print("применить: docker compose up -d && docker compose restart ouroboros")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
.env для запуска: копия .env.example + случайные внутренние токены (только stdlib). Пароль UI Ouroboros — из
шаблона (по умолчанию ouroboros).

    python scripts/init_env.py            # создать .env (существующий не трогает)
    python scripts/init_env.py --fill     # дописать пустые внутренние токены в существующий .env

Внешние ключи (ключ LLM-профиля, TAVILY_API_KEY, TELEGRAM_BOT_TOKEN) скрипт не знает — их вписывает человек.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

from llm_profile import REQUIRED  # внешний ключ каждого LLM-профиля

ROOT = Path(__file__).resolve().parents[1]
GENERATED = (
    "GATEWAY_TOKEN_OUROBOROS",
    "GATEWAY_TOKEN_TOOLS",
    "GATEWAY_TOKEN_CONSOLE",
    "TOOLS_API_TOKEN",
    "MCP_TOKEN",
    "GPT2GIGA_API_KEY",
)


def fill(text: str) -> tuple[str, list[str]]:
    done = []
    out = []
    for line in text.splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m and m.group(1) in GENERATED and not m.group(2).strip():
            line = f"{m.group(1)}={secrets.token_hex(24)}"
            done.append(m.group(1))
        out.append(line)
    return "\n".join(out) + "\n", done


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # консоль Windows (cp1251) не печатает «—»
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--fill", action="store_true", help="дописать пустые токены в существующий .env")
    a = p.parse_args()
    env = ROOT / ".env"
    if env.is_file() and not a.fill:
        print(".env уже есть — ничего не меняю (--fill дописывает пустые токены)")
        return 0
    base = env.read_text(encoding="utf-8") if env.is_file() else (ROOT / ".env.example").read_text(encoding="utf-8")
    text, done = fill(base)
    env.write_text(text, encoding="utf-8", newline="\n")
    try:
        env.chmod(0o600)
    except OSError:
        pass
    print(f".env: сгенерированы {', '.join(done) or '—'}")
    m = re.search(r"^LLM_PROFILE=(\S+)", text, flags=re.M)
    profile = m.group(1) if m else ""
    missing = [k for k in REQUIRED.get(profile, ()) if not re.search(rf"^{k}=\S+", text, flags=re.M)]
    if missing:
        print(
            f"впишите {', '.join(missing)} в .env (LLM-профиль {profile}; другой — scripts/llm_profile.py), "
            "по желанию TAVILY_API_KEY и TELEGRAM_BOT_TOKEN, затем: docker compose up -d"
        )
    print("консоль — без пароля: http://127.0.0.1:38620 (OILO_BIND:OILO_PORT), режим администратора — кнопкой в шапке")
    pw = re.search(r"^OUROBOROS_PASSWORD=(.*)$", text, flags=re.M)
    pw = pw.group(1).strip() if pw else ""
    print(
        f"UI Ouroboros (профиль owner) — http://127.0.0.1:38630, пароль {pw} (OUROBOROS_PASSWORD в .env)"
        if pw
        else "впишите OUROBOROS_PASSWORD в .env — без него агент не стартует"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

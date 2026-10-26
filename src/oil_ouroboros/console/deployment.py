"""
Развёртывание для вкладки «Архитектура»: контейнеры, сети, тома и порядок запуска — из docker-compose.yml, а что
делает каждый сервис и зачем он отдельный — из его полей x-role и x-why там же, связи сервисов для карты — x-links и
x-externals (одно место описания; тест проверяет, что всё описано и концы связей существуют). Дополнения деплоя
(deploy/compose/*.yml) и значения из .env сюда не входят — подставляются значения по умолчанию; из окружения берётся
только COMPOSE_PROFILES: какие профили включены в этом развёртывании.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any

import yaml

from ..config import ROOT

ENV = re.compile(r"\$\{(\w+)(?:(:?-)([^}]*)|:?\?[^}]*)?\}")
# секрет — ключ из x-no-secrets compose или производный (GATEWAY_TOKENS, OPENAI_COMPAT_API_KEY);
# карта показывает только имена
SECRET_NAME = re.compile(r"(?:TOKENS?|KEY|PASSWORD|CREDENTIALS)$")


def _env(value: Any) -> str:
    """«${OILO_PORT:-38620}» → «38620», «${NAME:?…}» → «NAME»: значение по умолчанию, а без него —
    имя переменной .env."""
    return ENV.sub(lambda m: m.group(3) if m.group(2) else m.group(1), str(value))


def _when(svc: dict) -> str:
    if svc.get("profiles"):
        return "по профилю " + ", ".join(svc["profiles"])
    return "разово" if str(svc.get("restart", "")) == "no" else "постоянно"


def _order(services: dict[str, dict]) -> list[list[str]]:
    """Порядок запуска ядра по depends_on: ступени — сервисы, которые могут стартовать одновременно."""
    left = {n: set((s.get("depends_on") or {}).keys()) for n, s in services.items() if not s.get("profiles")}
    out: list[list[str]] = []
    while left:
        ready = sorted(n for n, deps in left.items() if not deps & left.keys())
        if not ready:  # цикл в depends_on — отдаём остаток как есть
            out.append(sorted(left))
            break
        out.append(ready)
        for n in ready:
            left.pop(n)
    return out


def _health(s: dict) -> dict | None:
    """Проверка здоровья: команда (без CMD) и сроки — для паспорта в режиме «Развёртывание»."""
    hc = s.get("healthcheck")
    if not hc:
        return None
    test = hc.get("test") or []
    cmd = " ".join(str(x) for x in (test[1:] if isinstance(test, list) and test[:1] == ["CMD"] else test))
    return {
        "test": cmd if len(cmd) <= 140 else cmd[:139] + "…",
        "interval": hc.get("interval", ""),
        "start_period": hc.get("start_period", ""),
    }


def _service(name: str, s: dict, secret_names: set[str]) -> dict:
    deps = s.get("depends_on") or {}
    limits = [
        f"память {s['mem_limit']}" if s.get("mem_limit") else "",
        f"CPU {_env(s['cpus'])}" if s.get("cpus") else "",
    ]
    env = s.get("environment") or {}
    secrets = sorted(k for k, v in env.items() if (k in secret_names or SECRET_NAME.search(k)) and str(v or "") != "")
    command = s.get("command") or []
    return {
        "name": name,
        "image": _env(s.get("image", "")),
        "own_build": bool(s.get("build")),
        "when": _when(s),
        "networks": list(s.get("networks") or []),
        "ports": [_env(p) for p in s.get("ports") or []],
        "volumes": [str(v).split(":")[0] for v in s.get("volumes") or []],
        "depends_on": [f"{k} ({(v or {}).get('condition', '').replace('service_', '')})" for k, v in deps.items()],
        "healthcheck": bool(s.get("healthcheck")),
        "health": _health(s),
        "depends": [
            {"name": k, "condition": (v or {}).get("condition", "").replace("service_", "")} for k, v in deps.items()
        ],
        "secrets": secrets,
        "env_file": s.get("env_file") not in (None, []),
        "restart": str(s.get("restart", "")),
        "user": str(s.get("user", "")),
        "command": " ".join(command) if isinstance(command, list) else str(command),
        "mem": str(s.get("mem_limit", "")),
        "cpus": _env(s["cpus"]) if s.get("cpus") else "",
        "limits": ", ".join(x for x in limits if x),
        "role": s.get("x-role", ""),
        "why": s.get("x-why", ""),
    }


@lru_cache(maxsize=1)
def describe() -> dict:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose.get("services") or {}
    networks = compose.get("networks") or {}
    secret_names = set(compose.get("x-no-secrets") or {})
    return {
        "services": [_service(n, s, secret_names) for n, s in services.items()],
        "networks": [
            {"name": n, "internal": bool(v.get("internal")), "why": v.get("x-why", "")} for n, v in networks.items()
        ],
        "volumes": [{"name": n, "why": (v or {}).get("x-why", "")} for n, v in (compose.get("volumes") or {}).items()],
        "order": _order(services),
        "externals": [{"id": k, "title": v} for k, v in (compose.get("x-externals") or {}).items()],
        "links": [{"from": x["from"], "to": x["to"], "what": x.get("what", "")} for x in compose.get("x-links") or []],
        "profiles_on": _profiles_on(services),
    }


def _profiles_on(services: dict[str, dict]) -> list[str]:
    """Профили compose, включённые в этом развёртывании (COMPOSE_PROFILES из .env); неизвестные имена отбрасываются."""
    known = {p for s in services.values() for p in s.get("profiles") or []}
    return sorted({p.strip() for p in os.environ.get("COMPOSE_PROFILES", "").split(",")} & known)

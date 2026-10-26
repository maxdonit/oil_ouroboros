"""
Развёртывание на любой сервер с Docker: код — из локального рабочего дерева, настройки и секреты — из локального
файла окружения. Один и тот же сценарий для стенда и для промышленного контура (Cloud.ru или любая ВМ).

    python scripts/deploy.py up                  # окружение по умолчанию: локальный .env
    python scripts/deploy.py --env prod up       # окружение prod: локальный .env.prod
    python scripts/deploy.py push                # только копия кода
    python scripts/deploy.py env                 # заполненные строки локального файла → .env на сервере
    python scripts/deploy.py env --pull          # серверный .env → локальный (локальные значения главнее)
    python scripts/deploy.py env --prune         # + удалить с сервера ключи, которых нет в локальном файле
    python scripts/deploy.py restart tools console  # пересоздать сервисы (после смены ключей)
    python scripts/deploy.py ps | logs tools | sh "docker compose top"
    python scripts/deploy.py backup [каталог]    # состояние сервера → архив у оператора (по умолчанию backups/)
    python scripts/deploy.py down --volumes      # остановить и удалить ВСЁ проекта (тома тоже) — для чистого деплоя

Файл окружения (.env, .env.prod, …; в git не попадает) содержит всё: ключи, параметры продукта и цель деплоя:
    DEPLOY_SSH=user@host      DEPLOY_DIR=/opt/oil-ouroboros      DEPLOY_KEY=~/.ssh/id_ed25519 (необязательно)
    COMPOSE_FILE=docker-compose.yml:deploy/compose/proxy.yml   COMPOSE_PROFILES=owner,gigachat
Строки DEPLOY_* остаются локально: на сервер переносится всё остальное, значения не печатаются, файл — 600.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = "# Настройки окружения (в git и в образ не попадает). На сервер: python scripts/deploy.py [--env ИМЯ] env\n"
LOCAL_ONLY = ("DEPLOY_",)
BINARY = (".pdf", ".xls", ".xlsx", ".png", ".jpg", ".ico", ".bundle", ".gz", ".zip")


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():  # -sig: Блокнот сохраняет с BOM
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    out[k.strip()] = v.strip()
    return out


def profile_keys() -> set[str]:
    """Ключи LLM-профилей (deploy/llm/*.env) и COMPOSE_PROFILES: на сервер уходят и пустыми — пустое значение здесь
    тоже выбор профиля (иначе при возврате на OpenRouter на сервере осталась бы модель GigaChat)."""
    keys = {"LLM_PROFILE", "COMPOSE_PROFILES"}
    for f in (ROOT / "deploy" / "llm").glob("*.env"):
        keys |= set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", f.read_text(encoding="utf-8"), re.M)) - {
            "REQUIRES_COMPOSE_PROFILE"
        }
    return keys


def raw_env(path: Path, keys: set[str]) -> dict[str, str]:
    """Значения ключей keys из файла, включая пустые."""
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            k, eq, v = line.strip().partition("=")
            if eq and k.strip() in keys:
                out[k.strip()] = v.strip()
    return out


class Target:
    def __init__(self, env_name: str) -> None:
        self.file = ROOT / (".env" if env_name in ("", "default") else f".env.{env_name}")
        if not self.file.is_file():
            sys.exit(
                f"нет файла окружения {self.file.name}: создайте его из .env.example "
                "и заполните DEPLOY_SSH, DEPLOY_DIR"
            )
        self.env = parse_env(self.file)
        self.host = self.env.get("DEPLOY_SSH") or sys.exit(f"{self.file.name}: не задан DEPLOY_SSH (user@host)")
        self.dir = self.env.get("DEPLOY_DIR") or sys.exit(f"{self.file.name}: не задан DEPLOY_DIR")
        # путь в удалённых командах shell: в кавычках, «~/» — через $HOME (в кавычках тильда не раскрывается)
        self.qdir = '"$HOME"/' + shlex.quote(self.dir[2:]) if self.dir.startswith("~/") else shlex.quote(self.dir)
        key = self.env.get("DEPLOY_KEY", "")
        self.key = os.path.expanduser(key) if key else ""

    def ssh(
        self, cmd: str, stdin: bytes | None = None, check: bool = True, capture: bool = False
    ) -> subprocess.CompletedProcess:
        args = ["ssh", *(["-i", self.key] if self.key else []), "-o", "BatchMode=yes", self.host, cmd]
        return subprocess.run(args, input=stdin, check=check, capture_output=capture)

    def sh(self, cmd: str, check: bool = False) -> int:
        return self.ssh(f"cd {self.qdir} && {cmd}", check=check).returncode


def files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return [f for f in out.decode().split("\0") if f and (ROOT / f).is_file()]


def push(t: Target) -> None:
    """Копия рабочего дерева; файлы, удалённые из репозитория с прошлой выкладки, удаляются и на сервере (манифест)."""
    buf = io.BytesIO()
    names = files()
    manifest = ("\n".join(names) + "\n").encode()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(".deploy-manifest")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
        for f in names:
            data = (ROOT / f).read_bytes()
            if not f.lower().endswith(BINARY):
                # правки на Windows дают CRLF: sh-скрипты в контейнере с ним не стартуют
                data = data.replace(b"\r\n", b"\n")
            info = tarfile.TarInfo(f)
            info.size = len(data)
            info.mode = 0o755 if f.endswith(".sh") else 0o644
            # настоящее время правки: сборка Docker узнаёт изменённый файл по размеру и времени, и при mtime=0
            # правка того же размера («22» → «23») осталась бы в образе старой
            info.mtime = int((ROOT / f).stat().st_mtime)
            tar.addfile(info, io.BytesIO(data))
    print(f"копия: {len(names)} файлов, {buf.tell() // 1024} КБ → {t.host}:{t.dir}")
    t.ssh(
        f"mkdir -p {t.qdir} && cd {t.qdir} && old=$(mktemp) && (cp .deploy-manifest $old 2>/dev/null || :) "
        f"&& tar -xzf - && grep -vxFf .deploy-manifest $old | while IFS= read -r f; "
        f'do rm -f -- "$f" && echo "удалён: $f"; done; '
        f"rm -f $old; test -f .env || (umask 077 && cp .env.example .env)",
        buf.getvalue(),
    )


def merge(base: str, pairs: dict[str, str]) -> tuple[str, list[str], list[str], list[str]]:
    """Вписать pairs в текст .env: существующие строки заменяются на месте, новые — в конец."""
    lines, changed, same = [], [], []
    for line in base.splitlines():
        k, eq, v = line.partition("=")
        k = k.strip()
        if eq and not k.startswith("#") and k in pairs and k not in changed + same:
            (same if v.strip() == pairs[k] else changed).append(k)
            line = f"{k}={pairs[k]}"
        lines.append(line)
    added = [k for k in pairs if k not in changed + same]
    lines += [f"{k}={pairs[k]}" for k in added]
    return "\n".join(lines) + "\n", changed, added, same


def prune_text(base: str, pairs: dict[str, str]) -> tuple[str, list[str]]:
    """Убрать из текста .env ключи, которых нет в pairs (комментарии и пустые строки остаются)."""
    kept, removed = [], []
    for line in base.splitlines():
        k, eq, _ = line.partition("=")
        if eq and not k.strip().startswith("#") and k.strip() not in pairs:
            removed.append(k.strip())
            continue
        kept.append(line)
    return "\n".join(kept) + "\n", removed


def env_sync(t: Target, pull: bool = False, prune: bool = False) -> None:
    remote = t.ssh(f"cat {t.qdir}/.env", capture=True).stdout.decode("utf-8")
    if not remote.strip():
        sys.exit("на сервере нет .env: сначала push")
    if pull:
        local = {k: v for k, v in t.env.items()}
        text, changed, added, same = merge(remote, local)
        t.file.write_text(text if text.startswith(HEADER) else HEADER + text, encoding="utf-8", newline="\n")
        print(
            f"{t.file.name}: забраны параметры сервера; "
            f"локальные значения сохранены: {', '.join(changed + same + added) or '—'}"
        )
        return
    pairs = {k: v for k, v in t.env.items() if not k.startswith(LOCAL_ONLY)}
    pairs |= raw_env(t.file, profile_keys())  # LLM-профиль — и пустые значения
    removed: list[str] = []
    if prune:  # локальный файл — главная копия: ключи, которых в нём нет (или они пустые), удаляются с сервера
        remote, removed = prune_text(remote, pairs)
    text, changed, added, same = merge(remote, pairs)
    if changed or added or removed:  # umask 077: файл с ключами читает только владелец
        t.ssh(f"cd {t.qdir} && umask 077 && cat > .env.new && mv .env.new .env", text.encode())
    print(
        f"изменены: {', '.join(changed) or '—'}; добавлены: {', '.join(added) or '—'}; "
        f"удалены: {', '.join(removed) or '—'}; без изменений: {len(same)}"
    )


def up(t: Target, services: list[str], prune: bool = False) -> None:
    push(t)
    env_sync(t, prune=prune)
    svc = " ".join(services)
    t.sh(f"docker compose build {svc} && docker compose up -d --remove-orphans {svc}", check=True)
    t.sh("docker compose ps --format 'table {{.Service}}	{{.Status}}'")


# Что невосстановимо (белый список): всё остальное — кэши моделей и pip, виртуальные окружения, рабочие деревья,
# индекс Qdrant, кэш прогнозов — пересоздаётся само или командой (ingest --recreate).
BACKUP_PATHS = (
    "tools_var/reports",  # выпуски наблюдателя и ручные загрузки, их каталог, журнал, отметки индекса
    "tools_var/runs.sqlite",  # трейсы инструментов
    "tools_var/evidence.sqlite",  # журнал доказательств (R#/W#/F# — на них ссылаются ответы и трейсы)
    "tools_var/parsed",  # разобранные документы (Docling — самая дорогая операция)
    "tools_var/mcp_audit.jsonl",
    "console_var/turns.sqlite",  # ходы агента, собранные мониторингом
    "console_var/evals",  # прогоны eval
    "gateway_var/gateway/ledger.sqlite",  # журнал вызовов LLM и расходов (GATEWAY_DIR=/app/var/gateway)
    "ouroboros_data",  # состояние и память агента (Ouroboros), settings.json, журналы
)


def backup(t: Target, dest: str) -> None:
    """Невосстановимое состояние сервера → архив на машине оператора (BACKUP_PATHS)."""
    import datetime

    project = t.env.get("COMPOSE_PROJECT_NAME", "oilo")
    out = (
        Path(dest or ROOT / "backups")
        / f"{t.file.name.lstrip('.') or 'env'}-{datetime.datetime.now():%Y%m%d-%H%M}.tgz"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    # SQLite копируем онлайн-бэкапом в контейнерах (рядом, *.sqlite.bak): tar живого файла может дать несогласованную
    # копию; при восстановлении .bak переименовывается обратно (docs/guide/operations.md, раздел 6)
    for svc, vol in (("tools", "tools_var"), ("console", "console_var"), ("gateway", "gateway_var")):
        files = [p.split("/", 1)[1] for p in BACKUP_PATHS if p.startswith(vol + "/") and p.endswith(".sqlite")]
        names = ", ".join(repr(f) for f in files)
        t.sh(
            f'docker compose exec -T {svc} python -c "import sqlite3; '
            f"[sqlite3.connect(f'/app/var/{{n}}').backup(sqlite3.connect(f'/app/var/{{n}}.bak')) "
            f'for n in ({names},)]"'
        )
    vols = ("tools_var", "console_var", "gateway_var", "ouroboros_data")
    mounts = " ".join(f"-v {project}_{v}:/b/{v}:ro" for v in vols)
    pick = " ".join(p + (".bak" if p.endswith(".sqlite") else "") for p in BACKUP_PATHS)
    cmd = (
        f"docker run --rm {mounts} alpine:3.20.3 sh -c "
        f'\'cd /b && tar czf - $(for p in {pick}; do [ -e "$p" ] && echo "$p"; done)\''
    )
    args = ["ssh", *(["-i", t.key] if t.key else []), "-o", "BatchMode=yes", t.host, cmd]
    with out.open("wb") as f:
        rc = subprocess.run(args, stdout=f).returncode
    if rc != 0:
        out.unlink(missing_ok=True)
        sys.exit(f"резервная копия не снята (код {rc})")
    print(f"резервная копия: {out} ({out.stat().st_size // 1024 // 1024} МБ)")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # консоль Windows (cp1251) не печатает «→», «≥»
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description="Развёртывание oil-ouroboros на сервер с Docker")
    p.add_argument("--env", default="", help="окружение: '' — .env, prod — .env.prod, …")
    p.add_argument("action", choices=["push", "up", "env", "restart", "ps", "logs", "sh", "backup", "down"])
    p.add_argument("args", nargs="*")
    p.add_argument("--pull", action="store_true", help="env: забрать серверный .env в локальный")
    p.add_argument(
        "--prune", action="store_true", help="env/up: удалить с сервера ключи, которых нет в локальном файле"
    )
    p.add_argument(
        "--volumes", action="store_true", help="down: удалить и тома проекта (индекс, журналы, память агента)"
    )
    a = p.parse_args()
    t = Target(a.env)
    if a.action == "push":
        push(t)
    elif a.action == "up":
        up(t, a.args, a.prune)
    elif a.action == "env":
        env_sync(t, pull=a.pull, prune=a.prune)
    elif a.action == "restart":
        t.sh(f"docker compose up -d --no-deps --force-recreate {' '.join(a.args)}", check=True)
    elif a.action == "ps":
        t.sh("docker compose ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'")
    elif a.action == "logs":
        t.sh(f"docker compose logs --tail=120 {' '.join(a.args)}")
    elif a.action == "sh":
        t.sh(" ".join(a.args))
    elif a.action == "backup":
        backup(t, a.args[0] if a.args else "")
    elif a.action == "down":
        t.sh("docker compose --profile '*' down --remove-orphans" + (" --volumes" if a.volumes else ""), check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

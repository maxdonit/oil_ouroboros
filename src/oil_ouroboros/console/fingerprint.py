"""
Отпечаток версии агента — то, что ставится на каждый ход и каждый прогон eval, чтобы улучшения были измеримы.

Версия агента = Ouroboros (релиз) + его поведение (identity.md, WORLD.md, knowledge/overview.md — регламент,
patterns.md; модели и режимы из settings.json) + поведение инструментов (отпечаток каталога config/ сервиса tools).
Любая правка регламента (в том числе самим агентом через knowledge_write) или параметра инструментов меняет
agent_version: в мониторинге и eval метрики группируются по нему, а в консоли видно, ЧТО изменилось.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .. import net
from ..config import get_settings

MEMORY_FILES = (
    "memory/identity.md",
    "memory/WORLD.md",
    "memory/knowledge/overview.md",
    "memory/knowledge/patterns.md",
)
JOURNALS = ("memory/identity_journal.jsonl", "memory/knowledge_history.jsonl")
SETTINGS_KEYS = (  # то, что меняет поведение агента; бюджеты и таймауты — не версия (записываются отдельно)
    "OUROBOROS_MODEL",
    "OUROBOROS_MODEL_LIGHT",
    "OUROBOROS_MODEL_HEAVY",
    "OUROBOROS_MODEL_FALLBACKS",
    "OUROBOROS_RUNTIME_MODE",
    "OUROBOROS_CONTEXT_MODE",
    "OUROBOROS_SAFETY_MODE",
    "OUROBOROS_TASK_REVIEW_MODE",
    "OUROBOROS_REVIEW_ENFORCEMENT",
    "OUROBOROS_POST_TASK_EVOLUTION",
    "OUROBOROS_CONSCIOUSNESS_AUTONOMY",
    "OUROBOROS_MAIN_WEB_SEARCH",
    "OUROBOROS_EFFORT_MIN",
    "OUROBOROS_EFFORT_TASK",
    "OUROBOROS_EFFORT_MAX",
    "OUROBOROS_MAX_ROUNDS",
)
OUROBOROS_RELEASE = "7.7.0@e41596c"
_cache: dict = {"at": 0.0, "value": None}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


def agent_side() -> dict:
    root = Path(get_settings().ouroboros_data_dir or "/nonexistent")
    files = {}
    for rel in MEMORY_FILES:
        p = root / rel
        files[rel] = _sha(p.read_bytes()) if p.is_file() else ""
    seed: dict = {}
    try:  # settings.json с секретами недоступен консоли (600) — версия настроек лежит в state/oilo_seed.json (644)
        seed = json.loads((root / "state" / "oilo_seed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    public = seed.get("settings_public") or {}
    live = live_settings()  # владелец меняет режимы в UI Ouroboros — версия агента это видит
    settings = {k: (live if live else public).get(k) for k in SETTINGS_KEYS}
    seeded = seed.get("seeded", {})
    modified_by_agent = [rel for rel, s in files.items() if s and rel in seeded and not seeded[rel].startswith(s)]
    from ..agent import TURN_RULES, compose, disabled_tools

    dialog = compose(
        "{q}", [{"q": "{q0}", "a": "{a0}"}], dialog_pack="P0"
    )  # и блок диалога с пакетом его доказательств
    template = _sha("\n".join([TURN_RULES, compose("{q}"), dialog, "forked", *disabled_tools()]).encode())
    return {
        "files": files,
        "settings": settings,
        "budgets": {k: public.get(k) for k in ("TOTAL_BUDGET", "OUROBOROS_PER_TASK_COST_USD")},
        "task_template": template,
        "modified_by_agent": modified_by_agent,
        "data_dir_mounted": root.is_dir(),
    }


def live_settings() -> dict:
    """Действующие настройки Ouroboros (GET /api/settings, сетевой пароль консоли): только ключи версии, без секретов.
    {} — Ouroboros недоступен: версия берётся из записи засева."""
    s = get_settings()
    headers = {"X-Ouroboros-Password": s.ouroboros_password} if s.ouroboros_password else {}
    try:
        with net.internal(10, headers=headers) as h:
            r = h.get(s.ouroboros_url.rstrip("/") + "/api/settings")
            r.raise_for_status()
            data = r.json()
    except Exception:  # noqa: BLE001
        return {}
    return {k: data.get(k) for k in SETTINGS_KEYS} if isinstance(data, dict) else {}


def tools_side() -> dict:
    try:
        hl, graphs = net.tools_api("/api/health", timeout=10), net.tools_api("/api/graphs", timeout=10)
        reports = net.tools_api("/api/reports", timeout=10)
        mcp = _sha(json.dumps(graphs.get("mcp_tools", []), sort_keys=True, ensure_ascii=False).encode())
        corpus = _sha(json.dumps(sorted(f"{d.get('doc_id')}#{d.get('sha256', '')}" for d in reports)).encode())
        return {
            "catalog": (hl.get("catalog") or {}).get("ref", ""),
            "reranker": hl.get("reranker"),
            "embedder": hl.get("embedder"),
            "tools_version": hl.get("version"),
            "mcp_tools": mcp,
            "corpus": corpus,
            "index_chunks": hl.get("index_chunks"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


def ouroboros_version() -> str:
    s = get_settings()
    try:
        with net.internal(10) as h:
            hl = h.get(s.ouroboros_url.rstrip("/") + "/api/health").json()
        return str(hl.get("version") or hl.get("app_version") or "")
    except Exception:  # noqa: BLE001
        return ""


def current(max_age_s: float = 30.0) -> dict:
    """Отпечаток (кэш на 30 с: файлы памяти меняются редко, а ход читает его в начале и в конце)."""
    if _cache["value"] and time.time() - _cache["at"] < max_age_s:
        return _cache["value"]
    a, t = agent_side(), tools_side()
    core = {
        "ouroboros": OUROBOROS_RELEASE,
        "files": a["files"],
        "settings": a["settings"],
        "task_template": a["task_template"],
        "catalog": t.get("catalog", ""),
        "tools_version": t.get("tools_version"),
        "mcp_tools": t.get("mcp_tools"),
        "corpus": t.get("corpus"),
        "reranker": t.get("reranker"),
    }
    value = {
        **core,
        "ouroboros_reported": ouroboros_version(),
        "budgets": a["budgets"],
        "modified_by_agent": a["modified_by_agent"],
        "index_chunks": t.get("index_chunks"),
        "agent_version": _sha(json.dumps(core, sort_keys=True).encode()),
    }
    _cache.update(at=time.time(), value=value)
    return value


def journals(limit: int = 50) -> dict:
    """Журналы самоизменений агента (identity, knowledge): кто, когда, что поменял — для вкладки «Параметры»."""
    root = Path(get_settings().ouroboros_data_dir or "/nonexistent")
    out = {}
    for rel in JOURNALS:
        p = root / rel
        rows = []
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        out[rel] = rows[::-1]
    return out


def memory_text(rel: str) -> str:
    if rel not in MEMORY_FILES:
        raise ValueError("файл не из списка памяти агента")
    p = Path(get_settings().ouroboros_data_dir or "/nonexistent") / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

"""Согласованность кода, конфигурации, документов и спек OpenSpec — гейт перед коммитом (скилл .claude/skills/sdd).

Ловит дрейф, который не видят функциональные тесты: имена удалённых модулей и режимов, битые ссылки и пути в
документах, параметры без описания в справочнике, инструменты без упоминания, спеки вне таблицы процесса.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

from oil_ouroboros.config import ROOT

TEXT = {".md", ".py", ".js", ".yml", ".yaml", ".json", ".sh", ".toml", ".svg", ".css", ".html", ".env", ".txt"}
# не текущее состояние: записанные ходы демо, инструкции OpenSpec (их обновляет `openspec update`) и change OpenSpec —
# активный описывает файлы, которых ещё нет, архив — которых уже нет
GENERATED = ("data/demo/", ".claude/skills/openspec-", ".claude/commands/opsx/", "openspec/changes/")
# имена удалённых модулей, режимов и вкладок: не должны вернуться ни в код, ни в документы
LEGACY = [
    r"\bsubagents\b", r"console/bridge", r"\bbridge\.", r"rag/watch", r"rag\.watch", r"rag/documents",
    r"rag\.documents", r"egress-direct", r"egress-upstream", r"EGRESS_NETWORK", r"\bAGENT_CPUS\b", r"\bHF_PROXY\b",
    r"\bENV_PROFILE\b", r"config/env", r"GATEWAY_CONSOLE_TOKEN", r"reranker=llm", r"RERANKER=llm", r"docs/demo/turns",
    r"obs/store", r"\bgate_busy\b", r"load_catalog", r"\bDocMeta\b", r"/api/verify\b", r"evidence_of_runs",
    r"deploy/compose/egress", r"вкладк\w* «Ход»", r"«Отчёты»", r"«Карта»", r"«Как это работает»",
    r"\boperator\b", r"\bUI_USERS\b", r"\bUI_OPEN\b",
]  # fmt: skip
DOC_PATH = re.compile(r"`((?:src/oil_ouroboros/|config/|deploy/|ouroboros/|ui/|data/|docs/|scripts/|evals/)[\w./-]+)`")
MODULE_PATH = re.compile(r"`((?:graphs|reports|evidence|rag|console|web|forecast|evals|gateway|llm|mcp)/\w+\.py)`")


@lru_cache(maxsize=1)
def texts() -> dict[str, str]:
    """Отслеживаемые и новые (не игнорируемые) текстовые файлы репозитория."""
    if not shutil.which("git") or not (ROOT / ".git").exists():
        pytest.skip("нет git-репозитория")
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()  # fmt: skip
    files = {}
    for f in out:
        p = ROOT / f
        if (p.suffix in TEXT or f.endswith(".env.example")) and not f.startswith(GENERATED) and p.is_file():
            if p.stat().st_size < 2_000_000:
                files[f] = p.read_text(encoding="utf-8", errors="replace")
    return files


def _where(f: str, text: str, pos: int) -> str:
    return f"{f}:{text[:pos].count(chr(10)) + 1}"


def test_no_legacy_names():
    own = Path(__file__).relative_to(ROOT).as_posix()  # сам список LEGACY
    hits = [
        f"{pat} — {_where(f, t, m.start())}"
        for pat in LEGACY
        for f, t in texts().items()
        if f != own
        for m in re.finditer(pat, t)
    ]
    assert hits == []


def test_markdown_links_resolve():
    broken = []
    for f, t in texts().items():
        if not f.endswith(".md"):
            continue
        for m in re.finditer(r"\]\(([^)#\s]+)(#[^)]*)?\)", t):
            target = m.group(1)
            if not re.match(r"^(https?:|mailto:)", target) and not (ROOT / Path(f).parent / target).exists():
                broken.append(f"{_where(f, t, m.start())} → {target}")
    assert broken == []


def test_paths_named_in_docs_exist():
    """Пути вида `src/oil_ouroboros/…`, `config/…`, `docs/…` в документах, спеках и комментариях существуют."""
    missing = set()
    for f, t in texts().items():
        for m in DOC_PATH.finditer(t):
            path = m.group(1).rstrip("/.")
            if "<" not in path and "*" not in path and not (ROOT / path).exists():
                missing.add(f"{_where(f, t, m.start())} `{path}`")
        for m in MODULE_PATH.finditer(t):
            if not (ROOT / "src/oil_ouroboros" / m.group(1)).exists():
                missing.add(f"{_where(f, t, m.start())} `{m.group(1)}`")
    assert sorted(missing) == []


def test_parameters_are_documented():
    """Каждый параметр кода и compose описан в справочнике или шаблоне .env, а каждый ключ шаблона кем-то читается."""
    from oil_ouroboros.config import Settings

    t = texts()
    doc = t["docs/guide/parameters.md"] + t[".env.example"]
    settings = {n.upper() for n in Settings.model_fields}
    gateway = set(re.findall(r'os\.environ\.get\("([A-Z_]+)"', t["src/oil_ouroboros/gateway/app.py"]))
    compose = set(re.findall(r"\$\{([A-Z_0-9]+)", t["docker-compose.yml"] + t["deploy/compose/proxy.yml"]))
    code = "".join(text for f, text in t.items() if f.endswith((".py", ".yml", ".json", ".sh", ".env")))
    env_reads = set(re.findall(r'os\.environ(?:\.get)?\(?\[?"([A-Z_]+)"', code))
    undocumented = sorted(n for n in settings | gateway | compose | env_reads if n not in doc)
    assert undocumented == []
    example = set(re.findall(r"^([A-Z_0-9]+)=", t[".env.example"], re.M))
    assert sorted(k for k in example if k not in code and k not in compose) == []


def test_catalog_keys_are_documented():
    from oil_ouroboros import catalog

    doc = texts()["docs/guide/parameters.md"]
    keys = [r["key"] for r in catalog.describe()["values"]]
    assert [k for k in keys if k not in doc and k.split(".")[-1] not in doc] == []


def test_mcp_tools_are_described_everywhere():
    """Инструменты агента названы в README, DESIGN, памяти агента и в спеке их контракта — живой или в дельте
    активного change (до архива новое поведение описано там)."""
    from oil_ouroboros.mcp.server import TOOLS

    names = [n for n, *_ in TOOLS]
    t = dict(texts())
    deltas = sorted((ROOT / "openspec/changes").glob("*/specs/mcp-tools/spec.md"))
    t["openspec/specs/mcp-tools/spec.md"] += "".join(d.read_text(encoding="utf-8") for d in deltas)
    places = (
        "README.md",
        "docs/architecture/DESIGN.md",
        "ouroboros/memory/WORLD.md",
        "ouroboros/memory/knowledge/overview.md",
        "openspec/specs/mcp-tools/spec.md",
    )
    assert {f: [n for n in names if n not in t[f]] for f in places if any(n not in t[f] for n in names)} == {}


def test_openspec_specs_match_process_table():
    """Каждая живая спека — в таблице «Возможности и спеки» скилла sdd (и наоборот), без заглушки Purpose после
    архива. Структуру проверяет `openspec validate --all --strict` в CI."""
    specs = {p.parent.name for p in (ROOT / "openspec/specs").glob("*/spec.md")}
    table = set(re.findall(r"^\| `([a-z0-9-]+)` \|", texts()[".claude/skills/sdd/SKILL.md"], re.M))
    assert specs and specs == table, {"нет в таблице": specs - table, "нет спеки": table - specs}
    stubs = [s for s in sorted(specs) if re.search(r"## Purpose\s+TBD", texts()[f"openspec/specs/{s}/spec.md"])]
    assert stubs == []


def test_smoke_expects_exactly_the_mcp_tools():
    """Дымовой тест ждёт в запросе к модели ровно инструменты MCP-сервера: набор консоли не отстаёт от TOOLS."""
    from oil_ouroboros.console.smoke import MCP
    from oil_ouroboros.mcp.server import TOOLS

    assert set(MCP) == {n for n, *_ in TOOLS}


# «N инструментов MCP», «N MCP», «N `mcp_oil__*`»: число инструментов агента в текстах (снимки прошлых прогонов —
# docs/eval_report.md, docs/eval_report_gigachat.md, docs/demo_dialogs.md — не трогаем: там число своего времени)
TOOL_COUNT = re.compile(r"\b(\d+)\s+(?:инструмент\w*\s+(?:по\s+)?MCP|MCP\b|`mcp_oil__\*`)")
SNAPSHOTS = ("docs/eval_report.md", "docs/eval_report_gigachat.md", "docs/demo_dialogs.md")


def test_mcp_tool_count_in_texts():
    """Число инструментов MCP в документах, compose, схеме и docstring'ах равно числу инструментов сервера."""
    from oil_ouroboros.mcp.server import TOOLS

    wrong = [
        f"{_where(f, t, m.start())} «{m.group(0)}»"
        for f, t in texts().items()
        if f not in SNAPSHOTS and f != Path(__file__).relative_to(ROOT).as_posix()
        for m in TOOL_COUNT.finditer(t)
        if int(m.group(1)) != len(TOOLS)
    ]
    assert wrong == []

"""Засев агента: интеграция — как код, правка владельца в UI Ouroboros и память, улучшенная агентом, —
не затираются."""

from __future__ import annotations

import json

import pytest

from oil_ouroboros import bootstrap

ENV = {
    "GATEWAY_TOKEN_OUROBOROS": "gw-o",
    "MCP_TOKEN": "mcp-t",
    "OUROBOROS_PASSWORD": "pw",
    "OUROBOROS_MODEL": "openai/gpt-6-luna",
}


def test_first_run_seeds_settings_and_memory(tmp_path):
    rep = bootstrap.run(tmp_path, env=ENV)
    s = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert s["OUROBOROS_MODEL"] == "openai-compatible::openai/gpt-6-luna"
    assert (
        s["OUROBOROS_SAFETY_MODE"] == "off"
        and s["OUROBOROS_TASK_REVIEW_MODE"] == "off"
        and s["OUROBOROS_RUNTIME_MODE"] == "advanced"
    )
    assert s["MCP_SERVERS"][0]["auth_token"] == "Bearer mcp-t" and s["OPENAI_COMPATIBLE_API_KEY"] == "gw-o"
    assert s["TOTAL_BUDGET"] == 4 and isinstance(s["OUROBOROS_PER_TASK_COST_USD"], float)
    assert {m["action"] for m in rep["memory"]} == {"создан"}
    assert "Регламент" in (tmp_path / "memory/knowledge/overview.md").read_text(encoding="utf-8")


def test_integration_is_code_owner_choice_and_foreign_keys_survive(tmp_path):
    bootstrap.run(tmp_path, env=ENV)
    p = tmp_path / "settings.json"
    s = json.loads(p.read_text(encoding="utf-8"))
    first_boot = s["OUROBOROS_ONBOARDING_COMPLETED_AT"]
    s["OUROBOROS_SAFETY_MODE"] = "full"  # владелец включил проверку безопасности в UI Ouroboros
    s["OPENAI_COMPATIBLE_BASE_URL"] = "https://elsewhere"  # интеграцию правили руками — вернётся
    s["OUROBOROS_SOME_RUNTIME_KEY"] = "kept"  # ключ, который дописал сам Ouroboros
    p.write_text(json.dumps(s), encoding="utf-8")
    rep = bootstrap.run(tmp_path, env=ENV)
    s2 = json.loads(p.read_text(encoding="utf-8"))
    assert s2["OUROBOROS_SAFETY_MODE"] == "full" and rep["settings_kept"] == ["OUROBOROS_SAFETY_MODE"]
    assert (
        s2["OPENAI_COMPATIBLE_BASE_URL"] == "http://gateway:8030/api/v1"
        and "OPENAI_COMPATIBLE_BASE_URL" in rep["settings_changed"]
    )
    assert s2["OUROBOROS_SOME_RUNTIME_KEY"] == "kept" and s2["OUROBOROS_ONBOARDING_COMPLETED_AT"] == first_boot


def test_new_seed_updates_only_owner_keys_the_owner_did_not_touch(tmp_path):
    bootstrap.run(tmp_path, env=ENV)
    p = tmp_path / "settings.json"
    s = json.loads(p.read_text(encoding="utf-8"))
    s["OUROBOROS_RUNTIME_MODE"] = "light"  # значение прошлого засева: владелец его не трогал
    s["OUROBOROS_MAX_ACTIVE_SUBAGENTS_PER_ROOT"] = 2  # а это выбрал владелец
    p.write_text(json.dumps(s), encoding="utf-8")
    state_file = tmp_path / "state" / "oilo_seed.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["settings_seeded"]["OUROBOROS_RUNTIME_MODE"] = "light"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    bootstrap.run(tmp_path, env=ENV)
    s2 = json.loads(p.read_text(encoding="utf-8"))
    assert s2["OUROBOROS_RUNTIME_MODE"] == "advanced" and s2["OUROBOROS_MAX_ACTIVE_SUBAGENTS_PER_ROOT"] == 2


def test_telegram_token_from_env_is_seeded_once(tmp_path):
    bootstrap.run(tmp_path, env={**ENV, "TELEGRAM_BOT_TOKEN": "123:abc"})
    p = tmp_path / "settings.json"
    s = json.loads(p.read_text(encoding="utf-8"))
    assert s["TELEGRAM_BOT_TOKEN"] == "123:abc"
    s["TELEGRAM_BOT_TOKEN"] = "456:rotated"  # владелец сменил токен в UI
    p.write_text(json.dumps(s), encoding="utf-8")
    bootstrap.run(tmp_path, env={**ENV, "TELEGRAM_BOT_TOKEN": "123:abc"})
    assert json.loads(p.read_text(encoding="utf-8"))["TELEGRAM_BOT_TOKEN"] == "456:rotated"
    assert (
        "TELEGRAM_BOT_TOKEN"
        not in json.loads((tmp_path / "state" / "oilo_seed.json").read_text(encoding="utf-8"))["settings_public"]
    )


def test_agent_improved_playbook_is_kept_and_reset_restores(tmp_path):
    bootstrap.run(tmp_path, env=ENV)
    ov = tmp_path / "memory/knowledge/overview.md"
    ov.write_text(ov.read_text(encoding="utf-8") + "\n- Новое правило, которое агент вывел сам.\n", encoding="utf-8")
    rep = bootstrap.run(tmp_path, env=ENV)
    assert any(m["file"].endswith("overview.md") and m["action"].startswith("оставлен") for m in rep["memory"])
    assert "агент вывел сам" in ov.read_text(encoding="utf-8")
    bootstrap.run(tmp_path, env=ENV, reset=True)
    assert "агент вывел сам" not in ov.read_text(encoding="utf-8")


def test_missing_secrets_fail_loudly(tmp_path):
    with pytest.raises(SystemExit) as e:
        bootstrap.run(tmp_path, env={"OUROBOROS_MODEL": "x/y"})
    assert "GATEWAY_TOKEN_OUROBOROS" in str(e.value) and "MCP_TOKEN" in str(e.value)


def test_agent_is_allowed_exactly_the_mcp_tools():
    """Список инструментов агента в засеве настроек = инструменты MCP-сервера: новый инструмент, не добавленный в
    allowed_tools, агент молча не увидел бы."""
    import json
    import re

    from oil_ouroboros.config import ROOT
    from oil_ouroboros.mcp.server import TOOLS

    raw = (ROOT / "ouroboros" / "settings.template.json").read_text(encoding="utf-8")
    allowed = json.loads(re.search(r'"allowed_tools"\s*:\s*(\[[^\]]*\])', raw).group(1))
    assert allowed == [name for name, *_ in TOOLS]

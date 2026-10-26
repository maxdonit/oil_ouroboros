"""LLM-профили стенда (scripts/llm_profile.py, deploy/llm/): переключение .env туда и обратно без потерь."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENV = """OPENROUTER_API_KEY=sk-x
OUROBOROS_MODEL=anthropic/claude-haiku-5.5
OUROBOROS_MODEL_LIGHT=deepseek/deepseek-v4.1-flash
COMPOSE_PROFILES=owner
GIGACHAT_CREDENTIALS=
GPT2GIGA_API_KEY=
"""


def test_switch_to_gigachat_and_back_keeps_other_profiles_and_keys():
    lp = _load("llm_profile")
    giga, missing = lp.switch(ENV, "gigachat")
    v = dict(line.split("=", 1) for line in giga.splitlines() if "=" in line and not line.startswith("#"))
    assert v["OUROBOROS_MODEL"].startswith("GigaChat") and v["LLM_MODEL_SUBAGENT"].startswith("GigaChat")
    assert v["COMPOSE_PROFILES"] == "owner,gigachat" and len(v["GPT2GIGA_API_KEY"]) == 48
    assert v["LLM_PROFILE"] == "gigachat" and v["OPENROUTER_API_KEY"] == "sk-x" and missing == ["GIGACHAT_CREDENTIALS"]
    back, missing = lp.switch(giga, "openrouter")
    w = dict(line.split("=", 1) for line in back.splitlines() if "=" in line and not line.startswith("#"))
    assert w["OUROBOROS_MODEL"] == "anthropic/claude-haiku-5.5" and w["LLM_MODEL_SUBAGENT"] == ""
    assert w["COMPOSE_PROFILES"] == "owner" and w["GPT2GIGA_API_KEY"] == v["GPT2GIGA_API_KEY"] and not missing


def test_env_template_is_the_default_profile():
    """Шаблон .env — профиль по умолчанию gigachat с каналом владельца: переключатель на gigachat меняет в нём только
    пустой внутренний токен прокси, а переход на OpenRouter требует ключ OpenRouter."""
    lp = _load("llm_profile")
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert "LLM_PROFILE=gigachat" in lines and "COMPOSE_PROFILES=owner,gigachat" in lines
    same, missing = lp.switch(text, "gigachat")
    changed = set(lines) ^ set(same.splitlines())
    assert {x.split("=", 1)[0] for x in changed} == {"GPT2GIGA_API_KEY"} and missing == ["GIGACHAT_CREDENTIALS"]
    other, missing = lp.switch(text, "openrouter")
    assert "COMPOSE_PROFILES=owner" in other.splitlines() and missing == ["OPENROUTER_API_KEY"]


def test_deploy_syncs_profile_keys_even_when_empty(tmp_path):
    """Пустое значение ключа профиля тоже уходит на сервер: иначе при возврате на OpenRouter там осталась бы модель
    GigaChat. Остальные пустые ключи (например, ключ, вписанный только на сервере) не трогаются."""
    dp = _load("deploy")
    env = tmp_path / ".env"
    env.write_text("LLM_MODEL_SUBAGENT=\nCOMPOSE_PROFILES=\nGIGACHAT_CREDENTIALS=\n", encoding="utf-8")
    keys = dp.profile_keys()
    assert {
        "OUROBOROS_MODEL",
        "LLM_MODEL_SUBAGENT",
        "COMPOSE_PROFILES",
        "LLM_PROFILE",
    } <= keys and "REQUIRES_COMPOSE_PROFILE" not in keys
    assert dp.raw_env(env, keys) == {"LLM_MODEL_SUBAGENT": "", "COMPOSE_PROFILES": ""}

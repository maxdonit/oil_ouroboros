"""Каталог поведения config/: строгая загрузка, слои, отпечаток — без сети."""

import shutil

import pytest

from oil_ouroboros import catalog


@pytest.fixture()
def cfg(tmp_path):
    """Копия каталога: тест портит её, не трогая настоящий."""
    d = tmp_path / "config"
    shutil.copytree(catalog.config_dir(), d)
    return d


def _load(cfg, **kw):
    import os

    old = os.environ.get("CONFIG_DIR")
    os.environ["CONFIG_DIR"] = str(cfg)
    from oil_ouroboros.config import get_settings

    get_settings.cache_clear()
    try:
        return catalog.load(**kw)
    finally:
        if old is None:
            os.environ.pop("CONFIG_DIR", None)
        else:
            os.environ["CONFIG_DIR"] = old
        get_settings.cache_clear()


def test_current_catalog_is_valid_and_fingerprinted():
    c = catalog.load(set_spec="")
    assert set(c.prompts) == set(catalog.PROMPTS)
    assert c.retrieval.top_k >= 1 and c.reports.sources
    assert len(c.fp) == 12 and c.ref.startswith("current@")
    assert "retrieval.yaml" in c.lock and "prompts/research_plan.md" in c.lock


def test_strictness_collects_all_problems(cfg):
    (cfg / "current/retrieval.yaml").write_text(
        (cfg / "current/retrieval.yaml").read_text(encoding="utf-8") + "top_k: 7\n", encoding="utf-8"
    )
    (cfg / "current/web.yaml").write_text(
        (cfg / "current/web.yaml").read_text(encoding="utf-8") + "surprise: 1\n", encoding="utf-8"
    )
    p = cfg / "current/prompts/select_grade.md"
    p.write_text(p.read_text(encoding="utf-8").replace("{top_k}", "{k}"), encoding="utf-8")
    (cfg / "current/stray.yaml").write_text("a: 1\n", encoding="utf-8")
    with pytest.raises(catalog.CatalogError) as e:
        _load(cfg, set_spec="")
    text = "\n".join(e.value.problems)
    for needle in ("повтор ключа 'top_k'", "surprise", "select_grade.md: переменные", "stray.yaml: файл без схемы"):
        assert needle in text, needle


def test_yaml_12_booleans_and_ranges(cfg):
    w = cfg / "current/web.yaml"
    w.write_text(
        w.read_text(encoding="utf-8").replace("needs_freshness: true", "needs_freshness: yes"), encoding="utf-8"
    )
    with pytest.raises(catalog.CatalogError):  # «yes» — строка, а не логическое значение
        _load(cfg, set_spec="")


def test_set_layer_changes_fingerprint():
    a = catalog.load(set_spec="")
    b = catalog.load(set_spec="retrieval.top_k=8")
    assert b.retrieval.top_k == 8 and b.layers["retrieval.top_k"] == "set" and a.fp != b.fp


def test_env_layer_overrides_model(monkeypatch):
    """LLM_MODEL_* — слой env: переопределяет модель роли и отмечается в layers."""
    from oil_ouroboros.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_model_subagent", "test/override-model")
    c = catalog.load(set_spec="")
    assert c.llm.subagent.model == "test/override-model" and c.layers["llm.subagent.model"] == "env"


def test_broken_frontmatter_is_collected(cfg):
    p = cfg / "current/prompts/select_grade.md"
    p.write_text(p.read_text(encoding="utf-8").replace("format: python", "format: jinja"), encoding="utf-8")
    with pytest.raises(catalog.CatalogError) as e:
        _load(cfg, set_spec="")
    assert "select_grade.md: format" in "\n".join(e.value.problems)


def test_set_layer_is_restricted_to_overlay_sections():
    with pytest.raises(catalog.CatalogError) as e:
        catalog.load(set_spec="source_tiers.thresholds.deny_below=0;foo.bar=1")
    text = "\n".join(e.value.problems)
    assert "source_tiers" in text and "foo" in text and "нельзя менять слоем" in text


def test_fp_ignores_trailing_whitespace_in_prompts(cfg):
    a = _load(cfg, set_spec="")
    p = cfg / "current/prompts/research_plan.md"
    head, sep, body = p.read_text(encoding="utf-8").partition("\n---\n")
    p.write_text(head + sep + body.replace("\n", "   \n"), encoding="utf-8")  # хвостовые пробелы только в тексте
    assert _load(cfg, set_spec="").fp == a.fp


def test_any_broken_file_is_a_problem_not_a_crash(cfg):
    """Кривой файл не роняет загрузку исключением: всё — в списке проблем (сервис отвечает 503 с перечнем)."""
    p = cfg / "current/prompts/select_grade.md"
    p.write_text("---\nпросто текст\n---\nтело", encoding="utf-8")  # frontmatter — строка, а не словарь
    (cfg / "current/research.yaml").write_bytes("version: 1\n# коммент\n".encode("cp1251"))  # не UTF-8
    with pytest.raises(catalog.CatalogError) as e:
        _load(cfg, set_spec="web.policy=1;web.policy.x=2")  # противоречивый CATALOG_SET
    text = "\n".join(e.value.problems)
    assert "select_grade.md: frontmatter — не словарь" in text and "research.yaml: файл не в кодировке UTF-8" in text
    assert "CATALOG_SET: web.policy.x" in text


def test_section_that_is_a_list_with_a_layer_on_top_is_a_problem(cfg):
    (cfg / "current/retrieval.yaml").write_text("- список\n- вместо разделов\n", encoding="utf-8")
    with pytest.raises(catalog.CatalogError) as e:
        _load(cfg, set_spec="retrieval.top_k=8")  # слой поверх такого файла не должен падать
    assert "retrieval.yaml: ожидается словарь ключей, а не list" in "\n".join(e.value.problems)


def test_scenario_and_threshold_values_must_be_consistent():
    with pytest.raises(catalog.CatalogError) as e:
        catalog.load(
            set_spec="forecast.scenario.presets.cci.eps_s=0;forecast.scenario.presets.cci.eps_d=0;"
            "forecast.scenario.urals_discount.low=40"
        )
    text = "\n".join(e.value.problems)
    assert "оба равны 0" in text and "low ≤ base ≤ high" in text

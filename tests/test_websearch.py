"""Веб-поиск без сети: откат на резервный провайдер при исчерпанном лимите, кэш выдачи для замеров, учёт лимита."""

import httpx
import pytest

from oil_ouroboros.config import get_settings
from oil_ouroboros.web import search


def _doc(url: str, name: str) -> search.WebDoc:
    return search.WebDoc(url=url, title="t", content="c", published="2026-10-20", provider=name, provider_score=1.0)


@pytest.fixture()
def web(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "web_provider", "tavily")
    monkeypatch.setattr(s, "tavily_api_key", "k")
    monkeypatch.setattr(s, "brave_api_key", "")
    monkeypatch.setattr(s, "web_fallback", "ddgs")
    monkeypatch.setattr(s, "web_cache_dir", "")
    monkeypatch.setattr(search, "_down", {})
    calls = {"tavily": 0, "ddgs": 0}

    def tavily_over_limit(q, include, exclude, fresh, n):
        calls["tavily"] += 1
        req = httpx.Request("POST", "https://api.tavily.com/search")
        raise httpx.HTTPStatusError("432", request=req, response=httpx.Response(432, request=req))

    def ddgs(q, include, exclude, fresh, n):
        calls["ddgs"] += 1
        return [
            _doc("https://www.reuters.com/a", "ddgs"),
            _doc("https://www.eia.gov/b", "ddgs"),
            _doc("https://www.iea.org/c", "ddgs"),
        ]

    monkeypatch.setitem(search.PROVIDERS, "tavily", tavily_over_limit)
    monkeypatch.setitem(search.PROVIDERS, "ddgs", ddgs)
    return calls


def test_quota_exhausted_falls_back_and_is_marked(web):
    r = search.search(["OPEC+ decision"])
    assert r.docs and r.provider == "ddgs" and not r.error  # ответ есть — из резервного провайдера
    assert "tavily: лимит исчерпан" in r.passes[0]["fallback"]
    search.search(["другой вопрос"])
    assert web["tavily"] == 1  # провайдер с исчерпанным лимитом дальше не дёргается (пауза 30 мин)


def test_without_fallback_the_error_is_explicit(web, monkeypatch):
    monkeypatch.setattr(get_settings(), "web_fallback", "none")
    r = search.search(["OPEC+ decision"])
    assert not r.docs and "все провайдеры недоступны" in r.error


def test_cache_returns_the_same_results_without_calling_provider(web, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "web_cache_dir", str(tmp_path / "cache"))
    first = search.search(["OPEC+ decision"])
    second = search.search(["OPEC+ decision"])
    assert web["ddgs"] == 1 and [d.url for d in first.docs] == [d.url for d in second.docs]
    assert second.passes[0]["source"] == "cache"


def test_empty_week_falls_through_to_open_and_month(web, monkeypatch):
    seen: list[tuple] = []

    def ddgs(q, include, exclude, fresh, n):
        seen.append((bool(include), fresh))
        return [] if fresh == "week" else [_doc("https://www.reuters.com/opec", "ddgs")]  # за неделю пусто

    monkeypatch.setitem(search.PROVIDERS, "ddgs", ddgs)
    r = search.search(["OPEC+ decision this week"], freshness="week")
    assert seen == [(True, "week"), (False, "week"), (False, "month")]  # доверенные → открытый → месяц
    assert [d.url for d in r.docs] == ["https://www.reuters.com/opec"] and not r.error
    assert r.passes[-1]["mode"] == "open_month_fallback"


def test_ddgs_no_results_is_an_empty_answer_not_an_error(monkeypatch):
    from ddgs.exceptions import DDGSException

    def boom(*a, **k):
        raise DDGSException("No results found.")

    monkeypatch.setattr(search, "_ddgs_rows", boom)
    assert search._ddgs("OPEC+", None, None, "week", 5) == []

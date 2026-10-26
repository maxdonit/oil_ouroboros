"""Вызов LLM субагента: дублирующий запрос при всплеске задержки; предзагрузка веба в research."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future

from langchain_core.messages import AIMessage, HumanMessage

from oil_ouroboros.graphs import research
from oil_ouroboros.llm import core


class _SlowFirst:
    """Первый запрос «завис» у провайдера, второй отвечает сразу."""

    def __init__(self) -> None:
        self.calls = 0
        self.lock = threading.Lock()

    def invoke(self, messages):
        with self.lock:
            self.calls += 1
            n = self.calls
        if n == 1:
            time.sleep(0.5)
            return AIMessage(content="медленный")
        return AIMessage(content="быстрый")


def test_hedged_request_takes_the_first_answer():
    m = _SlowFirst()
    msg, hedged = core._invoke(m, [HumanMessage(content="план")], hedge_after_s=0.05)
    assert hedged and msg.content == "быстрый" and m.calls == 2
    msg, hedged = core._invoke(_SlowFirst(), [HumanMessage(content="план")], hedge_after_s=0)
    assert not hedged and msg.content == "медленный"


def test_web_node_takes_the_prefetched_search(monkeypatch):
    monkeypatch.setattr(
        research.web, "search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("поиск не повторяется"))
    )

    from oil_ouroboros.web.search import WebResult

    res = WebResult(docs=[], rejected=[], provider="ddgs", policy_version="p1", ms=5)

    fut: Future = Future()
    fut.set_result(res)
    research._PREFETCH["t1"] = fut
    out = research.web_node(
        {"question": "новости ОПЕК+", "plan": {"needs_freshness": True, "queries": ["q"]}, "web_prefetch": "t1"}
    )
    assert out["web"]["provider"] == "ddgs" and out["web"]["freshness"] == "week" and "t1" not in research._PREFETCH


def test_gigachat_price_is_zero_without_openrouter_catalog(monkeypatch):
    """Профиль gigachat: цена GigaChat-* — 0, каталог цен OpenRouter не скачивается (это шло бы внутри хода)."""
    import pytest

    from oil_ouroboros import net
    from oil_ouroboros.llm import pricing

    monkeypatch.setattr(pricing, "_prices", {})
    monkeypatch.setattr(net, "outbound", lambda *a, **kw: pytest.fail("за каталогом цен в сеть не ходим"))
    assert pricing.cost("GigaChat-3-Ultra", 100_000, 1_000) == 0.0

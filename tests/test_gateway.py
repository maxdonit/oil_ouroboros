"""LLM-шлюз: токены вызывающих, жёсткий лимит денег, журнал вызовов — на поддельном upstream (без сети и денег)."""

from __future__ import annotations

import importlib
import json

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def gw(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_DIR", str(tmp_path / "gw"))
    monkeypatch.setenv("GATEWAY_TOKENS", "ouroboros:tok-o,tools:tok-t,console:tok-c")
    monkeypatch.setenv("GATEWAY_BUDGET_USD", "0.05")
    monkeypatch.setenv("GATEWAY_RESERVE_USD", "0.001")
    monkeypatch.setenv("GATEWAY_CALLER_BUDGETS", "tools:0.015")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-upstream")
    from oil_ouroboros.gateway import app as mod

    mod = importlib.reload(mod)
    seen: list[dict] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"auth": request.headers.get("authorization"), "body": body, "headers": dict(request.headers)})
        if body.get("stream"):
            chunks = (
                'data: {"id":"gen-1","choices":[{"delta":{"content":"При"}}]}\n\n'
                'data: {"id":"gen-1","choices":[{"delta":{"content":"вет"},"finish_reason":"stop"}]}\n\n'
                'data: {"id":"gen-1","choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":20,"cost":0.01,'
                '"prompt_tokens_details":{"cached_tokens":800}}}\n\ndata: [DONE]\n\n'
            )
            return httpx.Response(
                200, stream=httpx.ByteStream(chunks.encode()), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(
            200,
            json={
                "id": "gen-2",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 10, "cost": 0.01},
            },
        )

    monkeypatch.setattr(
        mod, "_client", lambda external=True: httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    )
    return mod, TestClient(mod.app), seen


def _chat(client, token: str, stream: bool = False, run: str = ""):
    extra = {"X-Oil-Run": run, "X-Oil-Purpose": "select_grade"} if run else {}
    headers = {"Authorization": f"Bearer {token}"} | extra
    return client.post(
        "/api/v1/chat/completions",
        headers=headers,
        json={
            "model": "openai/gpt-6-luna",
            "stream": stream,
            "messages": [{"role": "user", "content": "Прогноз Brent?"}],
        },
    )


def test_token_is_required_and_upstream_key_is_substituted(gw):
    mod, c, seen = gw
    assert _chat(c, "wrong").status_code == 401
    r = _chat(c, "tok-o")
    assert r.status_code == 200 and seen[-1]["auth"] == "Bearer sk-upstream"
    assert seen[-1]["body"]["usage"] == {"include": True}  # OpenRouter вернёт usage.cost


def test_ledger_records_calls_and_service_headers_do_not_leak(gw):
    mod, c, seen = gw
    _chat(c, "tok-t", run="abc123")
    assert "x-oil-run" not in {k.lower() for k in seen[-1]["headers"]}
    _chat(c, "tok-o", stream=True)
    led = c.get("/ledger", headers={"Authorization": "Bearer tok-c"}).json()
    rows = {r["caller"]: r for r in led["calls"]}
    assert rows["tools"]["run_id"] == "abc123" and rows["tools"]["purpose"] == "select_grade"
    o = rows["ouroboros"]
    assert (
        o["stream"] == 1
        and o["tokens_in"] == 1000
        and o["tokens_cached"] == 800
        and o["cost"] == 0.01
        and o["gen_id"] == "gen-1"
    )
    assert o["first_user"] == "Прогноз Brent?"
    body = c.get(f"/calls/{o['id']}", headers={"Authorization": "Bearer tok-c"}).json()
    assert body["response"]["stream_text"] == "Привет"
    assert c.get("/ledger", headers={"Authorization": "Bearer tok-o"}).status_code == 401  # агент журнал не читает


def test_budget_is_a_hard_stop_globally_and_per_caller(gw):
    mod, c, _ = gw
    assert _chat(c, "tok-t").status_code == 200
    r = _chat(c, "tok-t")  # 0.01 потрачено, резерв 0.001: второй вызов tools ещё в лимите 0.015
    assert r.status_code == 200
    r = _chat(c, "tok-t")  # 0.02 + 0.001 > 0.015
    assert r.status_code == 402 and "tools" in r.json()["error"]["message"]
    for _ in range(3):
        _chat(c, "tok-o")
    r = _chat(c, "tok-o")  # 0.05 + резерв > 0.05: общий лимит
    assert r.status_code == 402 and "общий бюджет" in r.json()["error"]["message"]
    assert c.get("/health").json()["refused"] == 2


def test_max_tokens_is_capped_and_stream_reservation_released(gw):
    mod, c, seen = gw
    c.post(
        "/api/v1/chat/completions",
        headers={"Authorization": "Bearer tok-o"},
        json={"model": "m", "max_tokens": 65536, "messages": [{"role": "user", "content": "x"}]},
    )
    assert seen[-1]["body"]["max_tokens"] == mod.MAX_TOKENS
    assert seen[-1]["headers"].get("accept-encoding") == "identity"
    _chat(c, "tok-o", stream=True)
    assert c.get("/health").json()["in_flight"] == 0


def test_anthropic_prompt_cache_markers_are_restored(gw):
    mod, c, seen = gw
    msgs = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "SYSTEM+BIBLE"},
                {"type": "text", "text": "память"},
                {"type": "text", "text": "динамика раунда"},
            ],
        },
        {"role": "user", "content": "Вопрос и правила хода"},
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "content": "результат research"},
        {"role": "user", "content": "[Host clock …]"},
    ]
    c.post(
        "/api/v1/chat/completions",
        headers={"Authorization": "Bearer tok-o"},
        json={"model": "anthropic/claude-haiku-5.5", "messages": msgs},
    )
    sent = seen[-1]["body"]["messages"]
    marked = [
        (i, j)
        for i, m in enumerate(sent)
        for j, b in enumerate(m["content"] if isinstance(m["content"], list) else [])
        if isinstance(b, dict) and "cache_control" in b
    ]
    assert marked == [
        (0, 0),
        (0, 1),
        (1, 0),
        (4, 0),
    ]  # стабильные блоки системы, задача, хвост; динамика — без маркера
    c.post(
        "/api/v1/chat/completions",
        headers={"Authorization": "Bearer tok-o"},
        json={"model": "openai/gpt-6-luna", "messages": [{"role": "user", "content": "x"}]},
    )
    assert "cache_control" not in json.dumps(seen[-1]["body"])


def test_only_generation_and_reference_reads_are_forwarded(gw):
    mod, c, seen = gw
    h = {"Authorization": "Bearer tok-o"}
    n = len(seen)
    assert c.post("/api/v1/keys", headers=h, json={"name": "x"}).status_code == 404  # управление ключами — закрыто
    assert c.get("/api/v1/../admin", headers=h).status_code in (404, 400)
    assert len(seen) == n  # провайдер не вызван
    assert mod.allowed_path("POST", "chat/completions") and mod.allowed_path("GET", "models/openai/gpt-6-luna")
    assert not mod.allowed_path("POST", "models") and not mod.allowed_path("GET", "keys")


def test_encoded_dot_segments_cannot_escape_the_allowlist(gw):
    mod, c, seen = gw
    h = {"Authorization": "Bearer tok-o"}
    n = len(seen)
    for p in ("/api/v1/models/%2e%2e/keys", "/api/v1/models/..%2Fkeys", "/api/v1/credits/%2E%2E/activity"):
        assert c.get(p, headers=h).status_code == 404, p
    assert len(seen) == n  # провайдер не вызван
    assert (
        mod.clean_path("models/%2e%2e/keys") is None
        and mod.clean_path("models/openai/gpt-6-luna") == "models/openai/gpt-6-luna"
    )


def test_call_without_provider_cost_is_priced_not_free(gw, monkeypatch):
    """Провайдер не прислал usage.cost: вызов оценивается по прайс-листу и попадает в лимит, а не считается
    бесплатным."""
    mod, c, seen = gw
    no_cost = httpx.Response(
        200,
        json={
            "id": "gen-3",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 100_000, "completion_tokens": 1000},
        },
    )
    monkeypatch.setattr(
        mod, "_client", lambda external=True: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: no_cost))
    )
    assert _chat(c, "tok-o").status_code == 200
    assert mod._state["spent"] == pytest.approx(100_000 * 0.10e-6 + 1000 * 0.50e-6)  # запасная цена openai/gpt-6-luna


def test_gigachat_models_go_to_the_gpt2giga_proxy_without_openrouter_fields(gw, monkeypatch):
    """Профиль gigachat: модели GigaChat-* — в прокси gpt2giga со своим ключом и без полей OpenRouter; остальные — как
    раньше. Ключ OpenRouter в прокси не уходит."""
    mod, c, _ = gw
    monkeypatch.setattr(mod, "GIGACHAT_KEY", "giga-key")
    got: list[httpx.Request] = []

    def upstream(request: httpx.Request) -> httpx.Response:
        got.append(request)
        return httpx.Response(
            200,
            json={
                "id": "g",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 30},
            },
        )

    monkeypatch.setattr(
        mod, "_client", lambda external=True: httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    )
    for model in ("GigaChat-3-Ultra", "openai/gpt-6-luna"):
        r = c.post(
            "/api/v1/chat/completions",
            headers={"Authorization": "Bearer tok-o"},
            json={"model": model, "stream": False, "messages": [{"role": "user", "content": "Прогноз Brent?"}]},
        )
        assert r.status_code == 200
    giga, orr = got
    assert (
        str(giga.url) == "http://gpt2giga:8090/v1/chat/completions"
        and giga.headers["authorization"] == "Bearer giga-key"
    )
    assert "usage" not in json.loads(giga.content) and "usage" in json.loads(orr.content)
    assert orr.headers["authorization"] == "Bearer sk-upstream" and str(orr.url).startswith(mod.UPSTREAM)
    rows = {r["model"]: r for r in c.get("/ledger", headers={"Authorization": "Bearer tok-c"}).json()["calls"]}
    assert rows["GigaChat-3-Ultra"]["tokens_in"] == 1200 and rows["GigaChat-3-Ultra"]["cost"] == 0.0  # Freemium

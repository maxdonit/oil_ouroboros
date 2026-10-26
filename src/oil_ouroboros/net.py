"""
Общие HTTP-клиенты проекта, чтобы прокси и сертификаты были одинаковы (свои клиенты — только у шлюза, вызова LLM и
Qdrant, со своими адресами):

- outbound(): внешний интернет (сайты агентств, веб-поиск, цены EIA, котировки Мосбиржи iss.moex.com, каталог моделей
  OpenRouter) — через OUTBOUND_PROXY, если он задан, и с корневыми сертификатами CA_BUNDLE, если заданы;
- internal(): сервисы проекта во внутренней сети (tools, gateway, encoder, Ouroboros) — без прокси из окружения;
- tools_api() / gateway_api(): служебное API сервиса инструментов и журнал LLM-шлюза с токенами консоли.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import get_settings


def outbound(timeout: float = 30.0, **kw: Any) -> httpx.Client:
    s = get_settings()
    return httpx.Client(timeout=timeout, proxy=s.outbound_proxy or None, verify=s.ca_bundle or True, **kw)


def internal(timeout: float = 15.0, **kw: Any) -> httpx.Client:
    return httpx.Client(timeout=timeout, trust_env=False, **kw)


def _json(client: httpx.Client, url: str, params: dict | None, body: dict | None) -> Any:
    r = client.post(url, json=body) if body is not None else client.get(url, params=params)
    r.raise_for_status()
    return r.json()


def tools_api(path: str, params: dict | None = None, body: dict | None = None, timeout: float = 60.0) -> Any:
    """GET (или POST с body) служебного API сервиса инструментов: /api/runs/…, /api/finalize, /api/sources, …"""
    s = get_settings()
    headers = {"Authorization": f"Bearer {s.tools_api_token}"} if s.tools_api_token else {}
    with internal(timeout, headers=headers) as c:
        return _json(c, s.tools_url.rstrip("/") + path, params, body)


def gateway_api(path: str, params: dict | None = None, timeout: float = 15.0) -> Any:
    """Журнал LLM-шлюза (токен консоли): /health, /ledger, /calls/{id}."""
    s = get_settings()
    with internal(timeout, headers={"Authorization": f"Bearer {s.gateway_token_console}"}) as c:
        return _json(c, s.gateway_url.rstrip("/") + path, params, None)

"""
Слой LLM субагентов: модели по ролям + единая точка вызова `call()` с учётом токенов/стоимости и трейсом.

Роль одна — subagent: план поиска, отбор фрагментов и оценка достаточности. Модель — по LLM-профилю: openrouter —
дешёвая модель каталога (config/current/llm.yaml), gigachat — LLM_MODEL_SUBAGENT (слой env, GigaChat-3-Ultra).
В Docker все вызовы идут через LLM-шлюз (gateway): провайдер openai_compatible, токен «tools».
К каждому запросу добавляются заголовки X-Oil-Run (прогон инструмента) и X-Oil-Purpose (назначение вызова):
по ним журнал шлюза связывает деньги с трейсом инструмента.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from ..config import get_settings
from . import pricing

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------------------------------------------
# контекст прогона (заполняет агент; читают call() и инструменты)
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class RunContext:
    run_id: str
    sink: Callable[[dict], None] | None = None  # приёмник событий трейса
    node: str = ""
    purpose: str = ""  # назначение текущего вызова LLM (заголовок X-Oil-Purpose)
    counters: dict[str, int] = field(default_factory=dict)
    usage: dict[str, float] = field(
        default_factory=lambda: {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "calls": 0}
    )
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, event: dict) -> None:
        if self.sink is not None:
            self.sink({"ts": time.time(), "node": self.node, **event})


_ctx: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar("oil_run", default=None)


def current() -> RunContext | None:
    return _ctx.get()


def bind(ctx: RunContext):
    return _ctx.set(ctx)


def reset(token) -> None:
    _ctx.reset(token)


# ---------------------------------------------------------------------------------------------------------------
# модели
# ---------------------------------------------------------------------------------------------------------------

_models: dict[str, Any] = {}
_models_lock = threading.Lock()


def _catalog():
    from .. import catalog

    return catalog.get()


def model_name(role: str) -> str:
    return getattr(_catalog().llm, role).model


def _tag_request(request) -> None:
    """httpx-хук: прогон и назначение вызова — в заголовки (журнал шлюза связывает деньги с трейсом)."""
    ctx = current()
    if ctx:
        request.headers["X-Oil-Run"] = ctx.run_id
        if ctx.purpose:
            request.headers["X-Oil-Purpose"] = ctx.purpose[:60]


async def _tag_request_async(request) -> None:
    _tag_request(request)


def chat_model(role: str):
    s = get_settings()
    name = model_name(role)
    r = getattr(_catalog().llm, role)
    key = f"{s.llm_provider}|{name}|{r.temperature}|{r.timeout_s}|{r.retries}|{r.reasoning_effort}"
    with _models_lock:
        if key in _models:
            return _models[key]
        if s.llm_provider in ("openrouter", "openai_compatible"):
            import httpx
            from langchain_openai import ChatOpenAI

            kw: dict = {"timeout": r.timeout_s}
            is_or = s.llm_provider == "openrouter"
            if s.outbound_proxy and is_or:  # шлюз — во внутренней сети: прокси нужен только прямому OpenRouter
                kw["proxy"] = s.outbound_proxy
            if s.ca_bundle:
                kw["verify"] = s.ca_bundle
            m = ChatOpenAI(
                model=name,
                base_url=s.openrouter_base_url if is_or else s.openai_compat_base_url,
                api_key=(s.openrouter_api_key if is_or else s.openai_compat_api_key) or "none",
                temperature=r.temperature,
                max_retries=r.retries,
                timeout=r.timeout_s,
                http_client=httpx.Client(**kw, event_hooks={"request": [_tag_request]}),
                http_async_client=httpx.AsyncClient(**kw, event_hooks={"request": [_tag_request_async]}),
                default_headers={
                    "HTTP-Referer": "https://github.com/maxdonit/oil_ouroboros",
                    "X-Title": "oil-ouroboros",
                }
                if is_or
                else None,
                # единый параметр рассуждений OpenRouter (профиль openrouter): у рассуждающих моделей (gpt-6-luna)
                # время вызова — в основном рассуждение, а JSON-узлам субагентов его глубина почти не нужна. На
                # профиле gigachat поле уходит через шлюз в gpt2giga, а рассуждения там выключены
                # (GPT2GIGA_DISABLE_REASONING)
                extra_body={"reasoning": {"effort": r.reasoning_effort}} if r.reasoning_effort else None,
            )
        elif s.llm_provider == "fake":
            from .fake import ScriptedChat

            m = ScriptedChat()
        else:
            raise ValueError(f"неизвестный LLM_PROVIDER={s.llm_provider}")
        _models[key] = m
        return m


# ---------------------------------------------------------------------------------------------------------------
# вызов
# ---------------------------------------------------------------------------------------------------------------


_MARKER = re.compile(r"^\[\[[a-z_]+\]\]\s*")


def _strip_markers(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Маркеры [[research_plan]]/[[select_grade]] в начале промптов — для тестовой модели (llm/fake.py)."""
    out = []
    for m in messages:
        if isinstance(m.content, str) and m.content.startswith("[["):
            m = m.model_copy(update={"content": _MARKER.sub("", m.content, count=1)})
        out.append(m)
    return out


def call(role: str, messages: list[BaseMessage], purpose: str = "") -> AIMessage:
    """Единственная точка вызова LLM в субагентах: учёт денег и токенов, событие трейса."""
    ctx = current()
    node = ctx.node if ctx else "?"
    idx = 0
    if ctx:
        with ctx.lock:
            idx = ctx.counters.get(node, 0)
            ctx.counters[node] = idx + 1
    key = f"{node}#{idx}"
    name = model_name(role)
    t0 = time.monotonic()
    model = chat_model(role)
    if ctx:
        ctx.purpose = purpose
    try:
        msgs = messages if get_settings().llm_provider == "fake" else _strip_markers(messages)
        msg, hedged = _invoke(model, msgs, getattr(_catalog().llm, role).hedge_after_s)
    finally:
        if ctx:
            ctx.purpose = ""
    ms = int((time.monotonic() - t0) * 1000)
    usage = getattr(msg, "usage_metadata", None) or {}
    tin, tout = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    token_usage = (getattr(msg, "response_metadata", None) or {}).get("token_usage") or {}
    # шлюз просит у OpenRouter usage.cost — это фактическая цена; иначе — по прайсу моделей
    cost = float(token_usage.get("cost") or 0.0) or pricing.cost(name, tin, tout)
    if ctx:
        with ctx.lock:
            ctx.usage["tokens_in"] += tin
            ctx.usage["tokens_out"] += tout
            ctx.usage["cost_usd"] += cost
            ctx.usage["calls"] += 1
        ctx.emit(
            {
                "type": "llm_call",
                "key": key,
                "role": role,
                "model": name,
                "purpose": purpose,
                "ms": ms,
                "tokens_in": tin,
                "tokens_out": tout,
                "cost_usd": round(cost, 6),
                "hedged": hedged,
                "request": [_short(m) for m in messages],
                "response": _short(msg),
                "tool_calls": [
                    {"name": tc["name"], "args": tc["args"]} for tc in getattr(msg, "tool_calls", []) or []
                ],
            }
        )
    return msg  # type: ignore[return-value]


def _invoke(model, messages: list[BaseMessage], hedge_after_s: float) -> tuple[AIMessage, bool]:
    """Вызов с дублирующим запросом (hedged request): нет ответа за hedge_after_s — второй такой же запрос, берётся
    первый ответ. У провайдера бывают всплески задержки (вызов в 4–6 раз дольше обычного) — дубль срезает хвост за
    цену короткого вызова. Второй запрос получает свою копию контекста (метки прогона для журнала шлюза)."""
    if not hedge_after_s:
        return model.invoke(messages), False
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-hedge")
    try:
        first = pool.submit(contextvars.copy_context().run, model.invoke, messages)
        done, _ = wait([first], timeout=hedge_after_s)
        if done:
            return first.result(), False
        second = pool.submit(contextvars.copy_context().run, model.invoke, messages)
        done, _ = wait([first, second], return_when=FIRST_COMPLETED)
        winner = next(iter(done))
        if winner.exception() is None:
            return winner.result(), True
        return (second if winner is first else first).result(), True
    finally:
        pool.shutdown(wait=False)  # проигравший запрос дорабатывает в фоне; его цена — в журнале шлюза


def _short(m: BaseMessage, limit: int = 12000) -> dict:
    content = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
    return {"role": m.type, "content": content[:limit] + ("…" if len(content) > limit else "")}


# ---------------------------------------------------------------------------------------------------------------
# структурированный вывод: «JSON как текст» + валидация + одна попытка ремонта (работает с любой моделью)
# ---------------------------------------------------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


def call_json(role: str, messages: list[BaseMessage], schema, purpose: str = ""):
    """Вызов, ожидающий JSON по pydantic-схеме; при ошибке — одна попытка ремонта с текстом ошибки."""
    from langchain_core.messages import HumanMessage

    msg = call(role, messages, purpose=purpose)
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    try:
        return schema.model_validate(extract_json(text))
    except Exception as exc:  # noqa: BLE001
        repair = messages + [
            msg,
            HumanMessage(content=f"Ответ не прошёл проверку схемы: {exc}. Верни ТОЛЬКО корректный JSON по схеме."),
        ]
        msg2 = call(role, repair, purpose=purpose + ":repair")
        text2 = msg2.content if isinstance(msg2.content, str) else str(msg2.content)
        return schema.model_validate(extract_json(text2))

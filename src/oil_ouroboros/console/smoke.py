"""
Дымовой тест живого стека (oilo smoke): один короткий ход через Ouroboros и проверка того, что видела модель.

Что проверяется (по телам запросов в журнале LLM-шлюза, по метке хода):
- агент ответил, ход завершился, деньги учтены (cost > 0; у бесплатного GigaChat — токены);
- в запросе к модели ровно наши инструменты (MCP-инструменты mcp/server.TOOLS + finish_task, knowledge_read,
  knowledge_list; набор MCP ниже сверяет с TOOLS тест):
  статический список отключаемых встроенных инструментов не устарел;
- регламент аналитика (knowledge/overview.md) и identity в контексте;
- язык ответа — язык вопроса; раунды, кэш, стоимость.
Стоимость — около $0.01 на профиле openrouter, на GigaChat Freemium — бесплатно (токены).
"""

from __future__ import annotations

import json
import time

from .. import agent, net
from . import turns

EXPECTED = {"finish_task", "knowledge_read", "knowledge_list"}
MCP = {
    "research",
    "search_reports",
    "search_web",
    "forecast_price",
    "supply_shock_scenario",
    "price_history",
    "price_quote",
    "verify_answer",
    "list_reports",
}


def run(
    question: str = "Кратко: с какими вопросами ты можешь помочь? Ответь в двух предложениях без инструментов.",
) -> dict:
    out: dict = {"ok": True, "checks": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        out["checks"].append({"check": name, "ok": ok, "detail": detail})
        out["ok"] &= ok

    c = agent.Ouroboros()
    check("ouroboros_health", bool(c.health()), "")
    t0 = time.time()
    raw = agent.ask(question, history=[], conversation_id="smoke")
    turn = turns.assemble(raw)
    turns.save(turn, source="smoke", started=t0)
    out["turn_id"], out["task_id"], out["status"] = turn["turn_id"], turn["task_id"], turn["status"]
    check("answered", bool(turn.get("answer")), (turn.get("answer") or "")[:300])
    calls = net.gateway_api("/ledger", {"turn": turn["turn_id"], "limit": 100}).get("calls", [])
    check("gateway_sees_turn", bool(calls), f"вызовов LLM с меткой хода: {len(calls)}")
    cost = sum(float(x.get("cost") or 0) for x in calls)
    paid = [x for x in calls if not str(x.get("model") or "").startswith("GigaChat")]  # GigaChat Freemium — бесплатно
    tokens = sum(int(x.get("tokens_in") or 0) for x in calls)
    check(
        "cost_known",
        cost > 0 if paid else tokens > 0,
        f"${cost:.5f} по журналу шлюза; Ouroboros: ${turn.get('cost_agent_usd')}"
        if paid
        else f"бесплатная модель, {tokens} токенов",
    )
    out["llm_calls"] = [
        {k: x.get(k) for k in ("model", "tokens_in", "tokens_cached", "tokens_out", "cost", "ms", "tools", "status")}
        for x in calls
    ]
    main = [x for x in calls if (x.get("tools") or 0) > 0]
    if main:
        body = net.gateway_api(f"/calls/{main[0]['id']}")
        req = body.get("request") or {}
        names = sorted((t.get("function") or {}).get("name", t.get("name", "")) for t in req.get("tools") or [])
        mcp = {n.removeprefix("mcp_oil__") for n in names if n.startswith("mcp_oil__")}
        builtins = {n for n in names if not n.startswith("mcp_oil__")}
        check("tools_mcp", mcp == MCP, f"MCP: {sorted(mcp)}")
        check(
            "tools_builtin",
            builtins <= EXPECTED | {"enable_tools", "list_available_tools"},
            f"встроенные: {sorted(builtins)}",
        )
        text = json.dumps(req.get("messages") or [], ensure_ascii=False)
        check(
            "playbook_in_context",
            "Регламент: старший аналитик" in text,
            "knowledge/overview.md в «Shared understanding»",
        )
        check(
            "identity_in_context",
            "моя работа — анализ нефтегазового рынка" in text,
            "identity.md",
        )
        out["context_chars"] = len(text)
        out["tools_in_request"] = names
    from ..agent import language

    check("language", language(turn.get("answer") or "") == language(question), "")
    out["rounds"], out["ms"], out["cost_usd"] = turn.get("rounds"), turn.get("ms"), cost
    return out

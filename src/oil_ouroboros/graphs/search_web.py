"""
Инструмент search_web — граф LangGraph: точечный веб-поиск свежих данных после research.

    check_pack → search → pack

- check_pack: правило «сначала отчёты» — нужен свежий пакет research или search_reports этого хода;
- search: поиск по уровням доверия к доменам, жёлтая пресса отсекается (web/search.py, web/credibility.py);
- pack: доказательства W# в журнал, пакет P#, текст для агента.
"""

from __future__ import annotations

import functools
import time
from typing import TypedDict

from .. import catalog
from ..evidence import ledger
from ..web import search as web
from ..web.credibility import TIER_TITLES
from . import kit
from .kit import ToolError, decision


class WebState(TypedDict, total=False):
    run_id: str
    queries: list[str]
    freshness: str
    pack_ref: str
    reason: str
    web: dict
    docs: list[dict]
    pack_id: str
    result: str
    error: str
    path: list[str]


def web_check_pack(state: WebState) -> dict:
    """Код: веб только после отчётов — нужен пакет research или search_reports этого хода не старше 20 минут. Иначе
    ошибка с подсказкой агенту, что сделать (ToolError)."""
    p = ledger.pack(state.get("pack_ref", ""))
    if not p or p.get("tool") not in ("research", "search_reports"):
        raise ToolError(
            "search_web: сначала research (поиск по отчётам), затем веб — передай pack_id из его результата"
        )
    if time.time() - float(p.get("created") or 0) > ledger.PACK_MAX_AGE_S:
        raise ToolError(f"search_web: пакет {p['id']} устарел (старше 20 мин) — сначала research по текущему вопросу")
    return {}


def web_search(state: WebState) -> dict:
    """Код: провайдер (Tavily → Brave → DuckDuckGo), уровни доверия к доменам (web/credibility.py), жёлтая пресса и
    deny-список отсекаются, отсеянное сохраняется с причиной для трейса."""
    fresh = state.get("freshness") or "month"
    res = web.search(state["queries"], freshness=None if fresh == "any" else fresh)
    return {"web": res.summary(fresh), "docs": [x.as_dict() for x in res.docs]}


def web_pack(state: WebState) -> dict:
    """Код: найденное — в журнал как W# с уровнем доверия, решение фильтра — в пакет P#, агенту — текст с метками и
    напоминанием: веб — дополнение к отчётам."""
    docs = []
    for d in state.get("docs") or []:
        eid = ledger.put_web(d)
        docs.append({**d, "id": eid})
    w = state["web"]
    d = decision(
        "search_web",
        "web_source_filter",
        ", ".join(x["id"] for x in docs) or "—",
        [f"provider={w['provider']}", f"freshness={w['freshness']}", f"after={state['pack_ref']}"],
        evidence_ids=[x["id"] for x in docs],
        rationale=state.get("reason", ""),
        policy=f"source_tiers {w.get('policy_version')}",
        options=[{"option": x["url"], "tier": x["tier"], "reason": x["reason"], "kept": False} for x in w["rejected"]],
    )
    pid = ledger.new_pack(
        "search_web",
        [x["id"] for x in docs],
        run_id=state.get("run_id", ""),
        question="; ".join(state["queries"]),
        decisions=[d],
        catalog=catalog.get().ref,
        after=state["pack_ref"],
    )
    out = [
        f"Пакет {pid} · trace {state.get('run_id', '')} · после {state['pack_ref']} · "
        f"провайдер {w['provider']}, свежесть {w['freshness']}" + (f" · сбой: {w['error']}" if w.get("error") else "")
    ]
    for x in docs:
        out.append(f"[{x['id']}] {x['label']} — {TIER_TITLES.get(x.get('tier'), '')}\n{x['title']}. {x['content']}")
    if not docs:
        out.append("— подходящих источников нет —")
    if w["rejected"]:
        out.append(f"Отсеяно фильтром источников: {len(w['rejected'])} (жёлтая пресса, deny-список, лимит на домен).")
    out.append("Веб — дополнение к отчётам: в ответе явно отделяй свежие веб-данные от данных отчётов.")
    return {"docs": docs, "pack_id": pid, "result": "\n\n".join(out)}


NODES = [
    kit.Node("check_pack", web_check_pack, "Правило «сначала отчёты»: нужен пакет research"),
    kit.Node(
        "search",
        web_search,
        "Поиск по уровням доверия, фильтр жёлтой прессы",
        "web",
        ("web.max_results", "research.max_web_queries"),
    ),
    kit.Node("pack", web_pack, "Журнал W#, пакет, текст для агента"),
]
EDGES = [("check_pack", "search"), ("search", "pack")]


@functools.cache
def graph():
    return kit.build(WebState, NODES, EDGES)


def run_web(queries: list[str], pack_id: str, freshness: str = "month", reason: str = "") -> kit.ToolRun:
    args = {"queries": queries, "pack_id": pack_id, "freshness": freshness, "reason": reason}
    return kit.run_graph(
        "search_web",
        graph(),
        {"queries": queries, "pack_ref": pack_id, "freshness": freshness, "reason": reason},
        args,
        question="; ".join(queries),
    )

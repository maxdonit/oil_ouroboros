"""
Общая обвязка графов инструментов: сборка графа LangGraph из списка узлов, трейс каждого узла, запуск инструмента
как прогона (run) с журналом событий, запись решений.

Каждый MCP-вызов — отдельный прогон в var/runs.sqlite (graphs/runs.py, source = «mcp:<инструмент>»). В нём:
- события узлов (node_start / node_end с длительностью и итогом);
- вызовы LLM субагентов с промптами, токенами и стоимостью;
- итоговое состояние графа.
Номер прогона (trace_id) возвращается агенту в результате инструмента. Так мониторинг связывает раунд Ouroboros
с тем, что инструмент сделал внутри.
"""

from __future__ import annotations

import inspect
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from .. import catalog
from ..llm import core as llm
from . import runs


class ToolError(Exception):
    """Ошибка, которую агент исправит сам (не тот порядок вызовов, неверный аргумент): уходит ему результатом с
    isError и текстом «что сделать» (mcp/server.py)."""


KINDS = {"llm": "LLM", "code": "код", "db": "векторная база", "web": "веб", "calc": "расчёт"}


@dataclass(frozen=True)
class Node:
    id: str
    fn: Callable[[dict], dict]  # её docstring — «как и почему» узла на карте консоли
    title: str  # что делает узел — для карты и трейса
    kind: str = "code"  # один из KINDS: чем работает узел
    params: tuple[str, ...] = ()  # ключи каталога config/, от которых зависит узел (карта показывает значения)


def instrument(node: Node, summarize: Callable[[str, dict], dict] | None = None) -> Callable[[dict], dict]:
    """Обёртка узла: текущий узел в контексте прогона, события node_start/node_end с длительностью и итогом."""

    def run(state: dict) -> dict:
        ctx = llm.current()
        prev = ctx.node if ctx else ""
        if ctx:
            ctx.node = node.id
            ctx.emit({"type": "node_start", "title": node.title})
        t0 = time.perf_counter()
        try:
            out = node.fn(state)
        except Exception as exc:
            if ctx:
                ctx.emit({"type": "node_error", "error": f"{type(exc).__name__}: {exc}"[:500]})
                ctx.node = prev
            raise
        out = dict(out or {})
        out["path"] = list(state.get("path", [])) + [node.id]
        if ctx:
            summary = summarize(node.id, out) if summarize else {}
            ctx.emit({"type": "node_end", "ms": round((time.perf_counter() - t0) * 1000, 1), "summary": summary})
            ctx.node = prev
        return out

    run.__name__ = node.id
    return run


def build(
    state_type: type,
    nodes: list[Node],
    edges: list[tuple[str, str]],
    routers: dict[str, Callable[[dict], str]] | None = None,
    summarize: Callable[[str, dict], dict] | None = None,
):
    """Граф: узлы по порядку, рёбра (src → dst); у узла с несколькими исходящими — маршрутизатор."""
    routers = routers or {}
    g = StateGraph(state_type)
    for n in nodes:
        g.add_node(n.id, instrument(n, summarize))
    g.add_edge(START, nodes[0].id)
    by_src: dict[str, list[str]] = {}
    for src, dst in edges:
        by_src.setdefault(src, []).append(dst)
    for src, dsts in by_src.items():
        if src in routers:
            g.add_conditional_edges(src, routers[src], {d: (END if d == "END" else d) for d in dsts})
        else:
            if len(dsts) != 1:
                raise ValueError(f"граф: у {src} несколько исходящих рёбер без маршрутизатора")
            g.add_edge(src, END if dsts[0] == "END" else dsts[0])
    terminals = {n.id for n in nodes} - set(by_src)
    for t in terminals:
        g.add_edge(t, END)
    return g.compile()


def _param(path: str) -> object:
    """Текущее значение ключа каталога «раздел.ключ.…» (модели pydantic и словари)."""
    obj: object = catalog.get()
    for part in path.split("."):
        obj = obj.get(part) if isinstance(obj, dict) else getattr(obj, part)
    return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj


def describe(
    nodes: list[Node],
    edges: list[tuple[str, str]],
    routers: dict[str, Callable[[dict], str]] | None = None,
    labels: dict[tuple[str, str], str] | None = None,
) -> dict:
    """Граф для карты консоли: узлы (вид, как и почему — docstring функции, параметры каталога с текущими
    значениями) и рёбра (основной путь или ветвление по решению маршрутизатора, с подписью)."""
    routers, labels = routers or {}, labels or {}

    def node(n: Node) -> dict:
        doc = inspect.getdoc(n.fn) or ""
        return {
            "id": n.id,
            "title": n.title,
            "kind": n.kind,
            "kind_title": KINDS.get(n.kind, n.kind),
            "how": doc,
            "code": f"{n.fn.__module__.removeprefix('oil_ouroboros.')}.{n.fn.__name__}",
            "params": [{"key": p, "value": _param(p)} for p in n.params],
        }

    return {
        "nodes": [node(n) for n in nodes],
        "edges": [
            {"from": a, "to": b, "kind": "branch" if a in routers else "main", "label": labels.get((a, b), "")}
            for a, b in edges
        ],
    }


@dataclass
class ToolRun:
    run_id: str
    state: dict
    usage: dict
    ms: int
    error: str = ""


def run_graph(tool: str, graph, inputs: dict, args: dict[str, Any], question: str = "") -> ToolRun:
    """Прогон графа инструмента с трейсом: события и итог — в журнал прогонов (graphs/runs.py)."""
    run_id = uuid.uuid4().hex[:12]
    events: list[dict] = []

    def collect(ev: dict) -> None:
        events.append({"run_id": run_id, "ts": time.time(), "node": "", **ev})

    ctx = llm.RunContext(run_id=run_id, sink=collect)
    token = llm.bind(ctx)
    shown = json.dumps(args, ensure_ascii=False, default=str)
    runs.start_run(run_id, tool, question or shown[:500], args, source=f"mcp:{tool}")
    collect({"type": "run_start", "tool": tool, "args": args})
    t0 = time.monotonic()
    final: dict = {}
    error = ""
    try:
        final = graph.invoke({**inputs, "run_id": run_id, "path": []}, {"recursion_limit": 40})
    except Exception as exc:  # noqa: BLE001 — ошибка инструмента уходит агенту как isError и остаётся в трейсе
        # ToolError — исправимая агентом ошибка: только текст «что сделать»; иначе — с классом исключения
        error = str(exc) if isinstance(exc, ToolError) else f"{type(exc).__name__}: {exc}"
        collect({"type": "run_error", "error": error[:1000]})
    finally:
        llm.reset(token)
    ms = int((time.monotonic() - t0) * 1000)
    collect({"type": "run_end", "ms": ms, "usage": ctx.usage})
    state = {k: v for k, v in (final or {}).items() if k != "run_id"}
    runs.finish_run(run_id, str(state.get("result", ""))[:20000], state, events, dict(ctx.usage), ms, error)
    return ToolRun(run_id, state, dict(ctx.usage), ms, error)


def decision(
    node: str,
    dtype: str,
    chosen: str,
    reason_codes: list[str],
    options: list | None = None,
    evidence_ids: list | None = None,
    rationale: str = "",
    policy: str = "",
    confidence_method: str = "",
) -> dict:
    """Запись решения. Авторитетны reason_codes и evidence_ids; rationale модели хранится,
    но доказательством не считается."""
    return {
        "ts": time.time(),
        "node": node,
        "type": dtype,
        "chosen": chosen,
        "reason_codes": reason_codes,
        "options": options or [],
        "evidence_ids": evidence_ids or [],
        "rationale": rationale,
        "policy": policy,
        "catalog": catalog.get().ref,
        "confidence_method": confidence_method,
    }

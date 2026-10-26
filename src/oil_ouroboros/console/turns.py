"""
Ход агента — единица мониторинга: вопрос → раунды Ouroboros → вызовы инструментов → графы LangGraph → проверка →
ответ, со стоимостью и временем по слоям и отпечатком версии агента. Ходы хранятся в var/turns.sqlite консоли.

Откуда что берётся:
- Ouroboros: поток событий задачи (agent.py) — итог, раунды, токены, стоимость, вызовы инструментов (первая строка
  результата нашего инструмента несёт trace);
- сервис инструментов: прогон каждого вызова по trace (/api/runs/{trace}) — узлы графа, LLM субагентов, решения;
  финал хода (/api/finalize) — какой текст увидит пользователь, с проверкой и метками; журнал доказательств
  (/api/evidence).

Онлайн-проверки хода не требуют эталона: проверка ответа вызвана и пройдена, пользователь получил проверенный
текст, первый инструмент — research или расчёт/справка (list_reports, forecast_price, supply_shock_scenario,
price_history, price_quote), метки источников, ошибки инструментов. Это метрики мониторинга; эталонные
факты проверяет eval (evals/checks.py).
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Iterator

from .. import net
from ..agent import REFUSAL
from ..config import get_settings
from ..evals.checks import CALC_TOOLS
from ..evidence.text import CITE, LABEL, cited_ids, normalize_ids, strip_own_sources

log = logging.getLogger(__name__)

SCHEMA = """
create table if not exists turns (
  turn_id text primary key, conversation_id text, started real, finished real, ms integer, status text,
  question text, answer text, task_id text, source text, fingerprint text, agent_version text,
  cost_usd real, cost_agent_usd real, cost_tools_usd real, rounds integer, tokens_in integer, tokens_cached integer,
  tools text, checks text, data text
);
create index if not exists turns_started on turns(started);
create index if not exists turns_conv on turns(conversation_id, started);
"""
_lock = threading.Lock()


@contextlib.contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Соединение на один блок: транзакция фиксируется (или откатывается при ошибке), соединение закрывается."""
    p = get_settings().var_dir / "turns.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), timeout=30, check_same_thread=False)
    try:
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        with c:
            yield c
    finally:
        c.close()


def _run_summary(trace: str) -> dict:
    """Прогон инструмента: путь по узлам, деньги субагентов, выданные доказательства, ключевые выводы узлов."""
    try:
        run = net.tools_api(f"/api/runs/{trace}")
    except Exception as exc:  # noqa: BLE001 — трейса может не быть (инструмент упал до записи)
        return {"trace": trace, "error": str(exc)[:200]}
    ev = run.get("events") or []
    nodes = [
        {"node": e["node"], "ms": e.get("ms"), "summary": e.get("summary")} for e in ev if e.get("type") == "node_end"
    ]
    llm = [
        {
            "purpose": e.get("purpose"),
            "model": e.get("model"),
            "ms": e.get("ms"),
            "tokens_in": e.get("tokens_in"),
            "tokens_out": e.get("tokens_out"),
            "cost_usd": e.get("cost_usd"),
        }
        for e in ev
        if e.get("type") == "llm_call"
    ]
    st = run.get("state") or {}
    ev_ids = [
        x.get("id")
        for x in (st.get("reports") or []) + (st.get("web_docs") or []) + (st.get("docs") or [])
        if isinstance(x, dict)
    ]
    if st.get("eid"):
        ev_ids.append(st["eid"])
    out = {
        "trace": trace,
        "evidence_ids": [i for i in ev_ids if i],
        "source": run.get("source"),
        "ms": run.get("ms"),
        "status": run.get("status"),
        "error": run.get("error"),
        "path": [n["node"] for n in nodes],
        "nodes": nodes,
        "llm": llm,
        "cost_usd": (run.get("usage") or {}).get("cost_usd", 0.0),
        "pack_id": st.get("pack_id"),
        "decisions": st.get("decisions", []),
    }
    if st.get("verification"):
        out["verification"] = {k: v for k, v in st["verification"].items() if k != "claims"}
        out["draft"] = st.get("answer", "")  # черновик, который агент отдал на проверку (с id)
        out["shown"] = st.get("final_markdown", "")  # его текст для показа (метки вместо id)
    if st.get("web") is not None:
        out["web"] = {k: (st["web"] or {}).get(k) for k in ("provider", "freshness", "error")}
        out["web_reasons"] = st.get("web_reasons", [])
    if st.get("grade"):
        out["grade"] = st["grade"]
    return out


# «внутренняя кухня» в ответе пользователю: имя агента, его инструменты, номера пакетов доказательств
PERSONA_LEAK = re.compile(
    r"\bOuroboros\b|\bУробор|verify_answer|finish_task|mcp_oil|\b(?:пакет\w*|package) P\d+", re.I
)
CALC = tuple(sorted(CALC_TOOLS))  # расчётные инструменты: первым вызовом засчитываются наравне с research


def _finalize(raw: dict, answer: str, traces: list[str], history_ids: list[str], refused: bool) -> dict:
    """Что увидит пользователь — решает сервис инструментов (/api/finalize): там доказательства хода и проверка."""
    plain = {
        "text": answer,
        "shown": answer,
        "verification": {},
        "trace": "",
        "rejected": {},
        "allowed": sorted(set(history_ids)),
    }
    if refused:  # отказ без инструментов не проверяется: в нём нет ни цифр, ни ссылок
        return plain | {"source": "refusal"}
    try:
        return net.tools_api(
            "/api/finalize",
            body={"answer": answer, "question": raw.get("question", ""), "traces": traces, "history_ids": history_ids},
        )
    except Exception as exc:  # noqa: BLE001 — без финала показываем текст агента с пометкой «не проверен»
        log.warning("ход %s: финал хода недоступен: %s", raw.get("turn_id"), exc)
        return plain | {"source": "unverified", "verification": {"ok": False, "error": str(exc)[:200]}}


def assemble(raw: dict, fingerprint: dict | None = None, history_ids: list[str] | None = None) -> dict:
    """Сырые журналы хода (agent.ask) → ход для консоли, мониторинга и eval: что увидел пользователь (финал хода —
    в сервисе инструментов, graphs/verify_answer.finalize), вызовы инструментов с их прогонами, деньги, время,
    онлайн-проверки.

    answer_source: verified_draft (ответ = проверенный черновик) · reverified_final (агент изменил текст — проверен
    заново) · fallback_verified_draft (агент не завершил или правка не прошла — показан проверенный черновик) ·
    refusal · unverified."""
    calls = raw.get("tool_calls") or []
    for c in calls:
        c["run"] = _run_summary(c["trace"]) if c.get("trace") else {}
    answer_raw = normalize_ids(raw.get("answer") or "")  # [Р12] → [R12]
    names = [c["tool"] for c in calls]
    mcp_names = [c["tool"] for c in calls if c.get("mcp")]
    verifies = [
        c["run"] for c in calls if c.get("tool") == "verify_answer" and (c.get("run") or {}).get("verification")
    ]
    last_v = verifies[-1] if verifies else {}
    traces = [c["trace"] for c in calls if c.get("trace")]
    refused = not mcp_names and not CITE.search(answer_raw) and bool(REFUSAL.search(answer_raw))
    fin = _finalize(raw, answer_raw, traces, list(history_ids or []), refused)
    text, shown, final_v, source, rejected = (
        fin["text"],
        fin["shown"],
        fin["verification"] or {},
        fin["source"],
        fin["rejected"],
    )
    final_trace, allowed = fin.get("trace", ""), fin.get("allowed") or []
    # ссылки ответа — без своего списка источников агента: его проверка отбрасывает, пользователь его не видит
    cited = sorted(cited_ids(strip_own_sources(text)), key=lambda x: (x[0], int(x[1:])))
    evidence = {}
    if cited:
        try:
            evidence = net.tools_api("/api/evidence", {"ids": ",".join(cited)})
        except Exception:  # noqa: BLE001
            evidence = {}
    agent_cited = set(cited_ids(strip_own_sources(answer_raw)))
    foreign = sorted((set(cited) | agent_cited) - set(allowed))  # и в тексте агента, даже если показан черновик
    went_web = any(c.get("tool") == "search_web" and c.get("ok") for c in calls) or any(
        (c.get("run") or {}).get("web") is not None and c.get("tool") == "research" for c in calls
    )
    rounds = raw.get("llm_rounds") or []
    cost_agent = round(sum(float(r.get("cost_usd") or 0) for r in rounds), 6)
    cost_tools = round(sum(float((c.get("run") or {}).get("cost_usd") or 0) for c in calls), 6)
    from ..agent import language, strip_sources

    body_text = LABEL.sub(" ", strip_sources(shown))  # текст ответа: метки и подвал ставит система
    lang_q, lang_a = language(raw.get("question") or ""), language(body_text) if shown else ""
    fv_ok = bool(final_v.get("ok"))
    checks = {
        "answered": bool(shown.strip()) and raw.get("status") in ("completed", "completed_by_console"),
        "finish_held": raw.get("status") == "completed_by_console",
        "refused": refused,
        # первый инструмент — допустимое начало по регламенту: research, list_reports или чистый расчёт
        "research_first": refused or (bool(mcp_names) and mcp_names[0] in ("research", "list_reports", *CALC)),
        "verify_called": refused or bool(verifies),
        "verify_ok": refused or fv_ok,
        "answer_is_checked_text": refused or (source == "verified_draft" and fv_ok),
        "labels": not CITE.search(strip_sources(shown)),
        "citations_in_turn": not foreign,
        "persona_clean": not PERSONA_LEAK.search(body_text),
        "language_ok": not shown or lang_q == lang_a,
        "final_rewrite_rejected": bool(rejected),  # агент изменил проверенный текст, и правка не прошла проверку
        "tool_errors": sum(1 for c in calls if not c.get("ok")),
        "builtin_tools": [n for n in names if not any(n == c["tool"] and c.get("mcp") for c in calls)],
    }
    return {
        **{
            k: raw.get(k)
            for k in ("turn_id", "conversation_id", "task_id", "question", "status", "ms", "task", "description")
        },
        "answer": shown,
        "answer_raw": answer_raw,
        "answer_shown_ids": text,
        "answer_source": source,
        "tool_calls": calls,
        "llm_rounds": rounds,
        "went_web": went_web,
        "verification": final_v,
        "verification_trace": final_trace,
        "rejected_final_verification": rejected,
        "verification_inloop": last_v.get("verification") or {},
        "cited": cited,
        "allowed_ids": allowed,
        "foreign_citations": foreign,
        "evidence": evidence,
        "checks": checks,
        "cost_usd": round(cost_agent + cost_tools, 6),
        "cost_agent_usd": cost_agent,
        "cost_tools_usd": cost_tools,
        "rounds": len(rounds),
        "tokens_in": sum(int(r.get("tokens_in") or 0) for r in rounds),
        "tokens_cached": sum(int(r.get("tokens_cached") or 0) for r in rounds),
        "fingerprint": fingerprint or {},
    }


def save(turn: dict, source: str = "chat", started: float | None = None) -> None:
    fp = turn.get("fingerprint") or {}
    with _lock, _db() as c:
        c.execute(
            "insert or replace into turns values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                turn["turn_id"],
                turn.get("conversation_id", ""),
                started or time.time() - (turn.get("ms") or 0) / 1000,
                time.time(),
                turn.get("ms"),
                turn.get("status"),
                turn.get("question"),
                turn.get("answer"),
                turn.get("task_id"),
                source,
                json.dumps(fp, ensure_ascii=False),
                fp.get("agent_version", ""),
                turn.get("cost_usd"),
                turn.get("cost_agent_usd"),
                turn.get("cost_tools_usd"),
                turn.get("rounds"),
                turn.get("tokens_in"),
                turn.get("tokens_cached"),
                json.dumps([c["tool"] for c in turn.get("tool_calls", [])]),
                json.dumps(turn.get("checks", {}), ensure_ascii=False),
                json.dumps(turn, ensure_ascii=False, default=str),
            ),
        )


def get(turn_id: str) -> dict | None:
    with _db() as c:
        r = c.execute("select data from turns where turn_id = ?", (turn_id,)).fetchone()
    return json.loads(r["data"]) if r else None


def listing(limit: int = 100, source: str = "", conversation: str = "") -> list[dict]:
    q = (
        "select turn_id, conversation_id, started, ms, status, question, source, agent_version, cost_usd, "
        "cost_agent_usd, cost_tools_usd, rounds, tokens_in, tokens_cached, tools, checks from turns"
    )
    where, args = [], []
    if source:
        where.append("source = ?")
        args.append(source)
    if conversation:
        where.append("conversation_id = ?")
        args.append(conversation)
    q += (" where " + " and ".join(where) if where else "") + " order by started desc limit ?"
    with _db() as c:
        rows = [dict(r) for r in c.execute(q, [*args, limit])]
    for r in rows:
        r["tools"] = json.loads(r["tools"] or "[]")
        r["checks"] = json.loads(r["checks"] or "{}")
    return rows


def dialog_pack(ids: list[str], question: str = "") -> str:
    """Пакет диалога в сервисе инструментов: доказательства прошлых реплик, на которые агент может сослаться снова.
    '' — в истории нет доказательств или сервис недоступен (тогда такие ссылки проверка хода сочтёт чужими)."""
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return ""
    try:
        return str(net.tools_api("/api/packs", body={"ids": ids, "question": question}).get("pack_id") or "")
    except Exception as exc:  # noqa: BLE001 — ход идёт и без пакета: проверка просто строже
        log.warning("пакет диалога не создан: %s", exc)
        return ""


def history(conversation_id: str, limit: int = 6) -> list[dict]:
    """Прошлые реплики разговора для контекста следующего хода: вопрос, показанный пользователю текст со ссылками
    [R#]/[W#]/[F#] (по ним агент может сослаться на прежние доказательства без нового поиска) и доказательства хода.
    Отвергнутая проверкой правка агента сюда не попадает: в историю идёт то, что пользователь видел."""
    with _db() as c:
        rows = c.execute(
            "select data from turns where conversation_id = ? and status in ('completed', 'completed_by_console') "
            "order by started desc limit ?",
            (conversation_id, limit),
        ).fetchall()
    out = []
    for r in reversed(rows):
        d = json.loads(r["data"])
        out.append(
            {
                "q": d.get("question", ""),
                "a": d.get("answer_shown_ids") or d.get("answer_raw") or d.get("answer") or "",
                "ids": d.get("allowed_ids", []),
            }
        )
    return out


def metrics(since: float = 0.0, source: str = "chat") -> dict:
    """Сводка мониторинга по ходам: доли проверок, деньги и время — по версиям агента."""
    rows = [r for r in listing(5000, source) if (r["started"] or 0) >= since]
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["agent_version"] or "—", []).append(r)

    def agg(rs: list[dict]) -> dict:
        n = len(rs)
        if not n:
            return {"turns": 0}
        ms = sorted(r["ms"] or 0 for r in rs)
        share = lambda key: round(sum(1 for r in rs if r["checks"].get(key)) / n, 3)  # noqa: E731
        return {
            "turns": n,
            "answered": share("answered"),
            "verify_called": share("verify_called"),
            "verify_ok": share("verify_ok"),
            "answer_is_checked_text": share("answer_is_checked_text"),
            "research_first": share("research_first"),
            "labels": share("labels"),
            "refused": share("refused"),
            "citations_in_turn": share("citations_in_turn"),
            "persona_clean": share("persona_clean"),
            "language_ok": share("language_ok"),
            "tool_error_turns": round(sum(1 for r in rs if r["checks"].get("tool_errors")) / n, 3),
            "cost_avg_usd": round(sum(r["cost_usd"] or 0 for r in rs) / n, 5),
            "cost_total_usd": round(sum(r["cost_usd"] or 0 for r in rs), 4),
            "cost_agent_share": round(
                sum(r["cost_agent_usd"] or 0 for r in rs) / max(sum(r["cost_usd"] or 0 for r in rs), 1e-9), 3
            ),
            "rounds_avg": round(sum(r["rounds"] or 0 for r in rs) / n, 2),
            "cache_share": round(
                sum(r["tokens_cached"] or 0 for r in rs) / max(sum(r["tokens_in"] or 0 for r in rs), 1), 3
            ),
            "ms_p50": ms[n // 2],
            "ms_p95": ms[min(n - 1, int(n * 0.95))],
        }

    return {"all": agg(rows), "by_version": {k: agg(v) for k, v in by.items()}}

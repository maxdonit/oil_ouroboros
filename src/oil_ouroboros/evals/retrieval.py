"""
Eval по слоям: поиск по отчётам (retrieve) и отбор фрагментов с оценкой достаточности (select_grade) — без Ouroboros.

Зачем: прогон eval через агента идёт ~20 минут и стоит денег, а его провал не говорит, где потерялся факт: не нашёлся
поиском, не попал в отбор или агент не взял его в ответ. Здесь те же узлы графа research (graphs/research.py)
прогоняются на кейсах eval напрямую: поиск — секунды, отбор — два вызова LLM субагента на кейс.

Что меряется по каждому кейсу с эталонными фактами (категории report, conflict, combined):
- факт в кандидатах — число факта с точностью эталона и одно из его ключевых слов (Fact.near) рядом есть во
  фрагменте ожидаемого агентства среди кандидатов, которых видит отбор (research.select_candidates); ранг первого;
- факт в отборе — то же среди выбранных фрагментов (до retrieval.top_k);
- охват агентств — у каждого агентства из must_cite_orgs есть выбранный фрагмент;
- вердикт достаточности и «пошёл бы в веб» по политике web.yaml: если веб в кейсе не ожидается — это «лишний веб».
Проверка факта — близость числа и слова, а не смысл: у малых чисел (0.2 млн б/с) бывают случайные совпадения, поэтому
ключевые слова у фактов конкретные.

Ничего не пишется в журнал доказательств и не ищется в вебе: узел pack и веб-поиск не вызываются, план строится без
фонового веб-поиска. Пока идёт прогон, данные заморожены (freeze.py, как при обычном eval). Чтобы сравнивать
именно поиск, планы запросов можно взять из прошлого прогона (--plans): тогда LLM зовётся только в отборе.

    oilo eval-retrieval [--only R05 R06] [--plans <прогон>] [--no-select] [--label baseline]
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import catalog, freeze
from ..config import get_settings
from ..evidence.numbers import NUM, decimals, values
from ..llm import core as llm
from . import stats
from .checks import Case, Fact, load_cases

SCHEMA = "oilo.eval_retrieval.v1"
CATEGORIES = ("report", "conflict", "combined")
WINDOW = 160  # символов по обе стороны от числа, где ищется ключевое слово факта

log = logging.getLogger(__name__)


def _ref_decimals(x: float) -> int:
    s = repr(float(x))
    return 0 if "e" in s else len(s.split(".")[1].rstrip("0"))


def fact_in_text(fact: Fact, text: str, window: int = WINDOW) -> bool:
    """Число факта (любое из any_of) с точностью эталона — «1.87» подтверждает 1.9, «3» не подтверждает 2.6 — и, если
    у факта есть ключевые слова, одно из них рядом с числом."""
    for m in NUM.finditer(text):
        tok, vals = m.group(0), values(m.group(0))
        for ref in fact.any_of:
            d = _ref_decimals(ref)
            if decimals(tok) < d or not any(abs(round(abs(v), d) - abs(ref)) < 1e-9 for v in vals):
                continue
            around = text[max(0, m.start() - window) : m.end() + window]
            if not fact.near or any(re.search(n, around, re.I) for n in fact.near):
                return True
    return False


def eligible(cases: list[Case]) -> list[Case]:
    return [c for c in cases if c.category in CATEGORIES and c.expect.facts]


def run_case(case: Case, select: bool = True, plan: dict | None = None) -> dict:
    """Узлы research для одного кейса: plan (или готовый план) → retrieve → select_grade; метрики по фактам."""
    from ..graphs import research

    state: dict = {
        "question": case.question,
        "focus": "",
        "today": time.strftime("%Y-%m-%d"),
        "mode": "reports",
        "given_queries": [],
        "given_orgs": [],
        "given_editions": [],
        "decisions": [],
        "run_id": "",
    }
    ctx = llm.RunContext(run_id=f"evalret-{case.id}-{uuid.uuid4().hex[:6]}")
    token = llm.bind(ctx)
    ms: dict[str, int] = {}
    error = ""
    try:
        for node, fn in (
            ("plan", None if plan else research.plan),
            ("retrieve", research.retrieve),
            ("select_grade", research.select_grade if select else None),
        ):
            t0 = time.monotonic()
            if node == "plan" and plan:
                state["plan"] = plan
            elif fn is not None:
                state.update(fn(state))  # mode=reports: план без фонового веб-поиска
            ms[node] = int((time.monotonic() - t0) * 1000)
    except Exception as exc:  # noqa: BLE001 — сбой кейса не роняет прогон
        error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        llm.reset(token)
    cands = state.get("candidates") or []
    picked = state.get("reports") or []
    orgs = set(case.expect.must_cite_orgs)
    expected = lambda h: not orgs or h["org"] in orgs  # noqa: E731 — факт ищется во фрагментах ожидаемых агентств

    facts = []
    for f in case.expect.facts:
        rank = next((i for i, h in enumerate(cands, 1) if expected(h) and fact_in_text(f, h["text"])), None)
        facts.append(
            {
                "what": f.what or str(f.any_of),
                "candidate_rank": rank,
                "selected": any(expected(h) and fact_in_text(f, h["text"]) for h in picked) if select else None,
            }
        )
    grade = state.get("grade") or {}
    reasons = research.web_reasons({**state, "mode": "research"}) if select and not error else []
    selected_orgs = sorted({h["org"] for h in picked})
    return {
        "case_id": case.id,
        "category": case.category,
        "question": case.question,
        "error": error,
        "plan": state.get("plan") or {},
        "facts": facts,
        "candidates": len(cands),
        "candidate_orgs": dict(Counter(h["org"] for h in cands)),
        "selected_orgs": selected_orgs,
        "orgs_covered": (all(o in selected_orgs for o in orgs) if orgs else None) if select else None,
        "verdict": grade.get("verdict") if select else None,
        "missing": grade.get("missing", []),
        "web_expected": case.expect.route_web,
        "would_web": bool(reasons) if select else None,
        "web_reasons": reasons,
        "selected": [{"label": h.get("label"), "chunk_id": h.get("chunk_id")} for h in picked],
        "ms": ms,
        "cost_usd": round(ctx.usage["cost_usd"], 6),
        "llm_calls": int(ctx.usage["calls"]),
        "retrieval": {
            k: (state.get("retrieval") or {}).get(k) for k in ("embedder", "reranker", "index_size", "timings_ms")
        },
    }


def _share(xs: list[bool]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if not r["error"]]
    facts = [f for r in ok for f in r["facts"]]
    selected = [f["selected"] for f in facts if f["selected"] is not None]
    ranks = [f["candidate_rank"] for f in facts if f["candidate_rank"]]
    with_orgs = [r["orgs_covered"] for r in ok if r["orgs_covered"] is not None]
    graded = [r for r in ok if r["would_web"] is not None]
    return {
        "cases": len(results),
        "errors": len(results) - len(ok),
        "facts": len(facts),
        "fact_in_candidates": _share([f["candidate_rank"] is not None for f in facts]),
        "fact_selected": _share(selected),
        "candidate_rank_p50": stats.percentile(ranks, 50),
        "orgs_covered": _share(with_orgs),
        "verdicts": dict(Counter(r["verdict"] for r in graded)),
        "extra_web": sum(1 for r in graded if r["web_expected"] is False and r["would_web"]),
        "missed_web": sum(1 for r in graded if r["web_expected"] is True and not r["would_web"]),
        "cost_usd": round(sum(r["cost_usd"] for r in results), 5),
        "ms_p50": {
            k: stats.percentile([r["ms"].get(k, 0) for r in ok], 50) for k in ("plan", "retrieve", "select_grade")
        },
    }


def runs_dir() -> Path:
    d = get_settings().var_dir / "evals" / "retrieval"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(ref: str) -> dict:
    """Прогон по id или пути к файлу записи."""
    p = Path(ref)
    p = p if p.is_file() else runs_dir() / f"{ref}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def run(
    cases_path: Path,
    only: list[str] | None = None,
    select: bool = True,
    plans_from: str = "",
    label: str = "",
    workers: int = 3,
) -> dict:
    cases = eligible(load_cases(cases_path))
    if only:
        cases = [c for c in cases if c.id in only or c.category in only]
    plans = {r["case_id"]: r["plan"] for r in load(plans_from)["results"] if r.get("plan")} if plans_from else {}
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    c = catalog.get()
    t0 = time.monotonic()
    with freeze.frozen(run_id, "retrieval"), ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        results = list(ex.map(lambda case: run_case(case, select, plans.get(case.id)), cases))
    retrieval = next((r["retrieval"] for r in results if r["retrieval"].get("embedder")), {})
    record = {
        "schema": SCHEMA,
        "run_id": run_id,
        "label": label,
        "dataset": cases_path.name,
        "select": select,
        "plans_from": plans_from,
        "duration_s": round(time.monotonic() - t0, 1),
        "config": {
            "catalog": c.ref,
            "subagent_model": c.llm.subagent.model,
            "embedder": retrieval.get("embedder"),
            "reranker": retrieval.get("reranker"),
            "index_size": retrieval.get("index_size"),
        },
        "results": results,
    }
    record["summary"] = summarize(results)
    (runs_dir() / f"{run_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def report_text(record: dict) -> str:
    """Таблица прогона для командной строки."""
    s = record["summary"]
    pct = lambda x: "—" if x is None else f"{x:.0%}"  # noqa: E731
    out = [
        f"eval поиска и отбора {record['run_id']} {record.get('label') or ''} · {record['config']['catalog']} · "
        f"{record['config']['subagent_model']} · индекс {record['config']['index_size']} фрагм.",
        f"факты в кандидатах {pct(s['fact_in_candidates'])} · в отборе {pct(s['fact_selected'])} · агентства "
        f"охвачены {pct(s['orgs_covered'])} · лишний веб {s['extra_web']} · пропущенный веб {s['missed_web']} · "
        f"вердикты {s['verdicts']} · ${s['cost_usd']} · {record['duration_s']} с",
        "",
        f"{'кейс':<5} {'факты: ранг в кандидатах':<26} {'в отборе':<10} {'агентства':<14} "
        f"{'вердикт':<13} {'веб':<6} ошибка",
    ]
    for r in record["results"]:
        ranks = ",".join(str(f["candidate_rank"] or "—") for f in r["facts"])
        sel = (
            "".join("✓" if f["selected"] else "✗" for f in r["facts"])
            if r["facts"] and r["facts"][0]["selected"] is not None
            else "—"
        )
        web = (
            "—"
            if r["would_web"] is None
            else ("да" if r["would_web"] else "нет") + ("!" if r["would_web"] and r["web_expected"] is False else "")
        )
        out.append(
            f"{r['case_id']:<5} {ranks:<26} {sel:<10} {','.join(r['selected_orgs']) or '—':<14} "
            f"{str(r['verdict']):<13} {web:<6} {r['error']}"
        )
    return "\n".join(out)

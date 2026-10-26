"""
Прогон eval-набора через настоящего агента (Ouroboros → инструменты), запись результата и сравнение прогонов.

Каждый кейс — ход агента, как в чате консоли (agent.py → console/turns.py), но в отдельном разговоре: история
диалога — только из самого кейса (поле history). Запись прогона — var/evals/<eval_id>/record.json (схема
oilo.eval_run.v1): отпечаток версии агента (console/fingerprint.py), результаты по кейсам и проверкам, сводка.
Сравнение двух прогонов — точный McNemar по парным исходам кейсов.
Пока идёт прогон, данные сервиса инструментов заморожены (POST /api/eval_lock → freeze.py): новые выпуски не идут в
индекс, цены не обновляются — эффект версии агента не смешивается с эффектом новых данных.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import get_settings
from . import stats
from .checks import INFO_CHECKS, Case, CaseResult, evaluate, load_cases

SCHEMA = "oilo.eval_run.v1"

log = logging.getLogger(__name__)
CATEGORY_RU = {
    "report": "по отчётам",
    "web": "по вебу",
    "combined": "комбинированные",
    "forecast": "прогноз",
    "scenario": "сценарий",
    "history": "история цен",
    "followup": "уточнение в диалоге",
    "conflict": "расхождение агентств",
    "honesty": "честность (нет в базе)",
    "out_of_scope": "вне компетенции",
    "advice": "запрос инвестсовета",
    "quote": "текущая цена",
}


def eval_dir() -> Path:
    d = get_settings().var_dir / "evals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_case(case: Case, repeat: int, eval_id: str, fingerprint: dict) -> CaseResult:
    from .. import agent
    from ..console import turns

    t0 = time.monotonic()
    error = ""
    turn: dict = {}
    try:
        raw = agent.ask(
            case.question,
            history=[h.model_dump() for h in case.history],
            conversation_id=f"eval:{eval_id}:{case.id}:{repeat}",
        )
        turn = turns.assemble(raw, fingerprint)
        turns.save(turn, source="eval")
        if turn.get("status") not in ("completed", "completed_by_console"):
            error = f"задача агента: {turn.get('status')}"
    except Exception as exc:  # noqa: BLE001 — сбой кейса не роняет прогон
        error = f"{type(exc).__name__}: {exc}"[:500]
    checks = evaluate(case, turn, error)
    v = turn.get("verification") or {}
    return CaseResult(
        case_id=case.id,
        category=case.category,
        split=case.split,
        run_id=turn.get("turn_id", ""),
        passed=all(c.ok for c in checks if c.name not in INFO_CHECKS),
        checks=checks,
        ms=int(turn.get("ms") or (time.monotonic() - t0) * 1000),
        cost_usd=round(float(turn.get("cost_usd") or 0.0), 6),
        llm_calls=int(turn.get("rounds") or 0),
        numbers_total=v.get("numbers_total", 0),
        numbers_verified=v.get("numbers_verified", 0),
        path=[c["tool"] for c in turn.get("tool_calls", [])],
        error=error,
    )


def run(cases_path: Path, repeats: int = 1, label: str = "", only: list[str] | None = None, workers: int = 2) -> dict:
    from ..console import fingerprint

    cases = load_cases(cases_path)
    if only:
        cases = [c for c in cases if c.id in only or c.category in only]
    eval_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    fp = fingerprint.current(max_age_s=0)
    _tools_lock(eval_id, True)
    jobs = [(c, k) for k in range(repeats) for c in cases]
    t0 = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            results = list(ex.map(lambda job: run_case(job[0], job[1], eval_id, fp), jobs))
    finally:
        _tools_lock(eval_id, False)
    record = {
        "schema": SCHEMA,
        "eval_id": eval_id,
        "label": label,
        "dataset": str(cases_path.name),
        "repeats": repeats,
        "config": fp,
        "started": time.time(),
        "duration_s": round(time.monotonic() - t0, 1),
        "results": [r.as_dict() for r in results],
    }
    record["summary"] = summarize(record)
    d = eval_dir() / eval_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def _tools_lock(eval_id: str, active: bool) -> None:
    """Блокировка индекса на время замера — в сервисе инструментов (у консоли свой том var)."""
    from .. import net

    if not get_settings().tools_url:
        return
    try:
        net.tools_api("/api/eval_lock", body={"eval_id": eval_id, "active": active})
    except Exception as exc:  # noqa: BLE001
        if active:  # без блокировки наблюдатель может обновить базу посреди замера — кейсы пойдут на разных данных
            raise RuntimeError(f"eval: не удалось заблокировать индекс в сервисе инструментов: {exc}") from exc
        log.warning("eval %s: блокировка индекса не снята (истечёт сама): %s", eval_id, exc)


def outcomes(record: dict) -> dict[str, bool]:
    """Исход кейса по повторам: верен большинством прогонов."""
    by: dict[str, list[bool]] = defaultdict(list)
    for r in record["results"]:
        by[r["case_id"]].append(r["passed"])
    return {k: stats.majority(v) for k, v in by.items()}


def summarize(record: dict) -> dict:
    res = record["results"]
    by_case: dict[str, list[bool]] = defaultdict(list)
    by_cat: dict[str, list[bool]] = defaultdict(list)
    checks: dict[str, list[bool]] = defaultdict(list)
    for r in res:
        by_case[r["case_id"]].append(r["passed"])
        by_cat[r["category"]].append(r["passed"])
        for c in r["checks"]:
            checks[c["name"]].append(c["ok"])
    n = len(res)
    k = sum(r["passed"] for r in res)
    pass_k = sum(all(v) for v in by_case.values()) / len(by_case) if by_case else 0.0
    nt = sum(r["numbers_total"] for r in res)
    nv = sum(r["numbers_verified"] for r in res)
    lat = [r["ms"] / 1000 for r in res]
    failures: dict[str, int] = defaultdict(int)
    for r in res:
        for c in r["checks"]:
            if not c["ok"]:
                failures[c["name"]] += 1
    return {
        "cases": len(by_case),
        "runs": n,
        "pass_rate": round(k / n, 4) if n else 0.0,
        "pass_rate_ci95": stats.wilson(k, n),
        f"pass^{record.get('repeats', 1)}": round(pass_k, 4),
        "by_category": {c: round(sum(v) / len(v), 3) for c, v in sorted(by_cat.items())},
        "by_check": {c: round(sum(v) / len(v), 3) for c, v in sorted(checks.items())},
        "failures_by_check": dict(sorted(failures.items(), key=lambda x: -x[1])),
        "numbers_verified_rate": round(nv / nt, 4) if nt else None,
        "cost_usd_total": round(sum(r["cost_usd"] for r in res), 4),
        "cost_usd_per_answer": round(sum(r["cost_usd"] for r in res) / n, 5) if n else 0.0,
        "latency_p50_s": stats.percentile(lat, 50),
        "latency_p95_s": stats.percentile(lat, 95),
        "rounds_mean": round(sum(r["llm_calls"] for r in res) / n, 2) if n else 0.0,
        "errors": sum(1 for r in res if r["error"]),
    }


def load(eval_id: str) -> dict:
    if eval_id == "latest":
        eval_id = sorted(d.name for d in eval_dir().iterdir() if (d / "record.json").is_file())[-1]
    return json.loads((eval_dir() / eval_id / "record.json").read_text(encoding="utf-8"))


def report_markdown(record: dict) -> str:
    """Прогон eval → markdown для репозитория (docs/): итоги, проверки, кейсы. Записи прогона живут в томе консоли,
    отчёт переживает пересоздание стенда."""
    s, cfg = record["summary"], record.get("config") or {}
    settings = cfg.get("settings") or {}
    lo, hi = s.get("pass_rate_ci95") or (0, 0)
    pct = lambda x: "—" if x is None else f"{x * 100:.0f}%"  # noqa: E731
    nv = s.get("numbers_verified_rate")
    verified = "—" if nv is None else f"{nv * 100:.1f}%"  # 99.5% не округляется до 100%
    lines = [
        f"# Прогон eval {record['eval_id']}" + (f" ({record['label']})" if record.get("label") else ""),
        "",
        f"Набор `{record.get('dataset')}`, повторов {record.get('repeats', 1)}, "
        f"длительность {record.get('duration_s', 0) / 60:.0f} мин. "
        f"Каждый кейс — настоящий ход агента: Ouroboros {cfg.get('ouroboros', '')} "
        f"(`{settings.get('OUROBOROS_MODEL', '')}`) → инструменты LangGraph по MCP. "
        f"Версия агента `{cfg.get('agent_version', '')}`, каталог инструментов `{cfg.get('catalog', '')}`.",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| Кейсы пройдены | {round(s['pass_rate'] * s['runs'])}/{s['runs']} = {pct(s['pass_rate'])} "
        f"(95% ДИ Уилсона {pct(lo)}–{pct(hi)}) |",
        f"| Цифры показанного ответа прошли проверку кодом по источникам хода | {verified} |",
        f"| Цена ответа, среднее | ${s['cost_usd_per_answer']:.4f} (всего ${s['cost_usd_total']:.2f}) |",
        f"| Время ответа p50 / p95 | {s['latency_p50_s']:.0f} с / {s['latency_p95_s']:.0f} с |",
        f"| Раундов агента на ответ | {s['rounds_mean']} |",
        f"| Ошибок прогона | {s['errors']} |",
        "",
        "**По категориям:** "
        + ", ".join(f"{CATEGORY_RU.get(c, c)} {pct(v)}" for c, v in s["by_category"].items())
        + ".",
        "",
        "**По проверкам** (доля кейсов, где проверка пройдена; _курсив_ — дисциплина протокола, "
        "исход кейса не решает): "
        + ", ".join((f"_{c}_" if c in INFO_CHECKS else c) + f" {pct(v)}" for c, v in s["by_check"].items())
        + ".",
        "",
        "| Кейс | Категория | Итог | Ход агента | с | $ | Не пройдено |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in sorted(record["results"], key=lambda r: r["case_id"]):
        bad = [c for c in r["checks"] if not c["ok"]]
        why = "; ".join(
            ("_" + c["name"] + "_" if c["name"] in INFO_CHECKS else c["name"])
            + (f": {c['detail'][:90]}" if c["detail"] else "")
            for c in bad
        )
        path = " → ".join(t for t in r["path"] if t != "finish_task") or "без инструментов"
        lines.append(
            f"| {r['case_id']} | {r['category']} | {'✅' if r['passed'] else '❌'} | {path} | {r['ms'] / 1000:.0f} | "
            f"{r['cost_usd']:.4f} | {why.replace('|', '/') or '—'} |"
        )
    return "\n".join(lines) + "\n"


def list_runs(limit: int = 30) -> list[dict]:
    out = []
    for d in sorted(eval_dir().iterdir(), reverse=True)[:limit]:
        f = d / "record.json"
        if f.is_file():
            r = json.loads(f.read_text(encoding="utf-8"))
            out.append(
                {
                    k: r.get(k)
                    for k in ("eval_id", "label", "dataset", "repeats", "config", "summary", "started", "duration_s")
                }
            )
    return out


def compare(base: dict, cand: dict) -> dict:
    """Сравнение кандидата с базой: McNemar по кейсам, дельты категорий и проверок, стоимость и латентность."""
    bo, co = outcomes(base), outcomes(cand)
    mc = stats.paired_outcomes(bo, co)
    sb, sc = base["summary"], cand["summary"]
    fixed = sorted(k for k in set(bo) & set(co) if co[k] and not bo[k])
    broken = sorted(k for k in set(bo) & set(co) if bo[k] and not co[k])
    return {
        "base": base["eval_id"],
        "cand": cand["eval_id"],
        "common_cases": len(set(bo) & set(co)),
        "mcnemar": mc.as_dict(),
        "fixed_cases": fixed,
        "broken_cases": broken,
        "pass_rate": {"base": sb["pass_rate"], "cand": sc["pass_rate"]},
        "by_category": {
            c: {"base": sb["by_category"].get(c), "cand": sc["by_category"].get(c)}
            for c in sorted(set(sb["by_category"]) | set(sc["by_category"]))
        },
        "by_check": {
            c: {"base": sb["by_check"].get(c), "cand": sc["by_check"].get(c)}
            for c in sorted(set(sb["by_check"]) | set(sc["by_check"]))
        },
        "cost_usd_per_answer": {"base": sb["cost_usd_per_answer"], "cand": sc["cost_usd_per_answer"]},
        "latency_p95_s": {"base": sb["latency_p95_s"], "cand": sc["latency_p95_s"]},
        "config_diff": {
            k: [base["config"].get(k), cand["config"].get(k)]
            for k in set(base["config"]) | set(cand["config"])
            if base["config"].get(k) != cand["config"].get(k)
        },
    }

"""
Кейсы eval и детерминированные проверки хода агента (ответ Ouroboros + вызовы инструментов + проверка ответа).

Принцип (Hamel Husain / Anthropic «demystifying evals»): сначала детерминированные бинарные проверки по одному
типу ошибки каждая; LLM-судьи здесь нет (он нужен только с правом вето и после калибровки на людях). Каждая
проверка возвращает
причину провала: по ней видно класс ошибки (и позже самоулучшение чинит класс, а не отдельный кейс).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from ..agent import REFUSAL
from ..evidence.numbers import claim_numbers, matches_reference
from ..evidence.text import FOOTER, LABEL, lang_of

# метки источников в ответе (evidence/text.LABEL) и голые ссылки [R1]/[W2]/[F1]; подвал — evidence/text.FOOTER
LABELS = re.compile(LABEL.pattern + r"|\[[RWF]\d+\]")


class Fact(BaseModel):
    """Ожидаемое число в ответе: любое из any_of (разные формулировки/единицы одного факта).

    near — ключевые слова (регулярные выражения, без учёта регистра), одно из которых стоит рядом с числом во
    фрагменте отчёта: по ним eval поиска и отбора (evals/retrieval.py) узнаёт фрагмент с этим фактом."""

    any_of: list[float]
    what: str = ""
    near: list[str] = Field(default_factory=list)


class Expect(BaseModel):
    refusal: bool = False
    route_web: bool | None = None  # True — должен сходить в веб; False — не должен; None — неважно
    tools: list[str] = Field(default_factory=list)  # обязательные инструменты (subset)
    no_tools: bool = False  # расчётные инструменты вызывать нельзя (research — можно)
    tool_args: dict[str, dict] = Field(default_factory=dict)  # инструмент → аргументы: значение или "<0" / ">0"
    facts: list[Fact] = Field(default_factory=list)
    must_cite_kinds: list[str] = Field(default_factory=list)  # report | web | tool
    must_cite_orgs: list[str] = Field(default_factory=list)  # OPEC | IEA | EIA
    must_include_any: list[str] = Field(default_factory=list)  # регулярные выражения: хотя бы одно есть в ответе
    must_not_include: list[str] = Field(default_factory=list)  # регулярные выражения: ни одного нет в ответе
    min_verified_rate: float = 0.9
    max_web_tier: int = 2


class Turn(BaseModel):
    q: str
    a: str = ""


class Case(BaseModel):
    id: str
    split: str = "dev"
    category: (
        str  # report | web | combined | forecast | scenario | out_of_scope | conflict | history | honesty | followup |
        # advice | quote (названия — evals/runner.CATEGORY_RU и ui/evals.js)
    )
    question: str
    history: list[Turn] = Field(default_factory=list)  # прошлые реплики разговора (follow-up)
    expect: Expect = Field(default_factory=Expect)
    notes: str = ""


def load_cases(path: Path) -> list[Case]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(Case.model_validate(json.loads(line)))
    return out


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    category: str
    split: str
    run_id: str
    passed: bool
    checks: list[CheckResult]
    ms: int
    cost_usd: float
    llm_calls: int
    numbers_total: int
    numbers_verified: int
    path: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# расчётные инструменты (граф graphs/calc.py): первым вызовом засчитываются наравне с research; консоль берёт их отсюда
CALC_TOOLS = frozenset({"forecast_price", "supply_shock_scenario", "price_history", "price_quote"})
# Проверки дисциплины протокола: считаются и показываются, но исход кейса не решают. Пример: агент переписал
# проверенный черновик — консоль всё равно проверяет показываемый текст заново, качество ответа решает «verified».
INFO_CHECKS = frozenset({"answer_is_checked_text"})
FOOTER_ID = re.compile(r"^- \[([RWF]\d+)\] \(", re.M)


def _arg_ok(expected: object, actual: object) -> bool:
    if isinstance(expected, str) and expected[:1] in "<>" and expected[1:].lstrip("-").replace(".", "", 1).isdigit():
        try:
            a, x = float(actual), float(expected[1:])
        except (TypeError, ValueError):
            return False
        return a < x if expected[0] == "<" else a > x
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() == actual.lower()
    try:
        return float(expected) == float(actual)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return expected == actual


def evaluate(case: Case, turn: dict, error: str = "") -> list[CheckResult]:
    """Проверки по ходу агента (console/turns.py: ответ, вызовы инструментов, финальная проверка, доказательства).

    Ход — это answer (что увидел пользователь, с метками источников), answer_source, tool_calls [{tool, args, ok,
    trace}], went_web, verification (финальная проверка консоли по доказательствам хода), evidence, checks (онлайн).
    """
    e = case.expect
    res: list[CheckResult] = []
    answer = turn.get("answer", "")
    body = FOOTER.split(answer)[0]
    calls = [c for c in turn.get("tool_calls", []) if c.get("ok", True)]
    called = [c["tool"] for c in calls]
    mcp = [c["tool"] for c in calls if c.get("mcp", True)]
    ev = turn.get("evidence", {})
    tc = turn.get("checks") or {}
    cited = set(FOOTER_ID.findall(answer)) or set(turn.get("cited", []))
    res.append(CheckResult("no_error", not error and bool(answer.strip()), error[:200]))
    refused = bool(tc.get("refused")) if "refused" in tc else (not mcp and bool(REFUSAL.search(answer)))
    res.append(
        CheckResult(
            "scope", refused == e.refusal, f"ожидался отказ={e.refusal}, получено {refused}; инструменты {called}"
        )
    )
    if e.must_include_any:
        hit = [p for p in e.must_include_any if re.search(p, body, re.I)]
        res.append(
            CheckResult(
                "admits_missing" if case.category == "honesty" else "must_include",
                bool(hit),
                f"нет ни одного из {e.must_include_any}",
            )
        )
    if e.must_not_include:
        bad = [p for p in e.must_not_include if re.search(p, body, re.I)]
        res.append(CheckResult("must_not_include", not bad, f"в ответе есть {bad}"))
    res.append(CheckResult("persona_clean", tc.get("persona_clean", True), "ответ от лица аналитика"))
    res.append(CheckResult("language", tc.get("language_ok", True), "ответ на языке вопроса"))
    if e.refusal:
        if mcp:
            res.append(CheckResult("no_tools", False, f"при отказе вызваны инструменты {mcp}"))
        return res
    res.append(
        CheckResult(
            "research_first",
            bool(mcp) and mcp[0] in ("research", "list_reports", *CALC_TOOLS),
            f"первый вызов: {mcp[:1]}",
        )
    )
    if e.route_web is not None:
        went = bool(turn.get("went_web"))
        res.append(CheckResult("route_web", went == e.route_web, f"ожидался веб={e.route_web}, был={went}"))
    calc = [t for t in called if t in CALC_TOOLS]
    if e.tools:
        missing = [t for t in e.tools if t not in called]
        res.append(CheckResult("tools", not missing, f"не вызваны {missing}; вызваны {called}"))
    if e.tool_args:
        problems = []
        for tool, want in e.tool_args.items():
            got = [c.get("args") or {} for c in calls if c["tool"] == tool]
            if not any(all(_arg_ok(v, a.get(k)) for k, v in want.items()) for a in got):
                problems.append(f"{tool}: нужно {want}, вызвано {got}")
        res.append(CheckResult("tool_args", not problems, "; ".join(problems)))
    if e.no_tools:
        res.append(CheckResult("no_tools", not calc, f"вызваны лишние расчёты {calc}"))
    if e.facts:
        toks = claim_numbers(LABELS.sub(" ", body))  # номера страниц и даты в метках — не факты
        missing_f = [
            f.what or str(f.any_of)
            for f in e.facts
            if not any(matches_reference(t, u, f.any_of, lang_of(body) == "ru") for t, _, u in toks)
        ]
        res.append(CheckResult("facts", not missing_f, f"нет фактов: {missing_f}"))
    kinds = {ev[c]["kind"] for c in cited if c in ev}
    for k in e.must_cite_kinds:
        res.append(CheckResult(f"cites_{k}", k in kinds, f"нет ссылок типа {k}; есть {sorted(kinds)}"))
    if e.must_cite_orgs:
        orgs = {ev[c].get("org") for c in cited if c in ev and ev[c]["kind"] == "report"}
        miss = [o for o in e.must_cite_orgs if o not in orgs]
        res.append(CheckResult("cites_orgs", not miss, f"нет ссылок на {miss}; есть {sorted(o for o in orgs if o)}"))
    v = turn.get("verification") or {}
    total, ok = v.get("numbers_total", 0), v.get("numbers_verified", 0)
    rate = ok / total if total else 1.0
    res.append(
        CheckResult(
            "verified",
            bool(v) and rate >= e.min_verified_rate and not v.get("citations_unknown"),
            f"финальная проверка: {ok}/{total} подтверждено, "
            f"неизвестных или чужих ссылок {v.get('citations_unknown', 0)}",
        )
    )
    res.append(
        CheckResult(
            "answer_is_checked_text",
            bool(tc.get("answer_is_checked_text")),
            f"источник ответа: {turn.get('answer_source', '—')}",
        )
    )
    bad_web = [
        c
        for c in cited
        if c in ev and ev[c]["kind"] == "web" and (ev[c].get("tier") is None or ev[c]["tier"] > e.max_web_tier)
    ]
    res.append(
        CheckResult("source_quality", not bad_web, f"процитированы источники уровня > {e.max_web_tier}: {bad_web}")
    )
    labels_ok = bool(tc.get("labels", True))
    # метка — в любом месте группы ссылок: «[Отчёт IEA OMR, …; Источник: iea.org, web, …]»
    if any(ev.get(c, {}).get("kind") == "report" for c in cited):
        labels_ok &= bool(re.search(r"[\[;]\s*(?:Отчёт|Report) ", body))
    if any(ev.get(c, {}).get("kind") == "web" for c in cited):
        labels_ok &= bool(re.search(r"[\[;]\s*(?:Источник|Source): ", body))
    res.append(CheckResult("labels", labels_ok, "метки [Отчёт …] / [Источник: …, web], без сырых id в тексте"))
    return res

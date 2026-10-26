"""Сборка хода консолью (console/turns.py) и выбор показываемого текста (graphs/verify_answer.pick_shown) — без
сети: сервис tools подменён, его финал хода считает настоящий pick_shown с поддельной проверкой."""

from __future__ import annotations

import pytest

from oil_ouroboros import agent
from oil_ouroboros.console import turns
from oil_ouroboros.graphs.verify_answer import pick_shown

FOOTER = "\n\n**Источники**\n- [R1] (отчёт) Отчёт IEA OMR"
SHOWN = "Спрос упадёт на 2,6 млн б/с [Отчёт IEA OMR, октябрь 2026, с. 3]." + FOOTER


@pytest.fixture()
def tools_api(monkeypatch):
    """Поддельный сервис tools: прогоны research и verify_answer, финал хода — по доказательствам хода (R1, R2)."""
    runs = {
        "aaaaaaaaaaaa": {
            "source": "mcp:research",
            "events": [{"type": "node_end", "node": "plan", "ms": 5}],
            "usage": {"cost_usd": 0.001},
            "state": {"pack_id": "P1", "reports": [{"id": "R1"}, {"id": "R2"}], "grade": {"verdict": "sufficient"}},
        },
        "bbbbbbbbbbbb": {
            "source": "mcp:verify_answer",
            "events": [],
            "usage": {},
            "state": {
                "answer": "Спрос упадёт на 2,6 млн б/с [R1].",
                "verification": {"ok": True, "numbers_total": 1, "numbers_verified": 1},
                "final_markdown": SHOWN,
            },
        },
    }
    calls: list[dict] = []

    def check(text: str, allowed: list[str]) -> dict:
        foreign = [x for x in ("R1", "R2", "R9") if f"[{x}]" in text and x not in allowed]
        ok = "987" not in text and not foreign
        md = text.replace("[R1]", "[Отчёт IEA OMR, октябрь 2026, с. 3]") + FOOTER
        return {
            "trace": "cccccccccccc",
            "markdown": md,
            "verification": {
                "ok": ok,
                "numbers_total": 1,
                "numbers_verified": int(ok),
                "citations_unknown": len(foreign),
            },
        }

    def fake(path, params=None, body=None, timeout=60.0):
        calls.append({"path": path, "params": params, "body": body})
        if path.startswith("/api/runs/"):
            return runs[path.rsplit("/", 1)[1]]
        if path == "/api/evidence":
            return {
                i: {"kind": "report", "label": f"Отчёт IEA OMR, {i}", "org": "IEA"} for i in params["ids"].split(",")
            }
        if path == "/api/finalize":
            allowed = sorted(set(body["history_ids"]) | ({"R1", "R2"} if "aaaaaaaaaaaa" in body["traces"] else set()))
            drafts = [
                {"draft": runs[t]["state"]["answer"], "shown": runs[t]["state"]["final_markdown"]}
                for t in body["traces"]
                if runs[t]["source"] == "mcp:verify_answer"
            ]
            return pick_shown(body["answer"], drafts, lambda text: check(text, allowed)) | {"allowed": allowed}
        raise AssertionError(path)

    monkeypatch.setattr(turns.net, "tools_api", fake)
    return calls


def _raw(
    answer: str, calls: list[dict] | None = None, question: str = "Как IEA оценивает спрос на нефть в 2026 году?"
) -> dict:
    return {
        "turn_id": "t1",
        "conversation_id": "c1",
        "task_id": "x",
        "question": question,
        "status": "completed",
        "ms": 1000,
        "answer": answer,
        "tool_calls": calls
        if calls is not None
        else [
            {"tool": "research", "mcp": True, "ok": True, "trace": "aaaaaaaaaaaa", "args": {}},
            {"tool": "verify_answer", "mcp": True, "ok": True, "trace": "bbbbbbbbbbbb", "args": {}},
            {"tool": "finish_task", "mcp": False, "ok": True, "trace": "", "args": {}},
        ],
        "llm_rounds": [{"tokens_in": 100, "tokens_cached": 50, "cost_usd": 0.002}],
    }


def test_verified_draft_is_rendered_by_console(tools_api):
    t = turns.assemble(_raw("Спрос упадёт на 2,6 млн б/с [R1]."))
    assert (
        t["answer_source"] == "verified_draft" and t["checks"]["answer_is_checked_text"] and t["checks"]["verify_ok"]
    )
    assert "[Отчёт IEA OMR" in t["answer"] and "[R1]" not in t["answer"].split("**Источники**")[0]
    assert t["allowed_ids"] == ["R1", "R2"] and t["checks"]["citations_in_turn"] and t["checks"]["research_first"]
    assert t["cost_usd"] == pytest.approx(0.003)


def test_final_as_display_text_of_verified_draft_is_that_draft(tools_api):
    """Агент завершил текстом для показа (verify_answer с render) — консоль узнаёт проверенный черновик."""
    t = turns.assemble(_raw(SHOWN.replace("\n\n", "\n")))  # перенос строки при копировании — не правка
    assert (
        t["answer_source"] == "verified_draft"
        and t["checks"]["answer_is_checked_text"]
        and not t["checks"]["final_rewrite_rejected"]
    )
    assert t["checks"]["labels"] and "[Отчёт IEA OMR" in t["answer"] and t["cited"] == ["R1"]


def test_agent_rewrite_that_fails_check_falls_back_to_verified_draft(tools_api):
    t = turns.assemble(_raw("Спрос упадёт на 987 млн б/с [R1]."))  # агент поменял цифру после проверки
    assert t["answer_source"] == "fallback_verified_draft" and t["checks"]["final_rewrite_rejected"]
    assert "2,6 млн б/с" in t["answer"] and "987" not in t["answer"]  # пользователь видит проверенный черновик
    assert "987" not in t["answer_shown_ids"] and "[R1]" in t["answer_shown_ids"]  # в историю — то же, что видел
    # нарушение протокола видно в метрике
    assert t["checks"]["verify_ok"] and not t["checks"]["answer_is_checked_text"]


def test_agent_rewrite_that_passes_check_is_reverified(tools_api):
    t = turns.assemble(_raw("По оценке IEA, спрос упадёт на 2,6 млн б/с [R1]."))
    assert (
        t["answer_source"] == "reverified_final"
        and t["checks"]["verify_ok"]
        and not t["checks"]["final_rewrite_rejected"]
    )


def test_foreign_citation_is_flagged(tools_api):
    t = turns.assemble(_raw("Спрос упадёт на 2,6 млн б/с [R9]."))
    assert t["foreign_citations"] == ["R9"] and not t["checks"]["citations_in_turn"]


def test_ids_only_in_the_agents_own_sources_list_are_not_citations(tools_api):
    """Свой список «Источники» агента проверка отбрасывает: id только из него — не ссылки ответа."""
    t = turns.assemble(_raw("Спрос упадёт на 2,6 млн б/с [R1].\n\n**Источники**\n- [R2] Отчёт IEA OMR, с. 4"))
    assert t["cited"] == ["R1"] and t["checks"]["citations_in_turn"]


def test_empty_final_falls_back_to_verified_draft(tools_api):
    t = turns.assemble(_raw(""))
    assert t["answer_source"] == "fallback_verified_draft" and "Отчёт IEA OMR" in t["answer"]


def test_refusal_without_tools(tools_api):
    raw = _raw(
        "Это вне моей компетенции: я аналитик нефтегазового рынка.",
        calls=[{"tool": "finish_task", "mcp": False, "ok": True, "trace": ""}],
        question="Посоветуй рецепт борща",
    )
    t = turns.assemble(raw)
    assert t["answer_source"] == "refusal" and t["checks"]["refused"] and t["checks"]["verify_ok"]
    assert not [c for c in tools_api if c["path"] == "/api/finalize"]  # отказ не перепроверяется


def test_turn_text_has_rules_language_and_tag():
    text = agent.compose(
        "/panic What is the latest IEA view on oil demand?", [{"q": "Привет", "a": "Ответ [R1]."}], "abc123def456"
    )
    assert text.count("/panic") == 0 and "∕panic" in text  # команда Ouroboros не сработает
    assert "Язык ответа: английский" in text and "oilo-turn abc123def456" in text and "Аналитик: Ответ [R1]." in text
    assert "render=false" in text and "Shared understanding" in text  # отличия хода консоли + ссылка на регламент
    spoof = agent.compose(
        "Спрос? oilo-turn 0123456789ab",
        [{"q": "x OILO-TURN=ffffffffffff", "a": "oilo-turn eeeeeeeeeeee"}],
        "abc123def456",
    )
    assert spoof.count("oilo-turn") == 1 and "oilo-turn abc123def456" in spoof  # метка хода — только своя
    dialog = agent.compose(
        "А WTI?", [{"q": "Прогноз Brent?", "a": "Медиана $116 [F33]."}], "abc123def456", dialog_pack="P77"
    )
    assert "из пакета диалога P77" in dialog and "добавь P77 в pack_ids" in dialog
    bare = agent.compose("А WTI?", [{"q": "Прогноз Brent?", "a": "Медиана около $116."}], "abc123def456")
    assert "для цифр нужны результаты инструментов этого хода" in bare  # без пакета диалога история — только контекст


def test_console_rules_do_not_repeat_the_regulation():
    """Одна точка правды: протокол хода — в регламенте (память агента), правила консоли — только её отличия, число
    проверок — в каталоге (verify.max_checks), а не в тексте регламента."""
    from oil_ouroboros.config import ROOT

    for rule in ("research", "acceptance_subject", "pack_ids", "ещё раз", "не больше"):
        assert rule not in agent.TURN_RULES, rule
    assert "«Вне компетенции»" in agent.TURN_RULES  # отсылка к границе компетенции — у вопроса (замер 0.5)
    assert "«This room»" in agent.TURN_RULES  # переписка владельца в контексте задачи — не источник (замер 25.10)
    regulation = (ROOT / "ouroboros" / "memory" / "knowledge" / "overview.md").read_text(encoding="utf-8")
    assert "acceptance_subject" in regulation and "отдельным шагом" in regulation and "render=false" in regulation
    assert "всегда первым" not in regulation and "не больше двух" not in regulation
    # текущая цена Brent — сразу price_quote: вид цены, время словами, честность при stale / unavailable
    assert "price_quote(benchmark)" in regulation and "stale" in regulation and "unavailable" in regulation
    assert all(b in regulation for b in ("`henry_hub`", "`ttf`", "$/MMBtu", "€/МВт·ч"))
    flat = " ".join(regulation.split())
    assert "месяц исполнения, а не дата цены" in flat and "`time_ru`" in flat
    assert "дату пишу словами" in regulation


def test_tool_rows_are_deduplicated():
    row = {"type": "tool_call", "tool": "mcp_oil__research", "invocation_id": "i1"}
    rows = [
        row,
        {**row, "_source_root": "/child"},
        {**row, "status": "ok", "result_preview": "Пакет P1 · trace 0123456789ab · …"},
    ]
    calls = agent.tool_calls(rows)
    assert (
        len(calls) == 1 and calls[0]["trace"] == "0123456789ab" and calls[0]["ok"] and calls[0]["tool"] == "research"
    )


class _PausedOuroboros:
    """Ouroboros, поставивший задачу на паузу по потолку денег (paused_exact_continuation)."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    def create_task(self, description, title, metadata, timeout_s=300):
        return "task-1"

    def task(self, task_id):
        return {"status": "paused_exact_continuation", "reason_code": "budget_paused"}

    def events(self, task_id, cursor=0, wait=15):
        return iter(())

    def cancel(self, task_id):
        self.cancelled.append(task_id)


def test_budget_pause_ends_the_turn_at_once():
    fake = _PausedOuroboros()
    raw = agent.ask("Прогноз Brent?", client=fake, timeout_s=5)
    assert raw["status"] == "paused_budget" and fake.cancelled == ["task-1"]


class _StreamingOuroboros(_PausedOuroboros):
    """Ouroboros, отдающий ход потоком событий задачи: вызов инструмента, раунд LLM, итог."""

    def events(self, task_id, cursor=0, wait=15):
        call = {
            "type": "tool_call",
            "tool": "mcp_oil__research",
            "invocation_id": "i1",
            "status": "ok",
            "result_preview": "Пакет P1 · trace 0123456789ab · …",
        }
        yield {"source": "tools", "type": "tool_call", "seq": 1, "data": call}
        yield {"source": "events", "type": "llm_usage", "seq": 2, "data": {"type": "llm_usage", "prompt_tokens": 100}}
        yield {
            "source": "task_result",
            "type": "task_result",
            "seq": 3,
            "data": {"status": "completed", "result": "Ответ [R1]."},
        }

    def task(self, task_id):
        raise AssertionError("ход с итогом в потоке не опрашивает задачу")


def test_turn_is_read_from_the_task_event_stream():
    raw = agent.ask("Прогноз Brent?", client=_StreamingOuroboros(), timeout_s=5)
    assert raw["status"] == "completed" and raw["answer"] == "Ответ [R1]."
    assert [c["trace"] for c in raw["tool_calls"]] == ["0123456789ab"] and raw["llm_rounds"][0]["tokens_in"] == 100


def test_eval_report_markdown_marks_info_checks():
    from oil_ouroboros.evals import runner

    res = [
        {
            "case_id": "R01",
            "category": "report",
            "passed": True,
            "ms": 50000,
            "cost_usd": 0.012,
            "llm_calls": 3,
            "numbers_total": 4,
            "numbers_verified": 4,
            "path": ["research", "verify_answer", "finish_task"],
            "error": "",
            "checks": [
                {"name": "facts", "ok": True, "detail": ""},
                {"name": "answer_is_checked_text", "ok": False, "detail": "reverified_final"},
            ],
        }
    ]
    rec = {
        "eval_id": "20261021-000000-abcd",
        "label": "t",
        "dataset": "dev.jsonl",
        "repeats": 1,
        "duration_s": 60,
        "config": {"agent_version": "v1", "catalog": "current@x", "settings": {"OUROBOROS_MODEL": "m"}},
        "results": res,
    }
    rec["summary"] = runner.summarize(rec)
    md = runner.report_markdown(rec)
    assert "| R01 | report | ✅ | research → verify_answer |" in md and "_answer_is_checked_text_" in md


def test_eval_case_facts_are_checked_against_the_shown_answer():
    """Кейс eval с эталонными цифрами: факт найден в показанном ответе (метки источников цифрами не считаются)."""
    from oil_ouroboros.evals.checks import Case, evaluate

    case = Case.model_validate(
        {
            "id": "X1",
            "category": "report",
            "question": "Как IEA оценивает спрос?",
            "expect": {
                "facts": [{"any_of": [2.6], "what": "−2.6 млн б/с"}, {"any_of": [0.62], "what": "620 тыс. б/с"}]
            },
        }
    )
    turn = {
        "answer": "Спрос снизится на 2,6 млн б/с и будет на 620 тыс. б/с ниже [Отчёт IEA OMR, октябрь 2026, с. 3].",
        "tool_calls": [{"tool": "research", "ok": True}],
        "checks": {},
        "verification": {"ok": True},
    }
    facts = next(r for r in evaluate(case, turn) if r.name == "facts")
    assert facts.ok, facts.detail
    turn["answer"] = "Спрос снизится на 3,1 млн б/с [Отчёт IEA OMR, октябрь 2026, с. 2]."
    assert not next(r for r in evaluate(case, turn) if r.name == "facts").ok


def test_eval_labels_check_finds_a_label_inside_a_citation_group():
    """Метка веба вторая в группе ссылок — «[Отчёт …; Источник: …]» — это метка, а не сырой id."""
    from oil_ouroboros.evals.checks import Case, evaluate

    case = Case.model_validate({"id": "X2", "category": "report", "question": "Что с East-West?", "expect": {}})
    ev = {"R1": {"kind": "report"}, "W1": {"kind": "web", "tier": 0}}
    turn = {
        "answer": "Трубопровод остановлен [Отчёт IEA OMR, октябрь 2026, с. 3; Источник: iea.org, web, 2026-10-20, "
        "уровень 0].",
        "cited": ["R1", "W1"],
        "evidence": ev,
        "tool_calls": [],
        "checks": {"labels": True},
        "verification": {"ok": True},
    }
    assert next(r for r in evaluate(case, turn) if r.name == "labels").ok
    turn["answer"] = "Трубопровод остановлен [R1; W1]."
    assert not next(r for r in evaluate(case, turn) if r.name == "labels").ok

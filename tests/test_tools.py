"""Инструменты агента (MCP + LangGraph) без сети и денег: fake-LLM, хеш-эмбеддер, встроенный Qdrant, веб выключен."""

from __future__ import annotations

import json
import re

import anyio
import pytest

from oil_ouroboros.evidence import ledger
from oil_ouroboros.mcp.server import call, server


def _call(srv, name: str, args: dict) -> dict:
    """Результат так, как его видит агент: текст и isError."""
    text, is_error = anyio.run(call, name, args)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _tools(srv) -> list[dict]:
    return [t.model_dump(by_alias=True, exclude_none=True) for t in anyio.run(srv.list_tools)]


@pytest.fixture(scope="module")
def srv(indexed):
    return server()


def test_tools_list_is_stable_and_described(srv):
    tools = _tools(srv)
    assert [t["name"] for t in tools] == [
        "research",
        "search_reports",
        "search_web",
        "forecast_price",
        "supply_shock_scenario",
        "price_history",
        "price_quote",
        "verify_answer",
        "list_reports",
    ]
    quote = next(t for t in tools if t["name"] == "price_quote")
    arg = quote["inputSchema"]["properties"]["benchmark"]
    assert quote["annotations"]["openWorldHint"] is True and arg["default"] == "brent"
    assert arg["enum"] == ["brent", "wti", "henry_hub", "ttf"] and "газ Henry Hub" in arg["description"]
    bad = _call(srv, "price_quote", {"benchmark": "urals"})
    assert bad["isError"] is True and "benchmark" in bad["content"][0]["text"]
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object" and "outputSchema" not in t
        for p in (
            t["inputSchema"].get("properties", {}).values()
        ):  # клиент Ouroboros режет строки схемы до 512 символов
            assert len(p.get("description", "")) <= 512


def test_research_returns_pack_with_report_evidence_and_no_structured_duplicate(srv):
    r = _call(srv, "research", {"question": "Как EIA оценивает добычу нефти в США в 2026 году?"})
    assert r["isError"] is False and "structuredContent" not in r
    text = r["content"][0]["text"]
    assert re.search(r"Пакет P\d+", text) and "Решение о вебе: не нужен" in text
    ids = re.findall(r"^\[(R\d+)\] Отчёт EIA STEO", text, flags=re.M)
    assert ids, text[:800]
    pid = re.search(r"Пакет (P\d+)", text).group(1)
    p = ledger.pack(pid)
    assert p["tool"] == "research" and set(ids) <= set(p["ids"])
    assert any(d["type"] == "source_priority" and d["chosen"] == "reports_only" for d in p["decisions"])


def test_same_chunk_gets_the_same_evidence_id(srv):
    a = _call(srv, "search_reports", {"queries": ["crude oil production United States"], "orgs": ["EIA"]})["content"][
        0
    ]["text"]
    b = _call(srv, "search_reports", {"queries": ["crude oil production United States"], "orgs": ["EIA"]})["content"][
        0
    ]["text"]
    ids_a = re.findall(r"^\[(R\d+)\]", a, flags=re.M)
    ids_b = re.findall(r"^\[(R\d+)\]", b, flags=re.M)
    assert ids_a and ids_a == ids_b


def test_web_requires_research_pack_first(srv):
    r = _call(srv, "search_web", {"queries": ["OPEC+ decision"], "pack_id": "P999999"})
    assert r["isError"] is True and "сначала research" in r["content"][0]["text"]


def test_freshness_question_goes_to_web_by_policy(srv):
    text = _call(srv, "research", {"question": "Какие последние новости о решениях ОПЕК+ на этой неделе?"})["content"][
        0
    ]["text"]
    assert "Решение о вебе: поиск выполнен (needs_freshness" in text
    assert "## Веб" in text  # веб выключен в тестах: раздел есть, источников нет, ответ не падает


def test_forecast_is_evidence_and_verify_renders_labels(srv):
    f = _call(srv, "forecast_price", {"benchmark": "brent", "horizon_months": 3})["content"][0]["text"]
    fid, fpid = re.match(r"\[(F\d+)\]", f).group(1), re.search(r"пакет (P\d+)", f).group(1)
    value = json.loads(f.splitlines()[1])
    median = value["path"][-1]["median"]
    draft = f"Медиана прогноза Brent через 3 месяца — {str(median).replace('.', ',')} $/барр. [{fid}]."
    v = _call(
        srv,
        "verify_answer",
        {"answer": draft, "pack_ids": [fpid], "question": "Спрогнозируй Brent на 3 месяца", "render": False},
    )["content"][0]["text"]
    assert v.startswith("Проверка ответа: ПРОЙДЕНА") and "этот черновик как есть" in v and "добавит консоль" in v
    assert "**Источники**" not in v  # ход консоли: метки и строку проверки рендерит консоль после финальной проверки
    # финальная проверка консоли: только доказательства этого хода → метки, список источников, строка проверки
    from oil_ouroboros.graphs import verify_answer as tg

    run = tg.run_verify(draft, "Спрогнозируй Brent на 3 месяца", allowed=[fid], tool="verify_final")
    final = run.state["final_markdown"]
    assert run.state["ok"] and "[Расчёт: прогноз Brent на 3 мес." in final and "**Источники**" in final
    assert f"[{fid}]" not in final.split("**Источники**")[0] and "Проверка: 1/1" in final
    foreign = tg.run_verify(draft, "", allowed=["F999999"], tool="verify_final")
    assert not foreign.state["ok"] and foreign.state["verification"]["citations_foreign"] == 1
    # по умолчанию (чат владельца, Telegram, расписание) агент получает текст для показа с метками и завершает им
    shown = _call(
        srv, "verify_answer", {"answer": draft, "pack_ids": [fpid], "question": "Спрогнозируй Brent на 3 месяца"}
    )["content"][0]["text"]
    head, _, body = shown.partition("Текст для показа (с метками источников):\n")
    assert (
        head.startswith("Проверка ответа: ПРОЙДЕНА") and '"answer": <текст для показа ниже, символ в символ>' in head
    )
    assert "отдельным шагом, без других полей" in head
    assert (
        "[Расчёт: прогноз Brent на 3 мес." in body
        and "**Источники**" in body
        and f"[{fid}]" not in body.split("**Источники**")[0]
    )
    bad = _call(srv, "verify_answer", {"answer": "Brent будет 987,65 $/барр. [F999999].", "pack_ids": [fpid]})[
        "content"
    ][0]["text"]
    assert "НЕ ПРОЙДЕНА" in bad and "проверка не пройдена — неподтверждённые цифры помечены" in bad
    assert "Проверка пройдена" not in bad


def test_finalize_shows_the_verified_draft_of_this_turn(srv):
    """Финал хода консоли (/api/finalize): текст проверяется по доказательствам именно этого хода, метки ставит код;
    правка агента после проверки, не прошедшая её, не показывается — пользователь видит проверенный черновик."""
    from oil_ouroboros.graphs.verify_answer import finalize

    q = "Спрогнозируй Brent на 3 месяца"
    f = _call(srv, "forecast_price", {"benchmark": "brent", "horizon_months": 3})["content"][0]["text"]
    fid, fpid = re.match(r"\[(F\d+)\]", f).group(1), re.search(r"пакет (P\d+)", f).group(1)
    median = json.loads(f.splitlines()[1])["path"][-1]["median"]
    draft = f"Медиана прогноза Brent через 3 месяца — {str(median).replace('.', ',')} $/барр. [{fid}]."
    v = _call(srv, "verify_answer", {"answer": draft, "pack_ids": [fpid], "question": q, "render": False})
    traces = [re.search(r"trace ([0-9a-f]{12})", x).group(1) for x in (f, v["content"][0]["text"])]

    fin = finalize(draft, q, traces)
    assert fin["source"] == "verified_draft" and fin["verification"]["ok"] and fid in fin["allowed"]
    assert (
        "[Расчёт: прогноз Brent на 3 мес." in fin["shown"] and f"[{fid}]" not in fin["shown"].split("**Источники**")[0]
    )
    rewrite = finalize(f"Медиана прогноза Brent через 3 месяца — 987,65 $/барр. [{fid}].", q, traces)
    assert rewrite["source"] == "fallback_verified_draft" and rewrite["rejected"] and "987" not in rewrite["shown"]
    assert finalize("", q, traces)["source"] == "fallback_verified_draft"  # агент не завершил — проверенный черновик
    assert not finalize(draft, q, [])["verification"]["ok"]  # без доказательств хода ссылка чужая


def test_verify_counts_the_checks_of_the_turn(srv):
    """Лимит проверок — в каталоге (verify.max_checks): verify_answer сам говорит, проверять снова или завершать.
    Ход узнаётся по его пакетам; пакет диалога общий у соседних ходов, поэтому проверки по нему не считаются."""
    t = _call(srv, "research", {"question": "Как EIA оценивает добычу нефти в США в 2026 году?"})["content"][0]["text"]
    pid = re.search(r"Пакет (P\d+)", t).group(1)
    bad = {"answer": "Добыча вырастет до 987,65 млн б/с [R999999].", "pack_ids": [pid], "render": False}
    first = _call(srv, "verify_answer", bad)["content"][0]["text"]
    assert "вызови verify_answer снова (проверка 1 из 2)" in first
    second = _call(srv, "verify_answer", bad)["content"][0]["text"]
    assert "Лимит проверок исчерпан (2)" in second and "Новая проверка не нужна" in second
    assert "verify_answer снова" not in second
    dialog = ledger.new_pack("dialog", [], question="история разговора")
    for _ in range(2):
        again = _call(srv, "verify_answer", {**bad, "pack_ids": [dialog]})["content"][0]["text"]
        assert "проверка 1 из 2" in again


def test_next_step_after_the_last_check_depends_on_who_renders():
    from oil_ouroboros.mcp.server import next_step

    shown = next_step(False, True, 2, 2)  # чат владельца, Telegram, сводка: метки ставит verify_answer
    assert "текст для показа ниже" in shown and "помечены" in shown
    console = next_step(False, False, 2, 2)  # ход консоли: черновик со ссылками, метки ставит консоль
    assert "исправленный черновик" in console and "консоль перепроверит" in console
    assert "цифры и ссылки, на которые есть замечания" in console  # чужая ссылка — тоже замечание
    assert next_step(True, False, 1, 2).startswith("Проверка пройдена: заверши ход отдельным шагом")


def test_list_reports_tells_time_watcher_and_upcoming_releases(srv):
    from oil_ouroboros.reports import watch

    text = _call(srv, "list_reports", {})["content"][0]["text"]
    assert re.match(r"Сейчас: \d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d\.", text)
    assert (
        "Наблюдатель выпусков: последняя проверка сайтов агентств" in text and "Ближайшие выпуски по графику:" in text
    )
    state = watch._dir() / "state.json"
    state.write_text(
        json.dumps(
            {
                "finished": "2026-10-22T03:00:00",
                "items": [
                    {"series": "OPEC MOMR", "edition": "2026-10", "status": "blocked"},
                    {"series": "EIA STEO", "edition": "2026-10", "status": "downloaded"},
                ],
            }
        ),
        encoding="utf-8",
    )
    try:
        text = _call(srv, "list_reports", {})["content"][0]["text"]
    finally:
        state.unlink()
    assert "последняя проверка сайтов агентств — 2026-10-22T03:00:00" in text
    assert (
        "Последняя проверка не получила: OPEC MOMR 2026-10 — закрыто проверкой от ботов." in text
        and "EIA STEO 2026-10 —" not in text
    )


def test_verify_catches_invented_number_and_unknown_citation(srv):
    t = _call(srv, "research", {"question": "Как EIA оценивает добычу нефти в США в 2026 году?"})["content"][0]["text"]
    rid, pid = re.search(r"^\[(R\d+)\]", t, flags=re.M).group(1), re.search(r"Пакет (P\d+)", t).group(1)
    v = _call(
        srv,
        "verify_answer",
        {"answer": f"Добыча вырастет до 987,65 млн б/с [{rid}]. Спрос упадёт на 4,4% [R999999].", "pack_ids": [pid]},
    )
    text = v["content"][0]["text"]
    assert "НЕ ПРОЙДЕНА" in text and "unverified_number" in text and "unknown_citation" in text


def test_verify_flags_citations_outside_the_turn_packs(srv):
    """Ссылка не из пакетов этого хода — чужая (из другой задачи или памяти): проверка не пройдена,
    агент правит черновик."""
    t = _call(srv, "research", {"question": "Как EIA оценивает добычу нефти в США в 2026 году?"})["content"][0]["text"]
    rid, pid = re.search(r"^\[(R\d+)\]", t, flags=re.M).group(1), re.search(r"Пакет (P\d+)", t).group(1)
    f = _call(srv, "forecast_price", {"benchmark": "wti", "horizon_months": 2})["content"][0]["text"]
    fid, fpid = re.match(r"\[(F\d+)\]", f).group(1), re.search(r"пакет (P\d+)", f).group(1)
    draft = f"Добычу нефти в США EIA оценивает в отчёте [{rid}]. Прогноз WTI — в расчёте [{fid}]."
    own = _call(srv, "verify_answer", {"answer": draft, "pack_ids": [pid, fpid], "render": False})["content"][0][
        "text"
    ]
    assert own.startswith("Проверка ответа: ПРОЙДЕНА"), own
    alien = _call(srv, "verify_answer", {"answer": draft, "pack_ids": [pid], "render": False})["content"][0]["text"]
    assert "НЕ ПРОЙДЕНА" in alien and f"источник {fid} не из этого хода" in alien and f"пакеты {pid}." in alien
    odd = _call(
        srv, "verify_answer", {"answer": draft, "pack_ids": ["Р999999", f"[{pid}]", fpid], "render": False}
    )  # кириллическая Р
    assert "Пакеты не приняты: P999999 (нет такого пакета)." in odd["content"][0]["text"]
    schema = next(x for x in _tools(srv) if x["name"] == "verify_answer")
    assert schema["inputSchema"]["required"] == ["answer", "pack_ids"]


def test_pack_ids_are_normalized():
    from oil_ouroboros.mcp.server import pack_list

    assert (
        pack_list(["P12", "[P13]", "пакет Р14", 15, "P12"]) == ["P12", "P13", "P14", "P15"] and pack_list(None) == []
    )


def test_dialog_pack_holds_only_known_evidence():
    """Пакет диалога (консоль): доказательства прошлых реплик — чтобы ссылки из истории разговора были своими."""
    from fastapi.testclient import TestClient

    from oil_ouroboros import tools_app

    known = ledger.put("report", ledger.key_of("report", "dialog-pack-test"), "Отчёт X, с. 1", "спрос 1,2 млн б/с")
    c = TestClient(tools_app.app)
    r = c.post("/api/packs", json={"ids": [known, "R999999"], "question": "А что по WTI?"}).json()
    assert ledger.pack(r["pack_id"])["ids"] == [known] and ledger.pack(r["pack_id"])["tool"] == "dialog"
    assert c.post("/api/packs", json={"ids": ["R999999"]}).status_code == 400


def test_tool_errors_are_results_for_the_agent(srv):
    r = _call(srv, "search_web", {"queries": ["x"], "pack_id": "P999999"})
    assert r["isError"] is True and "research" in r["content"][0]["text"]  # что сделать: сначала research
    bad = _call(srv, "research", {"q": "x"})  # нет обязательного question
    assert bad["isError"] is True and "question" in bad["content"][0]["text"]


@pytest.mark.parametrize(
    ("name", "args", "field"),
    [
        ("supply_shock_scenario", {"delta_mbd": 500}, "delta_mbd"),
        ("supply_shock_scenario", {"delta_mbd": -2, "compliance": 1.5}, "compliance"),
        ("forecast_price", {"horizon_months": 24}, "horizon_months"),
        ("forecast_price", {"benchmark": "dubai"}, "benchmark"),
        ("search_web", {"queries": ["x"], "pack_id": "P1", "freshness": "hour"}, "freshness"),
    ],
)
def test_arguments_are_checked_against_the_schema(srv, name, args, field):
    """Диапазоны и перечисления из сигнатуры проверяет SDK: нарушение — ошибка для агента с именем поля."""
    out = _call(srv, name, args)
    assert out["isError"] is True and field in out["content"][0]["text"]


def test_every_tool_call_is_a_traced_run(srv):
    from oil_ouroboros.graphs import runs as store

    _call(srv, "list_reports", {})
    t = _call(srv, "research", {"question": "Что EIA пишет о запасах нефти?"})["content"][0]["text"]
    trace = re.search(r"trace ([0-9a-f]{12})", t).group(1)
    run = store.get_run(trace)
    nodes = [e["node"] for e in run["events"] if e["type"] == "node_end"]
    assert nodes[:3] == ["plan", "retrieve", "select_grade"] and nodes[-1] == "pack"
    assert any(e["type"] == "llm_call" and e["purpose"] == "select_grade" for e in run["events"])


def test_english_question_gets_english_labels_and_footer():
    from oil_ouroboros.graphs.render import label_in, render_final

    assert label_in("Отчёт IEA OMR, октябрь 2026, с. 3", "en") == "Report IEA OMR, October 2026, p. 3"
    assert (
        label_in("Источник: reuters.com, web, 2026-10-14, уровень 1", "en")
        == "Source: reuters.com, web, 2026-10-14, tier 1"
    )
    assert (
        label_in("Расчёт: прогноз Brent на 3 мес., данные EIA по 2026-10-14", "en")
        == "Calculation: Brent forecast, 3 months, EIA data to 2026-10-14"
    )
    ev = {"R1": {"kind": "report", "label": "Отчёт IEA OMR, октябрь 2026, с. 3"}}
    md = render_final(
        "Demand falls by 2.6 mb/d [R1].", ev, {"numbers_total": 1, "numbers_verified": 1, "issues": []}, "en"
    )
    assert "[Report IEA OMR, October 2026, p. 3]" in md and "**Sources**" in md and "_Check: 1/1 figures" in md


def test_new_agency_from_the_catalog_needs_no_code(srv, tmp_path, monkeypatch):
    """Агентства базы — из каталога отчётов (только с файлом на месте); планировщик видит их в промпте, и фильтр
    поиска не выбрасывает новое агентство."""
    from oil_ouroboros import catalog
    from oil_ouroboros.graphs import research
    from oil_ouroboros.graphs import runs as store
    from oil_ouroboros.reports import editions

    (tmp_path / "ief_outlook_2026-10.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "catalog.yaml").write_text(
        "documents:\n"
        "  - {file: ief_outlook_2026-10.pdf, org: IEF, report: Outlook, title: IEF Outlook, edition: '2026-10', "
        "published: '2026-10-20', url: ''}\n"
        "  - {file: missing.pdf, org: GECF, report: GMR, title: GECF Report, edition: '2026-10', "
        "published: '2026-10-20', url: ''}\n",
        encoding="utf-8",
    )
    assert editions.orgs(editions.load(tmp_path)) == ["IEF"]  # без файла агентства в базе нет

    monkeypatch.setattr(research.editions, "orgs", lambda: ["EIA", "IEA", "IEF", "OPEC"])
    assert "Агентства в базе: EIA, IEA, IEF, OPEC." in catalog.get().prompt(
        "research_plan", today="x", latest="—", orgs="EIA, IEA, IEF, OPEC"
    )
    t = _call(srv, "research", {"question": "Что IEF пишет о спросе на нефть в 2026 году?"})["content"][0]["text"]
    plan = store.get_run(re.search(r"trace ([0-9a-f]{12})", t).group(1))["state"]["plan"]
    assert plan["orgs"] == ["IEF"]


def test_comparison_selection_keeps_every_agency():
    """Вопрос сравнивает IEA и EIA, отбор взял только IEA — код добавляет лучший фрагмент EIA; места нет — заменяет
    последний фрагмент агентства, у которого их несколько."""
    from oil_ouroboros.graphs.research import with_every_org

    cands = [{"org": o} for o in ("IEA", "IEA", "EIA", "IEA", "EIA")]
    assert with_every_org([1, 2], cands, ["iea", "eia"], 6) == ([1, 2, 3], ["EIA"])
    assert with_every_org([1, 2, 4], cands, ["IEA", "EIA"], 3) == ([1, 2, 3], ["EIA"])
    assert with_every_org([1, 3], cands, ["IEA", "EIA"], 6) == ([1, 3], [])  # оба уже есть
    assert with_every_org([1, 2], cands, ["IEA"], 6) == ([1, 2], [])  # не сравнение
    assert with_every_org([], cands, ["IEA", "EIA"], 6) == ([], [])  # отбор пуст — отчётов не хватает, не трогаем


def test_selection_sees_the_whole_fragment_and_the_table_title():
    """Фрагмент ~450 токенов отбор видит целиком (цифра в конце не теряется), у фрагмента PDF — и его раздел."""
    from oil_ouroboros import catalog
    from oil_ouroboros.graphs.research import _cand_block

    h = {
        "label": "Отчёт EIA STEO, октябрь 2026, с. 33",
        "page": 33,
        "published": "2026-10-06",
        "section": "Table 3a. World Petroleum and Other Liquid Fuels",
        "text": "Inventories " * 150 + "net withdrawals 1.87",
    }
    block = _cand_block([h], catalog.get().research.select_text_chars)
    assert "с. 33 · Table 3a. World Petroleum" in block and "1.87" in block


def test_foreign_citation_is_shown_as_missing_source_not_as_its_label():
    """Ссылка на доказательство не этого хода: в тексте — «источник не найден», в списке источников её нет, а строка
    предупреждения называет только цифры."""
    from oil_ouroboros.evidence.verify import verify
    from oil_ouroboros.graphs.verify_answer import v_render

    ev = {
        "R1": {
            "id": "R1",
            "kind": "report",
            "label": "Отчёт EIA STEO, октябрь 2026, с. 37",
            "text": "demand 102.7 mb/d",
        },
        "R2": {"id": "R2", "kind": "report", "label": "Отчёт OPEC MOMR, октябрь 2026, с. 85", "text": "demand falls"},
    }
    answer = "Спрос — 102,7 млн б/с [R1]. IEA тоже ждёт снижения [R2]."
    v = verify(answer, ev, allowed={"R1"})
    assert not v.ok and v.citations_foreign == 1
    md = v_render(
        {
            "answer": answer,
            "question": "",
            "allowed": ["R1"],
            "cited": ["R1", "R2"],
            "evidence": ev,
            "verification": v.as_dict(),
        }
    )["final_markdown"]
    assert "[R2: источник не найден]" in md and "OPEC MOMR" not in md and "[R1] (отчёт)" in md
    assert "_Проверка: 1/1" in md and "⚠️" not in md


def test_scenario_label_names_the_external_inventory_estimate():
    from oil_ouroboros.graphs.render import label_in

    ru = "Расчёт: сценарий -2 млн б/с, Brent; запасы — оценка Goldman Sachs"
    assert label_in(ru, "en") == "Calculation: scenario -2 mb/d, Brent; inventories — estimate by Goldman Sachs"
    from oil_ouroboros.forecast import service

    shock = service.shock(-2.0, "brent", 3)
    assert "по оценке Goldman Sachs (внешнее допущение, не расчёт модели)" in shock["scenario"]["explanation"]


def test_mcp_over_http_needs_the_token_and_works_without_sessions(srv):
    """/mcp — приложение официального SDK внутри сервиса tools: без токена 401, каждый запрос — без сессии (клиент
    Ouroboros открывает новую на каждый вызов)."""
    from fastapi.testclient import TestClient

    from oil_ouroboros import tools_app

    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    with TestClient(tools_app.app) as c:
        assert c.post("/mcp", json=init, headers=hdr).status_code == 401
        hdr["Authorization"] = "test-token"  # MCP_TOKEN тестов (conftest); клиент Ouroboros шлёт токен без «Bearer»
        r = c.post("/mcp", json=init, headers=hdr)
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "oil-ouroboros-tools"
        listed = c.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=hdr).json()
        assert [t["name"] for t in listed["result"]["tools"]][:2] == ["research", "search_reports"]


def test_sources_show_reports_web_trust_and_prices(srv):
    """Вкладка «Источники»: серии отчётов с выпусками в индексе, политика доверия к вебу,
    домены из доказательств, цены."""
    from fastapi.testclient import TestClient

    from oil_ouroboros import tools_app

    ledger.put(
        "web", ledger.key_of("web", "https://www.reuters.com/x", "t"), "Reuters", "t", tier=1, domain="reuters.com"
    )
    c = TestClient(tools_app.app)
    d = c.get("/api/sources").json()
    r = d["reports"]
    assert {s["series"] for s in r["series"]} >= {"OPEC MOMR", "EIA STEO", "IEA OMR"} and all(
        s["title"] for s in r["series"]
    )
    assert any(x["series"] == "EIA STEO" and x["chunks"] for x in r["catalog"])
    w = d["web"]
    assert (
        w["min_tier_for_numbers"] == 2
        and "opec.org" in w["tiers"]["tier0"]
        and w["titles"]["0"] == "первичный источник"
    )
    assert {"domain": "reuters.com", "tier": 1} in [{k: x[k] for k in ("domain", "tier")} for x in w["used"]]
    assert {x["benchmark"] for x in d["prices"]["series"]} == {"brent", "wti", "henry_hub"}
    assert all(x.get("as_of") or x.get("error") for x in d["prices"]["series"])
    ok = c.get("/api/sources/domain", params={"url": "https://www.eia.gov/outlooks/steo/"}).json()
    assert ok["domain"] == "eia.gov" and ok["tier"] == 0 and ok["allowed"]
    bad = c.get("/api/sources/domain", params={"url": "kp.ru"}).json()
    assert bad["allowed"] is False and "deny" in bad["reason"]


def test_tool_graphs_are_described_for_the_architecture_map():
    """Вкладка «Архитектура» рисует графы из кода: каждый инструмент MCP (кроме справки list_reports) — в каком-то
    графе, у каждого узла известный вид, описание «как и почему» (docstring) и параметры каталога со значениями."""
    from oil_ouroboros import tools_app
    from oil_ouroboros.graphs.kit import KINDS
    from oil_ouroboros.mcp.server import TOOLS

    d = anyio.run(tools_app.graphs)
    covered = {t for g in d["graphs"] for t in g["tools"]}
    assert covered == {name for name, *_ in TOOLS} - {"list_reports"}
    for g in d["graphs"]:
        ids = {n["id"] for n in g["nodes"]}
        assert all(e["from"] in ids and e["to"] in ids for e in g["edges"])
        for n in g["nodes"]:
            assert n["kind"] in KINDS and n["how"] and all(p["value"] is not None for p in n["params"]), n["id"]
    research = next(g for g in d["graphs"] if g["id"] == "research")
    branches = [e for e in research["edges"] if e["kind"] == "branch"]
    assert {e["to"] for e in branches} == {"web", "pack"} and all(e["label"] for e in branches)

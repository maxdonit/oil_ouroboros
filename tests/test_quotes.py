"""Котировки с Мосбиржи (forecast/quotes.py) — сценарии спеки price-quote на записанных ответах ISS."""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from oil_ouroboros import freeze, net
from oil_ouroboros.forecast import quotes

# ответы ISS 24.10.2026 по активам BR, WTI, NG, TTF (последние сделки — пятница 23.10, 23:50 МСК)
FIX = json.loads((Path(__file__).parent / "data" / "iss_quotes.json").read_text(encoding="utf-8"))
ISS = FIX["BR"]
ASSET = {"brent": "BR", "wti": "WTI", "henry_hub": "NG", "ttf": "TTF"}
SAT = date(2026, 10, 24)


def _parsed(benchmark: str = "brent", today: date = SAT) -> dict:
    return quotes.parse(FIX[ASSET[benchmark]], today, 3)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """Каждый тест — без кэша, без сохранённых котировок и без блокировки eval."""

    def wipe():
        quotes._state.clear()
        for key in ASSET:
            quotes._saved_path(key).unlink(missing_ok=True)

    wipe()
    monkeypatch.setattr(freeze, "reason", lambda: "")
    yield
    wipe()


def test_quote_after_friday_session():
    """Суббота 24.10: ближайший контракт BR-11.26, последняя сделка пятницы 23:50 МСК."""
    q = quotes.parse(ISS, SAT, roll_days=3)
    assert q["time_ru"] == "23.10.2026 23:50 МСК"
    assert (q["contract"], q["price"], q["kind"], q["time_msk"]) == (
        "BR-11.26",
        116.67,
        "последняя сделка",
        "2026-10-23 23:50",
    )
    assert q["delay_min"] == 15 and q["url"].endswith("code=BRX6")


def test_catalog_instruments_quotes():
    """Сорта каталога: WTI, газ Henry Hub и TTF — ближайшие контракты 24.10 с последними сделками пятницы."""
    got = {b: (_parsed(b)["contract"], _parsed(b)["price"]) for b in ("wti", "henry_hub", "ttf")}
    assert got == {"wti": ("WTI-11.26", 103.43), "henry_hub": ("NG-10.26", 2.628), "ttf": ("TTF-10.26", 93.005)}
    units = {k: v.unit for k, v in quotes.instruments().items()}
    assert units == {"brent": "$/барр.", "wti": "$/барр.", "henry_hub": "$/MMBtu", "ttf": "€/МВт·ч"}


def test_roll_to_next_month_before_expiry():
    assert quotes.parse(ISS, SAT, roll_days=3)["contract"] == "BR-11.26"  # до 02.11 — 9 дней
    assert quotes.parse(ISS, date(2026, 10, 30), roll_days=3)["contract"] == "BR-12.26"  # до 02.11 — 3 дня
    assert _parsed("henry_hub")["contract"] == "NG-10.26"  # до 28.10 — 4 дня
    assert _parsed("henry_hub", date(2026, 10, 25))["contract"] == "NG-11.26"  # 3 дня — уже следующий


def test_no_trades_yet_takes_previous_settlement():
    payload = copy.deepcopy(ISS)
    md = payload["marketdata"]
    for row in md["data"]:
        if row[0] == "BRX6":
            row[md["columns"].index("LAST")] = None
            row[md["columns"].index("SETTLEPRICE")] = None
    q = quotes.parse(payload, SAT, roll_days=3)
    assert (q["price"], q["kind"], q["time_msk"]) == (111.42, "расчётная цена предыдущей сессии", "2026-10-23")


def test_evening_session_time_is_calendar_not_trading_day():
    """Вечерняя сессия FORTS относится к следующему торговому дню: время сделки — SYSTIME, а не TRADEDATE."""
    payload = copy.deepcopy(ISS)
    md = payload["marketdata"]
    for row in md["data"]:
        row[md["columns"].index("TRADEDATE")] = "2026-10-26"
    assert quotes.parse(payload, SAT, roll_days=3)["time_msk"] == "2026-10-23 23:50"


def test_wrong_payload_is_an_error():
    with pytest.raises(ValueError, match="securities"):
        quotes.parse({"error": "rate limit"}, SAT, roll_days=3)


def test_fetch_asks_iss_for_the_instrument_asset(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=FIX["WTI"])

    monkeypatch.setattr(
        net, "outbound", lambda timeout, **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setattr(
        quotes, "parse", lambda payload, today, roll_days: {"contracts": len(payload["securities"]["data"])}
    )
    assert quotes.fetch("wti") == {"contracts": 6}
    assert seen and seen[0].startswith(
        "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?assets=WTI"
    )


def test_two_calls_within_cache_hit_exchange_once_per_instrument(monkeypatch):
    calls = []
    monkeypatch.setattr(quotes, "fetch", lambda benchmark="brent": calls.append(benchmark) or _parsed(benchmark))
    a, b = quotes.snapshot(), quotes.snapshot()
    assert calls == ["brent"] and a == b and a["status"] == "ok" and a["quote"]["price"] == 116.67
    w = quotes.snapshot("wti")
    assert calls == ["brent", "wti"] and w["quote"]["contract"] == "WTI-11.26" and w["unit"] == "$/барр."
    assert quotes._saved_path("brent").is_file() and quotes._saved_path("wti").is_file()


def test_eval_freeze_uses_saved_quote(monkeypatch):
    quotes._save("brent", _parsed(), 1.0)
    monkeypatch.setattr(freeze, "reason", lambda: "идёт замер eval")
    monkeypatch.setattr(quotes, "fetch", lambda benchmark="brent": pytest.fail("во время eval биржу не спрашиваем"))
    s = quotes.snapshot()
    assert s["frozen"] is True and s["status"] == "ok" and s["quote"]["time_msk"] == "2026-10-23 23:50"
    assert quotes.snapshot("ttf")["status"] == "unavailable"  # у TTF сохранённой котировки нет


def _down(exc):
    def fetch(benchmark="brent"):
        raise exc

    return fetch


def test_exchange_down_with_saved_quote_is_stale(monkeypatch):
    quotes._save("brent", _parsed(), 1.0)
    monkeypatch.setattr(quotes, "fetch", _down(httpx.ReadTimeout("timed out")))
    s = quotes.snapshot()
    assert s["status"] == "stale" and s["quote"]["price"] == 116.67 and "timed out" in s["error"]
    brent = next(x for x in quotes.status()["instruments"] if x["benchmark"] == "brent")
    assert brent["status"] == "stale" and "timed out" in brent["error"]


def test_no_quote_at_all_keeps_eia_spot_where_it_exists(monkeypatch):
    monkeypatch.setattr(quotes, "fetch", _down(httpx.ConnectError("no route")))
    s = quotes.snapshot()
    assert s["status"] == "unavailable" and s["quote"] is None
    assert s["spot_eia"]["as_of"] and s["spot_eia"]["price"] > 0
    hh = quotes.snapshot("henry_hub")
    assert hh["spot_eia"] == {
        "price": 3.1,
        "as_of": "2026-10-20",
        "source": "Henry Hub Natural Gas spot (EIA RNGWHHD)",
    }
    ttf = quotes.snapshot("ttf")
    assert ttf["spot_eia"] is None and ttf["note"] == quotes.NOTE_NO_SPOT and ttf["unit"] == "€/МВт·ч"
    assert {x["benchmark"]: x["status"] for x in quotes.status()["instruments"]} == dict.fromkeys(ASSET, "unavailable")


def _quote_run(monkeypatch, benchmark="brent", fetched=None):
    from oil_ouroboros.graphs import calc

    q = fetched or _parsed(benchmark)
    monkeypatch.setattr(quotes, "fetch", lambda b="brent": q)
    run = calc.run_calc("price_quote", {"benchmark": benchmark})
    head, body, rule = run.state["result"].splitlines()
    return run, head, json.loads(body), rule


def test_price_quote_label_and_english_label(monkeypatch):
    from oil_ouroboros.graphs import render

    run, head, value, rule = _quote_run(monkeypatch)
    spot = value["spot_eia"]["as_of"]
    label = run.state["label"]
    assert label == (
        f"Котировка: Brent, фьючерс BR-11.26 (Мосбиржа, задержка 15 мин), 23.10.2026 23:50 МСК; спот EIA на {spot}"
    )
    assert head.startswith(f"[{run.state['eid']}] {label} · пакет P") and run.state["eid"].startswith("F")
    assert rule.startswith(f"Цитируй [{run.state['eid']}]") and value["status"] == "ok" and value["note"]
    assert (value["benchmark"], value["unit"]) == ("brent", "$/барр.")
    assert render.label_in(label, "en") == (
        f"Quote: Brent futures BR-11.26 (Moscow Exchange, 15-min delay), 2026-10-23 23:50 MSK; EIA spot as of {spot}"
    )
    assert render.label_in(f"Котировка: Brent — биржевая котировка недоступна; спот EIA на {spot}", "en") == (
        f"Quote: Brent — exchange quote unavailable; EIA spot as of {spot}"
    )


def test_labels_of_other_instruments(monkeypatch):
    from oil_ouroboros.graphs import render

    want = {
        "wti": ("Котировка: WTI, фьючерс WTI-11.26", "Quote: WTI futures WTI-11.26"),
        "henry_hub": ("Котировка: газ Henry Hub, фьючерс NG-10.26", "Quote: Henry Hub gas futures NG-10.26"),
        "ttf": ("Котировка: газ TTF, фьючерс TTF-10.26", "Quote: TTF gas futures TTF-10.26"),
    }
    for benchmark, (ru, en) in want.items():
        run, *_ = _quote_run(monkeypatch, benchmark)
        label = run.state["label"]
        assert label.startswith(ru + " (Мосбиржа, задержка 15 мин), 23.10.2026 23:50 МСК"), label
        assert render.label_in(label, "en").startswith(en + " (Moscow Exchange, 15-min delay), 2026-10-23 23:50 MSK")
    ttf, *_ = _quote_run(monkeypatch, "ttf")
    assert ttf.state["label"].endswith("23:50 МСК")  # у TTF спота EIA нет — без хвоста


def test_stale_quote_label_in_both_languages(monkeypatch):
    from oil_ouroboros.graphs import calc, render

    quotes._save("brent", _parsed(), 1.0)
    monkeypatch.setattr(quotes, "fetch", _down(httpx.ReadTimeout("timed out")))
    run = calc.run_calc("price_quote", {"benchmark": "brent"})
    spot = json.loads(run.state["result"].splitlines()[1])["spot_eia"]["as_of"]
    label = run.state["label"]
    assert label == (
        "Котировка: Brent, фьючерс BR-11.26 (Мосбиржа, задержка 15 мин), 23.10.2026 23:50 МСК, сохранённая — биржа не "
        f"ответила; спот EIA на {spot}"
    )
    assert render.label_in(label, "en").endswith(f"23:50 MSK, saved — exchange did not respond; EIA spot as of {spot}")
    no_spot = calc.quote_label({"status": "unavailable", "quote": None, "spot_eia": {"error": "нет файла"}})
    assert render.label_in(no_spot, "en") == "Quote: Brent — exchange quote unavailable; EIA spot unavailable"
    ttf = calc.quote_label({"benchmark": "ttf", "status": "unavailable", "quote": None, "spot_eia": None})
    assert ttf == "Котировка: газ TTF — биржевая котировка недоступна"
    assert render.label_in(ttf, "en") == "Quote: TTF gas — exchange quote unavailable"


def test_quote_labels_are_stripped_before_text_checks():
    """Онлайн-проверки хода и eval вырезают метку котировки, как и другие метки источников (одно правило —
    evidence/text.LABEL)."""
    from oil_ouroboros.evals import checks
    from oil_ouroboros.evidence.text import LABEL

    text = (
        "Brent — 116,67 $ [Котировка: Brent, фьючерс BR-11.26 (Мосбиржа), 23.10.2026 23:50 МСК] [Quote: Brent futures]"
    )
    assert LABEL.sub("", text).strip() == checks.LABELS.sub("", text).strip() == "Brent — 116,67 $"


def test_same_quote_same_evidence_new_quote_new_evidence(monkeypatch):
    first, *_ = _quote_run(monkeypatch)
    quotes._state.clear()
    again, *_ = _quote_run(monkeypatch)
    assert first.state["eid"] == again.state["eid"]
    newer = dict(_parsed(), price=117.1, time_msk="2026-10-26 10:15", time_ru="26.10.2026 10:15 МСК")
    quotes._state.clear()
    later, *_ = _quote_run(monkeypatch, "brent", newer)
    assert later.state["eid"] != first.state["eid"]
    wti, *_ = _quote_run(monkeypatch, "wti")
    assert wti.state["eid"] not in (first.state["eid"], later.state["eid"])


def test_answer_numbers_confirmed_by_quote_evidence(monkeypatch):
    """verify_answer подтверждает цены котировок с единицами $/барр., $/MMBtu и €/МВт·ч."""
    from oil_ouroboros.graphs import verify_answer as tg

    drafts = {
        "brent": "Brent сейчас стоит 116,67 $/барр. — фьючерс BR-11.26 на Мосбирже, 23 октября в 23:50 МСК [{fid}].",
        "wti": "WTI сейчас — 103,43 $/барр., фьючерс WTI-11.26 на Мосбирже [{fid}].",
        "henry_hub": "Газ Henry Hub сейчас — 2,628 $/MMBtu, фьючерс NG-10.26 на Мосбирже [{fid}].",
        "ttf": "Газ TTF сейчас — 93,005 €/МВт·ч, фьючерс TTF-10.26 на Мосбирже [{fid}].",
    }
    for benchmark, draft in drafts.items():
        run, *_ = _quote_run(monkeypatch, benchmark)
        fid = run.state["eid"]
        checked = tg.run_verify(draft.format(fid=fid), "Сколько сейчас стоит?", allowed=[fid], tool="verify_final")
        assert checked.state["ok"], (benchmark, checked.state["verification"]["issues"])
        assert "[Котировка: " in checked.state["final_markdown"]


def test_sources_show_saved_quotes_without_asking_exchange(monkeypatch):
    """Раздел quote в /api/sources — последние сохранённые котировки сортов, биржу не спрашиваем."""
    from fastapi.testclient import TestClient

    from oil_ouroboros import tools_app

    monkeypatch.setattr(
        quotes, "fetch", lambda benchmark="brent": pytest.fail("вкладка «Источники» биржу не спрашивает")
    )
    c = TestClient(tools_app.app)
    empty = c.get("/api/sources").json()["quote"]
    assert empty["host"] == "iss.moex.com" and empty["cache_s"] == 300 and empty["delay_min"] == 15
    assert [x["benchmark"] for x in empty["instruments"]] == list(ASSET)
    assert all(x["status"] == "unavailable" and x["quote"] is None for x in empty["instruments"])
    quotes._save("brent", _parsed(), 1792800000.0)
    rows = {x["benchmark"]: x for x in c.get("/api/sources").json()["quote"]["instruments"]}
    b = rows["brent"]
    assert b["status"] == "ok" and b["fetched_at"] == 1792800000.0 and b["title"] == "Brent" and b["unit"] == "$/барр."
    assert (b["quote"]["contract"], b["quote"]["price"], b["quote"]["time_msk"]) == (
        "BR-11.26",
        116.67,
        "2026-10-23 23:50",
    )
    prices = {x["benchmark"]: x for x in c.get("/api/sources").json()["prices"]["series"]}
    assert set(prices) == {"brent", "wti", "henry_hub"} and prices["henry_hub"]["unit"] == "$/MMBtu"
    assert prices["henry_hub"]["url"] == "https://www.eia.gov/dnav/ng/hist_xls/RNGWHHDd.xls"


def test_dev_set_has_quote_cases():
    """Набор eval: 42 кейса, пять — о текущей цене (price_quote первым, сорт в аргументах, ссылка на расчёт)."""
    from collections import Counter

    from oil_ouroboros.config import ROOT
    from oil_ouroboros.evals.checks import load_cases

    cases = load_cases(ROOT / "evals" / "datasets" / "dev.jsonl")
    assert len(cases) == 42 and {c.split for c in cases} == {"dev"}
    assert Counter(c.category for c in cases) == {
        "report": 18, "out_of_scope": 4, "web": 3, "forecast": 3, "combined": 2, "followup": 2, "quote": 5,
        "conflict": 1, "scenario": 1, "history": 1, "honesty": 1, "advice": 1,
    }  # fmt: skip
    quote = [c for c in cases if c.category == "quote"]
    assert all(c.expect.tools == ["price_quote"] and c.expect.must_cite_kinds == ["tool"] for c in quote)
    sorts = {c.id: (c.expect.tool_args or {}).get("price_quote", {}).get("benchmark") for c in quote}
    assert sorts == {"Q01": None, "Q02": None, "Q03": "wti", "Q04": "henry_hub", "Q05": "ttf"}


def test_catalog_instruments_point_to_known_eia_series():
    """Ряд спота сорта в каталоге (`eia`) — ряд из forecast/data.SERIES или пусто: опечатка не прячется за «спот
    недоступен»."""
    from oil_ouroboros.forecast import data

    assert {k: v.eia for k, v in quotes.instruments().items() if v.eia and v.eia not in data.SERIES} == {}


def test_english_unit_goes_with_the_quote(monkeypatch):
    run, _, value, _ = _quote_run(monkeypatch, "ttf")
    assert (value["unit"], value["unit_en"]) == ("€/МВт·ч", "€/MWh") and "NYMEX или ICE" in value["note"]

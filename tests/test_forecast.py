import math

import pytest

from oil_ouroboros.forecast import engine, scenario, service


def test_dm_test_identical_errors_not_significant():
    e = [0.1, -0.2, 0.05, 0.3, -0.1, 0.0, 0.2, -0.05, 0.1, -0.1]
    p = engine.dm_test_hln(e, e, 1)
    assert math.isnan(p) or p >= 0.99


def test_conformal_quantiles_bracket_errors():
    errs = [x / 100 for x in range(-50, 51)]
    lo, hi = engine.conformal_quantiles(errs, 80)
    assert lo < 0 < hi and -0.5 <= lo and hi <= 0.5


def test_supply_shock_direction_and_presets():
    r = scenario.supply_shock(-2.0, base_price=100.0)
    assert r.net_shock_share > 0
    assert all(v["pct"] > 0 for v in r.impulse.values())
    # меньше суммарная эластичность → сильнее реакция
    assert r.impulse["kilian_low"]["pct"] > r.impulse["cci"]["pct"] > r.impulse["bh"]["pct"]
    assert r.inventory_channel_usd == (20.0, 40.0) and r.within_calibration
    assert r.explanation.startswith("С рынка уходит 2.00 млн б/с")  # сокращение — изъятие, а не «шок +2»
    with pytest.raises(ValueError):
        scenario.supply_shock(-1, 100, compliance=1.5)


@pytest.mark.parametrize("delta", [-20.0, -10.0, 10.0, 20.0])
def test_large_shocks_keep_prices_positive_and_flag_calibration(delta):
    """Лог-форма: цена > 0 при любом шоке; шок шире applicability_share — с предупреждением, без оценки запасов."""
    r = scenario.supply_shock(delta, base_price=127.0)
    assert all(v["price"] > 0 for v in r.impulse.values())
    assert not r.within_calibration and r.inventory_channel_usd is None
    assert "вне диапазона" in r.explanation and "порядок величины" in r.explanation


def test_shock_effect_is_monotone_and_symmetric_in_log():
    prices = [scenario.supply_shock(d, base_price=100.0).impulse["cci"]["price"] for d in (-4, -2, 0, 2, 4)]
    assert prices == sorted(prices, reverse=True) and prices[2] == 100.0  # больше сокращение — выше цена
    up, down = (scenario.supply_shock(d, base_price=100.0).impulse["cci"]["price"] for d in (-2, 2))
    assert math.isclose(up * down, 100.0**2, rel_tol=1e-3)  # лог-симметрия: ±шок — множитель и обратный ему
    zero = scenario.supply_shock(0, base_price=100.0)
    assert (
        "Чистого шока нет" in zero.explanation and "без изменения" in zero.explanation and "−0" not in zero.explanation
    )
    surplus = scenario.supply_shock(2, base_price=100.0)
    assert surplus.explanation.startswith("На рынок приходит дополнительно 2.00")
    effects = sorted(abs(v["pct"]) for v in surplus.impulse.values())
    lo, hi = effects[0], effects[-1]
    assert f"снижение цены на {lo:.1f}…{hi:.1f}%" in surplus.explanation  # диапазон — от меньшего к большему


def test_offset_is_not_counted_twice_in_inventory_channel():
    """Компенсация уже вычтена из шока, поэтому с ней — только оценка «без ответа OPEC+» (верхняя): нижняя оценка
    правила банков и есть шок с компенсацией."""
    r = scenario.supply_shock(-2.0, base_price=100.0, offset=0.5)
    assert r.inventory_channel_usd == (20.0, 20.0)  # 1 млн б/с после компенсации × $20


@pytest.mark.slow
def test_forecast_contract_brent_and_urals():
    r = service.forecast("brent", 3, "auto")
    assert r.horizon_months == 3 and len(r.path) == 3
    for p in r.path:
        assert p["lo95"] <= p["lo80"] <= p["lo50"] <= p["median"] <= p["hi50"] <= p["hi80"] <= p["hi95"]
    assert any(row["method"] == "rw" for row in r.skill)
    assert "random walk" in r.interpretation or "RW" in r.interpretation
    u = service.forecast("urals", 3, "rw")
    assert u.last_price < r.last_price  # Urals = Brent − дисконт


def test_scheduled_price_refresh_warms_forecast_only_when_data_moved(monkeypatch):
    from types import SimpleNamespace

    from oil_ouroboros import freeze
    from oil_ouroboros.forecast import data

    dates = iter(["2026-10-14"] * 3 + ["2026-10-20"] * 3)  # до обновления (brent, wti, henry_hub), после
    monkeypatch.setattr(data, "load", lambda b: SimpleNamespace(as_of=next(dates)))
    monkeypatch.setattr(data, "refresh", lambda: {"brent": "ok", "wti": "ok", "henry_hub": "ok"})
    monkeypatch.setattr(freeze, "reason", lambda: "")
    warmed: list = []
    out = data.refresh_once(on_update=lambda: warmed.append(1))
    assert out["after"] == {"brent": "2026-10-20", "wti": "2026-10-20", "henry_hub": "2026-10-20"} and warmed == [1]
    assert data.refresh_status()["as_of"]["brent"] == "2026-10-20"


def test_scheduled_price_refresh_waits_for_eval(monkeypatch):
    from oil_ouroboros import freeze
    from oil_ouroboros.forecast import data

    monkeypatch.setattr(freeze, "reason", lambda: "идёт замер eval: индекс обновится после")
    monkeypatch.setattr(
        data, "refresh", lambda: (_ for _ in ()).throw(AssertionError("во время eval цены не трогаем"))
    )
    assert "eval" in data.refresh_once()["skipped"]


def test_henry_hub_spot_is_a_series_but_not_for_forecast_or_history():
    """Третий ряд EIA — спот газа Henry Hub: загружается из снимка, но прогноз и история его не принимают."""
    import pytest

    from oil_ouroboros.forecast import data, service

    hh = data.load("henry_hub")
    assert str(hh.as_of) == "2026-10-20" and round(hh.last_price, 2) == 3.10
    with pytest.raises(ValueError, match=r"есть: brent, wti, henry_hub \(\+ urals через Brent\)"):
        data.load("dubai")
    for call in (lambda: service.history("henry_hub"), lambda: service.forecast("henry_hub", 3)):
        with pytest.raises(ValueError, match="не поддержан"):
            call()


def test_refresh_downloads_every_series_from_its_eia_section(monkeypatch):
    import httpx

    from oil_ouroboros import net
    from oil_ouroboros.config import ROOT, get_settings
    from oil_ouroboros.forecast import data

    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200, content=(ROOT / "data" / "prices" / request.url.path.rsplit("/", 1)[-1]).read_bytes()
        )

    monkeypatch.setattr(
        net, "outbound", lambda timeout, **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw)
    )
    try:
        out = data.refresh()
    finally:  # скачанные копии снимка не должны влиять на другие тесты
        for meta in data.SERIES.values():
            (get_settings().var_dir / "prices" / meta["file"]).unlink(missing_ok=True)
    assert out == {
        "brent": "ok, последняя дата 2026-10-14",
        "wti": "ok, последняя дата 2026-10-14",
        "henry_hub": "ok, последняя дата 2026-10-20",
    }
    assert "https://www.eia.gov/dnav/ng/hist_xls/RNGWHHDd.xls" in seen
    assert "https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls" in seen


def test_scheduled_refresh_runs_when_a_series_has_no_file(monkeypatch):
    """У ряда ещё нет файла — плановое обновление всё равно скачивает все ряды, а его дата до обновления пустая."""
    from oil_ouroboros import freeze
    from oil_ouroboros.forecast import data

    real = data.load

    def load(key):
        if key == "henry_hub":
            raise FileNotFoundError("нет файла цен RNGWHHDd.xls")
        return real(key)

    calls = []
    monkeypatch.setattr(data, "load", load)
    monkeypatch.setattr(data, "refresh", lambda: calls.append(1) or {})
    monkeypatch.setattr(freeze, "reason", lambda: "")
    out = data.refresh_once()
    assert calls == [1] and out["before"]["henry_hub"] == "" and out["before"]["brent"]

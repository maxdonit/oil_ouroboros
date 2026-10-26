"""
Фасад расчётного модуля: то, что вызывает агент (инструменты) и консоль (вкладка «Прогноз»).

Формат результата повторяет институциональную практику (EIA STEO): медианный путь, полосы 50/80/95 из собственных
ошибок модели, карточка скилла против наивного прогноза, метка данных «по состоянию на» и оговорка, что события
после отсечки данных не учтены. Интерпретация — детерминированный текст из чисел (не LLM): объяснения, которые
генерирует модель, повышают доверие без роста точности (arXiv 2401.13835).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from . import data, engine, scenario

BENCHMARKS = ("brent", "wti", "urals")
METHOD_TITLES = {
    "rw": "Random walk (no-change)",
    "drift": "Random walk со сносом",
    "ets": "AutoETS (экспоненциальное сглаживание)",
    "arima": "AutoARIMA",
    "theta": "AutoTheta",
    "ensemble": "Комбинация ETS+ARIMA+Theta",
}


@dataclass
class ForecastResult:
    benchmark: str
    method: str
    method_title: str
    as_of: str
    last_price: float
    last_month_end: str
    horizon_months: int
    path: list[dict]
    skill: list[dict]
    coverage: dict[str, float]
    best_method_by_h: dict[str, str]
    interpretation: str
    caveats: list[str]
    data_source: str
    urals_discount: dict | None = None
    all_methods_last: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)

    def brief(self) -> dict:
        """Компактная версия для LLM: без полной таблицы скилла."""
        d = self.as_dict()
        d["skill"] = [r for r in self.skill if r["method"] in (self.method, "rw")]
        return d


def history(benchmark: str, start: str | None = None, freq: str = "M") -> dict:
    """История цен: месячные (конец месяца) или дневные значения."""
    if benchmark.lower() not in BENCHMARKS:  # спот газа Henry Hub в data.SERIES — только для котировки
        raise ValueError(f"бенчмарк {benchmark!r} не поддержан; есть {', '.join(BENCHMARKS)}")
    base = "brent" if benchmark == "urals" else benchmark
    ps = data.load(base)
    ser = ps.monthly if freq.upper().startswith("M") else ps.daily
    if start:
        ser = ser[ser.index >= pd.Timestamp(start)]
    values = ser.round(2)
    if benchmark == "urals":
        values = (values - scenario.params().urals_discount.base).round(2)
    return {
        "benchmark": benchmark,
        "freq": "M" if freq.upper().startswith("M") else "D",
        "as_of": str(ps.as_of),
        "source": ps.title
        + (
            " минус ТЕКУЩИЙ базовый дисконт Urals: оценка, не котировки (дисконт в прошлом был другим)"
            if benchmark == "urals"
            else ""
        ),
        "points": [{"date": str(i.date()), "price": float(v)} for i, v in values.items()],
        "stats": {  # по тому же ряду, что и точки (для Urals — уже со сдвигом на дисконт)
            "last": float(values.iloc[-1]),
            "min": float(values.min()),
            "max": float(values.max()),
            "mean": round(float(values.mean()), 2),
        },
    }


_CACHE: dict[tuple, ForecastResult] = {}


def forecast(benchmark: str = "brent", horizon_months: int = 3, method: str = "auto") -> ForecastResult:
    """Прогноз с кэшем по (бенчмарк, горизонт, метод, дата данных): модели не переобучаются при каждом вызове."""
    base = "brent" if benchmark.lower() == "urals" else benchmark.lower()
    try:
        key = (benchmark.lower(), int(horizon_months), method, str(data.load(base).as_of))
    except ValueError:
        key = None
    if key and key in _CACHE:
        return _CACHE[key]
    res = _forecast(benchmark, horizon_months, method)
    if key:
        if len(_CACHE) > 200:
            _CACHE.clear()
        _CACHE[key] = res
    return res


def _forecast(benchmark: str = "brent", horizon_months: int = 3, method: str = "auto") -> ForecastResult:
    benchmark = benchmark.lower()
    if benchmark not in BENCHMARKS:
        raise ValueError(f"бенчмарк {benchmark!r} не поддержан; есть {', '.join(BENCHMARKS)}")
    if method not in engine.METHODS and method != "auto":
        raise ValueError(f"метод {method!r} не поддержан; есть auto, {', '.join(engine.METHODS)}")
    horizon = int(max(1, min(horizon_months, engine._catalog().forecast.max_horizon)))
    base = "brent" if benchmark == "urals" else benchmark
    ps = data.load(base)
    bt = engine.backtest(ps)
    if method == "auto":  # метод с наименьшей ошибкой бэктеста на запрошенном горизонте (включая сам RW)
        rows = [r for r in engine.skill_table(bt) if r["h"] == horizon]
        method = min(rows, key=lambda r: r["rmse_log"])["method"]
    fc = engine.point_forecast(ps.monthly, horizon)
    skill = engine.skill_table(bt)
    coverage = engine.coverage_check(bt, method, upto=horizon)

    discount = scenario.params().urals_discount if benchmark == "urals" else None
    # Urals = Brent − дисконт: медиана — с базовым дисконтом, границы интервалов — с худшим и лучшим из сценария
    shift, shift_lo, shift_hi = (-discount.base, -discount.high, -discount.low) if discount else (0.0, 0.0, 0.0)
    path = []
    for h, (_, row) in enumerate(fc.iterrows(), start=1):
        point = {
            "date": str(pd.Timestamp(row["ds"]).date()),
            "h": h,
            "median": round(float(np.exp(row[method])) + shift, 2),
        }
        for level in engine.LEVELS:
            lo, hi = engine.conformal_quantiles(bt.errors[method][h], level)
            point[f"lo{level}"] = round(float(np.exp(row[method] + lo)) + shift_lo, 2)
            point[f"hi{level}"] = round(float(np.exp(row[method] + hi)) + shift_hi, 2)
        path.append(point)

    best = {}
    for h in range(1, horizon + 1):
        rows = [r for r in skill if r["h"] == h]
        best[str(h)] = min(rows, key=lambda r: r["rmse_log"])["method"]

    last_month_end = str(ps.monthly.index[-1].date())
    res = ForecastResult(
        benchmark=benchmark,
        method=method,
        method_title=METHOD_TITLES[method],
        as_of=str(ps.as_of),
        last_price=round(ps.last_price + shift, 2),
        last_month_end=last_month_end,
        horizon_months=horizon,
        path=path,
        skill=skill,
        coverage=coverage,
        best_method_by_h=best,
        interpretation="",
        caveats=[],
        data_source=f"{ps.title}; наблюдения по {ps.as_of}" + ("; Urals = Brent − дисконт" if discount else ""),
        urals_discount=discount.model_dump() if discount else None,  # в результате — простой dict: он уходит в JSON
        all_methods_last={m: round(float(np.exp(fc.iloc[-1][m])) + shift, 2) for m in engine.METHODS},
    )
    res.interpretation = _interpret(res)
    res.caveats = _caveats(res)
    return res


def _interpret(r: ForecastResult) -> str:
    last = r.path[-1]
    rows = {x["h"]: x for x in r.skill if x["method"] == r.method}
    sk = rows.get(r.horizon_months, {})
    rel = sk.get("rel_rmse_vs_rw")
    p = sk.get("dm_p_vs_rw")
    if r.method == "rw":
        skill_txt = "Метод — сам наивный бенчмарк (no-change)."
    elif rel is None:
        skill_txt = ""
    elif rel < 1 and p is not None and p < 0.1:
        skill_txt = f"На горизонте {r.horizon_months} мес. метод точнее random walk (RMSE {rel:.2f} от RW, p={p:.2f})."
    elif rel < 1:
        skill_txt = (
            f"На горизонте {r.horizon_months} мес. ошибка метода {rel:.2f} от random walk, но различие статистически "
            f"не значимо (p={p:.2f}) — используйте прогноз как ориентир, а не как точное значение."
        )
    else:
        skill_txt = (
            f"На горизонте {r.horizon_months} мес. метод не обыгрывает random walk (RMSE {rel:.2f} от RW): "
            "надёжнее опираться на диапазон, а не на медиану."
        )
    chg = (last["median"] / r.last_price - 1) * 100 if r.last_price else 0.0
    return (
        f"{r.benchmark.upper()}: последняя цена {r.last_price:.2f} $/барр. (на {r.as_of}). {r.method_title}: медиана "
        f"через {r.horizon_months} мес. — {last['median']:.2f} $/барр. ({chg:+.1f}%), 80%-интервал "
        f"{last['lo80']:.2f}–{last['hi80']:.2f}, 95%-интервал {last['lo95']:.2f}–{last['hi95']:.2f}. {skill_txt} "
        f"Честное покрытие 80%-интервала на отложенных окнах бэктеста (в среднем по горизонтам "
        f"1–{r.horizon_months} мес.) — {r.coverage.get('80', float('nan')):.0%}."
    )


def _caveats(r: ForecastResult) -> list[str]:
    c = list(engine._catalog().forecast.caveats)  # оговорки — в каталоге: устаревают и правятся без кода
    if r.horizon_months > 6:
        c.append("Горизонт больше 6 месяцев: подавайте результат как условный сценарий, а не как предсказание.")
    if r.benchmark == "urals":
        d = scenario.params().urals_discount
        c.append(
            f"Urals = прогноз Brent минус базовый дисконт ${d.base:.0f}; "
            f"интервалы учитывают дисконт {d.low:.0f}–{d.high:.0f} "
            "(сценарий); официального ряда Urals в открытом доступе нет."
        )
    return c


def shock(
    delta_mbd: float,
    benchmark: str = "brent",
    horizon_months: int = 3,
    compliance: float = 1.0,
    offset: float = 0.0,
) -> dict:
    """Сценарий шока предложения поверх медианы базового прогноза (тот же метод, что и прогноз по умолчанию)."""
    base = forecast(benchmark, horizon_months, "auto")
    median = base.path[-1]["median"]
    sc = scenario.supply_shock(delta_mbd, median, compliance=compliance, offset=offset)
    return {
        "benchmark": benchmark,
        "horizon_months": base.horizon_months,
        "baseline_median": median,
        "baseline_interval80": [base.path[-1]["lo80"], base.path[-1]["hi80"]],
        "as_of": base.as_of,
        "scenario": sc.as_dict(),
    }

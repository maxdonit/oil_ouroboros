"""
Расчётный модуль прогноза: модели, rolling-origin бэктест, conformal-интервалы, тест Диболда—Мариано.

Почему такой набор (ссылки на литературу — у каждого метода):
- **rw** (no-change, random walk) — обязательный бенчмарк: в литературе по нефти его очень трудно обыграть
  (Alquist–Kilian–Vigfusson; Fed IFDP 1022). Любая модель показывается вместе со скиллом против RW.
- **drift** — RW со сносом.
- **ets** (AutoETS), **arima** (AutoARIMA), **theta** (AutoTheta) — проверенные автоматические методы
  (M3/M4); реализация statsforecast (Nixtla), AutoARIMA в бенчмарке Nixtla точнее Prophet.
- **ensemble** — равновзвешенная комбинация ets+arima+theta в лог-ценах: комбинации прогнозов нефти устойчиво
  снижают ошибку (Baumeister–Kilian–Lee: MSPE на 3–29% ниже RW).
Prophet и LSTM как основа отвергнуты: систематического выигрыша на нефти не доказано.

Интервалы — эмпирические (split conformal) квантили ошибок того же бэктеста по каждому горизонту, в лог-шкале:
гауссовы интервалы моделей в кризис слишком узки и симметричны. Покрытие проверяется честно: квантили считаются
на ранних 2/3 окон, покрытие — на поздней 1/3.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import get_settings
from .data import PriceSeries

log = logging.getLogger(__name__)


def _catalog():
    from .. import catalog

    return catalog.get()


METHODS = ("rw", "drift", "ets", "arima", "theta", "ensemble")
ENSEMBLE_OF = ("ets", "arima", "theta")
LEVELS = (50, 80, 95)
_COLS = {"rw": "Naive", "drift": "RWD", "ets": "AutoETS", "arima": "AutoARIMA", "theta": "AutoTheta"}


def _models():
    from statsforecast.models import AutoARIMA, AutoETS, AutoTheta, Naive, RandomWalkWithDrift

    return [
        Naive(alias="Naive"),
        RandomWalkWithDrift(alias="RWD"),
        AutoETS(season_length=1, alias="AutoETS"),
        AutoARIMA(season_length=1, max_p=3, max_q=3, seasonal=False, alias="AutoARIMA"),
        AutoTheta(season_length=1, alias="AutoTheta"),
    ]


def _frame(series: pd.Series) -> pd.DataFrame:
    return pd.DataFrame({"unique_id": "p", "ds": series.index, "y": np.log(series.values.astype(float))})


def _with_ensemble(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={v: k for k, v in _COLS.items()})
    df["ensemble"] = df[list(ENSEMBLE_OF)].mean(axis=1)
    return df


def point_forecast(series: pd.Series, h: int) -> pd.DataFrame:
    """Точечные прогнозы всех методов в лог-шкале: столбцы ds + METHODS."""
    from statsforecast import StatsForecast

    sf = StatsForecast(models=_models(), freq="ME", n_jobs=1)
    out = sf.forecast(df=_frame(series), h=h)
    return _with_ensemble(out.reset_index(drop=True))[["ds", *METHODS]]


# ---------------------------------------------------------------------------------------------------------------
# бэктест
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Backtest:
    """Ошибки rolling-origin бэктеста: errors[method][h] — массив ошибок log(y) − log(ŷ) по окнам."""

    errors: dict[str, dict[int, list[float]]]
    hits: dict[str, dict[int, list[int]]]
    cutoffs: list[str]
    horizon: int
    windows: int
    as_of: str

    def to_json(self) -> dict:
        return {
            "errors": {m: {str(h): v for h, v in d.items()} for m, d in self.errors.items()},
            "hits": {m: {str(h): v for h, v in d.items()} for m, d in self.hits.items()},
            "cutoffs": self.cutoffs,
            "horizon": self.horizon,
            "windows": self.windows,
            "as_of": self.as_of,
        }

    @classmethod
    def from_json(cls, d: dict) -> Backtest:
        return cls(
            errors={m: {int(h): v for h, v in x.items()} for m, x in d["errors"].items()},
            hits={m: {int(h): v for h, v in x.items()} for m, x in d["hits"].items()},
            cutoffs=d["cutoffs"],
            horizon=d["horizon"],
            windows=d["windows"],
            as_of=d["as_of"],
        )


def _cache_path(ps: PriceSeries, horizon: int, windows: int) -> Path:
    digest = hashlib.sha1(f"{ps.benchmark}|{ps.as_of}|{horizon}|{windows}|v2".encode()).hexdigest()[:12]
    d = get_settings().var_dir / "forecast_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"backtest_{ps.benchmark}_{ps.as_of}_{digest}.json"


def backtest(ps: PriceSeries, horizon: int | None = None, windows: int | None = None) -> Backtest:
    """Rolling-origin (расширяющееся окно), месячные точки старта, горизонты 1..horizon. Результат кэшируется."""
    fc = _catalog().forecast
    horizon = horizon or fc.max_horizon
    windows = windows or fc.backtest_windows
    path = _cache_path(ps, horizon, windows)
    if path.is_file():
        try:
            return Backtest.from_json(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, KeyError, TypeError) as exc:  # битый кэш (оборванная запись) — просто пересчитать
            log.warning("кэш бэктеста %s не читается (%s): пересчёт", path.name, exc)

    from statsforecast import StatsForecast

    series = ps.monthly
    df = _frame(series)
    sf = StatsForecast(models=_models(), freq="ME", n_jobs=1)
    cv = sf.cross_validation(df=df, h=horizon, step_size=1, n_windows=windows)
    cv = _with_ensemble(cv.reset_index(drop=True))
    logy = pd.Series(df["y"].values, index=pd.to_datetime(df["ds"]))
    errors: dict[str, dict[int, list[float]]] = {m: {h: [] for h in range(1, horizon + 1)} for m in METHODS}
    hits: dict[str, dict[int, list[int]]] = {m: {h: [] for h in range(1, horizon + 1)} for m in METHODS}
    for cutoff, grp in cv.groupby("cutoff"):
        grp = grp.sort_values("ds")
        y0 = float(logy.loc[pd.Timestamp(cutoff)])
        for h, (_, row) in enumerate(grp.iterrows(), start=1):
            for m in METHODS:
                e = float(row["y"] - row[m])
                errors[m][h].append(e)
                # направление: совпал ли знак изменения прогноза со знаком фактического изменения
                hits[m][h].append(int(np.sign(row[m] - y0) == np.sign(row["y"] - y0)))
    bt = Backtest(
        errors=errors,
        hits=hits,
        cutoffs=[str(pd.Timestamp(c).date()) for c in sorted(cv["cutoff"].unique())],
        horizon=horizon,
        windows=windows,
        as_of=str(ps.as_of),
    )
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")  # у каждого потока свой временный файл
    tmp.write_text(json.dumps(bt.to_json()), encoding="utf-8")
    os.replace(tmp, path)
    return bt


# ---------------------------------------------------------------------------------------------------------------
# метрики и тесты
# ---------------------------------------------------------------------------------------------------------------


def _rmse(x: list[float]) -> float:
    a = np.asarray(x, dtype=float)
    return float(np.sqrt(np.mean(a**2))) if a.size else float("nan")


def dm_test_hln(e_model: list[float], e_bench: list[float], h: int) -> float:
    """
    Двусторонний p-value теста Диболда—Мариано с поправкой Harvey–Leybourne–Newbold (квадратичная потеря).
    Долгосрочная дисперсия — автоковариации до лага h−1 (ошибки h-шагового прогноза перекрываются).
    """
    from scipy import stats

    d = np.asarray(e_model, float) ** 2 - np.asarray(e_bench, float) ** 2
    t = d.size
    if t < 8:
        return float("nan")
    dbar = d.mean()
    dc = d - dbar
    gamma0 = float(np.dot(dc, dc) / t)
    var = gamma0 + 2 * sum(float(np.dot(dc[k:], dc[:-k]) / t) for k in range(1, h))
    if var <= 0:
        var = gamma0
    if var <= 0:
        return 1.0
    dm = dbar / math.sqrt(var / t)
    hln = math.sqrt(max((t + 1 - 2 * h + h * (h - 1) / t) / t, 1e-9))
    stat = dm * hln
    return float(2 * (1 - stats.t.cdf(abs(stat), df=t - 1)))


def skill_table(bt: Backtest, methods: tuple[str, ...] = METHODS) -> list[dict]:
    """Скилл по горизонтам: RMSE лог-ошибки, относительно RW (<1 — лучше RW), hit rate, p-value DM против RW."""
    rows = []
    for h in range(1, bt.horizon + 1):
        rmse_rw = _rmse(bt.errors["rw"][h])
        for m in methods:
            r = _rmse(bt.errors[m][h])
            rows.append(
                {
                    "method": m,
                    "h": h,
                    "rmse_log": round(r, 4),
                    "rel_rmse_vs_rw": round(r / rmse_rw, 3) if rmse_rw > 0 else None,
                    "hit_rate": round(float(np.mean(bt.hits[m][h])), 3) if m != "rw" else None,
                    "dm_p_vs_rw": None if m == "rw" else round(dm_test_hln(bt.errors[m][h], bt.errors["rw"][h], h), 4),
                    "n": len(bt.errors[m][h]),
                }
            )
    return rows


def conformal_quantiles(errors: list[float], level: int) -> tuple[float, float]:
    """Нижний и верхний эмпирические квантили ошибок для центрального интервала уровня level%."""
    a = np.asarray(errors, float)
    alpha = 1 - level / 100
    n = a.size
    # поправка конечной выборки split conformal: ранг ⌈(n+1)(1−α/2)⌉
    q_hi = min(1.0, math.ceil((n + 1) * (1 - alpha / 2)) / n)
    q_lo = max(0.0, math.floor((n + 1) * (alpha / 2)) / n)
    return float(np.quantile(a, q_lo)), float(np.quantile(a, q_hi))


def coverage_check(bt: Backtest, method: str, upto: int | None = None) -> dict[str, float]:
    """Честное покрытие: квантили на ранних 2/3 окон, проверка на поздней 1/3 (среднее по горизонтам 1..upto)."""
    out = {}
    for level in LEVELS:
        hits = []
        for h in range(1, min(upto or bt.horizon, bt.horizon) + 1):
            e = bt.errors[method][h]
            split = max(int(len(e) * 2 / 3), 5)
            lo, hi = conformal_quantiles(e[:split], level)
            hits.extend(int(lo <= x <= hi) for x in e[split:])
        out[str(level)] = round(float(np.mean(hits)), 3) if hits else float("nan")
    return out

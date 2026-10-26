"""
Исторические цены: официальные ряды EIA (public domain).

Источник — таблицы EIA `hist_xls`: Brent spot FOB (RBRTE) и WTI Cushing (RWTC), дневные, — основа прогноза и истории;
спот газа Henry Hub (RNGWHHD) — только спот рядом с котировкой газа (forecast/quotes.py), прогноз и история его не
принимают (forecast/service.BENCHMARKS). Снимок лежит в
`data/prices/` (воспроизводимость и офлайн-работа), `refresh()` скачивает свежие файлы в `var/prices/` —
при наличии свежего файла используется он. FRED зеркалит те же ряды (DCOILBRENTEU, DCOILWTICO).

Почему месячный ряд строится как цена **на конец месяца**, а не среднемесячная: no-change прогноз по цене конца
месяца значимо точнее среднемесячного на горизонтах до года (Bank of Canada SWP 2020-39), а среднемесячные ряды
EIA (RBRTEm) нельзя смешивать с дневными. Последнее дневное наблюдение используется как значение текущего месяца.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from ..config import get_settings

log = logging.getLogger(__name__)

SERIES = {  # section — раздел сайта EIA (pet — нефть, ng — газ)
    "brent": {
        "file": "RBRTEd.xls",
        "eia_id": "RBRTE",
        "title": "Brent spot FOB (EIA RBRTE)",
        "section": "pet",
        "unit": "$/барр.",
    },
    "wti": {
        "file": "RWTCd.xls",
        "eia_id": "RWTC",
        "title": "WTI Cushing spot (EIA RWTC)",
        "section": "pet",
        "unit": "$/барр.",
    },
    "henry_hub": {
        "file": "RNGWHHDd.xls",
        "eia_id": "RNGWHHD",
        "title": "Henry Hub Natural Gas spot (EIA RNGWHHD)",
        "section": "ng",
        "unit": "$/MMBtu",
    },
}
EIA_XLS_URL = "https://www.eia.gov/dnav/{section}/hist_xls/{file}"


@dataclass(frozen=True)
class PriceSeries:
    benchmark: str
    title: str
    daily: pd.Series  # индекс — даты, значения — в единице ряда (SERIES[…]["unit"]: $/барр., у henry_hub — $/MMBtu)
    monthly: pd.Series  # конец месяца; последний месяц может быть неполным (значение — последнее наблюдение)
    as_of: date
    source_file: str

    @property
    def last_price(self) -> float:
        return float(self.daily.iloc[-1])


def _candidate_files(name: str) -> list[Path]:
    s = get_settings()
    return [s.var_dir / "prices" / name, s.data_dir / "prices" / name]


def _read_eia_xls(path: Path) -> pd.Series:
    df = pd.read_excel(path, sheet_name="Data 1", header=2)
    df.columns = ["date", "price"]
    df = df.dropna()
    df["date"] = pd.to_datetime(df["date"])
    ser = df.set_index("date")["price"].astype(float).sort_index()
    return ser[~ser.index.duplicated(keep="last")]


def load(benchmark: str) -> PriceSeries:
    """Загрузить ряд EIA из SERIES: brent, wti или henry_hub (спот газа — только рядом с котировкой). Urals строится
    поверх Brent: Brent − дисконт (каталог: forecast.scenario.urals_discount)."""
    key = benchmark.lower()
    if key not in SERIES:
        raise ValueError(f"неизвестный бенчмарк {benchmark!r}; есть: {', '.join(SERIES)} (+ urals через Brent)")
    meta = SERIES[key]
    files = [p for p in _candidate_files(meta["file"]) if p.is_file()]
    if not files:
        raise FileNotFoundError(f"нет файла цен {meta['file']} ни в var/prices, ни в data/prices")
    series = [(p, _read_eia_xls(p)) for p in files]
    path, daily = max(series, key=lambda x: x[1].index[-1])  # самый свежий из снимка и обновления
    monthly = daily.resample("ME").last().dropna()
    return PriceSeries(
        benchmark=key,
        title=meta["title"],
        daily=daily,
        monthly=monthly,
        as_of=daily.index[-1].date(),
        source_file=str(path),
    )


REFRESH_START_DELAY_S = 30  # первое обновление — вскоре после старта сервиса инструментов
REFRESH_RETRY_S = 600  # идёт замер eval — повтор через 10 мин, чтобы прогон шёл на одних данных
_refresh: dict = {"last_run": None, "result": {}, "as_of": {}, "next": None, "skipped": ""}


def refresh_status() -> dict:
    """Последнее обновление цен для /api/health: когда, результат, дата последнего наблюдения по рядам."""
    return dict(_refresh)


def _as_of(key: str) -> str:
    """Дата данных ряда; файла ещё нет — пусто: плановое обновление всё равно скачает все ряды."""
    try:
        return str(load(key).as_of)
    except (OSError, ValueError):
        return ""


def refresh_once(on_update=None) -> dict:
    """Одно плановое обновление: замер eval не трогаем; новая дата ряда — on_update (прогрев прогноза)."""
    from .. import freeze

    busy = freeze.reason()
    if busy:
        _refresh["skipped"] = busy
        return {"skipped": busy}
    before = {k: _as_of(k) for k in SERIES}
    result = refresh()
    after = {k: _as_of(k) for k in SERIES}
    _refresh.update(last_run=time.time(), result=result, as_of=after, skipped="")
    if after != before and on_update is not None:
        on_update()
    return {"result": result, "before": before, "after": after}


def start_refresh_scheduler(on_update=None) -> bool:
    """Цены EIA обновляются сами: при старте и каждые prices_refresh_hours ч (EIA выкладывает дневные цены раз в
    неделю, поэтому чаще раза в несколько часов смысла нет); снимок в data/prices — только на первый запуск."""
    hours = get_settings().prices_refresh_hours
    if hours <= 0:
        return False
    try:
        _refresh["as_of"] = {k: str(load(k).as_of) for k in SERIES}
    except Exception as exc:  # noqa: BLE001 — нет файлов цен: их принесёт первое обновление
        log.info("цены EIA до первого обновления недоступны: %s", exc)

    def loop() -> None:
        delay = REFRESH_START_DELAY_S
        while True:
            _refresh["next"] = time.time() + delay
            time.sleep(delay)
            try:
                out = refresh_once(on_update)
                delay = REFRESH_RETRY_S if out.get("skipped") else hours * 3600
            except Exception:  # noqa: BLE001 — обновление не должно останавливать сервис
                log.exception("плановое обновление цен EIA")
                delay = hours * 3600

    threading.Thread(target=loop, daemon=True, name="prices-refresh").start()
    return True


def refresh(timeout: float = 60.0) -> dict[str, str]:
    """Скачать свежие таблицы EIA в var/prices (по расписанию — start_refresh_scheduler,
    вручную — `oilo prices refresh`)."""
    from .. import net

    out: dict[str, str] = {}
    target = get_settings().var_dir / "prices"
    target.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 (oil-ouroboros; research)"}
    with net.outbound(timeout, follow_redirects=True, headers=headers) as client:
        for key, meta in SERIES.items():
            url = EIA_XLS_URL.format(**meta)
            try:
                r = client.get(url)
                r.raise_for_status()
                tmp = target / (meta["file"] + ".tmp")
                tmp.write_bytes(r.content)
                ser = _read_eia_xls(tmp)  # проверка, что файл читается
                tmp.replace(target / meta["file"])
                out[key] = f"ok, последняя дата {ser.index[-1].date()}"
            except Exception as exc:  # noqa: BLE001 — обновление не обязательно: остаётся снимок
                log.warning("не удалось обновить %s: %s", url, exc)
                out[key] = f"ошибка: {exc}"
    return out

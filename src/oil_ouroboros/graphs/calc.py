"""
Расчётный модуль как инструменты агента — один граф LangGraph из двух узлов на четыре инструмента:

    forecast_price:        compute → pack   (статистика: ETS / ARIMA / Theta / ансамбль, интервалы по бэктесту)
    supply_shock_scenario: compute → pack   (эластичности + канал запасов поверх базового прогноза)
    price_history:         compute → pack   (ряды EIA, месячные или дневные)
    price_quote:           compute → pack   (котировка фьючерса сорта на Мосбирже и спот EIA — forecast/quotes.py)

compute считает (forecast/service.py), pack кладёт результат в журнал доказательств как F# и пишет текст для агента.
"""

from __future__ import annotations

import functools
import json
import time
from typing import TypedDict

from .. import catalog
from ..evidence import ledger
from ..forecast import quotes, service
from . import kit
from .kit import ToolError


class CalcState(TypedDict, total=False):
    run_id: str
    tool: str
    args: dict
    value: dict
    label: str
    eid: str
    pack_id: str
    result: str
    path: list[str]


def scenario_discount() -> float:
    return catalog.get().forecast.scenario.urals_discount.base


def _bench(b: str) -> str:
    return {"brent": "Brent", "wti": "WTI", "urals": "Urals"}.get(str(b).lower(), str(b))


def quote_label(v: dict) -> str:
    """Метка котировки: сорт, контракт, биржа, задержка, время сделки и дата спота EIA (у сорта без спота — без неё);
    английский вариант — шаблон render.LABEL_EN."""
    q, spot = v.get("quote"), v.get("spot_eia")
    title = quotes.instruments()[v.get("benchmark", "brent")].title
    tail = "" if spot is None else (f"; спот EIA на {spot['as_of']}" if spot.get("as_of") else "; спот EIA недоступен")
    if not q:
        return f"Котировка: {title} — биржевая котировка недоступна{tail}"
    stale = ", сохранённая — биржа не ответила" if v.get("status") == "stale" else ""
    return (
        f"Котировка: {title}, фьючерс {q['contract']} (Мосбиржа, задержка {q['delay_min']} мин), "
        f"{q['time_ru']}{stale}{tail}"
    )


def calc_compute(state: CalcState) -> dict:
    """Расчётный модуль (forecast/service.py), без LLM: прогноз — модели statsforecast (rw, дрейф, ETS, ARIMA, Theta,
    ансамбль) с выбором по бэктесту и интервалами по его ошибкам; сценарий — эластичности и канал запасов поверх
    базового прогноза; история — ряды EIA; котировка — фьючерс сорта на Мосбирже и спот EIA. Метка — здесь же."""
    a = state["args"]
    tool = state["tool"]
    if tool == "forecast_price":
        r = service.forecast(a.get("benchmark", "brent"), int(a.get("horizon_months", 3)), a.get("method", "auto"))
        v = r.brief()
        v["skill"] = [x for x in v["skill"] if x["h"] in (1, r.horizon_months)]
        label = f"Расчёт: прогноз {_bench(r.benchmark)} на {r.horizon_months} мес., данные EIA по {r.as_of}"
    elif tool == "supply_shock_scenario":
        v = service.shock(
            float(a["delta_mbd"]),
            a.get("benchmark", "brent"),
            int(a.get("horizon_months", 3)),
            float(a.get("compliance", 1.0)),
            float(a.get("offset", 0.0)),
        )
        who = catalog.get().forecast.scenario.inventory_rule.attribution
        label = (
            f"Расчёт: сценарий {float(a['delta_mbd']):+g} млн б/с, {_bench(a.get('benchmark', 'brent'))}; "
            f"запасы — оценка {who}"
        )
    elif tool == "price_history":
        v = service.history(a.get("benchmark", "brent"), a.get("start") or "2025-01-01", a.get("freq", "M"))
        pts = v["points"]
        if len(pts) > 40:  # компактно для контекста: равномерная выборка + последняя точка
            step = max(len(pts) // 36, 1)
            pts = pts[::step] + ([pts[-1]] if pts[-1] not in pts[::step] else [])
        v["points"] = pts
        since = a.get("start") or "2025-01-01"
        label = (
            f"Расчёт: оценка Urals = Brent (EIA) − текущий дисконт ${scenario_discount():.0f}, "
            f"с {since}; не котировки Urals"
            if str(a.get("benchmark", "brent")).lower() == "urals"
            else f"Расчёт: история цен {_bench(a.get('benchmark', 'brent'))} (EIA) с {since}"
        )
    elif tool == "price_quote":
        v = quotes.snapshot(a.get("benchmark", "brent"))
        label = quote_label(v)
    else:
        raise ToolError(f"нет расчёта {tool}")
    return {"value": v, "label": label}


def calc_pack(state: CalcState) -> dict:
    """Код: результат — в журнал как F# (тот же расчёт на тех же данных — тот же id), пакет P#, агенту — JSON
    расчёта и правило цитирования."""
    v = state["value"]
    text = json.dumps(v, ensure_ascii=False, default=str)
    when = v.get("as_of") or time.strftime("%Y-%m-%d")
    if state["tool"] == "price_quote":  # дата данных котировки — время сделки и дата спота EIA
        when = f"{(v.get('quote') or {}).get('time_msk', '')}|{(v.get('spot_eia') or {}).get('as_of', '')}"
    # тот же расчёт на тех же данных — тот же F#; другой результат (каталог, данные) журнал сам выдаст под новым id
    eid = ledger.put(
        "tool",
        ledger.key_of("tool", state["tool"], state["args"], when),
        state["label"],
        text,
        tier=0,
        tool=state["tool"],
        args=state["args"],
    )
    pid = ledger.new_pack(
        state["tool"],
        [eid],
        run_id=state.get("run_id", ""),
        question=json.dumps(state["args"], ensure_ascii=False),
        catalog=catalog.get().ref,
    )
    head = f"[{eid}] {state['label']} · пакет {pid} · trace {state.get('run_id', '')}"
    tail = f"Цитируй [{eid}] в каждом предложении с цифрами этого расчёта; интервалы и оговорки передай пользователю."
    return {"eid": eid, "pack_id": pid, "result": f"{head}\n{text}\n{tail}"}


NODES = [
    kit.Node(
        "compute",
        calc_compute,
        "Расчёт: данные EIA, модель, интервалы, интерпретация; котировки сортов с Мосбиржи",
        "calc",
        (
            "forecast.max_horizon",
            "forecast.backtest_windows",
            "forecast.scenario.world_supply_mbd",
            "forecast.scenario.urals_discount.base",
            "forecast.quote.roll_days",
            "forecast.quote.cache_s",
        ),
    ),
    kit.Node("pack", calc_pack, "Журнал F#, пакет, текст для агента"),
]
EDGES = [("compute", "pack")]


@functools.cache
def graph():
    return kit.build(CalcState, NODES, EDGES)


def run_calc(tool: str, args: dict) -> kit.ToolRun:
    return kit.run_graph(tool, graph(), {"tool": tool, "args": args}, args)

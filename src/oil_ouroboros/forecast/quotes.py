"""
Текущие котировки: фьючерс ближайшего месяца на Московской бирже (ISS — открытый API биржи без ключа, данные с
задержкой 15 минут) для сортов каталога forecast.quote.instruments — Brent, WTI, газ Henry Hub и TTF. Рядом —
последняя спот-цена EIA, если у сорта есть ряд (forecast/data.py): фьючерс и спот — разные цены, агент называет обе,
каждую со своим временем.

Контракты Мосбиржи рассчитываются по фьючерсам NYMEX и ICE и истекают вместе с их фронтом (BR-11.26 ↔ ICE Brent
декабрь, экспирация 30.10), поэтому котировка переходит на следующий контракт за forecast.quote.roll_days дней до
последнего дня торгов, а не держится за истекающий.

Удачная котировка сорта кэшируется на forecast.quote.cache_s и сохраняется в var/quotes/<сорт>.json: файл — запас при
сбое биржи и источник котировки на время eval (данные заморожены, сеть не трогается). Биржа не ответила — котировка
из файла со status «stale», файла нет — «unavailable»; спот EIA (если он есть у сорта) в результате есть всегда.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .. import catalog, freeze, net
from ..config import get_settings
from . import data

log = logging.getLogger(__name__)

ISS_URL = (
    "https://iss.moex.com/iss/engines/futures/markets/forts/securities.json?assets={asset}&iss.meta=off"
    "&iss.only=securities,marketdata&securities.columns=SECID,SHORTNAME,LASTTRADEDATE,PREVSETTLEPRICE"
    "&marketdata.columns=SECID,LAST,SETTLEPRICE,TRADEDATE,UPDATETIME,SYSTIME"
)
CONTRACT_URL = "https://www.moex.com/ru/contract.aspx?code={secid}"
DELAY_MIN = 15
SOURCE = "Московская биржа (ISS): фьючерс ближайшего месяца"
NOTE = (
    "Котировка — фьючерс ближайшего месяца на Мосбирже (рассчитывается по фьючерсу NYMEX или ICE), её обычно и "
    "называют текущей ценой. Спот EIA — цена физического товара; EIA выкладывает её раз в неделю. Это разные цены, у "
    "каждой своё время."
)
NOTE_NO_SPOT = (
    "Котировка — фьючерс ближайшего месяца на Мосбирже (рассчитывается по фьючерсу NYMEX или ICE), её обычно и "
    "называют текущей ценой. Открытого дневного спота у этого сорта нет."
)
MSK = timezone(timedelta(hours=3))

_state: dict[str, dict[str, Any]] = {}  # по сорту: время кэша, котировка, последняя ошибка
_lock = threading.Lock()


def instruments() -> dict:
    """Сорта котировок из каталога: ключ — аргумент benchmark инструмента price_quote."""
    return catalog.get().forecast.quote.instruments


def _st(benchmark: str) -> dict[str, Any]:
    return _state.setdefault(benchmark, {"cached_at": 0.0, "quote": None, "error": ""})


def _table(block: dict) -> list[dict]:
    return [dict(zip(block["columns"], row, strict=True)) for row in block["data"]]


def parse(payload: dict, today: date, roll_days: int) -> dict:
    """Ответ ISS → котировка контракта, у которого до последнего дня торгов больше roll_days дней. ValueError —
    ответ не того вида или нет подходящего контракта."""
    try:
        securities = _table(payload["securities"])
        market = {r["SECID"]: r for r in _table(payload["marketdata"])}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"ISS: в ответе нет таблиц securities и marketdata ({exc})") from exc
    live = [
        s
        for s in securities
        if s.get("LASTTRADEDATE") and (date.fromisoformat(s["LASTTRADEDATE"]) - today).days > roll_days
    ]
    if not live:
        raise ValueError(f"ISS: нет контракта, до экспирации которого больше {roll_days} дн.")
    s = min(live, key=lambda r: r["LASTTRADEDATE"])
    m = market.get(s["SECID"], {})
    # время сделки — календарное время данных биржи (SYSTIME, МСК): вечерняя сессия FORTS относится к следующему
    # торговому дню, и TRADEDATE у неё — уже завтрашний; у расчётных цен — торговый день ответа
    day = str(m.get("TRADEDATE") or "")
    if m.get("LAST") is not None:
        when = str(m.get("SYSTIME") or f"{day} {m.get('UPDATETIME', '')}")[:16]
        price, kind = m["LAST"], "последняя сделка"
    elif m.get("SETTLEPRICE") is not None:
        price, kind, when = m["SETTLEPRICE"], "расчётная цена сессии", day
    elif s.get("PREVSETTLEPRICE") is not None:
        price, kind, when = s["PREVSETTLEPRICE"], "расчётная цена предыдущей сессии", day
    else:
        raise ValueError(f"ISS: у {s['SHORTNAME']} нет ни сделки, ни расчётной цены")
    return {
        "contract": s["SHORTNAME"],
        "price": round(float(price), 3),
        "kind": kind,
        "time_msk": when.strip(),
        "time_ru": _ru(when.strip()),
        "last_trade_date": s["LASTTRADEDATE"],
        "delay_min": DELAY_MIN,
        "source": SOURCE,
        "url": CONTRACT_URL.format(secid=s["SECID"]),
    }


def _ru(when: str) -> str:
    """«2026-10-23 23:50» → «23.10.2026 23:50 МСК», «2026-10-22» → «22.10.2026»: так дату пишет ответ агента."""
    day, _, hm = when.partition(" ")
    y, m, d = (day.split("-") + ["", "", ""])[:3]
    return f"{d}.{m}.{y}" + (f" {hm} МСК" if hm else "")


def _saved_path(benchmark: str = "brent"):
    return get_settings().var_dir / "quotes" / f"{benchmark}.json"


def _load_saved(benchmark: str) -> dict | None:
    try:
        return json.loads(_saved_path(benchmark).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(benchmark: str, quote: dict, fetched_at: float) -> None:
    p = _saved_path(benchmark)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"quote": quote, "fetched_at": fetched_at}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def fetch(benchmark: str = "brent") -> dict:
    """Запрос к ISS по активу сорта и разбор (исключение — биржа недоступна или ответ не того вида)."""
    cfg = catalog.get().forecast.quote
    url = ISS_URL.format(asset=cfg.instruments[benchmark].asset)
    with net.outbound(cfg.timeout_s, headers={"User-Agent": "Mozilla/5.0 (oil-ouroboros; research)"}) as client:
        r = client.get(url)
        r.raise_for_status()
        payload = r.json()
    return parse(payload, datetime.now(MSK).date(), cfg.roll_days)


def current(benchmark: str = "brent") -> dict:
    """Котировка сорта для инструмента: {status, quote, frozen, error}. Кэш в памяти, файл — запас и заморозка eval."""
    cfg = catalog.get().forecast.quote
    st = _st(benchmark)
    with _lock:
        if freeze.reason():  # идёт замер eval: прогон идёт на одних данных, сеть не трогаем
            saved = _load_saved(benchmark)
            return {"status": "ok" if saved else "unavailable", "quote": (saved or {}).get("quote"), "frozen": True}
        if st["quote"] and time.time() - st["cached_at"] < cfg.cache_s:
            return {"status": "ok", "quote": st["quote"], "frozen": False}
        try:
            quote = fetch(benchmark)
        except Exception as exc:  # noqa: BLE001 — сбой биржи не роняет инструмент: сохранённая котировка или спот EIA
            log.warning("котировка %s с ISS: %s", benchmark, exc)
            st["error"] = f"{type(exc).__name__}: {exc}"
            saved = _load_saved(benchmark)
            return {
                "status": "stale" if saved else "unavailable",
                "quote": (saved or {}).get("quote"),
                "frozen": False,
                "error": st["error"],
            }
        now = time.time()
        st.update(cached_at=now, quote=quote, error="")
        _save(benchmark, quote, now)
        return {"status": "ok", "quote": quote, "frozen": False}


def spot_eia(benchmark: str = "brent") -> dict | None:
    """Последняя спот-цена EIA сорта с датой; None — у сорта нет ряда EIA; ошибка чтения — в поле error."""
    series = instruments()[benchmark].eia
    if not series:
        return None
    try:
        p = data.load(series)
        return {"price": round(p.last_price, 3), "as_of": str(p.as_of), "source": p.title}
    except Exception as exc:  # noqa: BLE001 — нет файлов цен: котировка остаётся, спот — с ошибкой
        return {"error": f"{type(exc).__name__}: {exc}"}


def snapshot(benchmark: str = "brent") -> dict:
    """Результат инструмента price_quote: котировка сорта, спот EIA и пояснение (без изменчивых полей: тот же F#)."""
    inst = instruments()[benchmark]
    q = current(benchmark)
    spot = spot_eia(benchmark)
    out = {
        "benchmark": benchmark,
        "unit": inst.unit,
        "unit_en": inst.unit_en,
        "status": q["status"],
        "quote": q["quote"],
        "spot_eia": spot,
        "note": NOTE if spot is not None else NOTE_NO_SPOT,
    }
    if q.get("frozen"):
        out["frozen"] = True
    if q.get("error"):
        out["error"] = q["error"]
    return out


def status() -> dict:
    """Раздел котировок для /api/sources — без обращения к бирже: сохранённые котировки сортов и последние ошибки."""
    cfg = catalog.get().forecast.quote
    rows = []
    for key, inst in cfg.instruments.items():
        saved, err = _load_saved(key), _st(key)["error"]
        row = {
            "benchmark": key,
            "title": inst.title,
            "unit": inst.unit,
            "status": "unavailable" if not saved else ("stale" if err else "ok"),
            "quote": (saved or {}).get("quote"),
            "fetched_at": (saved or {}).get("fetched_at"),
        }
        if err:
            row["error"] = err
        rows.append(row)
    return {
        "cache_s": cfg.cache_s,
        "roll_days": cfg.roll_days,
        "delay_min": DELAY_MIN,
        "source": SOURCE,
        "host": "iss.moex.com",
        "instruments": rows,
    }

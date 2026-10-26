"""Журнал доказательств: «соседи» цитаты — последний пакет, а не все пакеты соседних разговоров."""

from __future__ import annotations

import time

from oil_ouroboros.evidence import ledger


def test_neighbours_take_the_latest_pack_of_each_citation():
    f = ledger.put("tool", "price_history:brent:test-neighbours", "Расчёт: история Brent", '{"last": 70.46}', tier=0)
    other = ledger.put("tool", "forecast:brent:test-neighbours", "Расчёт: прогноз Brent", '{"delta": 3.6}', tier=0)
    ledger.new_pack("forecast_price", [f, other], question="соседний разговор: прогноз")
    time.sleep(0.01)
    mine = ledger.put("report", "chunk:test-neighbours", "Отчёт EIA STEO", "Brent averaged $96 in 2026", tier=0)
    ledger.new_pack("research", [f, mine], question="этот ход: сценарий")
    got = ledger.neighbours([f])
    assert mine in got and other not in got  # цифра из пакета соседнего разговора проверку не подтверждает


def test_same_key_keeps_its_id_while_the_text_is_the_same():
    """Тот же фрагмент — тот же R#; изменился текст под тем же ключом (переразбор отчёта) — новый R#,
    старый не тронут."""
    key = ledger.key_of("report", "chunk-same-key")
    a = ledger.put("report", key, "Отчёт X, с. 1", "спрос 1,2 млн б/с")
    assert ledger.put("report", key, "Отчёт X, с. 1", "спрос 1,2 млн б/с") == a
    b = ledger.put("report", key, "Отчёт X, с. 1", "спрос 1,3 млн б/с")
    assert b != a and ledger.get([a])[a]["text"] == "спрос 1,2 млн б/с"
    assert ledger.put("report", key, "Отчёт X, с. 1", "спрос 1,3 млн б/с") == b


def test_items_of_packs_take_only_known_fresh_packs():
    """Доказательства хода по списку пакетов агента: несуществующий или старый пакет не засчитывается."""
    a = ledger.put("report", ledger.key_of("report", "pack-items-test"), "Отчёт X, с. 2", "добыча 13,6 млн б/с")
    pid = ledger.new_pack("research", [a])
    assert ledger.items_of_packs([pid, "P999999"], max_age_s=600) == ([a], [pid], ["P999999 (нет такого пакета)"])
    time.sleep(0.01)
    assert ledger.items_of_packs([pid], max_age_s=0) == ([], [], [f"{pid} (старше 0 мин)"])

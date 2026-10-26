"""Разбиение отчётов на фрагменты (rag/chunks.py): таблица — с подписью и без выравнивания markdown."""

from oil_ouroboros.rag.chunks import chunk_elements, compact_table
from oil_ouroboros.rag.parse import Element
from oil_ouroboros.reports.editions import Edition

META = Edition(
    file="steo.pdf",
    org="EIA",
    report="STEO",
    title="Short-Term Energy Outlook",
    edition="2026-10",
    published="2026-10-06",
    url="",
)
TABLE = (
    "| Q1                    | Q2 Q3 2025   |\n"
    "|-----------------------|--------------|\n"
    "| World total ......... | 102.66       |\n"
    "| Inventory net withdrawals ........ | 1.87 |"
)
TITLE_3A = "Table 3a. World Petroleum and Other Liquid Fuels Production, Consumption, and Inventories"
HEADER = "U.S. Energy Information Administration | Short-Term Energy Outlook - October 2026"


def _tables(els: list[Element]) -> list:
    return [c for c in chunk_elements(META, els) if c.kind == "table"]


def test_table_markdown_is_compacted_without_losing_numbers():
    assert (
        compact_table(TABLE) == "| Q1 | Q2 Q3 2025 |\n| World total | 102.66 |\n| Inventory net withdrawals | 1.87 |"
    )


def test_table_chunk_starts_with_its_caption():
    """Подпись — заголовок раздела (EIA Table 3a) или строка перед таблицей (EIA Table 4b, раздел — «Sources:»)."""
    t = _tables(
        [
            Element(33, "heading", TITLE_3A, TITLE_3A),
            Element(33, "text", HEADER, TITLE_3A),
            Element(33, "table", TABLE, TITLE_3A),
        ]
    )[0]
    assert t.text.startswith(TITLE_3A + "\n| Q1 |") and t.section == TITLE_3A and "1.87" in t.text
    title_4b = "Table 4b. U.S. Petroleum Refinery Balances (million barrels per day)"
    t = _tables(
        [
            Element(39, "text", title_4b, "Sources:"),
            Element(39, "text", HEADER, "Sources:"),
            Element(39, "table", TABLE, "Sources:"),
        ]
    )[0]
    assert t.text.startswith(title_4b) and t.section == title_4b
    assert _tables([Element(5, "table", TABLE, "Overview")])[0].section == "Overview"  # без подписи — как было


def test_big_table_parts_repeat_caption_and_header():
    """У OPEC подпись уже первая строка таблицы: каждая часть большой таблицы — с подписью и шапкой."""
    body = "\n".join(f"| Row {i} with a long descriptive name here | {i}.5 | {i}.7 |" for i in range(120))
    md = f"Table 1 - 1: OPEC Reference Basket, US$/b\n\n| Crude | Aug | Sep |\n|---|---|---|\n{body}"
    parts = _tables([Element(12, "table", md, "Crude spot prices")])
    assert len(parts) > 1 and parts[0].section == "Table 1 - 1: OPEC Reference Basket, US$/b"
    assert all(
        p.text.startswith("Table 1 - 1: OPEC Reference Basket, US$/b\n| Crude | Aug | Sep |\n| Row") for p in parts
    )

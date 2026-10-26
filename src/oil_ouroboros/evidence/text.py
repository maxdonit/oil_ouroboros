"""
Текст ответа: ссылки [R#]/[W#]/[F#] на журнал доказательств (кириллические «Р»/«Ф» → латинские), свой раздел
«Источники» агента в конце черновика (его ставит система, а не агент), язык текста, разбиение на предложения.
"""

from __future__ import annotations

import re

CITE = re.compile(r"\[((?:[RWF]\d+)(?:\s*[,;]\s*[RWF]\d+)*)\]")
# метки источников, которые ставит система (graphs/render.py): «[Отчёт …]», «[Источник: …]», «[Расчёт: …]»,
# «[Котировка: …]» и их английские варианты; подвал ответа со списком источников — «**Источники**» / «**Sources**»
LABEL_PREFIXES = ("Отчёт", "Источник", "Расчёт", "Котировка", "Report", "Source", "Calculation", "Quote")
LABEL = re.compile(r"\[(?:" + "|".join(LABEL_PREFIXES) + r")[^\]]*\]")
FOOTER = re.compile(r"\*\*(?:Источники|Sources)\*\*")
OWN_SOURCES = re.compile(
    r"\s*\n[ \t]*(?:#{1,4}[ \t]*)?\**[ \t]*(?:Источники|Список источников|Sources|References)[ \t]*:?[ \t]*\**[ \t]*:?"
    r"(?:\n[ \t]*(?:[-*•]|\d+[.)]|\[)[^\n]*|\n[ \t]*)*\s*\Z",
    re.I,
)
CYR_ID = re.compile(r"(?<=[\[,;\s])([РФ])(?=\d+\s*[\],;])")  # [Р12] (кириллица в русском тексте) → [R12]


def normalize_ids(text: str) -> str:
    return CYR_ID.sub(lambda m: {"Р": "R", "Ф": "F"}[m.group(1)], text or "")


def strip_own_sources(text: str) -> str:
    """Черновик без своего раздела «Источники» в конце: список источников ставит система."""
    return OWN_SOURCES.sub("", text)


def lang_of(text: str) -> str:
    """ru | en по письменности текста (то же правило, что у языка ответа в тексте задачи консоли)."""
    cyr = len(re.findall(r"[А-Яа-яЁё]", text or ""))
    lat = len(re.findall(r"[A-Za-z]", text or ""))
    return "ru" if cyr > 20 or cyr >= lat * 0.3 else "en"


def split_sentences(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # предложение заканчивается точкой/!/? — но не внутри числа и не перед ссылкой [R1]
        parts = re.split(r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ«*(\-•])", line)
        out.extend(p.strip() for p in parts if p.strip())
    return out


def cited_ids(text: str) -> list[str]:
    """Id из ссылок текста по порядку появления, без повторов: «[R1; W2] … [R1]» → [R1, W2]."""
    return list(dict.fromkeys(c.strip() for grp in CITE.findall(text) for c in re.split(r"[,;]", grp)))

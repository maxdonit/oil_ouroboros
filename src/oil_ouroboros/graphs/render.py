"""
Ответ для показа: ссылки [R12; W3] → метки источников, список источников и строка проверки — на языке вопроса.

Метки строятся по шаблонам (rag/retriever.py, web/search.py, graphs/calc.py); для ответа на английском они переводятся
здесь. Модель меток не пишет: их ставит этот код по журналу доказательств, поэтому подделать метку нельзя.
"""

from __future__ import annotations

import re

from ..evidence.text import CITE

MONTHS_EN = {
    "январь": "January",
    "февраль": "February",
    "март": "March",
    "апрель": "April",
    "май": "May",
    "июнь": "June",
    "июль": "July",
    "август": "August",
    "сентябрь": "September",
    "октябрь": "October",
    "ноябрь": "November",
    "декабрь": "December",
}


def _title_en(title: str) -> str:
    """Название сорта котировки по-английски — из каталога (forecast.quote.instruments)."""
    from .. import catalog

    return {i.title: i.title_en for i in catalog.get().forecast.quote.instruments.values()}.get(title, title)


def _spot_en(tail: str | None, as_of: str | None) -> str:
    if not tail:
        return ""
    return f"; EIA spot as of {as_of}" if as_of else "; EIA spot unavailable"


LABEL_EN = [  # метки строятся по шаблонам (retriever, web/search, расчёты) — для ответа на английском переводятся они
    (
        re.compile(r"^Отчёт (.+?), (\w+) (\d{4}), с\. (.+)$"),
        lambda m: f"Report {m[1]}, {MONTHS_EN.get(m[2], m[2])} {m[3]}, p. {m[4]}",
    ),
    (
        re.compile(r"^Отчёт (.+?), (\w+) (\d{4}), раздел «(.+)»$"),
        lambda m: f"Report {m[1]}, {MONTHS_EN.get(m[2], m[2])} {m[3]}, section “{m[4]}”",
    ),
    (
        re.compile(r"^Источник: (.+?), web(, [\d-]+)?, уровень (\d)(, гос\. СМИ)?$"),
        lambda m: f"Source: {m[1]}, web{m[2] or ''}, tier {m[3]}{', state media' if m[4] else ''}",
    ),
    (
        re.compile(r"^Расчёт: прогноз (\S+) на (\d+) мес\., данные EIA по (\S+)$"),
        lambda m: f"Calculation: {m[1]} forecast, {m[2]} months, EIA data to {m[3]}",
    ),
    (
        re.compile(r"^Расчёт: сценарий (\S+) млн б/с, (\S+); запасы — оценка (.+)$"),
        lambda m: f"Calculation: scenario {m[1]} mb/d, {m[2]}; inventories — estimate by {m[3]}",
    ),
    (
        re.compile(r"^Расчёт: история цен (\S+) \(EIA\) с (\S+)$"),
        lambda m: f"Calculation: {m[1]} price history (EIA) since {m[2]}",
    ),
    (
        re.compile(
            r"^Котировка: (.+?), фьючерс (\S+) \(Мосбиржа, задержка (\d+) мин\), (\d{2})\.(\d{2})\.(\d{4})"
            r"(?: (\d{2}:\d{2}) МСК)?(, сохранённая — биржа не ответила)?"
            r"(?:; (спот EIA на (\S+)|спот EIA недоступен))?$"
        ),
        lambda m: (
            f"Quote: {_title_en(m[1])} futures {m[2]} (Moscow Exchange, {m[3]}-min delay), {m[6]}-{m[5]}-{m[4]}"
            f"{f' {m[7]} MSK' if m[7] else ''}{', saved — exchange did not respond' if m[8] else ''}"
            f"{_spot_en(m[9], m[10])}"
        ),
    ),
    (
        re.compile(
            r"^Котировка: (.+?) — биржевая котировка недоступна(?:; (спот EIA на (\S+)|спот EIA недоступен))?$"
        ),
        lambda m: f"Quote: {_title_en(m[1])} — exchange quote unavailable{_spot_en(m[2], m[3])}",
    ),
]
FOOTER = {
    "ru": {
        "sources": "**Источники**",
        "kinds": {"report": "отчёт", "web": "веб", "tool": "расчёт"},
        "missing": "источник не найден",
        "check": "_Проверка: {ok}/{total} цифр подтверждены источниками_",
        "warn": "⚠️ Не подтверждено источниками: {nums} — используйте с осторожностью.",
    },
    "en": {
        "sources": "**Sources**",
        "kinds": {"report": "report", "web": "web", "tool": "calculation"},
        "missing": "source not found",
        "check": "_Check: {ok}/{total} figures confirmed by sources_",
        "warn": "⚠️ Not confirmed by sources: {nums} — use with caution.",
    },
}


def label_in(label: str, lang: str) -> str:
    if lang != "en":
        return label
    for rx, fn in LABEL_EN:
        m = rx.match(label)
        if m:
            return fn(m)
    return label


def render_final(text: str, evidence: dict, v: dict | None, lang: str = "ru") -> str:
    """Ссылки [R12; W3] → метки источников, список источников, строка проверки; на языке
    вопроса: ответ на английском получает английские метки и подвал."""
    used: list[str] = []
    t = FOOTER["en" if lang == "en" else "ru"]

    def repl(m: re.Match) -> str:
        labels = []
        for i in (x.strip() for x in re.split(r"[,;]", m.group(1))):
            if i in evidence:
                if i not in used:
                    used.append(i)
                labels.append(label_in(evidence[i]["label"], lang))
            else:
                labels.append(f"{i}: {t['missing']}")
        return "[" + "; ".join(labels) + "]"

    body = CITE.sub(repl, text).strip()
    footer = []
    if used:
        footer.append(t["sources"])
        for i in used:
            e = evidence[i]
            link = f" — {e['url']}" if e.get("url") else ""
            footer.append(f"- [{i}] ({t['kinds'][e['kind']]}) {label_in(e['label'], lang)}{link}")
    if v and v.get("numbers_total"):  # ответ без цифр (отказ, качественный ответ) строки проверки не получает
        footer.append("\n" + t["check"].format(ok=v.get("numbers_verified", 0), total=v.get("numbers_total", 0)))
        # ссылка без источника помечена в самом тексте («источник не найден»); здесь — только цифры
        bad = {
            i["number"]
            for i in v.get("issues", [])
            if i["type"] in ("unverified_number", "weak_source") and i.get("number")
        }
        if bad:
            footer.append(t["warn"].format(nums=", ".join(sorted(bad))))
    return body + ("\n\n" + "\n".join(footer) if footer else "")

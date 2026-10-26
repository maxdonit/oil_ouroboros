"""
Числа в тексте ответа и в источниках — для проверки ответа (evidence/verify.py):

- какие числа требуют подтверждения: без лет, дат, сезонов «2025/26», порядковых, длительностей «24 часа», номеров
  пунктов, страниц и таблиц, уровней интервалов прогноза;
- прочтения числа: десятичная запятая по-русски («0,2» — 0.2; «1,234» — 1234 или 1.234), неразрывный пробел —
  разделитель тысяч; совпадение до половины последнего показанного знака («79,6» ≈ 79.62, «79,3» — нет);
- единица: объём с масштабом (тыс. / млн / млрд б/с ↔ kb/d / mb/d), цена ($), проценты, п.п. — разные единицы не
  подтверждают друг друга; «32 cents» — это $0,32; доля в результате расчёта (0.983) — это и 98,3%;
- направление: число со знаком или со словом прямо перед ним («+1,9», «выросли на 1,9») не подтверждается числом
  источника с обратным направлением («fell by 1.9», «−1.9»);
- расчёт по подтверждённым цифрам (разница, сумма объёмов, изменение в %) — derived().
"""

from __future__ import annotations

import json
import re

from .text import CITE

ISO_DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
QUARTER = re.compile(
    r"\b(?:[1-4]Q\d{2,4}|Q[1-4](?:\s?\d{2,4})?|[12]H\d{2,4}|H[12](?:\s?\d{2,4})?|[1-4]\s?кв\.?)", re.I
)
DMY = re.compile(r"\b\d{1,2}[./]\d{1,2}[./]\d{2,4}\b")
SEASON = re.compile(r"\b\d{4}/\d{2,4}\b")  # «2025/26»
ORDINAL = re.compile(r"\b\d+-(?:й|я|е|го|му|м|ти|th|st|nd|rd)\b", re.I)  # «10-й месяц»
# уровень интервала прогноза («80%-интервал», «интервалы 50/80/95%», «95% CI») — параметр метода, а не цифра данных
CONF_LEVEL = re.compile(
    r"\b(?:50|80|90|95|99)(?:\s*/\s*(?:50|80|90|95|99))*\s*%[-\s]*(?:н\w*\s+)?"
    r"(?:интервал|доверит|ди\b|interval|ci\b|pi\b)",
    re.I,
)
CONF_LEVEL_AFTER = re.compile(
    r"(?:интервал\w*|interval\w*)\s+(?:50|80|90|95|99)(?:\s*/\s*(?:50|80|90|95|99))*\s*%", re.I
)
# «14 октября», диапазон «15–21 октября» / «1–14 Oct»; английские месяцы — целым словом («15 markets» — не дата)
DAY_MONTH = re.compile(
    r"\b\d{1,2}(?:\s*[–—-]\s*\d{1,2})?\s+"
    r"(?:(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*"
    r"|(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?"
    r"|nov(?:ember)?|dec(?:ember)?)\b\.?)",
    re.I,
)
# ссылки на страницы, таблицы и рисунки («с. 33, 44», «p. 12», «Table 3.1», «рис. 2») — не цифры данных ни в источнике,
# ни в ответе. У однобуквенных сокращений точка обязательна: в «с 5,2 до 5,6 млн б/с» — данные
REF = re.compile(
    r"\b(?:(?:p|pp|с)\.|стр\.?|pages?|страниц\w*)\s*\d+(?:\s*[,–—-]\s*\d+(?![.,]?\d)(?!\s*[%$\w]))*"
    r"|\b(?:table|tab|таблиц\w*|figure|fig|рис|chart|график\w*)\.?\s*\d+(?:\.\d+)*[a-z]?",
    re.I,
)
NUM = re.compile(r"(?<![\w.,])[-+−]?(?:\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,]\d+)?")
TIME_WORD = re.compile(
    r"^\s*(?:-?х\s*)?(?:мес|месяц|лет|год|недел|дн|день|час|минут|квартал|раз|пункт|шаг"
    r"|month|year|week|day|hour|minute|quarter|time|step)",
    re.I,
)
CALC_MARK = re.compile(r"\((?:расч[её]т|calculation|calc\.?)\)", re.I)  # только явная пометка агента
DERIVED_WINDOW = 3  # расчёт ищется по подтверждённым цифрам этого и двух предыдущих предложений (пунктов)
THOUSANDS = re.compile(r"[-+]?[1-9]\d{0,2}(?:,\d{3})+")  # «1,234» в англоязычном тексте — тысячи
SPACES = (" ", " ", " ")  # разделители тысяч
# объём: «б/с» и «б/д» (баррелей в сутки / в день — оба пишут модели), «барр.»
VOL_K = re.compile(r"^\s*(?:тыс\.?\s*(?:б/[сд]|барр)|kb/?d|kbpd|kbd|k\s?b/d|thousand\s+(?:barrels|bbl|b/d))", re.I)
VOL_M = re.compile(
    r"^\s*(?:млн\.?\s*(?:б/[сд]|барр|т\b)|mb/?d|mbpd|mn\s?b|m\s?b/d|million\s+(?:barrels|bbl|b/d|tonnes))", re.I
)
VOL_B = re.compile(r"^\s*(?:млрд\.?\s*(?:барр|т\b)|billion\s+(?:barrels|bbl|tonnes))", re.I)
VOL_ANY = re.compile(r"^\s*(?:б/[сд]|barrels|bbl|b/d)", re.I)
VOL_SCALE = {"vol_k": 0.001, "vol_m": 1.0, "vol_b": 1000.0}  # в млн б/с (млн барр.)


def values(token: str, decimal_comma: bool = False) -> set[float]:
    """Прочтения числа. Запятая — десятичная («0,2», «2,6»); только «1,234» / «12,345,678» может быть ещё и
    разделителем тысяч (англоязычные отчёты). Пробелы, в том числе неразрывные, — разделители тысяч."""
    t = token.replace("−", "-")
    for sp in SPACES:
        t = t.replace(sp, "")
    if "," in t and "." in t:  # «1,234.5» (en) или «1.234,5»
        cands = {t.replace(",", "")} if t.rfind(".") > t.rfind(",") else {t.replace(".", "").replace(",", ".")}
    elif THOUSANDS.fullmatch(t):
        if decimal_comma:  # в русском тексте запятая — всегда десятичная: «1,234 тыс.» — это 1.234 тыс.
            cands = {t.replace(",", ".")} if t.count(",") == 1 else {t.replace(",", "")}
        else:
            cands = {t.replace(",", ""), t.replace(",", ".")} if t.count(",") == 1 else {t.replace(",", "")}
    else:
        cands = {t.replace(",", ".")}
    out: set[float] = set()
    for c in cands:
        try:
            out.add(float(c))
        except ValueError:
            pass
    return out


def decimals(token: str) -> int:
    m = re.search(r"[.,](\d+)$", token)
    return len(m.group(1)) if m else 0


def _unit(before: str, after: str) -> str:
    """Единица числа по соседнему тексту: vol_k | vol_m | vol_b | vol (объём нефти; масштаб — тыс. / млн / млрд или
    неизвестен) | price ($) | pct (%) | pp (п.п.) | "" (не определена)."""
    a = after.lower().lstrip()
    if a.startswith(("п.п", "п. п", "pp ", "pp.", "pp,", "percentage point", "процентн")):
        return "pp"
    if a.startswith(("%", "процент", "percent")):
        return "pct"
    if before.lower().rstrip().endswith(("$", "us$", "usd")) or a.startswith(("$", "долл", "usd", "dollar")):
        return "price"
    for rx, unit in ((VOL_K, "vol_k"), (VOL_M, "vol_m"), (VOL_B, "vol_b"), (VOL_ANY, "vol")):
        if rx.match(after):
            return unit
    return ""


def _kind(unit: str) -> str:
    return "vol" if unit.startswith("vol") else unit


RANGE_TAIL = re.compile(r"^\s*(?:[–—…-]|\.{2,3}|до|to)\s*[-+−]?\d+(?:[.,]\d+)?")  # «20…40 $», «1,2–1,5 млн б/с»
CENTS = re.compile(r"^\s*(?:cents?\b|¢)", re.I)  # «down 32 cents/MMBtu» — это $0.32


def _direction_rx(en: str, ru: str) -> re.Pattern:
    """Слово направления прямо перед числом: «fell by about», «a decline of», «снизились на», «рост на примерно»."""
    adverb = (
        r"(?:\s+(?:about|around|some|nearly|almost|roughly|more than|over"
        r"|в среднем|примерно|почти|около|более чем|свыше))?"
    )
    return re.compile(rf"(?:\b(?:{en})(?:\s+of)?(?:\s+by)?|(?:{ru})\s+на){adverb}\s*\$?\s*$", re.I)


DIR_UP = _direction_rx(
    r"ros[ea]|risen|increased|grew|grown|gained|climbed|jumped|surged|rebounded"
    r"|(?:an?\s+)?(?:increase|rise|growth|gain|build)",
    r"выр[ао]сл?[аио]?|вырастет|увеличил\w*|увеличится|повысил\w*|подня\w*|прибавил\w*"
    r"|рост\w*|прирост\w*|увеличени\w*|повышени\w*",
)
DIR_DOWN = _direction_rx(
    r"fell|fallen|declined|decreased|dropped|slumped|eased|contracted"
    r"|(?:an?\s+)?(?:decline|decrease|drop|fall|draw|withdrawal|reduction)",
    r"снизил\w*|снизится|упал\w*|упадёт|сократил\w*|сократится|уменьшил\w*|уменьшится|опустил\w*"
    r"|снижени\w*|сокращени\w*|падени\w*|уменьшени\w*",
)


def direction(token: str, before: str) -> str:
    """up | down | "": знак числа или слово направления прямо перед ним."""
    sign = token.lstrip()[:1]
    if sign and sign in "-−":
        return "down"
    if sign == "+":
        return "up"
    tail = before[-60:]
    return "up" if DIR_UP.search(tail) else "down" if DIR_DOWN.search(tail) else ""


def _scan(text: str) -> list[tuple[str, int, str]]:
    """Числа текста с единицами: (токен, позиция, единица). Начало диапазона берёт единицу его конца."""
    out = []
    for m in NUM.finditer(text):
        after = text[m.end() : m.end() + 48]
        unit = _unit(text[max(0, m.start() - 4) : m.start()], after[:24])
        if not unit and (r := RANGE_TAIL.match(after)):
            unit = _unit("", after[r.end() : r.end() + 24])
        out.append((m.group(0), m.start(), unit))
    return out


def _blank(text: str, *rxs: re.Pattern) -> str:
    for rx in rxs:
        text = rx.sub(lambda m: " " * len(m.group(0)), text)
    return text


def claim_numbers(text: str) -> list[tuple[str, int, str]]:
    """Числа, требующие подтверждения, с единицами: (токен, позиция, единица). Годы, даты, сезоны, порядковые,
    длительности, номера пунктов, страниц и таблиц и уровни интервалов исключаются."""
    clean = _blank(
        CITE.sub(" ", text), ISO_DATE, QUARTER, DMY, SEASON, ORDINAL, DAY_MONTH, CONF_LEVEL, CONF_LEVEL_AFTER, REF
    )
    out = []
    for tok, pos, unit in _scan(clean):
        vals = values(tok)
        if not vals:
            continue
        v = next(iter(vals))
        after = clean[pos + len(tok) : pos + len(tok) + 14]
        before = clean[max(0, pos - 3) : pos]
        is_int = decimals(tok) == 0 and "," not in tok and "." not in tok
        if is_int and 1900 <= abs(v) <= 2100 and not after.lstrip().startswith(("%", "$", "долл", "млн", "тыс")):
            continue  # год
        if is_int and abs(v) < 1000 and TIME_WORD.match(after):
            continue  # «3 месяца», «24 часа», «30 дней», «2 раза»
        if is_int and abs(v) <= 20 and (before.strip() == "" and re.match(r"^\s*[.)]\s", after)):
            continue  # номер пункта списка
        if (
            is_int
            and abs(v) < 10
            and not before.rstrip().endswith("$")
            and not re.match(r"^\s*(?:%|\$|млн|тыс|млрд|mb|kb|bn|долл|руб|барр)", after, re.I)
        ):
            continue  # одиночные малые целые без единиц («5 крупнейших», «в 2 этапа») — шум
        out.append((tok, pos, unit))
    return out


PRICE_KEYS = {
    "median",
    "lo50",
    "hi50",
    "lo80",
    "hi80",
    "lo95",
    "hi95",
    "price",
    "last_price",
    "base_price",
    "baseline_median",
    "usd",
}


def _key_unit(key: str) -> str:
    """Единица числа результата расчётного модуля (JSON) по имени поля."""
    k = key.lower()
    if k in PRICE_KEYS or k.endswith(("_usd", "_price")) or k.startswith("baseline_interval"):
        return "price"
    if k == "pct" or k.endswith("_pct"):
        return "pct"
    if k.endswith("_mbd"):
        return "vol_m"
    return ""


SHARE_KEYS = {"coverage", "hit_rate"}  # доли 0..1 в результате расчёта: агент пишет их в процентах


def _json_values(obj, key: str = "", share: bool = False) -> set[tuple[float, str, str]]:
    """(значение, единица, направление) полей результата расчётного модуля."""
    share = share or key in SHARE_KEYS
    if isinstance(obj, bool):
        return set()
    if isinstance(obj, (int, float)):
        v = float(obj)
        out = {(v, _key_unit(key), "down" if v < 0 else "")}
        return out | ({(v * 100, "pct", "")} if share and 0 <= v <= 1 else set())
    if isinstance(obj, str):
        return _text_values(obj)
    if isinstance(obj, dict):
        return {x for k, v in obj.items() for x in _json_values(v, k, share)}
    if isinstance(obj, list):
        return {x for v in obj for x in _json_values(v, key, share)}
    return set()


def _text_values(text: str) -> set[tuple[float, str, str]]:
    """(значение, единица, направление) чисел текста источника."""
    clean = _blank(text.replace("−", "-"), REF, ISO_DATE, DMY)
    out = set()
    for tok, pos, unit in _scan(clean):
        d = direction(tok, clean[max(0, pos - 60) : pos])
        cents = not unit and CENTS.match(clean[pos + len(tok) : pos + len(tok) + 8])
        for v in values(tok):
            if not (unit == "" and v.is_integer() and 1900 <= v <= 2100):
                out.add((v, unit, d))
            if cents:
                out.add((v / 100, "price", d))
    return out


def evidence_values(text: str) -> set[tuple[float, str, str]]:
    """Числа источника с единицами; номера страниц, таблиц, рисунков, даты и голые годы — не цифры источника.
    Результат расчётного модуля (JSON) — по полям: единицу задаёт имя поля (median → $, pct → %, *_mbd → млн б/с)."""
    s = (text or "").lstrip()
    if s.startswith(("{", "[")):
        try:
            return _json_values(json.loads(s))
        except ValueError:
            pass
    return _text_values(text or "")


def _close(a: float, s: float, d: int) -> bool:
    """Совпадение до половины последнего показанного знака."""
    return abs(abs(a) - s) <= 0.5 * 10**-d + 1e-9


def matches(
    token: str,
    evidence: set[tuple[float, str, str]],
    unit: str = "",
    *,
    strict: bool = False,
    ru: bool = False,
    direction: str = "",
) -> bool:
    """Число ответа совпадает с числом источника той же единицы и того же направления (если оно известно с обеих
    сторон). strict — обе единицы известны (пере-сопоставление ссылки по чужим источникам: число источника без единицы
    чужую цифру не подтверждает)."""
    d = decimals(token)
    direction = direction or ("up" if token.lstrip().startswith("+") else "")
    for a in values(token, ru):
        for b, bu, bd in evidence:
            if (
                (strict and not (unit and bu))
                or (unit and bu and _kind(unit) != _kind(bu))
                or (direction and bd and direction != bd)
            ):
                continue
            if unit in VOL_SCALE and bu in VOL_SCALE:
                factors: tuple[float, ...] = (VOL_SCALE[bu] / VOL_SCALE[unit],)  # оба масштаба известны
            elif _kind(unit) == "vol" and (not bu or _kind(bu) == "vol"):
                factors = (1.0, 1 / 1000, 1000.0)  # масштаб одной из сторон не указан
            else:
                factors = (1.0,)
            if any(_close(a, abs(b) * f, d) for f in factors):
                return True
    return False


def matches_reference(token: str, unit: str, values, ru: bool = False) -> bool:
    """Число ответа совпадает с эталонным значением (кейсы eval): единица эталона не задана."""
    return matches(token, {(float(v), "", "") for v in values}, unit, ru=ru)


def derived(token: str, unit: str, pool: list[tuple[float, str]], ru: bool = False) -> str:
    """Число — простой расчёт из подтверждённых цифр одной единицы: a−b; a+b — только для объёмов и безразмерных;
    изменение a к b в % — для процентов; a/b — для безразмерных. Знак числа ответа учитывается. Выражение или «»."""
    sign = -1 if token.lstrip()[:1] in "-−" else 1 if token.lstrip()[:1] == "+" else 0
    for i, (a, ua) in enumerate(pool):
        for j, (b, ub) in enumerate(pool):
            if i == j or (ua and ub and _kind(ua) != _kind(ub)):
                continue
            base = _kind(ua or ub)
            cands = []
            if not unit or not base or _kind(unit) == base:
                cands.append((a - b, f"{a:g}−{b:g}"))
                if base in ("vol", ""):
                    cands.append((a + b, f"{a:g}+{b:g}"))
            if b and unit == "pct" and base != "pct":
                cands.append(((a / b - 1) * 100, f"({a:g}/{b:g}−1)·100%"))
            if b and not unit and not base:
                cands.append((a / b, f"{a:g}/{b:g}"))
            for val, expr in cands:
                if (
                    val
                    and (not sign or (val > 0) == (sign > 0))
                    and _close(next(iter(values(token, ru))), abs(val), decimals(token))
                ):
                    return expr
    return ""

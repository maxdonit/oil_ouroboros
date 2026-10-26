"""
Фрагменты (чанки) отчёта для поиска.

Решения:
- **Элементный чанкинг**, а не фиксированные окна: чанк не пересекает границу страницы (цитата всегда указывает
  точную страницу), таблица — отдельный чанк целиком (большие режутся по строкам с повтором шапки). Элементный
  чанкинг точнее фиксированного на финансовых отчётах (arXiv 2402.05131).
- **Контекстный префикс** «организация, отчёт, выпуск, раздел, страница» добавляется к тексту перед векторизацией:
  без него соседние ежемесячные выпуски почти неотличимы по смыслу (Anthropic Contextual Retrieval: −35% отказов
  поиска уже от контекстных эмбеддингов). Префикс шаблонный (бесплатно).
- **Таблица — с подписью и без выравнивания.** Подпись («Table 3a. … Inventories», в ней и единицы) — первая строка
  каждого фрагмента таблицы: без неё строки цифр не находятся по смыслу, а отбор и агент не знают, что за таблица.
  Пробелы-заполнители и точки markdown-экспорта Docling убираются: они раздували таблицу вдвое, и отбор видел меньше
  строк.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass

from ..reports.editions import Edition
from .parse import Element


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    org: str
    report: str
    title: str
    edition: str
    published: str
    url: str
    page: int | None
    section: str
    kind: str
    text: str
    prefix: str
    words: int = 0

    @property
    def embed_text(self) -> str:
        return f"{self.prefix}\n{self.text}"

    def payload(self) -> dict:
        return asdict(self)


def _words(text: str) -> int:
    return len(text.split())


TABLE_TITLE = re.compile(r"^(?:Table|Таблица)\s+\d+")  # «Table 3a. World Petroleum…», «Table 1 - 1: OPEC Reference…»
TABLE_FILL = re.compile(r"\.{3,}|…{2,}")  # точки-заполнители в названиях строк: «World total ..........»


def compact_table(md: str) -> str:
    """Таблица без выравнивания: без пробелов-заполнителей, точек в названиях строк и строк-разделителей шапки."""
    rows = (re.sub(r"[ \t]{2,}", " ", TABLE_FILL.sub(" ", r)).strip() for r in md.split("\n"))
    return "\n".join(r for r in rows if r and not re.fullmatch(r"[|:\s-]+", r))


def table_caption(elements: list[Element], i: int) -> str:
    """Подпись таблицы elements[i], если её нет в самом тексте таблицы (у OPEC Docling кладёт её первой строкой):
    заголовок раздела «Table 3a. …» или строка «Table 4b. …» перед таблицей на той же странице."""
    el = elements[i]
    if TABLE_TITLE.match(el.text):
        return ""
    if TABLE_TITLE.match(el.heading):
        return el.heading
    return next(
        (p.text[:240] for p in elements[max(0, i - 3) : i][::-1] if p.page == el.page and TABLE_TITLE.match(p.text)),
        "",
    )


def chunk_elements(meta: Edition, elements: list[Element], chunk_tokens: int = 450) -> list[Chunk]:
    """Элементы → чанки: не пересекают страницу/раздел; таблица — отдельный чанк; целевой размер ≈ chunk_tokens."""
    target_words = max(int(chunk_tokens / 1.3), 80)
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_page: int | None = None
    buf_heading = ""

    def emit(text: str, page: int | None, heading: str, kind: str) -> None:
        text = text.strip()
        if _words(text) < 8:
            return
        n = len(chunks)
        cid = hashlib.sha1(f"{meta.doc_id}|{page}|{n}|{text[:80]}".encode()).hexdigest()[:16]
        loc = f"стр. {page}" if page else f"раздел «{heading[:80]}»"
        prefix = (
            f"{meta.org} {meta.report} ({meta.title}), выпуск {meta.edition_ru}, {meta.published_ru}; "
            f"раздел: {heading or '—'}; {loc}."
        )
        chunks.append(
            Chunk(
                chunk_id=cid,
                doc_id=meta.doc_id,
                org=meta.org,
                report=meta.report,
                title=meta.title,
                edition=meta.edition,
                published=meta.published,
                url=meta.url,
                page=page,
                section=heading,
                kind=kind,
                text=text,
                prefix=prefix,
                words=_words(text),
            )
        )

    def flush() -> None:
        nonlocal buf
        if buf:
            emit("\n".join(buf), buf_page, buf_heading, "text")
        buf = []

    for i, el in enumerate(elements):
        boundary = el.page != buf_page if el.page is not None else el.heading != buf_heading
        if boundary:
            flush()
            buf_page, buf_heading = el.page, el.heading
        if el.kind == "heading":
            flush()
            buf_heading = el.heading
            buf = [el.text]
            continue
        if el.kind == "table":
            flush()
            caption = table_caption(elements, i)
            rows = ([caption] if caption else []) + compact_table(el.text).split("\n")
            titled = bool(TABLE_TITLE.match(rows[0]))
            section = rows[0][:160] if titled else el.heading  # раздел таблицы — её подпись
            words = sum(_words(r) for r in rows)
            if words <= target_words * 2 or len(rows) < 4:
                emit("\n".join(rows), el.page, section, "table")
            else:  # большая таблица — группы строк с повтором подписи и шапки
                head, body = rows[: 1 + titled], rows[1 + titled :]
                step = max(len(body) // max(words // target_words, 1), 3)
                for k in range(0, len(body), step):
                    emit("\n".join(head + body[k : k + step]), el.page, section, "table")
            continue
        for piece in _split_long(el.text, target_words):
            if buf and sum(_words(x) for x in buf) + _words(piece) > target_words:
                flush()
                buf_page, buf_heading = el.page, el.heading
            buf.append(piece)
        buf_heading = el.heading or buf_heading
    flush()
    return chunks


def _split_long(text: str, target_words: int) -> list[str]:
    """Длинный элемент (например, страница из pypdf) режется по границам предложений на куски ≈ target_words."""
    if _words(text) <= int(target_words * 1.5):
        return [text]
    parts, cur = [], []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        cur.append(sent)
        if sum(_words(x) for x in cur) >= target_words:
            parts.append(" ".join(cur))
            cur = []
    if cur:
        parts.append(" ".join(cur))
    return parts

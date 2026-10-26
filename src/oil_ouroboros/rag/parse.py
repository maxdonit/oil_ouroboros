"""
Разбор отчёта в элементы со страницами: PDF — Docling (MIT; номер страницы и структура таблиц), без него — постранично
pypdf (быстрый офлайн-режим); HTML-страница отчёта (IEA OMR) — основной текст до «Previous editions».

MinerU точнее Docling на CPU, но его лицензия ограничивает крупные компании (порог выручки) — для нефтяного заказчика
это риск.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Element:
    page: int | None
    kind: str  # text | table | heading | list
    text: str
    heading: str = ""


def parse_pdf(path: Path, prefer: str = "auto") -> tuple[list[Element], str]:
    """PDF → элементы со страницами. Возвращает (элементы, имя парсера)."""
    if prefer in ("auto", "docling"):
        try:
            return _parse_pdf_docling(path), "docling"
        except ImportError:
            if prefer == "docling":
                raise
            log.info("docling не установлен — pypdf")
    return _parse_pdf_pypdf(path), "pypdf"


def _parse_pdf_docling(path: Path) -> list[Element]:
    import os

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import DocItemLabel, TableItem

    opts = PdfPipelineOptions()
    opts.do_ocr = False  # отчёты агентств «рождены цифровыми»: OCR только замедляет
    opts.do_table_structure = True
    # FAST-режим TableFormer: на страницах STEO (в основном графики) ACCURATE давал ~15 с/стр. на CPU
    opts.table_structure_options.mode = TableFormerMode[os.environ.get("DOCLING_TABLE_MODE", "FAST")]
    try:
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions

        opts.accelerator_options = AcceleratorOptions(
            num_threads=int(os.environ.get("DOCLING_THREADS") or os.environ.get("OMP_NUM_THREADS") or 4),
            device=AcceleratorDevice.CPU,
        )
    except ImportError:
        pass
    conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    doc = conv.convert(str(path)).document
    skip = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER, DocItemLabel.PICTURE}
    out: list[Element] = []
    heading = ""
    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        if label in skip:
            continue
        prov = getattr(item, "prov", None) or []
        page = prov[0].page_no if prov else None
        if isinstance(item, TableItem):
            md = item.export_to_markdown(doc=doc).strip()
            if md:
                out.append(Element(page, "table", md, heading))
            continue
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        if label in (DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE):
            heading = text[:160]
            out.append(Element(page, "heading", text, heading))
        elif label == DocItemLabel.LIST_ITEM:
            out.append(Element(page, "list", text, heading))
        else:
            out.append(Element(page, "text", text, heading))
    return out


def _parse_pdf_pypdf(path: Path) -> list[Element]:
    from pypdf import PdfReader

    out: list[Element] = []
    for i, page in enumerate(PdfReader(str(path)).pages, start=1):
        text = page.extract_text() or ""
        text = re.sub(r"[ \t]+\n", "\n", text)
        for para in re.split(r"\n\s*\n", text):
            para = re.sub(r"\s+", " ", para).strip()
            if len(para) > 20:
                out.append(Element(i, "text", para, ""))
    return out


def parse_html(path: Path) -> list[Element]:
    """Публичная HTML-страница отчёта (IEA OMR): основной текст между заголовком и «Previous editions»."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    for t in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        t.decompose()
    root = soup.find("main") or soup.body or soup
    out: list[Element] = []
    heading = ""
    seen: set[str] = set()
    for node in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "table"]):
        text = node.get_text(" ", strip=True)
        if not text:
            continue
        key = re.sub(r"\s+", " ", text).lower()
        if key in seen:  # <li><p>…</p></li> дают один текст дважды
            continue
        seen.add(key)
        if node.name in ("h1", "h2", "h3", "h4"):
            if text.lower().startswith(("previous editions", "newsletters", "podcasts", "related")):
                break
            heading = text[:160]
            out.append(Element(None, "heading", text, heading))
        elif node.name == "table":
            rows = [
                " | ".join(c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"]))
                for tr in node.find_all("tr")
            ]
            out.append(Element(None, "table", "\n".join(r for r in rows if r), heading))
        elif node.name == "li" and node.find_parent(["nav", "footer"]) is None and len(text) > 30:
            out.append(Element(None, "list", text, heading))
        elif node.name == "p" and len(text) > 30:
            out.append(Element(None, "text", text, heading))
    return out

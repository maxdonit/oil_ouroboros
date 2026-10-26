"""
Выпуски отчётов в базе: курируемые (data/reports/catalog.yaml, файлы в git) и пришедшие позже — от наблюдателя или
ручной загрузки (<каталог выпусков>/catalog.yaml, общий том). Отсюда берут список выпусков индексация, поиск
(агентства и последний выпуск серии), наблюдатель и вкладка «Источники».
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import get_settings

MONTHS_RU = {
    "01": "январь",
    "02": "февраль",
    "03": "март",
    "04": "апрель",
    "05": "май",
    "06": "июнь",
    "07": "июль",
    "08": "август",
    "09": "сентябрь",
    "10": "октябрь",
    "11": "ноябрь",
    "12": "декабрь",
}


def doc_id(org: str, report: str, edition: str) -> str:
    """Id выпуска: opec-momr-2026-10."""
    return f"{org}-{report}-{edition}".lower()


@dataclass(frozen=True)
class Edition:
    file: str
    org: str
    report: str
    title: str
    edition: str
    published: str
    url: str
    download: str = ""
    license: str = ""
    source: str = "catalog"  # catalog — курируемый (data/reports) | watch — наблюдатель | upload — загружен вручную
    sha256: str = ""
    fetched: str = ""
    # откуда дата публикации: schedule — график серии | document — из документа | fetched — дата скачивания;
    # "" — курируемый выпуск
    published_by: str = ""
    dir: str = ""  # каталог файла; заполняет load()

    @property
    def path(self) -> Path:
        return Path(self.dir) / self.file

    @property
    def doc_id(self) -> str:
        return doc_id(self.org, self.report, self.edition)

    @property
    def edition_ru(self) -> str:
        y, m = self.edition.split("-")
        return f"{MONTHS_RU.get(m, m)} {y}"

    @property
    def published_ru(self) -> str:
        if self.published_by == "fetched":
            return f"скачан {self.published} (дата публикации неизвестна)"
        return f"опубликован {self.published}"


def auto_dir() -> Path:
    """Каталог выпусков наблюдателя и ручных загрузок (общий том tools и encoder)."""
    s = get_settings()
    return Path(s.reports_auto_dir) if s.reports_auto_dir else s.var_dir / "reports"


_FIELDS = set(Edition.__dataclass_fields__) - {"dir"}


def load(reports_dir: Path | None = None) -> list[Edition]:
    """Все выпуски: курируемые data/reports/catalog.yaml + наблюдателя и ручных загрузок (<auto>/catalog.yaml).

    Выпуск (doc_id) берётся из первого источника, где есть его файл: курируемый главнее, но запись без файла
    уступает скачанной."""
    out: dict[str, Edition] = {}
    for d in (reports_dir or get_settings().data_dir / "reports", auto_dir()):
        f = d / "catalog.yaml"
        if not f.is_file():
            continue
        for x in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("documents") or []:
            m = Edition(**{k: str(v) for k, v in x.items() if k in _FIELDS}, dir=str(d))
            if m.doc_id not in out or (not out[m.doc_id].path.is_file() and m.path.is_file()):
                out[m.doc_id] = m
    return list(out.values())


def orgs(editions: list[Edition] | None = None) -> list[str]:
    """Агентства, чьи отчёты есть в базе (файл на месте). Фильтр поиска и подсказки планировщику берут их отсюда:
    новое агентство — это файл и запись в каталоге, без правки кода."""
    return sorted({m.org.upper() for m in (load() if editions is None else editions) if m.path.is_file()})


def latest(editions: list[Edition], exclude: set[str] | frozenset = frozenset()) -> dict[str, Edition]:
    """Последний доступный выпуск каждой серии «ORG REPORT»: файл есть и он не ждёт индексации (exclude)."""
    out: dict[str, Edition] = {}
    for d in editions:
        if d.path.is_file() and d.doc_id not in exclude:
            key = f"{d.org} {d.report}"
            if key not in out or d.edition > out[key].edition:
                out[key] = d
    return out


def save_auto(entry: dict) -> None:
    """Записать выпуск наблюдателя или ручной загрузки в <каталог выпусков>/catalog.yaml (тот же выпуск заменяется)."""
    cat = auto_dir() / "catalog.yaml"
    docs = (yaml.safe_load(cat.read_text(encoding="utf-8")) or {}).get("documents") or [] if cat.is_file() else []
    key = (entry["org"], entry["report"], entry["edition"])
    docs = [x for x in docs if (x.get("org"), x.get("report"), x.get("edition")) != key] + [entry]
    docs.sort(key=lambda x: (x["org"], x["report"], x["edition"]))
    cat.write_text(
        "# Выпуски, скачанные наблюдателем или загруженные вручную (src/oil_ouroboros/reports/). Не править руками.\n"
        + yaml.safe_dump({"documents": docs}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

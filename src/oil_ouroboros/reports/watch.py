"""
Наблюдатель за выпусками отраслевых отчётов: график → проверка сайта → скачивание → проверка обложки → список
выпусков (reports/editions.py) → очередь индексации (reports/queue.py). Каждый шаг — в журнал (reports/journal.py).

Правила серий — config/current/reports.yaml (шаблоны прямых ссылок, график публикаций, сколько выпусков держать).

Решения:
- **Честный доступ.** Запрос подписан User-Agent проекта. Ответ с проверкой от ботов (заголовок
  `cf-mitigated: challenge` или страница «Just a moment») записывается как «закрыто проверкой» и не обходится:
  такой выпуск загружается вручную (`oilo reports --upload файл.pdf`), серия и выпуск определяются по обложке.
- **Проверка содержимого.** Ссылка «последний выпуск» и непостоянные имена файлов могут отдать не тот месяц:
  файл принимается, только если на первых страницах есть «{Month} {yyyy}» нужного выпуска.
- **Индекс не меняется во время замера eval.** Кейсы одного прогона должны идти на одной базе знаний: скачанный
  выпуск ждёт в очереди (reports/queue.py) и индексируется после прогона (freeze.py).
- Ничего не удаляется: старые выпуски остаются в базе, приоритет свежего даёт надбавка последнему выпуску.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
import time
from datetime import date
from pathlib import Path

import httpx

from .. import net
from ..config import get_settings
from . import editions, journal
from .queue import index_pending, pending

log = logging.getLogger(__name__)

UA = {"User-Agent": "oil-ouroboros-report-watch/1.0 (+https://github.com/maxdonit/oil_ouroboros)"}
MONTHS_EN = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
STATUS_RU = {
    "present": "в базе",
    "indexed": "в индексе",
    "downloaded": "скачан",
    "uploaded": "загружен вручную",
    "queued": "ждёт индекса",
    "scheduled": "ещё не вышел",
    "not_found": "нет по ссылкам",
    "blocked": "закрыто проверкой от ботов",
    "wrong_edition": "по ссылке другой выпуск",
    "error": "ошибка",
    "started": "проверка начата",
    "done": "проверка завершена",
}
MAX_BYTES = 80 * 1024 * 1024

_lock = threading.Lock()
_run = {"active": False, "started": 0.0, "trigger": ""}
_sched = {"next": 0.0, "queue_retry": 0.0}


def _acquire() -> bool:
    """Одна проверка/индексация за раз: в процессе (консоль + расписание) и между процессами (CLI в контейнере)."""
    if not _lock.acquire(blocking=False):
        return False
    try:
        import fcntl
    except ImportError:  # Windows (разработка): только блокировка процесса
        return True
    f = (_dir() / ".lock").open("w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        _lock.release()
        return False
    _run["lockfile"] = f
    return True


def _release() -> None:
    f = _run.pop("lockfile", None)
    if f:
        f.close()
    _lock.release()


def _dir() -> Path:
    d = editions.auto_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def sources() -> list[dict]:
    """Правила серий — config/current/reports.yaml (каталог поведения)."""
    from .. import catalog

    return [s.model_dump() for s in catalog.get().reports.sources]


# ---------------------------------------------------------------------------------------------------------------
# выпуски и ссылки
# ---------------------------------------------------------------------------------------------------------------


def edition_subst(edition: str) -> dict[str, str]:
    y, m = edition.split("-")
    name = MONTHS_EN[int(m) - 1]
    return {"yyyy": y, "yy": y[2:], "mm": m, "month": name, "mon": name[:3], "Month": name.capitalize()}


def targets(src: dict, today: date) -> list[str]:
    """Последние `lookback` месячных выпусков, которые должны быть в базе (новые первыми)."""
    out, y, m = [], today.year, today.month
    for _ in range(int(src.get("lookback") or 2)):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


# страница IEA: «Published» + <time datetime>
PUBLISHED = re.compile(r'Published.{0,300}?datetime="(\d{4}-\d\d-\d\d)', re.S)


def detect_published(kind: str, body: bytes) -> str:
    """Дата публикации из самого документа ('' — не нашлась). У страниц IEA она в метке «Published»; первое
    «14 October 2026» на странице брать нельзя — это может быть карточка соседнего выпуска."""
    if kind != "html":
        return ""
    m = PUBLISHED.search(body.decode("utf-8", "ignore"))
    return m.group(1) if m else ""


def next_release(src: dict, today: date) -> tuple[str, str] | None:
    """Ближайший выпуск: по официальному графику, а без него — «не раньше» обычного числа выхода (earliest_day)."""
    later = sorted((ed, d) for ed, d in (src.get("schedule") or {}).items() if str(d) > today.isoformat())
    if later:
        return later[0][0], str(later[0][1])
    day = int(src.get("earliest_day") or 1)
    if day <= 1:
        return None
    y, m = (today.year, today.month) if today.day < day else (today.year + today.month // 12, today.month % 12 + 1)
    return f"{y:04d}-{m:02d}", f"не раньше {y:04d}-{m:02d}-{day:02d}"


def _not_yet(src: dict, edition: str, today: date) -> str:
    """Дата, раньше которой выпуск не выходит ('' — уже мог выйти): по графику или по earliest_day текущего месяца."""
    pub = str((src.get("schedule") or {}).get(edition) or "")
    if pub:
        return pub if pub > today.isoformat() else ""
    day = int(src.get("earliest_day") or 1)
    return f"{edition}-{day:02d}" if edition == f"{today.year:04d}-{today.month:02d}" and today.day < day else ""


def _text_head(kind: str, body: bytes, pages: int = 3) -> str:
    if kind == "html":
        return re.sub(r"<[^>]+>", " ", body[:400_000].decode("utf-8", "ignore"))
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(body))
    return " ".join((p.extract_text() or "") for p in reader.pages[:pages])


def detect_edition(text: str) -> str | None:
    """Самый частый «Month yyyy» в тексте обложки → «yyyy-mm»."""
    counts: dict[str, int] = {}
    for mon, y in re.findall(r"\b(" + "|".join(MONTHS_EN) + r")\s+(20\d\d)\b", text, flags=re.I):
        ed = f"{y}-{MONTHS_EN.index(mon.lower()) + 1:02d}"
        counts[ed] = counts.get(ed, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


def detect_series(text: str) -> dict | None:
    low = text.lower()
    for src in sources():
        if src.get("detect") and all(k.lower() in low for k in src["detect"]):
            return src
    return None


def fetch(client: httpx.Client, url: str) -> tuple[str, int, bytes | None, str]:
    """(status, http, тело, пояснение); status: ok | blocked | not_found | error."""
    try:
        r = client.get(url)
    except httpx.HTTPError as exc:
        return "error", 0, None, f"{type(exc).__name__}: {exc}"[:200]
    body = r.content
    if r.headers.get("cf-mitigated") == "challenge" or (
        r.status_code in (403, 503) and b"Just a moment" in body[:6000]
    ):
        return "blocked", r.status_code, None, "Cloudflare: проверка «вы не бот» — не обходится, нужна ручная загрузка"
    if r.status_code in (403, 404, 410):
        return "not_found", r.status_code, None, f"HTTP {r.status_code}"
    if r.status_code != 200:
        return "error", r.status_code, None, f"HTTP {r.status_code}"
    if len(body) > MAX_BYTES:
        return "error", 200, None, f"файл больше {MAX_BYTES // 2**20} МБ"
    return "ok", 200, body, ""


# ---------------------------------------------------------------------------------------------------------------
# каталог выпусков наблюдателя
# ---------------------------------------------------------------------------------------------------------------


def _save(src: dict, edition: str, kind: str, body: bytes, source: str, download: str) -> dict:
    d = _dir()
    file = f"{src['org']}_{src['report']}_{edition}.{'pdf' if kind == 'pdf' else 'html'}".lower()
    tmp = d / (file + ".part")
    tmp.write_bytes(body)
    tmp.replace(d / file)
    pub, by = str((src.get("schedule") or {}).get(edition) or ""), "schedule"
    if not pub:
        pub, by = detect_published(kind, body), "document"
    if not pub:  # даты нет ни в графике, ни в документе: выпуск скачан вскоре после выхода (проверка каждые N ч)
        pub, by = date.today().isoformat(), "fetched"
    entry = {
        "file": file,
        "org": src["org"],
        "report": src["report"],
        "title": src.get("title", ""),
        "edition": edition,
        "published": pub,
        "published_by": by,
        "url": src.get("page", ""),
        "download": download,
        "license": src.get("license", ""),
        "source": source,
        "sha256": hashlib.sha256(body).hexdigest(),
        "fetched": journal.now(),
    }
    editions.save_auto(entry)
    return entry


def _try(client: httpx.Client, src: dict, edition: str, newest: bool, run: str) -> dict:
    kind = src.get("kind", "pdf")
    sub = edition_subst(edition)
    urls = [u.format(**sub) for u in src.get("urls") or []]
    if newest and src.get("latest_url"):
        urls.append(src["latest_url"])
    seen: list[str] = []
    for url in urls:
        t0 = time.monotonic()
        status, http, body, note = fetch(client, url)
        rec = {"run": run, "action": "fetch", "series": src["series"], "edition": edition, "url": url, "http": http}
        if status == "ok" and body is not None:
            want = f"{sub['Month']} {sub['yyyy']}"
            try:
                head = _text_head(kind, body)
            except Exception as exc:  # noqa: BLE001 — битый PDF
                status, note = "error", f"не читается: {type(exc).__name__}"
            else:
                if kind == "pdf" and not body.startswith(b"%PDF"):
                    status, note = "error", "ответ не PDF"
                else:
                    # нужный месяц — в заголовке (начало текста) или самый частый на первых страницах; упоминание
                    # «следующий выпуск — {Month}» в прошлом выпуске так не пройдёт
                    found = detect_edition(head)
                    if not (want.lower() in head[:1500].lower() or found == edition):
                        status, note = "wrong_edition", f"на обложке не «{want}» (найдено: {found or '—'})"
        rec |= {"status": status, "note": note, "ms": int((time.monotonic() - t0) * 1000)}
        if status == "ok" and body is not None:
            entry = _save(src, edition, kind, body, "watch", url)
            journal.add(
                rec
                | {
                    "action": "save",
                    "status": "downloaded",
                    "file": entry["file"],
                    "bytes": len(body),
                    "sha256": entry["sha256"],
                }
            )
            return {"status": "downloaded", "note": f"{len(body) // 1024} КБ с {url}"}
        journal.add(rec)
        seen.append(status)
    final = next((x for x in ("blocked", "wrong_edition", "error") if x in seen), "not_found")
    notes = {
        "blocked": "сайт закрыт проверкой от ботов — загрузите PDF вручную",
        "not_found": "по известным ссылкам файла нет (возможно, ещё не опубликован или сменилось имя)",
        "wrong_edition": "по ссылкам лежит другой выпуск",
        "error": "ошибки при скачивании — см. журнал",
    }
    return {"status": final, "note": notes[final]}


# ---------------------------------------------------------------------------------------------------------------
# проверка, ручная загрузка, статус
# ---------------------------------------------------------------------------------------------------------------


def check(trigger: str, index: bool | None = None) -> dict:
    """Одна проверка всех серий. Возвращает итог по выпускам; параллельный запуск отклоняется."""
    if not _acquire():
        return {"status": "busy", "note": "проверка или индексация уже идёт"}
    _run.update(active=True, started=time.time(), trigger=trigger)
    try:
        s = get_settings()
        index = s.reports_watch_ingest if index is None else index
        run = f"w{int(time.time())}"
        today = date.today()
        have = {m.doc_id: m for m in editions.load() if m.path.is_file()}
        items = []
        journal.add({"run": run, "action": "check", "status": "started", "trigger": trigger})
        with net.outbound(120, headers=UA, follow_redirects=True) as client:
            for src in sources():
                for i, ed in enumerate(targets(src, today)):
                    doc_id = editions.doc_id(src["org"], src["report"], ed)
                    item = {"series": src["series"], "edition": ed, "doc_id": doc_id}
                    pub = _not_yet(src, ed, today)
                    if doc_id in have:
                        item |= {"status": "present", "note": have[doc_id].file}
                    elif pub:
                        item |= {"status": "scheduled", "note": f"выходит не раньше {pub}"}
                    else:
                        item |= _try(client, src, ed, newest=i == 0, run=run)
                    items.append(item)
        idx = index_pending(run, trigger) if index else {"indexed": [], "queued": pending()}
        summary = {"run": run, "trigger": trigger, "finished": journal.now(), "items": items, "index": idx}
        journal.save_check(summary)
        journal.add(
            {
                "run": run,
                "action": "check",
                "status": "done",
                "trigger": trigger,
                "note": ", ".join(
                    f"{x['series']} {x['edition']}: {STATUS_RU.get(x['status'], x['status'])}" for x in items
                ),
            }
        )
        return summary
    finally:
        _run.update(active=False)
        _release()


def index_now(trigger: str = "cli") -> dict:
    """Проиндексировать очередь сейчас (после ручной загрузки): под той же блокировкой, что проверка сайтов."""
    if not _acquire():
        return {"status": "busy", "note": "идёт проверка или индексация — очередь проиндексирует она"}
    try:
        return index_pending(trigger=trigger)
    finally:
        _release()


def upload(body: bytes, filename: str = "", series: str = "", edition: str = "") -> dict:
    """Ручная загрузка PDF (для выпусков за проверкой от ботов): серия и выпуск — из параметров или по обложке."""
    if not _acquire():  # каталог выпусков пишет и проверка — не одновременно
        raise ValueError("идёт проверка или индексация выпусков — повторите через минуту")
    try:
        return _upload(body, filename, series, edition)
    finally:
        _release()


def _upload(body: bytes, filename: str, series: str, edition: str) -> dict:
    if not body.startswith(b"%PDF"):
        raise ValueError("это не PDF")
    if len(body) > MAX_BYTES:
        raise ValueError(f"файл больше {MAX_BYTES // 2**20} МБ")
    head = _text_head("pdf", body)
    src = (
        next((x for x in sources() if x["series"].lower() == series.lower()), None) if series else detect_series(head)
    )
    if not src:
        raise ValueError("не удалось определить серию по обложке — укажите её явно")
    edition = edition or detect_edition(head) or ""
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", edition):
        raise ValueError("не удалось определить выпуск (месяц) по обложке — укажите его явно, ГГГГ-ММ")
    entry = _save(src, edition, "pdf", body, "upload", f"загружен вручную: {filename or 'файл'}")
    journal.add(
        {
            "action": "upload",
            "series": src["series"],
            "edition": edition,
            "status": "uploaded",
            "file": entry["file"],
            "bytes": len(body),
            "sha256": entry["sha256"],
            "note": filename,
        }
    )
    return {
        "series": src["series"],
        "edition": edition,
        "doc_id": editions.doc_id(src["org"], src["report"], edition),
        **entry,
    }


def status() -> dict:
    """Сводка для вкладки «Источники»: серии и их график, выпуски в базе и в индексе, итог последней проверки,
    журнал."""
    from ..rag.ingest import index_state

    s = get_settings()
    today = date.today()
    last = journal.last_check() or None
    idx = (index_state().get(s.qdrant_collection) or {}).get("docs") or {}
    catalog = [
        {
            "doc_id": m.doc_id,
            "series": f"{m.org} {m.report}",
            "title": m.title,
            "edition": m.edition,
            "published": m.published,
            "published_by": m.published_by,
            "file": m.file,
            "source": m.source,
            "license": m.license,
            "url": m.url,
            "present": m.path.is_file(),
            "chunks": (idx.get(m.doc_id) or {}).get("chunks"),
            "sha256": m.sha256[:12],
            "fetched": m.fetched,
            "download": m.download,
        }
        for m in sorted(editions.load(), key=lambda m: (m.org, m.report, m.edition), reverse=True)
    ]
    series = []
    for src in sources():
        nxt = next_release(src, today)
        series.append(
            {
                "series": src["series"],
                "org": src["org"],
                "title": src.get("title", ""),
                "kind": src.get("kind", ""),
                "license": src.get("license", ""),
                "page": src.get("page"),
                "lookback": src.get("lookback", 2),
                "targets": targets(src, today),
                "next_release": {"edition": nxt[0], "date": nxt[1]} if nxt else None,
            }
        )
    return {
        "running": {k: v for k, v in _run.items() if k != "lockfile"},
        "every_hours": s.reports_watch_hours,
        "next_run": _sched["next"],
        "auto_index": s.reports_watch_ingest,
        "dir": str(_dir()),
        "series": series,
        "last": last,
        "pending": pending(),
        "catalog": catalog,
        "journal": journal.read(80),
        "statuses": STATUS_RU,
    }


def start_scheduler() -> None:
    """Проверка сайтов агентств: первая — через 2 мин после старта (недостающие выпуски, reports_fetch_on_start),
    дальше каждые reports_watch_hours ч; повтор индексации очереди — раз в 10 мин, пока идёт замер eval."""
    s = get_settings()
    hours = s.reports_watch_hours
    if hours <= 0 and not s.reports_fetch_on_start:
        return
    _sched["next"] = time.time() + (120 if s.reports_fetch_on_start else hours * 3600)

    def loop() -> None:
        trigger = "startup"
        while True:
            time.sleep(60)
            try:
                now = time.time()
                if _sched["next"] is not None and now >= _sched["next"]:
                    _sched["next"] = now + hours * 3600 if hours > 0 else None  # 0 — только проверка при старте
                    check(trigger=trigger)
                    trigger = "schedule"
                elif (
                    get_settings().reports_watch_ingest and now >= _sched["queue_retry"] and pending()
                ):  # без автоиндекса — не трогаем
                    _sched["queue_retry"] = now + 600
                    if not _run["active"] and _acquire():
                        _run.update(active=True, started=time.time(), trigger="queue")
                        try:
                            index_pending(trigger="queue")
                        finally:
                            _run.update(active=False)
                            _release()
            except Exception:  # noqa: BLE001 — наблюдатель не должен падать
                log.exception("наблюдатель отчётов")

    threading.Thread(target=loop, daemon=True, name="reports-watch").start()

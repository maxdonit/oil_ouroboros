"""Наблюдатель за выпусками: классификация ответов сайтов, проверка обложки, каталог, журнал — без сети."""

from datetime import date

import httpx
import pytest

from oil_ouroboros import freeze
from oil_ouroboros.reports import editions, journal, queue, watch


@pytest.fixture()
def auto_dir(tmp_path, monkeypatch):
    """Отдельный каталог выпусков: тест не трогает общий индекс сессии."""
    monkeypatch.setattr(editions, "auto_dir", lambda: tmp_path)
    return tmp_path


def _client(routes: dict[str, httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: routes.get(str(req.url), httpx.Response(404))))


def test_fetch_classifies_cloudflare_missing_and_ok():
    c = _client(
        {
            "https://x/cf": httpx.Response(
                403, headers={"cf-mitigated": "challenge"}, text="<title>Just a moment...</title>"
            ),
            "https://x/cf-page": httpx.Response(503, text="<html>Just a moment...</html>"),
            "https://x/forbidden": httpx.Response(403, text="nope"),
            "https://x/ok": httpx.Response(200, content=b"%PDF-1.7 ..."),
        }
    )
    assert watch.fetch(c, "https://x/cf")[0] == "blocked"
    assert watch.fetch(c, "https://x/cf-page")[0] == "blocked"
    assert watch.fetch(c, "https://x/forbidden")[0] == "not_found"
    assert watch.fetch(c, "https://x/none")[0] == "not_found"
    status, http, body, _ = watch.fetch(c, "https://x/ok")
    assert (status, http, body[:4]) == ("ok", 200, b"%PDF")


def test_edition_and_series_detection():
    assert (
        watch.detect_edition("OPEC Monthly Oil Market Report 13 October 2026 ... October 2026 ... September 2026")
        == "2026-10"
    )
    assert watch.detect_edition("no dates here") is None
    assert watch.detect_series("OPEC Monthly Oil Market Report")["series"] == "OPEC MOMR"
    assert watch.detect_series("Short-Term Energy Outlook, EIA")["series"] == "EIA STEO"


def test_targets_and_schedule():
    opec = next(s for s in watch.sources() if s["series"] == "OPEC MOMR")
    assert watch.targets(opec, date(2026, 1, 5)) == ["2026-01", "2025-12"]
    assert watch.next_release(opec, date(2026, 10, 20)) == ("2026-11", "2026-11-11")
    assert watch.edition_subst("2026-09") | {} == {
        "yyyy": "2026",
        "yy": "26",
        "mm": "09",
        "month": "september",
        "mon": "sep",
        "Month": "September",
    }


def test_download_checks_cover_and_journals(auto_dir, monkeypatch):
    """Ссылка «последний выпуск» отдала октябрь вместо ноября — файл не принят; прямая ссылка — принят."""
    covers = {
        b"%PDF-oct": "OPEC Monthly Oil Market Report October 2026",
        b"%PDF-nov": "OPEC Monthly Oil Market Report November 2026",
    }
    monkeypatch.setattr(watch, "_text_head", lambda kind, body, pages=3: covers[body])
    opec = next(s for s in watch.sources() if s["series"] == "OPEC MOMR")
    src = opec | {"urls": ["https://o/momr-{month}-{yyyy}.pdf"], "latest_url": "https://o/latest"}
    c = _client({"https://o/latest": httpx.Response(200, content=b"%PDF-oct")})
    assert watch._try(c, src, "2026-11", newest=True, run="t")["status"] == "wrong_edition"
    c = _client({"https://o/momr-november-2026.pdf": httpx.Response(200, content=b"%PDF-nov")})
    assert watch._try(c, src, "2026-11", newest=True, run="t")["status"] == "downloaded"

    meta = next(m for m in editions.load() if m.doc_id == "opec-momr-2026-11")
    assert meta.source == "watch" and meta.path.read_bytes() == b"%PDF-nov"
    assert meta.published == "2026-11-11"  # из официального графика, а не дата скачивания
    log = journal.read()
    assert log[0]["status"] == "downloaded" and log[0]["sha256"]
    assert any(r["status"] == "wrong_edition" for r in log)


def test_publication_date_from_schedule_document_or_marked_as_download_date(auto_dir, monkeypatch):
    """Даты публикации нет в графике — она из документа (IEA: метка «Published»); нет и там — это дата скачивания,
    и так и написано: старый выпуск, скачанный при первом запуске, не выдаётся за новый."""
    page = (
        b'<p>Fuel report - 14 October 2026</p><span>Published</span> <span><time datetime="2026-08-13T10:00:00+02:00">'
        b"13 August 2026</time></span>"
    )
    assert watch.detect_published("html", page) == "2026-08-13" and watch.detect_published("pdf", b"%PDF-1.7") == ""
    monkeypatch.setattr(watch, "_text_head", lambda kind, body, pages=3: "IEA Oil Market Report August 2026")
    iea = next(s for s in watch.sources() if s["series"] == "IEA OMR")
    c = _client({"https://www.iea.org/reports/oil-market-report-august-2026": httpx.Response(200, content=page)})
    assert watch._try(c, iea, "2026-08", newest=False, run="t")["status"] == "downloaded"
    meta = next(m for m in editions.load() if m.doc_id == "iea-omr-2026-08")
    assert (meta.published, meta.published_by, meta.published_ru) == (
        "2026-08-13",
        "document",
        "опубликован 2026-08-13",
    )
    steo = next(s for s in watch.sources() if s["series"] == "EIA STEO")
    watch._save(steo, "2026-11", "pdf", b"%PDF-nov", "watch", "https://e/steo.pdf")
    meta = next(m for m in editions.load() if m.doc_id == "eia-steo-2026-11")
    assert (
        meta.published_by == "fetched" and meta.published_ru == f"скачан {date.today()} (дата публикации неизвестна)"
    )


def test_blocked_site_is_reported_not_bypassed(auto_dir):
    src = {
        "series": "OPEC MOMR",
        "org": "OPEC",
        "report": "MOMR",
        "kind": "pdf",
        "latest_url": "https://o/latest",
        "urls": [],
    }
    c = _client({"https://o/latest": httpx.Response(403, headers={"cf-mitigated": "challenge"})})
    res = watch._try(c, src, "2026-10", newest=True, run="t")
    assert res["status"] == "blocked" and "вручную" in res["note"]
    assert not any(auto_dir.glob("*.pdf"))


def test_upload_detects_series_and_edition(auto_dir, monkeypatch):
    monkeypatch.setattr(
        watch, "_text_head", lambda kind, body, pages=3: "OPEC Monthly Oil Market Report 12 August 2026"
    )
    item = watch.upload(b"%PDF-1.7 fake", "momr.pdf")
    assert (item["series"], item["edition"], item["source"]) == ("OPEC MOMR", "2026-08", "upload")
    assert "opec-momr-2026-08" in watch.pending()  # файл есть, в индексе ещё нет
    with pytest.raises(ValueError):
        watch.upload(b"not a pdf")


def test_new_edition_is_queued_while_encoder_builds_the_base(monkeypatch):
    from oil_ouroboros.config import get_settings

    monkeypatch.setattr(queue, "pending", lambda: ["opec-momr-2026-10"])
    monkeypatch.setattr(queue.journal, "add", lambda rec: None)
    monkeypatch.setattr(get_settings(), "encoder_url", "http://encoder:8020")
    routes = {"http://encoder:8020/ingest": httpx.Response(200, json={"active": True, "trigger": "auto"})}
    real_client = httpx.Client
    monkeypatch.setattr(
        queue.net,
        "internal",
        lambda *a, **kw: real_client(transport=httpx.MockTransport(lambda r: routes[str(r.url)])),
    )
    r = queue.index_pending(trigger="test")
    assert r["queued"] == ["opec-momr-2026-10"] and "кодировщик строит базу" in r["note"]  # блокировку не держит час


def test_after_busy_encoder_only_still_missing_editions_are_indexed(monkeypatch):
    from oil_ouroboros.config import get_settings
    from oil_ouroboros.rag import ingest

    left = [["opec-momr-2026-10"], []]  # до ожидания выпуск ждал индекса, после — его взяла чужая загрузка
    monkeypatch.setattr(queue, "pending", lambda: left.pop(0) if left else [])
    monkeypatch.setattr(queue.journal, "add", lambda rec: None)
    monkeypatch.setattr(queue, "_encoder_auto_busy", lambda: "")
    monkeypatch.setattr(get_settings(), "encoder_url", "http://encoder:8020")
    calls: list = []
    monkeypatch.setattr(ingest, "run_via_encoder", lambda only=None, **kw: calls.append(only) or {"busy": True})
    r = queue.index_pending(trigger="test")
    assert calls == [["opec-momr-2026-10"]] and "другая загрузка" in r["note"]  # повторной векторизации нет


def test_eval_lock_is_set_through_tools_api_and_expires(monkeypatch, tmp_path):
    import os
    import time as _time

    from fastapi.testclient import TestClient

    from oil_ouroboros import tools_app
    from oil_ouroboros.config import get_settings

    monkeypatch.setattr(get_settings(), "var_dir", tmp_path)
    monkeypatch.setattr(get_settings(), "tools_api_token", "")
    c = TestClient(tools_app.app)
    assert c.post("/api/eval_lock", json={"eval_id": "e1", "active": True}).json() == {"locked": True}
    assert "eval" in freeze.reason()  # наблюдатель видит замер
    os.utime(tmp_path / "eval.lock", (_time.time() - 4 * 3600,) * 2)
    assert freeze.reason() == ""  # оборванный прогон не держит индекс вечно
    assert c.post("/api/eval_lock", json={"eval_id": "e1", "active": False}).json() == {"locked": False}


def test_edition_without_schedule_is_not_missing_before_its_usual_day():
    """EIA и IEA без графика в каталоге: до earliest_day выпуск месяца «ещё не вышел», а не «не получен»."""
    src = {"series": "IEA OMR", "earliest_day": 11, "schedule": {}}
    assert watch._not_yet(src, "2026-11", date(2026, 11, 3)) == "2026-11-11"
    assert (
        watch._not_yet(src, "2026-11", date(2026, 11, 12)) == ""
        and watch._not_yet(src, "2026-10", date(2026, 11, 3)) == ""
    )
    assert watch.next_release(src, date(2026, 11, 3)) == ("2026-11", "не раньше 2026-11-11")
    assert watch.next_release(src, date(2026, 12, 20)) == ("2027-01", "не раньше 2027-01-11")

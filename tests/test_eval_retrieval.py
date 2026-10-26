"""Eval поиска и отбора по слоям (evals/retrieval.py): без Ouroboros, без сети и денег — fake-LLM, хеш-эмбеддер."""

from __future__ import annotations

from pathlib import Path

from oil_ouroboros.config import ROOT
from oil_ouroboros.evals import retrieval
from oil_ouroboros.evals.checks import Fact, load_cases

DEV = ROOT / "evals" / "datasets" / "dev.jsonl"


def test_fact_needs_the_reference_precision_and_a_keyword_nearby():
    demand = Fact(any_of=[2.6], near=["demand"])
    assert retrieval.fact_in_text(demand, "World oil demand is forecast to contract by 2.6 mb/d in 2026.")
    assert not retrieval.fact_in_text(demand, "World oil demand falls by 3 mb/d.")  # «3» не подтверждает 2.6
    assert not retrieval.fact_in_text(demand, "Supply drops by 2.6 mb/d.")  # число есть, слова рядом нет
    draw = Fact(any_of=[1.9], near=["inventor", "stock"])
    assert retrieval.fact_in_text(draw, "Global inventories decline by 1.87 million b/d in 3Q26")  # 1.87 ≈ 1.9
    assert retrieval.fact_in_text(Fact(any_of=[940, 0.94]), "lowered by 940 kb/d")  # без near — только число
    assert not retrieval.fact_in_text(draw, "Global inventories " + "x" * 400 + " 1.9")  # слово слишком далеко


def test_dataset_facts_of_report_cases_have_keywords():
    cases = retrieval.eligible(load_cases(DEV))
    assert len(cases) >= 15 and all(f.near for c in cases for f in c.expect.facts)


def test_run_measures_layers_without_ledger_web_or_agent(indexed, monkeypatch):
    from oil_ouroboros.evidence import ledger
    from oil_ouroboros.web import search

    monkeypatch.setattr(search, "search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("веб не нужен")))
    before = ledger.stats()
    rec = retrieval.run(DEV, only=["R02", "R13", "R18"], label="test", workers=2)
    assert ledger.stats() == before  # журнал доказательств не тронут: узел pack не вызывается
    assert [r["case_id"] for r in rec["results"]] == ["R02", "R13", "R18"] and not rec["summary"]["errors"]
    for r in rec["results"]:
        assert (
            r["plan"]["queries"] and r["candidates"] > 0 and r["verdict"] in ("sufficient", "partial", "insufficient")
        )
        assert r["ms"]["retrieve"] >= 0 and r["llm_calls"] == 2  # план и отбор
        assert all(set(f) == {"what", "candidate_rank", "selected"} for f in r["facts"])
    s = rec["summary"]
    assert s["facts"] == 5 and 0 <= s["fact_in_candidates"] <= 1 and 0 <= s["fact_selected"] <= 1
    assert rec["config"]["embedder"] == "hashing" and Path(retrieval.runs_dir() / f"{rec['run_id']}.json").is_file()
    assert "R13" in retrieval.report_text(rec)
    # планы из прошлого прогона: LLM зовётся только в отборе, запросы те же
    again = retrieval.run(DEV, only=["R13"], plans_from=rec["run_id"])
    r13 = next(r for r in rec["results"] if r["case_id"] == "R13")
    assert again["results"][0]["plan"] == r13["plan"] and again["results"][0]["llm_calls"] == 1
    # только поиск: без отбора LLM и без вердикта
    bare = retrieval.run(DEV, only=["R13"], select=False, plans_from=rec["run_id"])
    assert bare["results"][0]["llm_calls"] == 0 and bare["results"][0]["verdict"] is None
    assert bare["summary"]["fact_selected"] is None


def test_index_stays_frozen_during_the_run_and_foreign_lock_is_kept():
    from oil_ouroboros import freeze

    with freeze.frozen("x1", "retrieval"):
        assert freeze.reason()  # наблюдатель не тронет индекс посреди замера
    assert not freeze.reason()
    lock = retrieval.get_settings().var_dir / "eval.lock"
    lock.write_text("{}", encoding="utf-8")  # блокировку держит обычный eval — её не снимаем
    try:
        with freeze.frozen("x2", "retrieval"):
            pass
        assert lock.is_file()
    finally:
        lock.unlink(missing_ok=True)

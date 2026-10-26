"""
Поиск по отчётам:
- при сравнении агентств top_k делится между ними, а серия с большим числом фрагментов их не вытесняет;
- оценки фрагмента по запросам складываются (RRF), все запросы — одним вызовом Qdrant;
- клиент кодировщика: на первом запуске ждёт загрузки моделей, занятый реранкингом сервис не долбит повторами.
"""

from oil_ouroboros.rag.retriever import Hit, _balanced


def _hit(i: int, org: str, final: float) -> Hit:
    return Hit(
        chunk_id=f"{org}-{i}",
        org=org,
        report="r",
        edition="2026-10",
        published="",
        page=1,
        section="",
        kind="text",
        text="t",
        url="",
        rrf=final,
        rrf_rank=i,
        rerank=final,
        bonus=0.0,
        final=final,
    )


def test_balanced_keeps_both_agencies_when_one_dominates():
    # OPEC выигрывает по оценке все места, IEA — худшие оценки
    ranked = [_hit(i, "OPEC", 0.9 - i * 0.01) for i in range(10)] + [_hit(i, "IEA", 0.5 - i * 0.01) for i in range(3)]
    top = _balanced(ranked, ["OPEC", "IEA"], 6)
    orgs = [h.org for h in top]
    assert len(top) == 6
    assert orgs.count("IEA") >= 3
    assert orgs.count("OPEC") >= 3


def test_queries_fuse_by_rrf_sum(monkeypatch):
    """«b» второй по обоим запросам, «a» и «c» — первые по одному: по сумме RRF выше «b», по максимуму был бы «a»."""
    from oil_ouroboros.rag import retriever, vectors

    class Stub:
        name = "stub"

        def embed(self, texts, is_query=False):
            return [None] * len(texts)

    def pt(cid: str) -> dict:
        return {"chunk_id": cid, "org": "IEA", "report": "OMR", "edition": "2026-10", "text": cid}

    found = [[(pt("a"), 0.5), (pt("b"), 0.4)], [(pt("c"), 0.5), (pt("b"), 0.4)]]
    batches: list[int] = []
    monkeypatch.setattr(vectors, "get_embedder", Stub)
    monkeypatch.setattr(vectors, "get_reranker", vectors.NoReranker)
    monkeypatch.setattr(vectors, "count", lambda name: 3)
    monkeypatch.setattr(
        vectors, "hybrid_queries", lambda name, reqs, limit, prefetch: batches.append(len(reqs)) or found
    )
    monkeypatch.setattr(retriever.editions, "load", list)
    res = retriever.retrieve(["q1", "q2"], top_k=3)
    assert [h.chunk_id for h in res.hits] == ["b", "a", "c"] and res.hits[0].matched_queries == ["q1", "q2"]
    assert batches == [2]  # оба запроса — одним вызовом Qdrant


def test_single_or_no_agency_keeps_plain_order():
    ranked = [_hit(i, "OPEC", 1.0 - i * 0.1) for i in range(8)]
    assert _balanced(ranked, [], 6) == ranked[:6]
    assert _balanced(ranked, ["OPEC"], 6) == ranked[:6]


def test_remote_encoder_waits_while_models_load_on_first_start():
    # первый запуск: кодировщик качает модели, запрос висит за ними — клиент ждёт, а не роняет загрузку отчётов
    import httpx

    from oil_ouroboros.rag.vectors import RemoteEmbedder

    calls = {"embed": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "loaded": [] if calls["embed"] < 3 else ["emb", "rr"]})
        calls["embed"] += 1
        if calls["embed"] < 3:
            raise httpx.ReadTimeout("модели ещё грузятся", request=req)
        return httpx.Response(200, json={"vectors": [{"dense": [0.1], "sparse_idx": [1], "sparse_val": [0.5]}]})

    emb = RemoteEmbedder("http://encoder:8020")
    emb.http = httpx.Client(transport=httpx.MockTransport(handler))
    assert emb.embed(["q"])[0].dense == [0.1]
    assert calls["embed"] == 3


def test_remote_encoder_busy_with_loaded_models_fails_fast():
    import httpx
    import pytest

    from oil_ouroboros.rag.vectors import RemoteEmbedder

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "loaded": ["emb", "rr"]})
        raise httpx.ReadTimeout("занят реранкингом", request=req)

    emb = RemoteEmbedder("http://encoder:8020")
    emb.http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="не ответил вовремя"):
        emb.embed(["q"])

"""
Векторизация, хранилище и реранкер.

- **BGE-M3** (BAAI, MIT): одна модель даёт плотный (1024) и разреженный (обученные лексические веса) векторы,
  мультиязычная, контекст 8k. На русском поиске RusBEIR — лучшая открытая модель (nDCG@10 61.13 против 60.12 у
  mE5-large и 52.16 у BM25); разреженная ветка заменяет BM25 без русской морфологии.
- **Qdrant**: серверное гибридное слияние (prefetch dense + sparse → RRF), payload-фильтры по организации и
  выпуску. Chroma документирует BM25/RRF только в облаке, у Weaviate нет русской морфологии в токенизаторе.
- **bge-reranker-v2-m3** (cross-encoder): +4.7 nDCG на RusBEIR поверх BGE-M3.
- **hashing** — лёгкий детерминированный эмбеддер без моделей: для тестов, CI и офлайн-разработки.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from ..config import get_settings


def _catalog():
    from .. import catalog

    return catalog.get()


log = logging.getLogger(__name__)
TOKEN = re.compile(r"[\w.%$]+", re.UNICODE)


@dataclass
class Vec:
    dense: list[float]
    sparse_idx: list[int]
    sparse_val: list[float]


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str], is_query: bool = False) -> list[Vec]: ...


class HashingEmbedder:
    """Хеширование токенов: плотный вектор (знаковое хеширование, log-tf) и разреженный (tf по хешу токена)."""

    name = "hashing"

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    @staticmethod
    def _h(tok: str) -> int:
        return int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "little")

    def embed(self, texts: list[str], is_query: bool = False) -> list[Vec]:
        out = []
        for t in texts:
            toks = [x.lower() for x in TOKEN.findall(t)]
            tf = Counter(toks)
            dense = [0.0] * self.dim
            sparse: dict[int, float] = {}
            for tok, c in tf.items():
                h = self._h(tok)
                w = 1.0 + math.log(c)
                dense[h % self.dim] += w if (h >> 16) & 1 else -w
                sparse[h % 2_000_000_000] = sparse.get(h % 2_000_000_000, 0.0) + w
            norm = math.sqrt(sum(x * x for x in dense)) or 1.0
            out.append(Vec([x / norm for x in dense], list(sparse.keys()), list(sparse.values())))
        return out


class BGEM3Embedder:
    name = "bge-m3"
    dim = 1024
    _lock = threading.Lock()

    def __init__(self, model: str) -> None:
        from FlagEmbedding import BGEM3FlagModel

        log.info("загрузка %s", model)
        self.model = BGEM3FlagModel(model, use_fp16=False)

    def embed(self, texts: list[str], is_query: bool = False) -> list[Vec]:
        with self._lock:
            res = self.model.encode(
                texts,
                batch_size=8,
                max_length=512 if is_query else 1024,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False,
            )
        out = []
        for dense, lex in zip(res["dense_vecs"], res["lexical_weights"], strict=True):
            items = [(int(k), float(v)) for k, v in lex.items() if float(v) > 0]
            out.append(Vec([float(x) for x in dense], [k for k, _ in items], [v for _, v in items]))
        return out


class Reranker(Protocol):
    name: str

    def score(self, query: str, passages: list[str]) -> list[float]: ...


class BGEReranker:
    name = "bge-reranker-v2-m3"
    _lock = threading.Lock()

    def __init__(self, model: str) -> None:
        from FlagEmbedding import FlagReranker

        log.info("загрузка %s", model)
        self.model = FlagReranker(model, use_fp16=False)

    def score(self, query: str, passages: list[str], max_length: int | None = None) -> list[float]:
        if not passages:
            return []
        with self._lock:
            s = self.model.compute_score(
                [[query, p] for p in passages],
                normalize=True,
                max_length=max_length or _catalog().retrieval.rerank_max_length,
            )
        return [float(x) for x in (s if isinstance(s, list) else [s])]


class NoReranker:
    name = "none"

    def score(self, query: str, passages: list[str]) -> list[float]:
        return []


class _Remote:
    """Клиент сервиса кодировщика (encoder_service.py, контейнер encoder) — те же модели по HTTP."""

    def __init__(self, url: str) -> None:
        import httpx

        self.url = url.rstrip("/")
        self.http = httpx.Client(timeout=120, trust_env=False)

    def _post(self, path: str, body: dict) -> dict:
        import time

        import httpx

        last: Exception | None = None
        for _ in range(30):  # сервис может ещё подниматься — ждём до ~1 мин; ошибки самого запроса не повторяем
            try:
                r = self.http.post(self.url + path, json=body)
                if r.status_code < 500:
                    r.raise_for_status()
                    return r.json()
                last = RuntimeError(f"HTTP {r.status_code}")
            except httpx.ReadTimeout as exc:
                if not self._loading():  # сервис жив и занят: повтор встал бы в ту же очередь за ним
                    raise RuntimeError(f"кодировщик {self.url} не ответил вовремя (долгий реранкинг на CPU?)") from exc
                # первый запуск: модели ещё скачиваются (BGE-M3 и Docling, ~4.8 ГБ) — запрос ждёт их, ждём и мы
                last = exc
                continue
            except httpx.TransportError as exc:  # не поднялся / перезапускается — подождать
                last = exc
            time.sleep(2)
        raise RuntimeError(f"кодировщик {self.url} недоступен: {last}")

    def _loading(self) -> bool:
        """Кодировщик ещё загружает модели: /health перечисляет готовые (loaded) и нужные (expected)."""
        try:
            h = self.http.get(self.url + "/health", timeout=10).json()
            return len(h.get("loaded", [])) < len(h.get("expected", ["emb", "rr"]))
        except Exception:  # noqa: BLE001
            return False


class RemoteEmbedder(_Remote):
    name = "bge-m3@encoder"
    dim = 1024

    def embed(self, texts: list[str], is_query: bool = False) -> list[Vec]:
        out = self._post("/embed", {"texts": texts, "is_query": is_query})
        return [Vec(v["dense"], v["sparse_idx"], v["sparse_val"]) for v in out["vectors"]]


class RemoteReranker(_Remote):
    name = "bge-reranker-v2-m3@encoder"

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return self._post(
            "/rerank", {"query": query, "passages": passages, "max_length": _catalog().retrieval.rerank_max_length}
        )["scores"]


_cache: dict[str, object] = {}
_cache_lock = threading.Lock()


def get_embedder() -> Embedder:
    s = get_settings()
    with _cache_lock:
        if "emb" not in _cache:
            if s.embedder == "bge-m3" and s.encoder_url:
                _cache["emb"] = RemoteEmbedder(s.encoder_url)
            else:
                _cache["emb"] = BGEM3Embedder(s.embed_model) if s.embedder == "bge-m3" else HashingEmbedder()
        return _cache["emb"]  # type: ignore[return-value]


def get_reranker() -> Reranker:
    s = get_settings()
    with _cache_lock:
        if "rr" not in _cache:
            if s.reranker.startswith("bge") and s.encoder_url:
                _cache["rr"] = RemoteReranker(s.encoder_url)
            else:
                _cache["rr"] = BGEReranker(s.rerank_model) if s.reranker.startswith("bge") else NoReranker()
        return _cache["rr"]  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------------------------------------------


def get_client():
    from qdrant_client import QdrantClient

    s = get_settings()
    with _cache_lock:
        if "qd" not in _cache:
            if s.qdrant_url:
                _cache["qd"] = QdrantClient(url=s.qdrant_url, timeout=60)
            else:
                path = s.var_dir / "qdrant"
                path.mkdir(parents=True, exist_ok=True)
                _cache["qd"] = QdrantClient(path=str(path))
        return _cache["qd"]


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def ensure_collection(name: str, dim: int, recreate: bool = False) -> None:
    from qdrant_client import models

    client = get_client()
    exists = client.collection_exists(name)
    if exists and recreate:
        client.delete_collection(name)
        exists = False
    if not exists:
        client.create_collection(
            name,
            vectors_config={"dense": models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={"sparse": models.SparseVectorParams()},
        )
        for field_name in ("org", "report", "edition", "doc_id"):
            try:
                client.create_payload_index(name, field_name, models.PayloadSchemaType.KEYWORD)
            except Exception:  # noqa: BLE001 — локальный режим индексы не поддерживает
                pass


def upsert(name: str, payloads: list[dict], vecs: list[Vec]) -> None:
    from qdrant_client import models

    points = [
        models.PointStruct(
            id=point_id(p["chunk_id"]),
            vector={"dense": v.dense, "sparse": models.SparseVector(indices=v.sparse_idx, values=v.sparse_val)},
            payload=p,
        )
        for p, v in zip(payloads, vecs, strict=True)
    ]
    get_client().upsert(name, points=points, wait=True)


def hybrid_queries(
    name: str, reqs: list[tuple[Vec, dict | None]], limit: int, prefetch: int
) -> list[list[tuple[dict, float]]]:
    """Гибридный поиск пачкой запросов (вектор, фильтр) — один вызов query_batch_points. В каждом запросе dense и
    sparse кандидаты сливаются RRF на стороне Qdrant."""
    from qdrant_client import models

    requests = []
    for vec, filt in reqs:
        must = [models.FieldCondition(key=k, match=models.MatchAny(any=list(v))) for k, v in (filt or {}).items() if v]
        qfilter = models.Filter(must=must) if must else None
        sparse = models.SparseVector(indices=vec.sparse_idx, values=vec.sparse_val)
        requests.append(
            models.QueryRequest(
                prefetch=[
                    models.Prefetch(query=vec.dense, using="dense", limit=prefetch, filter=qfilter),
                    models.Prefetch(query=sparse, using="sparse", limit=prefetch, filter=qfilter),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
        )
    res = get_client().query_batch_points(name, requests=requests) if requests else []
    return [[(p.payload or {}, float(p.score)) for p in r.points] for r in res]


def delete_doc(name: str, doc_id: str) -> None:
    """Убрать все фрагменты документа перед повторной загрузкой: иначе при смене разбиения остаются дубликаты."""
    from qdrant_client import models

    client = get_client()
    if client.collection_exists(name):
        client.delete(
            name,
            points_selector=models.FilterSelector(
                filter=models.Filter(must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))])
            ),
            wait=True,
        )


def count(name: str) -> int:
    client = get_client()
    if not client.collection_exists(name):
        return 0
    return int(client.count(name).count)


def share_model(kind: str, model) -> None:
    """Модель, уже загруженная в этом процессе (кодировщик), — для загрузки отчётов: kind — "emb" или "rr"."""
    with _cache_lock:
        _cache[kind] = model

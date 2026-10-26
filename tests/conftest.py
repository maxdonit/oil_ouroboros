"""
Тесты без сети и без денег: fake-LLM, хеш-эмбеддер, встроенный Qdrant, веб выключен, бэктест на 12 окнах.
Переменные выставляются до импорта пакета, чтобы настройки (lru_cache) прочитали их.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="oil_test_"))
os.environ.update(
    {
        "LLM_PROVIDER": "fake",
        "EMBEDDER": "hashing",
        "RERANKER": "none",
        "WEB_PROVIDER": "none",
        "QDRANT_URL": "",
        "VAR_DIR": str(_TMP),
        "CATALOG_SET": "forecast.backtest_windows=12;forecast.max_horizon=6",
        "LLM_MODEL_SUBAGENT": "",
        "MCP_TOKEN": "test-token",
        "TOOLS_API_TOKEN": "",  # служебное API инструментов в тестах открыто (локальный .env не мешает)
        "REPORTS_WATCH_HOURS": "0",  # наблюдатель выпусков в тестах не ходит в сеть
        "REPORTS_FETCH_ON_START": "false",
        "PRICES_REFRESH_HOURS": "0",  # цены EIA в тестах не скачиваются: только снимок data/prices
    }
)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def indexed():
    """Индекс отчётов в лёгком режиме (pypdf + хеш-эмбеддер) — один раз на сессию."""
    from oil_ouroboros.rag import ingest

    return ingest.run(recreate=True, parser="pypdf")


def pytest_sessionfinish(session, exitstatus):
    # встроенный Qdrant закрываем сами: иначе его __del__ срабатывает при остановке интерпретатора и печатает traceback
    from oil_ouroboros.rag import vectors

    client = vectors._cache.pop("qd", None)
    if client is not None:
        client.close()
    shutil.rmtree(_TMP, ignore_errors=True)

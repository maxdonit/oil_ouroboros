"""
Настройки окружения (инфраструктура): адреса, ключи, пути, режимы — из переменных окружения / `.env`
(пример — `.env.example`). Поведение инструментов (промпты субагентов, параметры поиска, веб-политика, прогноз,
модели по ролям) здесь НЕ живёт — оно в каталоге config/ (src/oil_ouroboros/catalog.py). Поведение самого агента
(роль, правила ответа) — в его памяти: засев ouroboros/memory/ (регламент knowledge/overview.md, identity.md).

Принцип: у каждого параметра есть имя, значение по умолчанию и описание; описания видны в консоли («Параметры»),
поэтому «винтик» всегда виден вместе со своим значением.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _root() -> Path:
    """Корень проекта (ui/, evals/, config/, docs/): OILO_ROOT; иначе исходники рядом (pyproject.toml); иначе cwd.
    В Docker пакет установлен в site-packages, поэтому корень задаётся явно (OILO_ROOT=/app)."""
    if os.environ.get("OILO_ROOT"):
        return Path(os.environ["OILO_ROOT"])
    here = Path(__file__).resolve().parents[2]
    return here if (here / "pyproject.toml").is_file() else Path.cwd()


ROOT = _root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- пути -------------------------------------------------------------------------------------------------
    data_dir: Path = Field(ROOT / "data", description="Каталог исходных данных: отчёты, цены, рейтинги доменов")
    var_dir: Path = Field(ROOT / "var", description="Рабочие данные: индекс, журнал доказательств, трейсы, кэш")

    # --- LLM субагентов (в Docker — через LLM-шлюз gateway) ---------------------------------------------------
    llm_provider: str = Field("openai_compatible", description="openai_compatible (шлюз) | openrouter | fake")
    openrouter_api_key: str = Field("", description="Ключ OpenRouter (только без шлюза: разработка вне Docker)")
    openrouter_base_url: str = Field("https://openrouter.ai/api/v1", description="Базовый URL OpenRouter")
    openai_compat_base_url: str = Field(
        "", description="OpenAI-совместимый endpoint; в Docker — LLM-шлюз http://gateway:8030/api/v1"
    )
    openai_compat_api_key: str = Field("", description="Токен для endpoint (в Docker — токен шлюза «tools»)")
    llm_profile: str = Field(
        "gigachat", description="LLM-профиль стенда: gigachat | openrouter (scripts/llm_profile.py, deploy/llm/)"
    )
    llm_model_subagent: str = Field(
        "", description="Переопределение модели субагентов из каталога (слой env; пусто — config/current/llm.yaml)"
    )
    outbound_proxy: str = Field(
        "",
        description="HTTP(S)-прокси для внешних запросов: веб-поиск, сайты отчётов, цены EIA, котировки Мосбиржи "
        "(iss.moex.com); у шлюза — OpenRouter (gpt2giga ходит к GigaChat напрямую)",
    )
    ca_bundle: str = Field("", description="Путь к CA bundle для исходящих TLS-соединений")

    # --- RAG ----------------------------------------------------------------------------------------------------
    qdrant_url: str = Field("", description="URL Qdrant (пусто — встроенный локальный режим в var/qdrant)")
    qdrant_collection: str = Field("reports", description="Коллекция фрагментов отчётов")
    embedder: str = Field("bge-m3", description="bge-m3 (dense+sparse) | hashing (лёгкий офлайн-режим для тестов)")
    encoder_url: str = Field("", description="URL сервиса кодировщика (контейнер encoder); пусто — модели в процессе")
    embed_model: str = Field("BAAI/bge-m3", description="Модель эмбеддингов")
    reranker: str = Field(
        "none",
        description="none — порядок кандидатов по RRF, отбор делает LLM субагента (узел select_grade) | "
        "bge-reranker-v2-m3 — cross-encoder в кодировщике (на CPU 24–130 с на вопрос: только с GPU)",
    )
    rerank_model: str = Field(
        "BAAI/bge-reranker-v2-m3", description="Модель реранкера (cross-encoder), если reranker=bge-reranker-v2-m3"
    )
    reports_auto_dir: str = Field(
        "", description="Каталог выпусков от наблюдателя и ручных загрузок (пусто — var/reports)"
    )
    reports_watch_hours: float = Field(
        6.0,
        description=(
            "Как часто наблюдатель проверяет новые выпуски, ч "
            "(0 — только при старте и командой `oilo reports --check`)"
        ),
    )
    reports_watch_ingest: bool = Field(True, description="Сразу добавлять скачанные выпуски в индекс")
    prices_refresh_hours: float = Field(
        6.0,
        description=(
            "Как часто обновлять дневные ряды EIA, ч: Brent и WTI для прогноза, спот Henry Hub — рядом с котировкой; "
            "первое — при старте (0 — только `oilo prices refresh`)"
        ),
    )
    reports_fetch_on_start: bool = Field(
        True,
        description="Через 2 мин после старта инструментов проверить сайты агентств и скачать недостающие выпуски",
    )

    # --- веб-поиск ------------------------------------------------------------------------------------------------
    web_provider: str = Field("auto", description="auto (Tavily → Brave → DuckDuckGo) | tavily | brave | ddgs | none")
    web_fallback: str = Field(
        "ddgs", description="Резерв, если у основного провайдера кончился лимит или ключ: ddgs | none"
    )
    web_cache_dir: str = Field("", description="Постоянный каталог кэша веб-выдачи (пусто — окно WEB_CACHE_HOURS)")
    web_cache_hours: float = Field(
        6.0, description="Окно кэша веб-выдачи, ч: одинаковый запрос в окне → та же выдача (0 — без кэша)"
    )
    web_time_budget_s: float = Field(
        40.0, description="Бюджет времени веб-поиска на вызов, с: новые проходы после него не начинаются"
    )
    tavily_api_key: str = Field("", description="Ключ Tavily")
    brave_api_key: str = Field("", description="Ключ Brave Search API")

    # --- каталог поведения инструментов (config/) ---------------------------------------------------------------
    config_dir: Path = Field(
        ROOT / "config", description="Каталог поведения инструментов: промпты субагентов, параметры, политики"
    )
    catalog_slot: str = Field("current", description="Слот каталога: current или экспериментальный exp-*")
    catalog_set: str = Field("", description="Точечные переопределения «раздел.ключ=значение;…» (перебор в eval)")

    # --- сервисы ------------------------------------------------------------------------------------------------
    api_host: str = Field("0.0.0.0", description="Адрес HTTP-сервиса")
    api_port: int = Field(8000, description="Порт HTTP-сервиса")
    mcp_token: str = Field(
        "", description="Bearer-токен MCP: с ним Ouroboros вызывает инструменты (пусто — MCP выключен)"
    )
    tools_url: str = Field("http://tools:8000", description="Адрес сервиса инструментов (для консоли)")
    tools_api_token: str = Field(
        "", description="Токен служебного API инструментов (/api/*): его знает только консоль; MCP — свой токен"
    )
    ouroboros_url: str = Field("http://ouroboros:8765", description="Адрес Ouroboros (чат агента, задачи, журналы)")
    ouroboros_password: str = Field(
        "", description="Сетевой пароль Ouroboros (в его настройках — OUROBOROS_NETWORK_PASSWORD)"
    )
    ouroboros_data_dir: str = Field(
        "", description="Данные Ouroboros только на чтение (журналы, память, навыки) — для мониторинга"
    )
    gateway_url: str = Field("http://gateway:8030", description="Адрес LLM-шлюза (журнал вызовов и расходы)")
    gateway_token_console: str = Field("", description="Токен консоли у LLM-шлюза: чтение журнала вызовов LLM")

    @property
    def runs_db(self) -> Path:
        return self.var_dir / "runs.sqlite"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.var_dir.mkdir(parents=True, exist_ok=True)
    return s


SECRET_SUFFIXES = ("_key", "_credentials", "_password", "_token")


def describe_settings() -> list[dict[str, object]]:
    """Параметры с описаниями и текущими значениями (секреты скрыты) — для консоли."""
    s = get_settings()
    out = []
    for name, field in Settings.model_fields.items():
        value = getattr(s, name)
        if name.endswith(SECRET_SUFFIXES):
            value = "<задан>" if value else "<пусто>"
        elif isinstance(value, str) and "://" in value and "@" in value:  # логин и пароль в адресе прокси
            value = re.sub(r"//[^/@]*@", "//<скрыто>@", value)
        out.append({"name": name.upper(), "value": str(value), "description": field.description or ""})
    return out

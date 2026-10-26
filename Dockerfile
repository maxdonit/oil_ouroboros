# Образы проекта (один Dockerfile, две цели):
#   app — инструменты (MCP + LangGraph), консоль, LLM-шлюз: без torch, лёгкий;
#   ml  — кодировщик BGE-M3 и загрузка отчётов (Docling): torch CPU, FlagEmbedding, docling.
# Ouroboros собирается из своего официального Dockerfile (docker-compose.yml, сервис ouroboros).

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VAR_DIR=/app/var \
    DATA_DIR=/app/data \
    OILO_ROOT=/app \
    NUMBA_CACHE_DIR=/app/var/numba
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# ---------------------------------------------------------------------------------------------------------------
FROM base AS app
# слой зависимостей кэшируется, пока не меняется pyproject.toml (README — пустая заглушка для метаданных пакета)
COPY pyproject.toml ./
RUN mkdir -p src/oil_ouroboros && touch src/oil_ouroboros/__init__.py README.md \
    && pip install . && pip uninstall -y oil-ouroboros && rm -rf src build *.egg-info
COPY README.md ./
COPY src ./src
RUN pip install --no-deps . && rm -rf build *.egg-info && python -c "import oil_ouroboros; print(oil_ouroboros.__version__)"
COPY config ./config
COPY data ./data
COPY evals ./evals
COPY ui ./ui
COPY docs ./docs
COPY ouroboros ./ouroboros
# вкладка консоли «Архитектура» читает из docker-compose.yml сервисы, сети и тома (x-role, x-why)
COPY docker-compose.yml ./
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN useradd --create-home --uid 10001 oilo \
    && mkdir -p /app/var && chown -R oilo:oilo /app/var \
    && chmod +x /usr/local/bin/entrypoint.sh
USER oilo
EXPOSE 8000
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["tools"]

# ---------------------------------------------------------------------------------------------------------------
FROM base AS ml
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
# torch — CPU-сборка (GPU стенда занят другим проектом; на CPU BGE-M3 кодирует вопрос за доли секунды).
# torchvision — тоже из CPU-индекса: сборка из PyPI собрана под CUDA-torch и падает на импорте.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
RUN pip install torch==2.14.1 torchvision==0.29.1 --index-url ${TORCH_INDEX}
COPY pyproject.toml ./
RUN mkdir -p src/oil_ouroboros && touch src/oil_ouroboros/__init__.py README.md \
    && pip install ".[ml]" && pip uninstall -y oil-ouroboros \
    && pip install --force-reinstall --no-deps torch==2.14.1 torchvision==0.29.1 --index-url ${TORCH_INDEX} \
    && rm -rf src build *.egg-info
COPY README.md ./
COPY src ./src
RUN pip install --no-deps . && rm -rf build *.egg-info && python -c "import oil_ouroboros"
COPY config ./config
COPY data ./data
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN useradd --create-home --uid 10001 oilo \
    && mkdir -p /app/var /app/models && chown -R oilo:oilo /app/var /app/models \
    && chmod +x /usr/local/bin/entrypoint.sh
USER oilo
# модели HuggingFace — отдельный каталог (свой том): пересоздание индекса и рабочих данных их не трогает
ENV HF_HOME=/app/models
EXPOSE 8020
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["encoder"]

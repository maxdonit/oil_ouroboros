"""
Стоимость вызовов LLM: цены за токен из публичного каталога OpenRouter (/api/v1/models), кэш на сутки.

Модели GigaChat-* (LLM-профиль gigachat, Freemium) — 0 сразу, без каталога: их там нет. Неизвестная модель —
стоимость 0 (цену не выдумываем). Основной учёт денег — usage.cost из ответа OpenRouter; эти цены — запасной путь
шлюза, когда провайдер цену не прислал или поток оборвался. Шлюз считает без сети (fetch=False) — по запасным ценам
FALLBACK: каталог скачивает только сервис инструментов в свой var, а в томе шлюза кэша каталога нет.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from ..config import get_settings

log = logging.getLogger(__name__)
_prices: dict[str, tuple[float, float]] = {}
_loaded_at = 0.0
_lock = threading.Lock()
GIGACHAT_PREFIX = "GigaChat"  # тот же префикс, по которому шлюз шлёт модель в gpt2giga (gateway/app.py)

# запасные цены (USD за 1 токен, октябрь 2026, OpenRouter), если каталог недоступен
FALLBACK = {
    "anthropic/claude-haiku-5.5": (0.10e-6, 0.50e-6),
    "anthropic/claude-sonnet-5.5": (2.0e-6, 10.0e-6),
    "openai/gpt-6-luna": (0.10e-6, 0.50e-6),
    "google/gemini-3.8-flash": (0.825e-6, 4.125e-6),
    "deepseek/deepseek-v4.1-flash": (0.30e-6, 1.20e-6),
    "qwen/qwen3.8-flash": (0.15e-6, 0.47e-6),
}


def _load(fetch: bool = True) -> None:
    global _loaded_at
    s = get_settings()
    cache = s.var_dir / "openrouter_models.json"
    data = None
    if cache.is_file() and (time.time() - cache.stat().st_mtime < 86400 or not fetch):
        data = json.loads(cache.read_text(encoding="utf-8"))
    elif fetch:
        try:
            from .. import net

            with net.outbound(20.0) as c:
                r = c.get(s.openrouter_base_url.rstrip("/") + "/models")
                r.raise_for_status()
                data = r.json()
            cache.write_text(json.dumps(data), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            log.info("каталог цен OpenRouter недоступен: %s", exc)
    _prices.update(FALLBACK)
    for m in (data or {}).get("data", []):
        p = m.get("pricing") or {}
        try:
            _prices[m["id"]] = (float(p.get("prompt", 0)), float(p.get("completion", 0)))
        except (TypeError, ValueError):
            continue
    _loaded_at = time.time()


def cost(model: str, tokens_in: int, tokens_out: int, fetch: bool = True) -> float:
    """fetch=False — без сети (кэш каталога или запасные цены): так считает шлюз, его цикл событий ждать нельзя.
    Модели GigaChat-* (профиль gigachat, Freemium) — 0 без каталога OpenRouter: их там нет, а скачивание шло бы внутри
    хода."""
    if model.startswith(GIGACHAT_PREFIX):
        return 0.0
    if not _prices or time.time() - _loaded_at > 86400:
        if _lock.acquire(blocking=False):  # один поток обновляет цены, остальные считают по старым (или 0)
            try:
                _load(fetch)
            finally:
                _lock.release()
    pin, pout = _prices.get(model, (0.0, 0.0))
    return tokens_in * pin + tokens_out * pout

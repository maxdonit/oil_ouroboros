"""
LLM-шлюз (контейнер gateway): OpenAI-совместимый endpoint для всех вызовов LLM системы. Маршрут — по имени модели:
- GigaChat-* (LLM-профиль gigachat, по умолчанию) — в прокси gpt2giga внутри сети проекта, без исходящего прокси:
  он сам ходит к API GigaChat и обновляет токен Сбера;
- остальные (профиль openrouter) — в OpenRouter (GATEWAY_UPSTREAM) через исходящий прокси OUTBOUND_PROXY, если задан.

Зачем:
- воркеры Ouroboros намеренно игнорируют системный прокси (ouroboros/utils.py::in_worker_process), а OpenRouter со
  стенда доступен только через прокси. Ouroboros штатно умеет провайдер `openai-compatible::` → направляем его сюда,
  ядро Ouroboros не меняется;
- ключ OpenRouter знает ТОЛЬКО шлюз (ключ GigaChat — только gpt2giga, шлюз говорит с ним по внутреннему токену
  GPT2GIGA_API_KEY). Ouroboros и сервис инструментов получают свои токены шлюза: утечка токена не даёт денег мимо
  лимита;
- жёсткий лимит денег (GATEWAY_BUDGET_USD, общий и по вызывающим): при превышении — 402, что бы ни решил агент.
  Вызовы GigaChat-* (Freemium) стоят 0: их расход — в токенах журнала;
- журнал КАЖДОГО вызова LLM всей системы (кто, модель, токены, кэш, стоимость, длительность, id генерации) и, по
  желанию, тела запроса и ответа — по ним мониторинг восстанавливает раунды Ouroboros и считает цену хода.
  Шлюз только записывает: ни промпты, ни ответы он не меняет. Исключения:
  - `usage.include` — только на маршруте OpenRouter: он вернёт цену;
  - потолок `max_tokens` (GATEWAY_MAX_TOKENS): Ouroboros просит 65 536 на раунд, а OpenRouter заранее сверяет остаток
    кредита с этим числом — при малом остатке ключа отказ 402 пришёл бы раньше, чем кончатся деньги;
  - маркеры кэша промпта Anthropic (GATEWAY_PROMPT_CACHE), только для моделей anthropic/*: Ouroboros ставит
    `cache_control` на стабильные блоки системного промпта, сообщение задачи и хвост переписки, но на маршруте
    `openai-compatible::` их снимает — шлюз возвращает их на те же места. Без них каждый раунд платит за ~60 тыс.
    входных токенов полностью.

    python -m oil_ouroboros.gateway.app      # порт 8030
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote, unquote

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

log = logging.getLogger("gateway")

UPSTREAM = os.environ.get("GATEWAY_UPSTREAM", "https://openrouter.ai/api/v1").rstrip("/")
UPSTREAM_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# LLM-профиль gigachat: модели GigaChat-* идут в прокси gpt2giga внутри сети проекта (он переводит формат OpenAI
# в API GigaChat и сам обновляет токен Сбера), остальные — в UPSTREAM. Ключ OpenRouter туда не уходит
GIGACHAT_URL = os.environ.get("GATEWAY_GIGACHAT_URL", "http://gpt2giga:8090/v1").rstrip("/")
GIGACHAT_KEY = os.environ.get("GPT2GIGA_API_KEY", "")
GIGACHAT_PREFIX = "GigaChat"  # тот же префикс в llm/pricing.py: модели GigaChat-* идут в gpt2giga и бесплатны
BUDGET = float(os.environ.get("GATEWAY_BUDGET_USD", "5"))
RESERVE_USD = float(os.environ.get("GATEWAY_RESERVE_USD", "0.05"))  # резерв на вызов в полёте
VAR = Path(os.environ.get("GATEWAY_DIR", "/app/var/gateway"))
LOG_BODIES = os.environ.get("GATEWAY_LOG_BODIES", "true").lower() == "true"
BODIES_MAX_MB = float(os.environ.get("GATEWAY_BODIES_MAX_MB", "400"))
MAX_TOKENS = int(os.environ.get("GATEWAY_MAX_TOKENS", "16384"))  # 0 — не ограничивать
PROMPT_CACHE = os.environ.get("GATEWAY_PROMPT_CACHE", "true").lower() == "true"
HOP = {
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
    "accept-encoding",
    "keep-alive",
    "authorization",
    "x-oil-run",
    "x-oil-purpose",
}  # служебные заголовки сервиса инструментов (прогон и назначение вызова) — не наружу


def _pairs(raw: str) -> dict[str, str]:
    out = {}
    for item in (raw or "").split(","):
        name, _, value = item.strip().partition(":")
        if name and value:
            out[name.strip()] = value.strip()
    return out


TOKENS = _pairs(os.environ.get("GATEWAY_TOKENS", ""))  # «ouroboros:<токен>,tools:<токен>,console:<токен>»
CALLER_BUDGETS = {k: float(v) for k, v in _pairs(os.environ.get("GATEWAY_CALLER_BUDGETS", "")).items()}

app = FastAPI(title="oil-ouroboros gateway", docs_url=None, redoc_url=None)
_lock = threading.Lock()
_state: dict = {"spent": 0.0, "by_caller": {}, "calls": 0, "refused": 0, "in_flight": 0}

SCHEMA = """
create table if not exists calls (
  id text primary key, ts real, caller text, model text, path text, stream integer, status integer,
  tokens_in integer, tokens_out integer, tokens_cached integer, cost real, ms integer, gen_id text,
  request_hash text, first_user text, tools integer, finish text, error text, run_id text, purpose text, turn text
);
create index if not exists calls_ts on calls(ts);
create index if not exists calls_caller on calls(caller, ts);
create index if not exists calls_run on calls(run_id);
create index if not exists calls_turn on calls(turn);
"""


@contextlib.contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Соединение на один блок: транзакция фиксируется (или откатывается при ошибке), соединение закрывается."""
    VAR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(VAR / "ledger.sqlite"), timeout=30)
    try:
        c.row_factory = sqlite3.Row
        with c:
            yield c
    finally:
        c.close()


def _init() -> None:
    with _db() as c:
        c.executescript(SCHEMA)
        for row in c.execute("select caller, sum(cost) s, count(*) n from calls group by caller"):
            _state["by_caller"][row["caller"]] = float(row["s"] or 0.0)
            _state["spent"] += float(row["s"] or 0.0)
            _state["calls"] += int(row["n"])


_init()
log.info("шлюз → %s, потрачено %.4f из %.2f USD", UPSTREAM, _state["spent"], BUDGET)


def _caller(header: str) -> str | None:
    scheme, _, value = header.partition(" ")
    token = value.strip() if scheme.lower() == "bearer" else header.strip()
    for name, expected in TOKENS.items():
        if token and hmac.compare_digest(token.encode(), expected.encode()):
            return name
    return None


def _route(model: object) -> tuple[str, str, bool]:
    """Куда идёт вызов: (адрес, ключ, внешний ли провайдер). GigaChat-* — в прокси gpt2giga в сети проекта."""
    if isinstance(model, str) and model.startswith(GIGACHAT_PREFIX):
        return GIGACHAT_URL, GIGACHAT_KEY, False
    return UPSTREAM, UPSTREAM_KEY, True


def _client(external: bool = True) -> httpx.AsyncClient:
    """Внешний провайдер — через OUTBOUND_PROXY и CA_BUNDLE; сервис внутри сети проекта — напрямую."""
    kw: dict = {"timeout": httpx.Timeout(900, connect=30)}
    if external and os.environ.get("OUTBOUND_PROXY"):
        kw["proxy"] = os.environ["OUTBOUND_PROXY"]
    if external and os.environ.get("CA_BUNDLE"):
        kw["verify"] = os.environ["CA_BUNDLE"]
    return httpx.AsyncClient(**kw)


def _first_user(payload: dict) -> str:
    """Первые символы последнего сообщения пользователя — чтобы в журнале было видно, к какому ходу относится вызов."""
    for m in reversed(payload.get("messages") or []):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
            return str(content or "")[:300]
    return ""


def _mark_cache(message: dict) -> bool:
    """cache_control на последний непустой текстовый блок сообщения (Anthropic отвергает маркер на пустом)."""
    content = message.get("content")
    if isinstance(content, str):
        if not content.strip():
            return False
        content = message["content"] = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return False
    for block in reversed(content):
        if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "").strip():
            block["cache_control"] = {"type": "ephemeral"}
            return True
    return False


def _prompt_cache(payload: dict) -> int:
    """Маркеры кэша Anthropic — там же, где их ставит сам Ouroboros (context_fit.py): стабильные блоки системного
    промпта (все, кроме последнего — динамического), сообщение задачи и хвост переписки. Не больше 4 (лимит API)."""
    model = str(payload.get("model") or "").lstrip("~")
    msgs = payload.get("messages") or []
    if not model.startswith("anthropic/") or "cache_control" in json.dumps(msgs, ensure_ascii=False):
        return 0
    marks = 0
    system = msgs[0] if msgs and msgs[0].get("role") == "system" else None
    if system and isinstance(system.get("content"), list):
        stable = [
            b
            for b in system["content"][:-1]
            if isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip()
        ]
        for block in [*stable[:1], *stable[1:][-1:]]:  # первый (ядро ~30 тыс. токенов) и последний стабильный
            block["cache_control"] = {"type": "ephemeral"}
            marks += 1
    task = next((m for m in msgs if m.get("role") == "user"), None)
    if task is not None and _mark_cache(task):
        marks += 1
    tail = next((m for m in reversed(msgs) if m is not task and m.get("role") != "system" and _has_text(m)), None)
    if tail is not None and marks < 4 and _mark_cache(tail):
        marks += 1
    return marks


def _has_text(message: dict) -> bool:
    c = message.get("content")
    return (
        bool(c.strip())
        if isinstance(c, str)
        else any(isinstance(b, dict) and str(b.get("text") or "").strip() for b in c or [])
    )


TURN_TAG = re.compile(rb"oilo-turn[= ]([0-9a-f]{12})")


def _turn_of(body: bytes) -> str:
    """Метка хода консоли в тексте задачи Ouroboros: каждый вызов LLM относится к своему ходу
    (и лёгкие вызовы тоже)."""
    m = TURN_TAG.search(body)
    return m.group(1).decode() if m else ""


def _usage(obj: dict) -> tuple[int, int, int, float]:
    u = obj.get("usage") or {}
    cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("cached_tokens") or 0)
    return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0), cached, float(u.get("cost") or 0.0)


def _save_bodies(call_id: str, request: dict | None, response: object) -> None:
    if not LOG_BODIES:
        return
    d = VAR / "bodies" / time.strftime("%Y%m%d")
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"{call_id}.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"request": request, "response": response}, f, ensure_ascii=False)


def _prune_bodies() -> None:
    root = VAR / "bodies"
    if not root.is_dir():
        return
    files = sorted(root.glob("*/*.json.gz"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    limit = BODIES_MAX_MB * 1024 * 1024
    for p in files:
        if total <= limit:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


def _record(rec: dict) -> None:
    if not rec["cost"] and rec.get("model") and (rec["tokens_in"] or rec["tokens_out"]):
        # провайдер не прислал usage.cost (не OpenRouter или поток оборван): оценка по прайс-листу, а не 0 —
        # иначе такой вызов не попал бы в лимит денег
        from ..llm import pricing

        rec["cost"] = pricing.cost(rec["model"], rec["tokens_in"], rec["tokens_out"], fetch=False)
    with _lock:
        _state["spent"] += rec["cost"]
        _state["by_caller"][rec["caller"]] = _state["by_caller"].get(rec["caller"], 0.0) + rec["cost"]
        _state["calls"] += 1
    with _db() as c:
        c.execute(
            "insert into calls values (:id,:ts,:caller,:model,:path,:stream,:status,:tokens_in,:tokens_out,"
            ":tokens_cached,:cost,:ms,:gen_id,:request_hash,:first_user,:tools,:finish,:error,:run_id,:purpose,:turn)",
            rec,
        )
    if _state["calls"] % 50 == 0:
        _prune_bodies()


def _record_stream(base: dict, status: int, chunks: list[bytes], t0: float, payload: dict | None) -> None:
    """Потоковый ответ (SSE) → запись журнала: usage — из последнего кадра (stream_options.include_usage)."""
    raw = b"".join(chunks).decode("utf-8", "ignore")
    tin = tout = cached = 0
    cost = 0.0
    gen_id = finish = ""
    text_parts: list[str] = []
    for line in raw.splitlines():
        if not line.startswith("data:") or line.strip() == "data: [DONE]":
            continue
        try:
            obj = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        gen_id = gen_id or str(obj.get("id") or "")
        for ch in obj.get("choices") or []:
            delta = ch.get("delta") or {}
            if delta.get("content"):
                text_parts.append(str(delta["content"]))
            finish = ch.get("finish_reason") or finish
        if obj.get("usage"):
            tin, tout, cached, cost = _usage(obj)
    if (
        status < 400 and not (tin or tout) and (payload or text_parts)
    ):  # поток оборван до кадра usage: ~4 символа на токен
        tin, tout = len(json.dumps(payload or {}, ensure_ascii=False)) // 4, len("".join(text_parts)) // 4
    _record(
        {
            **base,
            "stream": 1,
            "status": status,
            "tokens_in": tin,
            "tokens_out": tout,
            "tokens_cached": cached,
            "cost": cost,
            "ms": int((time.monotonic() - t0) * 1000),
            "gen_id": gen_id,
            "finish": finish,
            "error": "" if status < 400 else raw[:300],
        }
    )
    _save_bodies(base["id"], payload, {"stream_text": "".join(text_parts), "raw_tail": raw[-20000:]})


def _over_budget(caller: str) -> str:
    in_flight = (_state["in_flight"] + 1) * RESERVE_USD
    if _state["spent"] + in_flight > BUDGET:
        return f"общий бюджет {BUDGET} USD исчерпан (потрачено {_state['spent']:.4f})"
    cap = CALLER_BUDGETS.get(caller)
    if cap is not None and _state["by_caller"].get(caller, 0.0) + in_flight > cap:
        return f"бюджет «{caller}» {cap} USD исчерпан"
    return ""


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "upstream": UPSTREAM,
        "budget_usd": BUDGET,
        "spent_usd": round(_state["spent"], 6),
        "by_caller": {k: round(v, 6) for k, v in _state["by_caller"].items()},
        "caller_budgets": CALLER_BUDGETS,
        "calls": _state["calls"],
        "refused": _state["refused"],
        "in_flight": _state["in_flight"],
        "key_set": bool(UPSTREAM_KEY),
        "gigachat": {
            "url": GIGACHAT_URL,
            "key_set": bool(GIGACHAT_KEY),
        },  # маршрут моделей GigaChat-* (профиль gigachat)
    }


def _reader(request: Request) -> bool:
    """Журнал читают консоль и мониторинг (токен console); агент и инструменты — нет."""
    return _caller(request.headers.get("authorization", "")) == "console"


@app.get("/ledger")
def ledger(request: Request, since: float = 0.0, caller: str = "", turn: str = "", limit: int = 500) -> Response:
    if not _reader(request):
        return JSONResponse({"error": "нужен токен console"}, status_code=401)
    q = (
        "select * from calls where ts >= ?"
        + (" and caller = ?" if caller else "")
        + (" and turn = ?" if turn else "")
        + " order by ts desc limit ?"
    )
    args: list = [since] + ([caller] if caller else []) + ([turn] if turn else []) + [min(limit, 5000)]
    with _db() as c:
        rows = [dict(r) for r in c.execute(q, args)]
        by_model = [
            dict(r)
            for r in c.execute(
                "select caller, model, count(*) calls, sum(tokens_in) tokens_in, sum(tokens_out) tokens_out, "
                "sum(tokens_cached) tokens_cached, round(sum(cost), 6) cost from calls where ts >= ? "
                "group by caller, model order by cost desc",
                [since],
            )
        ]
    return JSONResponse({"spent": round(_state["spent"], 6), "budget": BUDGET, "by_model": by_model, "calls": rows})


@app.get("/calls/{call_id}")
def call_body(call_id: str, request: Request) -> Response:
    if not _reader(request):
        return JSONResponse({"error": "нужен токен console"}, status_code=401)
    for p in (VAR / "bodies").glob(f"*/{call_id}.json.gz"):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return JSONResponse(json.load(f))
    return JSONResponse(
        {"error": "тело вызова не сохранено (GATEWAY_LOG_BODIES) или удалено по лимиту"}, status_code=404
    )


# Что шлюз пересылает провайдеру с его ключом: генерация и справочные чтения. Остальное (управление ключами и т. п.)
# закрыто — токен вызывающего не должен давать доступ ко всему API провайдера.
ALLOWED = {"POST": ("chat/completions", "completions"), "GET": ("models", "generation", "credits", "key", "auth/key")}


def clean_path(path: str) -> str | None:
    """Путь после раскодирования %xx; «.» и «..» в сегментах — отказ (иначе «models/%2e%2e/keys» уходит как «keys»)."""
    segs = unquote(path).replace("\\", "/").strip("/").split("/")
    if any(s in ("", ".", "..") for s in segs):
        return None
    return "/".join(segs)


def allowed_path(method: str, path: str) -> bool:
    p = clean_path(path)
    return p is not None and any(
        p == a or (method == "GET" and p.startswith(a + "/")) for a in ALLOWED.get(method, ())
    )


@app.api_route("/api/v1/{path:path}", methods=["GET", "POST"])
async def proxy(path: str, request: Request) -> Response:
    caller = _caller(request.headers.get("authorization", ""))
    if caller is None:
        return JSONResponse({"error": {"message": "LLM-шлюз: нужен токен шлюза", "code": 401}}, status_code=401)
    if caller == "console":  # консоль только читает журнал: генерацию её токен не открывает
        return JSONResponse(
            {"error": {"message": "LLM-шлюз: токен консоли — только для журнала", "code": 403}}, status_code=403
        )
    if not allowed_path(request.method, path):
        msg = f"LLM-шлюз: {request.method} /{path} не пересылается"
        return JSONResponse({"error": {"message": msg, "code": 404}}, status_code=404)
    with _lock:
        why = _over_budget(caller)
        if why:
            _state["refused"] += 1
            return JSONResponse({"error": {"message": f"LLM-шлюз: {why}", "code": 402}}, status_code=402)
        _state["in_flight"] += 1
    released = False

    def release() -> None:  # резерв вызова в полёте снимается один раз: для потока — когда он закончился
        nonlocal released
        if not released:
            released = True
            with _lock:
                _state["in_flight"] -= 1

    try:
        resp = await _forward(path, request, caller, release)
    except BaseException:
        release()
        raise
    if not isinstance(resp, StreamingResponse):
        release()
    return resp


async def _forward(path: str, request: Request, caller: str, release) -> Response:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    # поток пересылается байтами как есть: без сжатия, иначе тело ошибки нечитаемо
    headers["Accept-Encoding"] = "identity"
    body = await request.body()
    payload = None
    if request.method == "POST" and body:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = None
    upstream, key, external = _route(payload.get("model") if isinstance(payload, dict) else None)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    is_completion = isinstance(payload, dict) and path.endswith("completions")
    if is_completion:
        if (
            external
        ):  # OpenRouter вернёт usage.cost: учёт видит реальную цену (gpt2giga лишнее поле передал бы в GigaChat)
            payload["usage"] = {"include": True}
        for k in ("max_tokens", "max_completion_tokens"):
            if MAX_TOKENS and isinstance(payload.get(k), int) and payload[k] > MAX_TOKENS:
                payload[k] = MAX_TOKENS
        if PROMPT_CACHE:
            _prompt_cache(payload)
        if payload.get("stream") and external:
            payload.setdefault("stream_options", {})["include_usage"] = True
        body = json.dumps(payload).encode()
    url = f"{upstream}/{quote(clean_path(path) or '', safe='/')}" + (
        f"?{request.url.query}" if request.url.query else ""
    )
    call_id = uuid.uuid4().hex[:16]
    t0 = time.monotonic()
    base = {
        "id": call_id,
        "ts": time.time(),
        "caller": caller,
        "model": (payload or {}).get("model") if is_completion else None,
        "path": path,
        "request_hash": hashlib.sha256(body).hexdigest()[:16] if body else "",
        "first_user": _first_user(payload) if is_completion else "",
        "tools": len((payload or {}).get("tools") or []) if is_completion else 0,
        "run_id": request.headers.get("x-oil-run", ""),
        "purpose": request.headers.get("x-oil-purpose", ""),
        "turn": _turn_of(body) if is_completion else "",
    }
    client = _client(external)
    if is_completion and payload.get("stream"):
        req = client.build_request(request.method, url, headers=headers, content=body)
        try:
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            _record(
                {
                    **base,
                    "stream": 1,
                    "status": 599,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "tokens_cached": 0,
                    "cost": 0.0,
                    "ms": int((time.monotonic() - t0) * 1000),
                    "gen_id": "",
                    "finish": "",
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
            )
            return JSONResponse(
                {"error": {"message": f"LLM-шлюз: upstream недоступен: {exc}", "code": 502}}, status_code=502
            )

        async def gen():
            chunks: list[bytes] = []
            try:
                async for chunk in upstream.aiter_raw():
                    chunks.append(chunk)
                    yield chunk
            finally:
                # учёт — до любого await: при обрыве клиента (отмена) каждый await в finally прерывается снова
                try:
                    _record_stream(base, upstream.status_code, chunks, t0, payload)
                finally:
                    release()
                    try:
                        await upstream.aclose()
                        await client.aclose()
                    except BaseException:  # noqa: BLE001 — отмена: соединения закроет сборщик
                        pass

        return StreamingResponse(
            gen(), status_code=upstream.status_code, media_type=upstream.headers.get("content-type")
        )
    try:
        r = await client.request(request.method, url, headers=headers, content=body)
    except httpx.HTTPError as exc:
        await client.aclose()
        if is_completion:
            _record(
                {
                    **base,
                    "stream": 0,
                    "status": 599,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "tokens_cached": 0,
                    "cost": 0.0,
                    "ms": int((time.monotonic() - t0) * 1000),
                    "gen_id": "",
                    "finish": "",
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                }
            )
        return JSONResponse(
            {"error": {"message": f"LLM-шлюз: upstream недоступен: {exc}", "code": 502}}, status_code=502
        )
    await client.aclose()
    if is_completion:
        try:
            obj = r.json()
        except ValueError:
            obj = {}
        tin, tout, cached, cost = _usage(obj)
        finish = ""
        for ch in obj.get("choices") or []:
            finish = ch.get("finish_reason") or finish
        _record(
            {
                **base,
                "stream": 0,
                "status": r.status_code,
                "tokens_in": tin,
                "tokens_out": tout,
                "tokens_cached": cached,
                "cost": cost,
                "ms": int((time.monotonic() - t0) * 1000),
                "gen_id": str(obj.get("id") or ""),
                "finish": finish,
                "error": "" if r.status_code < 400 else r.text[:300],
            }
        )
        _save_bodies(call_id, payload, obj)
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("GATEWAY_PORT", "8030")), log_level="warning")


if __name__ == "__main__":
    main()

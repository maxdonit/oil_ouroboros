"""
Разговор с агентом Ouroboros: один ход диалога = одна задача Ouroboros (POST /api/tasks), ход — из потока событий
задачи, ответ — из её итога. Пользуются консоль, eval и засев расписаний.

Почему задачи API, а не чат по WebSocket (сверено с кодом Ouroboros v7.7.0):
- только в задаче можно отключить встроенные инструменты (disabled_tools): у Ouroboros их 140 — код, shell, git,
  браузер, память; аналитику они не нужны и стоят ~40 тыс. токенов схем на раунд;
- кадры /ws рассылаются всем клиентам, а у задачи свой id: вызовы инструментов, раунды LLM и итог приходят штатным
  потоком событий задачи (SSE GET /api/tasks/{id}/events) — без опроса журналов;
- Ouroboros — агент одного владельца; диалоги разных пользователей консоль хранит сама и передаёт последние реплики
  в тексте задачи: он попадает под кэш промпта (поле context задачи идёт в Runtime context — динамическую часть).

Контекст хода (сверено с кодом Ouroboros v7.7.0 и телом запроса на стенде): в режиме low Ouroboros всегда кладёт
ядро (SYSTEM.md, BIBLE.md) и карты книг ARCHITECTURE и DEVELOPMENT; memory_mode=forked пишет память в копию задачи, но
читает задача общую память — в том числе разговор владельца в комнате Main (memory_view.py: view_room_id). Штатного
рычага у задачи API для этого нет.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

import httpx

from . import net
from .config import ROOT, get_settings

log = logging.getLogger(__name__)
TERMINAL = {
    "completed",
    "failed",
    "cancelled",
    "canceled",
    "error",
    "stopped",
    "timeout",
    "timed_out",
    "rejected",
    "aborted",
    "paused",
    "budget_exceeded",
    "budget_paused",
    "interrupted",
}  # пауза по бюджету ждёт владельца — для консоли это конец
MCP_PREFIX = "mcp_oil__"
TRACE = re.compile(r"trace ([0-9a-f]{12})")
FINISH_HOLD_S = 30  # сколько ждать завершения задачи после finish_task, прежде чем консоль закроет ход сама
STREAM_WAIT_S = (
    15  # поток событий задачи переподключается так часто: между подключениями — пауза и удержание finish_task
)
TOOL_ARG_LIMIT = 2990  # журнал инструментов Ouroboros обрезает строки аргументов длиннее 3000 символов
HISTORY_TURNS = 3  # сколько прошлых реплик диалога попадает в текст задачи
HISTORY_CHARS = 1200  # сколько символов прошлого ответа (без списка источников)
# Время хода. Ouroboros просит финальный ответ без инструментов за 2 мин до конца бюджета задачи: при 300 с это 3-я
# минута, а ход с отчётами и вебом (research ~80 с + search_web ~45 с) к ней не успевал проверить ответ
TURN_TIMEOUT_S = 480

# Только то, чем ход консоли отличается от регламента (Shared understanding): сам протокол — порядок инструментов,
# проверка, завершение — живёт в регламенте и здесь не повторяется (одна точка правды). Исключение — отсылка к границе
# компетенции у самого вопроса: в регламенте правило далеко от вопроса, и GigaChat без отсылки отвечал на вопросы вне
# компетенции (стихи, калории) в 3 из 12 повторов. Раздел «This room» — переписку владельца (комната Main) Ouroboros
# кладёт в контекст задачи сам: по её готовым ответам GigaChat отвечал о цене газа без инструментов (повтор неудачного
# запроса 25.10: price_quote — 0 из 5, с этим правилом — 5 из 5).
TURN_RULES = """Правила хода консоли — дополнение к регламенту в Shared understanding:
- Вопрос не о нефтегазовой отрасли и энергетике — только отказ по регламенту (раздел «Вне компетенции»): без
  инструментов и без ответа по существу.
- mcp_oil__verify_answer вызывай с render=false; в finish_task — проверенный черновик со ссылками [R#]/[W#]/[F#]
  символ в символ: метки источников, их список и строку проверки добавит консоль.
- Не пиши в память и не запускай фоновых задач.
- Раздел «This room» (переписка владельца и строки его задач) — не источник для этого хода: прошлые ответы оттуда не
  повторяю, цифры и метки [R#]/[W#]/[F#] беру только из инструментов этого хода."""
ANSWER_IN_ENGLISH = (
    "Answer in English: the user asked in English. Tool results and these rules are in Russian, the "
    "answer is not; keep the [R#]/[W#]/[F#] ids as they are."
)


REFUSAL = re.compile(
    r"вне (?:моей|рамок моей|сферы моей) (?:компетенц|специализац|экспертиз)"
    r"|не (?:моя|по моей|входит в мою) (?:тем|специализац|компетенц)"
    r"|outside (?:my|the scope|of my)|not (?:within|in) my (?:area|scope|expertise)",
    re.I,
)


@lru_cache(maxsize=1)
def disabled_tools() -> list[str]:
    f = ROOT / "ouroboros" / "disabled_tools.txt"
    return [x.strip() for x in f.read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]


def sanitize(text: str) -> str:
    """Текст пользователя не должен стать командой Ouroboros (/panic, /evolve, /restart…) и разметкой хода."""
    text = text.replace("\r\n", "\n").strip()
    text = re.sub(r"^\s*/", "∕", text)  # ведущий «/» → похожий знак: команды распознаются только по началу текста
    text = untag(text)  # чужая метка хода в вопросе перепутала бы журнал вызовов LLM
    return text.replace("[Вопрос пользователя]", "").replace("[Диалог]", "")[:4000]


def untag(text: str) -> str:
    """Метку хода (oilo-turn …) ставит только консоль: в тексте пользователя и в прошлых ответах она
    обезвреживается."""
    return re.sub(r"oilo-turn", "oilo_turn", text, flags=re.I)


def language(text: str) -> str:
    """Язык ответа по письменности вопроса: ~30 тыс. токенов английского системного контекста Ouroboros тянут к
    английскому, поэтому язык называется явно."""
    from .evidence.text import lang_of  # одно правило языка для текста задачи и для проверки ответа

    return "русский" if lang_of(text) == "ru" else "английский (English)"


def strip_sources(answer: str) -> str:
    """Текст ответа без подвала со списком источников (его ставит система)."""
    from .evidence.text import FOOTER

    return FOOTER.split(answer)[0].strip()


def compose(question: str, history: list[dict] | None = None, turn_id: str = "", dialog_pack: str = "") -> str:
    """Текст задачи: прошлые реплики диалога + вопрос + правила хода (+ метка хода для журнала LLM-шлюза).
    dialog_pack — пакет доказательств прошлых реплик: с ним ссылки из истории для проверки хода свои."""
    parts = []
    hist = [h for h in (history or []) if h.get("q")][-HISTORY_TURNS:]
    if hist:
        refs = (
            f"ссылки [R#]/[W#]/[F#] в них — проверенные доказательства из пакета диалога {dialog_pack}: на них можно "
            f"сослаться снова, тогда добавь {dialog_pack} в pack_ids проверки"
            if dialog_pack
            else "для цифр нужны результаты инструментов этого хода"
        )
        lines = [f"[Диалог] Предыдущие реплики этого разговора (для местоимений и уточнений; {refs}):"]
        for h in hist:
            lines.append(f"Пользователь: {sanitize(h['q'])}")
            a = untag(strip_sources(h.get("a", "")))
            lines.append(f"Аналитик: {a[:HISTORY_CHARS]}{'…' if len(a) > HISTORY_CHARS else ''}")
        parts.append("\n".join(lines))
    parts.append(f"[Вопрос пользователя]\n{sanitize(question)}")
    lang = language(question)
    parts.append(
        f"Язык ответа: {lang}.\n\n{TURN_RULES}" + (f"\n\n{ANSWER_IN_ENGLISH}" if lang.startswith("английский") else "")
    )
    if turn_id:
        # метка для журнала LLM-шлюза (каждый вызов модели относится к ходу); словами, а не «голым» hex-id: модель
        # принимала hex-строку за хеш источника в параметрах finish_task
        parts.append(f"(служебная метка журнала консоли, к ответу не относится: oilo-turn {turn_id})")
    return "\n\n".join(parts)


class Ouroboros:
    """HTTP-клиент Ouroboros (сеть agent). Сетевой пароль — OUROBOROS_PASSWORD из .env (засев кладёт его в
    настройки Ouroboros как OUROBOROS_NETWORK_PASSWORD)."""

    def __init__(self, url: str | None = None, password: str | None = None) -> None:
        s = get_settings()
        self.url = (url or s.ouroboros_url).rstrip("/")
        pw = s.ouroboros_password if password is None else password
        self.http = net.internal(60, headers={"X-Ouroboros-Password": pw} if pw else {})

    def health(self) -> dict:
        r = self.http.get(self.url + "/api/health", timeout=10)
        r.raise_for_status()
        return r.json()

    def create_task(self, description: str, title: str, metadata: dict, timeout_s: int = TURN_TIMEOUT_S) -> str:
        body = {
            "description": description,
            "title": title[:80],
            "disabled_tools": disabled_tools(),
            "memory_mode": "forked",  # запись — в копию памяти задачи: память агента ходы консоли не меняют
            "execution_timeout_sec": timeout_s,
            "source": "oilo-console",
            "metadata": metadata,
        }
        r = self.http.post(self.url + "/api/tasks", json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"Ouroboros не принял задачу: HTTP {r.status_code} {r.text[:300]}")
        data = r.json()
        if not data.get("task_id"):
            raise RuntimeError(f"Ouroboros не вернул task_id: {str(data)[:300]}")
        return str(data["task_id"])

    def task(self, task_id: str) -> dict:
        r = self.http.get(f"{self.url}/api/tasks/{task_id}")
        if r.status_code == 503:  # результат ещё не записан / хранилище занято
            return {"status": "pending"}
        r.raise_for_status()
        return r.json()

    def events(self, task_id: str, cursor: int = 0, wait: int = STREAM_WAIT_S) -> Iterator[dict]:
        """Поток событий задачи (SSE): строки её журналов по мере записи — {source, type, seq, data} — и в конце итог
        (type=task_result). Поток кончается итогом или через wait секунд тишины; дальше — с курсором seq."""
        url = f"{self.url}/api/tasks/{task_id}/events"
        with self.http.stream("GET", url, params={"cursor": cursor, "wait": wait}, timeout=wait + 60) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line.startswith("data:"):
                    yield json.loads(line[5:])

    def cancel(self, task_id: str) -> None:
        try:
            self.http.post(f"{self.url}/api/tasks/{task_id}/cancel")
        except httpx.HTTPError:
            pass


def task_status(t: dict) -> str:
    st = t.get("status")
    if isinstance(st, dict):
        st = st.get("status")
    return str(st or (t.get("outcome_axes") or {}).get("lifecycle", {}).get("status") or "pending").lower()


def final_answer(t: dict) -> str:
    for k in ("final_answer", "result", "answer"):
        v = t.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            for kk in ("text", "answer", "final_answer", "content"):
                if isinstance(v.get(kk), str) and v[kk].strip():
                    return v[kk].strip()
    return ""


def tool_calls(rows: list[dict]) -> list[dict]:
    """Вызовы инструментов задачи из журнала Ouroboros: наши — с trace, встроенные — как есть.

    Один вызов даёт в журнале несколько строк: начало (без статуса) и итог, причём при memory_mode=forked — и в
    журнале копии памяти задачи, и в основном. Берётся одна строка на вызов (invocation_id) — итоговая."""
    best: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        if r.get("type") not in (None, "tool_call") or not r.get("tool"):
            continue
        key = str(r.get("invocation_id") or r.get("tool_call_id") or len(order))
        if key not in best:
            order.append(key)
            best[key] = r
        elif r.get("status") and not best[key].get("status"):
            best[key] = r
    out = []
    for r in (best[k] for k in order):
        name = str(r["tool"])
        preview = str(r.get("result_preview") or "")
        m = TRACE.search(preview)
        out.append(
            {
                "ts": r.get("ts"),
                "tool": name[len(MCP_PREFIX) :] if name.startswith(MCP_PREFIX) else name,
                "mcp": name.startswith(MCP_PREFIX),
                "args": r.get("args") or {},
                "ok": not r.get("is_error") and r.get("status", "ok") == "ok",
                "ms": r.get("elapsed_ms"),
                "trace": m.group(1) if m else "",
                "round": r.get("round_id", ""),
                "preview": preview[-600:] if r.get("is_error") else "",
            }
        )
    return out


def llm_rounds(rows: list[dict]) -> list[dict]:
    out = []
    for e in rows:
        if e.get("type") != "llm_usage":
            continue
        out.append(
            {
                "ts": e.get("ts"),
                "round": e.get("round"),
                "model": e.get("model"),
                "category": e.get("model_category") or e.get("category"),
                "source": e.get("source"),
                "tokens_in": e.get("prompt_tokens") or 0,
                "tokens_out": e.get("completion_tokens") or 0,
                "tokens_cached": e.get("cached_tokens") or 0,
                "cost_usd": e.get("cost") or 0.0,
                "call_id": e.get("llm_call_id"),
            }
        )
    return out


def ask(
    question: str,
    history: list[dict] | None = None,
    conversation_id: str = "",
    turn_id: str = "",
    timeout_s: int = TURN_TIMEOUT_S,
    on_progress: Any = None,
    client: Ouroboros | None = None,
    dialog_pack: str = "",
) -> dict:
    """Ход: задача → поток её событий до итога → ответ + сырые журналы задачи (сборку хода делает console/turns.py)."""
    c = client or Ouroboros()
    turn_id = turn_id or uuid.uuid4().hex[:12]
    t0 = time.monotonic()
    meta = {"oilo_turn": turn_id, "oilo_conversation": conversation_id, "oilo_question": question[:2000]}
    description = compose(question, history, turn_id, dialog_pack)
    task_id = c.create_task(description, title=question, metadata=meta, timeout_s=timeout_s)
    if on_progress:
        on_progress({"stage": "accepted", "task_id": task_id})
    tools_rows: list[dict] = []
    event_rows: list[dict] = []
    result: dict | None = None
    status, cursor, seen_calls = "pending", 0, 0
    finish_seen: float | None = None  # когда агент попросил завершить ход (finish_task)
    finish_answer = ""
    while time.monotonic() - t0 < timeout_s + 30:
        try:
            for ev in c.events(task_id, cursor):
                cursor = int(ev.get("seq") or cursor)
                data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                if ev.get("type") == "task_result" and task_status(data) in TERMINAL:
                    result = data
                elif ev.get("source") == "tools":
                    tools_rows.append(data)
                elif ev.get("source") == "events":
                    event_rows.append(data)
        except httpx.HTTPError as exc:
            log.warning("поток событий задачи %s: %s", task_id, exc)
        calls = tool_calls(tools_rows)
        if on_progress and len(calls) != seen_calls:
            seen_calls = len(calls)
            on_progress({"stage": "tools", "task_id": task_id, "tool_calls": calls})
        if result is not None:
            status = task_status(result)
            break
        t = c.task(task_id)
        if task_status(t).startswith("paused") or t.get("reason_code") in ("budget_paused", "owner_paused"):
            # пауза (потолок денег на задачу, paused_exact_continuation) ждёт владельца Ouroboros: для пользователя
            # консоли ход окончен — задача останавливается, чтобы не продолжилась позже сама
            c.cancel(task_id)
            status, result = "paused_budget", t
            break
        finishes = [
            x
            for x in calls
            if x["tool"] == "finish_task" and x["ok"] and (x.get("args") or {}).get("action") == "finish"
        ]
        if finishes and finish_seen is None:
            finish_seen, finish_answer = time.monotonic(), str((finishes[-1].get("args") or {}).get("answer") or "")
        if finish_seen is not None and time.monotonic() - finish_seen > FINISH_HOLD_S:
            # Ouroboros держит завершение (его приёмка ждёт acceptance_subject, который модель заполняет неверно), а
            # ответ агент уже выбрал: консоль закрывает ход сама — ответ проверит turns.assemble — и останавливает
            # задачу, чтобы она не тратила раунды до дедлайна
            c.cancel(task_id)
            status, result = "completed_by_console", t
            break
    else:
        c.cancel(task_id)
        status = "timeout"
    ms = int((time.monotonic() - t0) * 1000)
    t = result or {}
    answer = final_answer(t) if status == "completed" else ""
    if status == "completed" and not answer:  # итог потока без текста ответа — задача целиком
        t = c.task(task_id)
        answer = final_answer(t)
    if status == "completed_by_console" and len(finish_answer) < TOOL_ARG_LIMIT:  # длинные аргументы журнал обрезает
        answer = finish_answer
    return {
        "turn_id": turn_id,
        "conversation_id": conversation_id,
        "task_id": task_id,
        "question": question,
        "status": status,
        "answer": answer,
        "ms": ms,
        "task": _slim(t),
        "tool_calls": tool_calls(tools_rows),
        "llm_rounds": llm_rounds(event_rows),
        "description": description,
    }


def _slim(t: dict) -> dict:
    keep = (
        "status",
        "reason_code",
        "total_rounds",
        "prompt_tokens",
        "completion_tokens",
        "accounted_upper_bound_usd",
        "cost_breakdown",
        "model_execution",
        "error",
        "outcome_axes",
        "trace_refs",
    )
    out = {k: t.get(k) for k in keep if k in t}
    return json.loads(json.dumps(out, default=str))

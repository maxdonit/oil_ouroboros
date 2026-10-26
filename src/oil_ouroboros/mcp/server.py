"""
MCP-инструменты агента (Ouroboros видит их как mcp_oil__<имя>) на официальном MCP SDK (MCPServer): схема каждого
инструмента — сигнатура его функции, аргументы проверяет SDK. Инструменты анализа — графы LangGraph (graphs/),
list_reports — справка по базе отчётов (обычная функция).

Особенности клиента Ouroboros v7.7 (ouroboros/mcp_client.py), под которые сделан ответ:
- модель видит только name, description и inputSchema; строки описаний в схеме режутся до 512 символов, а
  описания и результаты помечены как «недоверенные данные» — поэтому ПРАВИЛА поведения живут в памяти агента
  (засев ouroboros/memory/knowledge/overview.md), а здесь только что делает инструмент и как его звать;
- structuredContent клиент дописывает к тексту, если он не совпадает с текстом побайтно, — результат отдаём
  одним компактным текстом без structuredContent (структура для консоли хранится в трейсе инструмента);
- общий таймаут вызова MCP_TOOL_TIMEOUT_SEC (в settings.json агента — 180 с): тяжёлые шаги укладываются в него.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import __version__, catalog
from ..config import get_settings
from ..evidence import ledger
from ..graphs import calc, research, runs, search_web, verify_answer
from ..graphs.kit import ToolRun
from ..reports import editions, journal, watch

log = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)
Bench = Annotated[Literal["brent", "wti", "urals"], Field(description="эталонный сорт")]


def _quote_instruments() -> dict:
    """Сорта котировок из каталога; каталог невалиден — только brent (сервис и так отвечает invalid_catalog)."""
    try:
        return {k: v.title for k, v in catalog.get().forecast.quote.instruments.items()}
    except catalog.CatalogError as exc:
        log.warning("схема price_quote без каталога (только brent): %s", exc)
        return {"brent": "Brent"}


QUOTES = _quote_instruments()
QuoteBench = Annotated[
    Literal[tuple(QUOTES)],
    Field(description="сорт: " + ", ".join(f"{k} — {v}" for k, v in QUOTES.items())),
]
Horizon = Annotated[int, Field(ge=1, le=12, description="горизонт, мес.")]
INSTRUCTIONS = (
    "Инструменты «Старшего аналитика нефтегазового рынка»: research — главный поиск (база отчётов агентств → оценка "
    "достаточности → веб по политике), search_reports / search_web — точечный добор, forecast_price / "
    "supply_shock_scenario / price_history — расчётный модуль, price_quote — текущие котировки, verify_answer — "
    "проверка цифр и метки источников."
)


def _text(run: ToolRun) -> dict[str, Any]:
    if run.error:  # ошибку графа агент получает результатом с isError (ToolError SDK)
        raise ToolError(f"{run.error} (trace {run.run_id})")
    return {
        "_text": run.state.get("result", ""),
        "_audit": {"trace": run.run_id, "ms": run.ms, "cost": round(run.usage.get("cost_usd", 0.0), 6)},
    }


def t_research(
    question: Annotated[str, Field(description="вопрос пользователя целиком (с учётом диалога)")],
    focus: Annotated[str, Field(description="необязательно: что именно искать (показатель, агентство, период)")] = "",
) -> dict:
    return _text(research.run(question, focus=focus))


def t_search_reports(
    queries: Annotated[list[str], Field(description="1–4 запроса, лучше на английском (корпус англоязычный)")],
    orgs: Annotated[
        list[str] | None, Field(description="необязательно: агентства из list_reports (OPEC, IEA, EIA, …)")
    ] = None,
    editions: Annotated[list[str] | None, Field(description="необязательно: выпуски ГГГГ-ММ")] = None,
    question: Annotated[str, Field(description="исходный вопрос (для отбора фрагментов)")] = "",
) -> dict:
    if not queries:
        raise ToolError("queries: нужен хотя бы один запрос (лучше на английском: корпус отчётов англоязычный)")
    return _text(
        research.run(
            question or queries[0],
            mode="reports",
            queries=queries,
            orgs=orgs,
            editions=editions,
            tool="search_reports",
        )
    )


def t_search_web(
    queries: Annotated[list[str], Field(description="1–2 запроса (лишние отбрасываются)")],
    pack_id: Annotated[str, Field(description="P# из результата research")],
    freshness: Literal["day", "week", "month", "year", "any"] = "month",
    reason: Annotated[str, Field(description="зачем нужен веб (чего нет в отчётах)")] = "",
) -> dict:
    if not queries:
        raise ToolError("queries: нужен хотя бы один запрос")
    return _text(search_web.run_web(queries, pack_id, freshness, reason))  # сколько запросов взять — решает каталог


def t_forecast(
    benchmark: Bench = "brent",
    horizon_months: Horizon = 3,
    method: Literal["auto", "ensemble", "ets", "arima", "theta", "drift", "rw"] = "auto",
) -> dict:
    return _text(
        calc.run_calc("forecast_price", {"benchmark": benchmark, "horizon_months": horizon_months, "method": method})
    )


def t_scenario(
    delta_mbd: Annotated[
        float, Field(ge=-30, le=30, description="изменение предложения, млн б/с (сокращение — отрицательное)")
    ],
    benchmark: Bench = "brent",
    horizon_months: Horizon = 3,
    compliance: Annotated[float, Field(ge=0, le=1, description="доля исполнения")] = 1.0,
    offset: Annotated[float, Field(ge=0, le=1, description="доля, компенсируемая другими")] = 0.0,
) -> dict:
    return _text(
        calc.run_calc(
            "supply_shock_scenario",
            {
                "delta_mbd": delta_mbd,
                "benchmark": benchmark,
                "horizon_months": horizon_months,
                "compliance": compliance,
                "offset": offset,
            },
        )
    )


def t_history(
    benchmark: Bench = "brent",
    start: Annotated[str, Field(description="ГГГГ-ММ-ДД")] = "2025-01-01",
    freq: Literal["M", "D"] = "M",
) -> dict:
    return _text(calc.run_calc("price_history", {"benchmark": benchmark, "start": start, "freq": freq}))


def t_quote(benchmark: QuoteBench = "brent") -> dict:
    return _text(calc.run_calc("price_quote", {"benchmark": benchmark}))


# Указание о следующем шаге после проверки — единственное место в коде; правило за ним — в регламенте агента
# (knowledge/overview.md), число проверок — в каталоге (verify.max_checks). finish_task — отдельным шагом: в одной
# пачке с другими вызовами Ouroboros завершение не принимает (unobserved_tool_results)
FINISH = 'заверши ход отдельным шагом, без других полей: finish_task({{"action": "finish", "answer": <{what}>}}).'
SHOWN = "текст для показа ниже, символ в символ"
DRAFT = "этот черновик как есть, со ссылками [R#]/[W#]/[F#]"
EDITED = "исправленный черновик со ссылками [R#]/[W#]/[F#]"


def next_step(ok: bool, render: bool, attempt: int, limit: int) -> str:
    """Что делать после проверки: завершать, проверить снова или — проверок больше не будет — завершать без неё."""
    if ok:
        return (
            "Проверка пройдена: "
            + FINISH.format(what=SHOWN if render else DRAFT)
            + ("" if render else " Метки источников, список источников и строку проверки добавит консоль.")
        )
    if attempt < limit:
        return f"Исправь черновик по замечаниям и вызови verify_answer снова (проверка {attempt} из {limit})."
    last = f"Лимит проверок исчерпан ({limit})"
    if render:
        return f"{last}: {FINISH.format(what=SHOWN)} Неподтверждённые цифры в нём помечены."
    return (
        f"{last}: убери цифры и ссылки, на которые есть замечания (цифру можно сформулировать качественно), затем "
        f"{FINISH.format(what=EDITED)} Новая проверка не нужна: консоль перепроверит текст."
    )


def checks_before(packs: list[str]) -> int:
    """Сколько проверок черновика уже было в этом ходе: прогоны verify_answer последних 20 минут с общими пакетами.
    MCP-вызов не несёт id хода, а пакеты research/расчётов у каждого хода свои; пакет диалога (история разговора)
    бывает общим у соседних ходов, поэтому не в счёт."""
    own = {p for p in packs if (ledger.pack(p) or {}).get("tool") not in (None, "dialog")}
    if not own:
        return 0
    since = time.time() - ledger.PACK_MAX_AGE_S
    return sum(1 for a in runs.recent_args("mcp:verify_answer", since) if own & set(pack_list(a.get("pack_ids"))))


def pack_list(raw: list | str | None) -> list[str]:
    """«P12», «[P12]», «пакет Р12» (кириллическая Р), 12 → P12; повторы убираются."""
    out: list[str] = []
    for x in raw if isinstance(raw, list) else [raw]:
        s = str(x if x is not None else "").strip()
        out += [f"P{n}" for n in re.findall(r"[PР](\d+)", s) or ([s] if s.isdigit() else [])]
    return list(dict.fromkeys(out))[:30]


def t_verify(
    answer: Annotated[str, Field(description="черновик ответа со ссылками [R#], [W#], [F#]")],
    pack_ids: Annotated[
        list[str | int] | str,
        Field(
            description="номера всех пакетов P# из результатов инструментов этого хода "
            "(research, search_*, forecast_price и др.) и пакет диалога, если "
            "он указан в задаче"
        ),
    ],
    question: Annotated[str, Field(description="вопрос пользователя")] = "",
    render: Annotated[
        bool,
        Field(
            description="по умолчанию true: вернуть и текст для показа с метками источников — им и "
            "завершить ответ; false — только вердикт (ход консоли: метки ставит консоль)"
        ),
    ] = True,
) -> dict:
    """Чат владельца, Telegram, расписание: меток за агента никто не поставит — он завершает текстом для показа.
    Ход консоли просит render=false: консоль сама перепроверяет показываемый текст и ставит метки.

    MCP-вызов не несёт id хода, поэтому агент перечисляет пакеты своего хода (pack_ids): ссылка на доказательство вне
    них — чужая (из другой задачи или из памяти), проверка не пройдена."""
    if not answer.strip():
        raise ToolError("answer: пустой черновик")
    packs = pack_list(pack_ids)
    attempt, limit = checks_before(packs) + 1, catalog.get().verify.max_checks
    run = verify_answer.run_verify(answer, question, pack_ids=packs)
    out, ok = _text(run), bool(run.state.get("ok"))
    if not packs:
        out["_text"] += (
            "\npack_ids не переданы: принадлежность ссылок этому ходу проверена приблизительно — "
            "передай номера пакетов P# из результатов инструментов этого хода."
        )
    out["_text"] += "\n" + next_step(ok, render, attempt, limit)
    out["_audit"]["check"] = f"{attempt}/{limit}"
    if render:
        note = "" if ok else "; проверка не пройдена — неподтверждённые цифры помечены"
        out["_text"] += f"\n\nТекст для показа (с метками источников{note}):\n" + run.state.get("final_markdown", "")
    return out


def t_list_reports() -> dict:
    docs = sorted(editions.load(), key=lambda d: (d.org, d.report, d.edition))
    rows = [f"- {d.org} {d.report} «{d.title}», выпуск {d.edition}, {d.published_ru}" for d in docs]
    now = datetime.now(timezone.utc).astimezone()
    upcoming = [
        f"{src['series']} {n[0]} — {n[1]}" for src in watch.sources() if (n := watch.next_release(src, now.date()))
    ]
    hours, last = get_settings().reports_watch_hours, journal.last_check()
    gaps = [
        # проверка не получила выпуск: нет по ссылкам, закрыт от ботов, по ссылке другой выпуск, ошибка
        f"{x['series']} {x['edition']} — {watch.STATUS_RU.get(x['status'], x['status'])}"
        for x in last.get("items") or []
        if x.get("status") not in ("present", "scheduled", "downloaded")
    ]
    return {
        "_text": f"Сейчас: {now.isoformat(timespec='seconds')}.\nБаза отчётов (RAG):\n"
        + "\n".join(rows)
        + f"\nНаблюдатель выпусков: последняя проверка сайтов агентств — {last.get('finished') or 'не было'}; "
        + (f"проверяет каждые {hours:g} ч." if hours > 0 else "только вручную.")
        + (f"\nПоследняя проверка не получила: {'; '.join(gaps)}." if gaps else "")
        + "\nБлижайшие выпуски по графику: "
        + ("; ".join(upcoming) or "графика нет")
        + "."
        + "\nОтчёты — приоритетный источник; события после дат публикации — только из веба."
    }


# имя · функция · описание для модели · подсказки клиенту; схема аргументов — сигнатура функции
TOOLS = [
    (
        "research",
        t_research,
        "Главный поиск ответа: база отчётов агентств (состав — list_reports) → отбор фрагментов и оценка "
        "достаточности → веб по политике приоритета (свежесть, нехватка данных). "
        "Возвращает пакет P# с доказательствами R# (отчёты) и W# (веб).",
        READ_ONLY,
    ),
    (
        "search_reports",
        t_search_reports,
        "Точечный поиск по базе отчётов (без веба), когда research не нашёл нужное: свои "
        "запросы, фильтр агентств и выпусков.",
        READ_ONLY,
    ),
    (
        "search_web",
        t_search_web,
        "Дополнительный веб-поиск свежих данных (новости, котировки, решения регуляторов) с фильтром "
        "жёлтой прессы. Только после research: передай его pack_id. Возвращает W#.",
        READ_ONLY.model_copy(update={"open_world_hint": True}),
    ),
    (
        "forecast_price",
        t_forecast,
        "Статистический прогноз цены нефти на 1–12 мес.: медиана, интервалы 50/80/95% по бэктесту, "
        "скилл против random walk, интерпретация и оговорки. Возвращает F#.",
        READ_ONLY,
    ),
    (
        "supply_shock_scenario",
        t_scenario,
        "Сценарий изменения предложения нефти: delta_mbd < 0 — сокращение добычи (например, "
        "ОПЕК+ −2 млн б/с), > 0 — рост; диапазон влияния на цену поверх базового прогноза. Возвращает F#.",
        READ_ONLY,
    ),
    (
        "price_history",
        t_history,
        "Фактические цены нефти (EIA): месячные или дневные с даты start. Возвращает F#.",
        READ_ONLY,
    ),
    (
        "price_quote",
        t_quote,
        "Текущая цена сорта — для вопросов «сколько сейчас стоит», «курс», «котировка»: фьючерс ближайшего месяца "
        "на Мосбирже (единица, время сделки, задержка 15 мин) и последняя спот-цена EIA с датой, если она есть. "
        "Возвращает F#.",
        READ_ONLY.model_copy(update={"open_world_hint": True}),
    ),
    (
        "verify_answer",
        t_verify,
        "Проверка черновика ответа: каждая цифра ↔ процитированный источник [R#/W#/F#] из журнала "
        "доказательств; ссылка не из пакетов этого хода — чужая. Возвращает вердикт, замечания и следующий шаг; "
        "с render=true — ещё и текст для показа с метками источников.",
        READ_ONLY,
    ),
    (
        "list_reports",
        t_list_reports,
        "Какие отчёты и выпуски есть в базе (серии, месяцы, даты публикации), когда наблюдатель "
        "последний раз проверял сайты агентств, ближайшие выпуски по графику и текущее время.",
        READ_ONLY.model_copy(update={"idempotent_hint": True}),
    ),
]


AUDIT_MAX_BYTES = 20 * 2**20  # журнал вызовов MCP: при 20 МБ — в mcp_audit.jsonl.1, хранится одна прошлая часть


def _audit(rec: dict[str, Any]) -> None:
    p = get_settings().var_dir / "mcp_audit.jsonl"
    if p.is_file() and p.stat().st_size > AUDIT_MAX_BYTES:
        p.replace(p.with_name(p.name + ".1"))
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _audited(name: str, fn):
    """Вызов инструмента → текст для модели и запись в журнал (var/mcp_audit.jsonl): аргументы коротко, итог, время и
    что записал сам инструмент (trace, номер проверки). Результат — одним текстом, без structuredContent: клиент
    Ouroboros дописывает его к тексту, и модель платила бы за результат дважды."""

    @functools.wraps(fn)
    def run(**kwargs: Any) -> str:
        started, ok, error, extra, text = time.monotonic(), True, "", {}, ""
        try:
            value = fn(**kwargs)
            extra, text = value.get("_audit") or {}, str(value["_text"])
            return text
        except Exception as exc:
            ok, error = False, str(exc) if isinstance(exc, ToolError) else f"{type(exc).__name__}: {exc}"
            raise
        finally:
            shown = {
                k: v if isinstance(v, (int, float, bool)) or len(str(v)) <= 80 else f"<{len(str(v))} симв.>"
                for k, v in kwargs.items()
            }
            _audit(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "tool": name,
                    "args": shown,
                    "ok": ok,
                    "error": error[:300],
                    "ms": int((time.monotonic() - started) * 1000),
                    **extra,
                    "chars": len(text),
                }
            )

    return run


@functools.cache
def server() -> MCPServer:
    srv = MCPServer("oil-ouroboros-tools", instructions=INSTRUCTIONS, version=__version__)
    for name, fn, description, hints in TOOLS:
        srv.add_tool(
            _audited(name, fn), name=name, description=description, annotations=hints, structured_output=False
        )
    return srv


async def call(name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Вызов инструмента так, как его увидит агент, — (текст, ошибка ли): `oilo tool` и тесты."""
    try:
        result = await server().call_tool(name, arguments)
    except Exception as exc:  # noqa: BLE001 — агенту SDK отдаёт то же: текст ошибки с isError
        return str(exc), True
    return result.content[0].text, bool(result.is_error)

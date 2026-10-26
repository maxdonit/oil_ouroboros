"""
Демо-диалоги — настоящие ходы агента (Ouroboros → инструменты LangGraph), записанные в markdown и в файл
проигрывателя консоли.

    oilo demo [--out var/demo_dialogs.md] [--replay var/demo_turns.json]
    oilo demo --export-only            # записанные ходы → файл проигрывателя, без новых вызовов агента

Сценарии: ответ по отчёту, по вебу, комбинированный, прогноз (+ уточнение в том же разговоре), сценарий шока
предложения, вне компетенции. Каждый ход сохраняется в журнал ходов консоли (источник demo) — его трейс открывается
во вкладке «Ходы». Файл проигрывателя (в репозитории — data/demo/turns.json) показывает те же ходы по шагам во вкладке
«Демо» без LLM и без агента: на чистом деплое и без ключа LLM (ни GigaChat, ни OpenRouter).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .. import agent
from ..config import ROOT
from . import fingerprint, turns

REPLAY_FILE = ROOT / "data" / "demo" / "turns.json"  # что проигрывает вкладка «Демо»
DROP = ("task",)  # объект задачи Ouroboros целиком: для показа хода не нужен, а весит треть записи
CHECKS_RU = [
    ("research_first", "сначала отчёты"),
    ("verify_ok", "цифры проверены"),
    ("answer_is_checked_text", "ответ = проверенный текст"),
    ("citations_in_turn", "источники этого хода"),
    ("labels", "метки источников"),
    ("language_ok", "язык вопроса"),
]


def _rounds(n: int) -> str:
    word = (
        "раунд"
        if n % 10 == 1 and n % 100 != 11
        else "раунда"
        if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)
        else "раундов"
    )
    return f"{n} {word}"


SCENARIOS = [
    ("по отчётам", "Сравни прогнозы изменения мирового спроса на нефть в 2026 году от OPEC и IEA.", None),
    ("по вебу (свежие данные)", "Какие последние новости о решениях ОПЕК+ по добыче на этой неделе?", None),
    (
        "комбинированный",
        "Что EIA прогнозирует по цене Brent до конца 2026 года и какие свежие события могут изменить прогноз?",
        None,
    ),
    ("прогноз (расчётный модуль)", "Спрогнозируй цену Brent на 3 месяца.", "forecast"),
    ("уточнение в том же разговоре", "А что по WTI на тот же срок?", "forecast"),
    ("сценарий", "Оцени диапазон цены Brent при сокращении добычи ОПЕК+ на 2 млн б/с.", None),
    ("вне компетенции", "Посоветуй рецепт борща.", None),
]


def export(out: Path) -> dict:
    """Последний прогон демо из журнала ходов → файл проигрывателя: сценарии по порядку, с заголовками."""
    rows = turns.listing(len(SCENARIOS), source="demo")[::-1]
    titles = {q: k for k, q, _ in SCENARIOS}
    items = []
    for r in rows:
        t = {k: v for k, v in (turns.get(r["turn_id"]) or {}).items() if k not in DROP}
        fp = t.get("fingerprint") or {}
        t["fingerprint"] = {k: fp.get(k) for k in ("agent_version", "catalog")}
        items.append({"title": titles.get(t.get("question", ""), ""), "recorded": r["started"], **t})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"recorded": time.strftime("%Y-%m-%d %H:%M"), "turns": items}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return {"replay": str(out), "turns": len(items), "kb": out.stat().st_size // 1024}


def replay(path: Path | None = None) -> dict:
    try:
        return json.loads((path or REPLAY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"recorded": "", "turns": []}


def run(out: Path, replay_out: Path | None = None) -> dict:
    fp = fingerprint.current(max_age_s=0)
    convs: dict[str, str] = {}
    lines = [
        "# Демо-диалоги",
        "",
        f"Реальные ходы агента: Ouroboros v7.7.0 (`{(fp.get('settings') or {}).get('OUROBOROS_MODEL', '')}`) "
        f"→ инструменты LangGraph по MCP. Записаны {time.strftime('%Y-%m-%d %H:%M')} командой `oilo demo`; "
        f"версия агента `{fp.get('agent_version')}`, каталог инструментов `{fp.get('catalog')}`.",
        "",
        "Метки источников, список источников и строку проверки ставит система (финал хода в сервисе инструментов) "
        "после проверки цифр по доказательствам хода. Каждый ход по шагам — вкладка консоли «Демо» (запись — "
        "[data/demo/turns.json](../data/demo/turns.json), без LLM и без агента).",
        "",
    ]
    total_cost, total_ms = 0.0, 0
    for i, (kind, question, conv_key) in enumerate(SCENARIOS, start=1):
        conv = (
            convs.setdefault(conv_key, f"demo-{uuid.uuid4().hex[:8]}") if conv_key else f"demo-{uuid.uuid4().hex[:8]}"
        )
        hist = turns.history(conv)
        hist_ids = [x for h in hist for x in h.get("ids", [])]
        t0 = time.time()
        raw = agent.ask(
            question, history=hist, conversation_id=conv, dialog_pack=turns.dialog_pack(hist_ids, question)
        )
        t = turns.assemble(raw, fp, history_ids=hist_ids)
        turns.save(t, source="demo", started=t0)
        total_cost += float(t.get("cost_usd") or 0.0)
        total_ms += int(t.get("ms") or 0)
        path = (
            " → ".join(c["tool"] for c in t.get("tool_calls", []) if c["tool"] != "finish_task") or "без инструментов"
        )
        ch = t.get("checks") or {}
        v = t.get("verification") or {}
        lines += [
            f"## {i}. {kind}",
            "",
            f"**Вопрос:** {question}",
            "",
            f"**Ход агента:** {path} · {_rounds(t.get('rounds') or 0)} · {t.get('ms', 0) / 1000:.0f} с · "
            f"${t.get('cost_usd', 0):.4f} · ход `{t.get('turn_id')}`"
            + (" · завершён консолью" if ch.get("finish_held") else ""),
            "",
            "**Проверки:** "
            + (
                "вежливый отказ вне компетенции, без инструментов"
                if ch.get("refused")
                else ", ".join(title for k, title in CHECKS_RU if ch.get(k))
                + (f" · цифр подтверждено {v.get('numbers_verified', 0)}/{v.get('numbers_total', 0)}" if v else "")
            ),
            "",
            "**Ответ:**",
            "",
            *("> " + line if line.strip() else ">" for line in (t.get("answer") or "(нет ответа)").splitlines()),
            "",
        ]
    lines += [f"Итого: {len(SCENARIOS)} ходов, {total_ms / 1000:.0f} с, ${total_cost:.4f}.", ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    rep = export(replay_out) if replay_out else {}
    return {"turns": len(SCENARIOS), "cost_usd": round(total_cost, 4), "ms": total_ms, "out": str(out), **rep}

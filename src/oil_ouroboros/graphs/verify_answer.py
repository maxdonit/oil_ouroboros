"""
Инструмент verify_answer — граф LangGraph: проверка черновика ответа кодом, а не моделью.

    resolve → check → render

- resolve: ссылки ответа → доказательства этого хода (пакеты, которые перечислил агент, или точный список консоли);
- check: каждая цифра ↔ процитированный источник (evidence/verify.py);
- render: текст для показа с метками источников (graphs/render.py) и замечания для агента.

Здесь же финал хода консоли (finalize, POST /api/finalize): какой текст увидит пользователь — решает сервис
инструментов, где лежат доказательства, а не консоль.
"""

from __future__ import annotations

import functools
import re
from typing import TypedDict

from .. import catalog
from ..evidence import ledger
from ..evidence.text import cited_ids, lang_of, normalize_ids
from ..evidence.verify import verify
from . import kit, runs
from .render import render_final


class VerifyState(TypedDict, total=False):
    run_id: str
    answer: str
    question: str
    allowed: list[str]  # доказательства хода: от консоли (точно) или из пакетов агента (pack_ids)
    pack_ids: list[str]  # пакеты P# этого хода, которые перечислил агент
    packs: list[str]  # принятые пакеты
    packs_skipped: list[str]  # отклонённые: нет такого или старше окна хода
    cited: list[str]
    evidence: dict
    verification: dict
    final_markdown: str
    ok: bool
    result: str
    path: list[str]


def v_resolve(state: VerifyState) -> dict:
    """Какие доказательства — этого хода: список консоли; иначе пакеты, которые перечислил агент; без них —
    приближение по последнему пакету каждой цитаты («соседи»)."""
    cited = cited_ids(state["answer"])
    allowed, packs, skipped = list(state.get("allowed") or []), [], []
    if not allowed and state.get("pack_ids"):
        allowed, packs, skipped = ledger.items_of_packs(state["pack_ids"], max_age_s=ledger.PACK_MAX_AGE_S)
    if allowed:
        ids = list(dict.fromkeys(cited + allowed))
    else:
        hours = catalog.get().verify.neighbours_hours
        ids = list(dict.fromkeys(cited + ledger.neighbours(cited, max_age_s=hours * 3600)))
    return {"cited": cited, "evidence": ledger.get(ids), "allowed": allowed, "packs": packs, "packs_skipped": skipped}


def v_check(state: VerifyState) -> dict:
    """Код (evidence/verify.py): каждое число ответа ищется в тексте процитированного источника из журнала — с учётом
    десятичной запятой, единиц и масштаба, направления; ссылка вне доказательств хода — чужая; цифра только из веба
    хуже допустимого уровня — слабая."""
    allowed = set(state["allowed"]) if state.get("allowed") else None
    v = verify(state["answer"], state.get("evidence") or {}, state.get("question", ""), allowed=allowed)
    return {"verification": v.as_dict(), "ok": v.ok}


def v_render(state: VerifyState) -> dict:
    """Код (graphs/render.py): текст для показа — ссылки заменены метками источников, список источников и строка
    проверки на языке вопроса; агенту — вердикт и замечания (что исправить)."""
    v = state["verification"]
    allowed = set(state.get("allowed") or [])
    # доказательство не этого хода показывается как «источник не найден», а не меткой чужого источника
    ev = {k: e for k, e in (state.get("evidence") or {}).items() if not allowed or k in allowed}
    final = render_final(
        v.get("fixed_text") or state["answer"], ev, v, lang_of(state.get("question") or state["answer"])
    )
    c = catalog.get()
    lines = [
        f"Проверка ответа: {'ПРОЙДЕНА' if v['ok'] else 'НЕ ПРОЙДЕНА'} — "
        f"цифр {v['numbers_verified']}/{v['numbers_total']} подтверждены источниками; "
        f"ссылок {v['citations_total'] - v['citations_unknown']}/{v['citations_total']} корректны "
        f"· trace {state.get('run_id', '')}"
    ]
    hard = [i for i in v["issues"] if i["type"] in ("unknown_citation", "unverified_number", "weak_source")]
    derived = [i for i in v["issues"] if i["type"] == "derived_number"]
    soft = [i for i in v["issues"] if i not in hard and i not in derived]
    if hard:  # что делать дальше — проверить снова или завершать — добавляет verify_answer (mcp/server.py: next_step)
        lines.append(
            "Замечания (цифру или ссылку без источника этого хода убери, цифру можно сформулировать качественно):"
        )
        for i in hard[: c.verify.max_issues]:
            lines.append(f"- {i['type']}: «{i['sentence'][:160]}» {i.get('number', '')} — {i.get('suggestion', '')}")
    packs, skipped = state.get("packs") or [], state.get("packs_skipped") or []
    if packs and v.get("citations_foreign"):
        lines.append(
            f"Доказательства этого хода — пакеты {', '.join(packs)}. Ссылка не из них — чужая: если её источник "
            "получен в этом ходе, добавь его пакет в pack_ids, иначе убери ссылку."
        )
    if skipped:
        lines.append("Пакеты не приняты: " + "; ".join(skipped) + ".")
    if soft:
        lines.append(f"Ссылки исправлены автоматически: {len(soft)} (число найдено в другом надёжном источнике).")
    if derived:
        lines.append(
            "Расчёты воспроизведены по подтверждённым цифрам: "
            + "; ".join(f"{i.get('number')} = {i.get('suggestion', '').split(': ', 1)[-1]}" for i in derived[:5])
            + "."
        )
    if not state.get("cited"):
        lines.append("В черновике нет ни одной ссылки [R#]/[W#]/[F#]: если в ответе есть цифры, их нужно подтвердить.")
    return {
        "final_markdown": final,
        "result": "\n".join(lines),
    }  # как завершить ход — добавляет verify_answer (mcp/server.py)


NODES = [
    kit.Node(
        "resolve",
        v_resolve,
        "Ссылки ответа → доказательства хода: пакеты агента или список консоли",
        "code",
        ("verify.neighbours_hours",),
    ),
    kit.Node(
        "check",
        v_check,
        "Каждая цифра ↔ процитированный источник (код, не LLM)",
        "code",
        ("web.min_tier_for_numbers",),
    ),
    kit.Node(
        "render",
        v_render,
        "Метки источников, список источников, строка проверки",
        "code",
        ("verify.max_issues", "verify.max_checks"),
    ),
]
EDGES = [("resolve", "check"), ("check", "render")]


@functools.cache
def graph():
    return kit.build(VerifyState, NODES, EDGES)


def run_verify(
    answer: str,
    question: str = "",
    allowed: list[str] | None = None,
    tool: str = "verify_answer",
    pack_ids: list[str] | None = None,
) -> kit.ToolRun:
    """allowed — точный список доказательств хода (консоль); pack_ids — пакеты хода, которые перечислил агент."""
    answer = normalize_ids(answer)  # [Р12] → [R12]: ссылки и метки источников — по латинским id журнала
    inputs = {"answer": answer, "question": question} | ({"allowed": list(allowed)} if allowed else {})
    args = {"answer": f"<{len(answer)} симв.>", "question": question} | ({"allowed": len(allowed)} if allowed else {})
    if pack_ids:
        inputs["pack_ids"] = args["pack_ids"] = list(pack_ids)
    return kit.run_graph(tool, graph(), inputs, args, question=question or answer[:200])


# ---------------------------------------------------------------------------------------------------------------
# финал хода консоли: что увидит пользователь
# ---------------------------------------------------------------------------------------------------------------


def _norm(text: str | None) -> str:
    """Сравнение текстов без пробелов и жирного шрифта: агент может перенести строку при копировании."""
    return re.sub(r"\s+", " ", (text or "").replace("**", "")).strip()


def pick_shown(answer: str, drafts: list[dict], check) -> dict:
    """Какой текст показать пользователю и с какой проверкой.

    answer — чем агент завершил ход; drafts — черновики этого хода, прошедшие verify_answer, по порядку
    ({draft: текст со ссылками, shown: его текст для показа}); check(text) → {markdown, verification, trace} —
    проверка по доказательствам именно этого хода.

    source: verified_draft — показан черновик, прошедший verify_answer · reverified_final — показан текст агента,
    который отличается от проверенных черновиков (или черновиков нет); он проверен здесь, итог — в verification ·
    fallback_verified_draft — агент не завершил или его правка не прошла: показан последний проверенный черновик ·
    unverified — показывать нечего (пустой ответ без проверенных черновиков)."""
    text, shown, final_v, trace, source, rejected = answer, answer, {}, "", "unverified", {}
    as_shown = next((d for d in reversed(drafts) if d.get("shown") and _norm(d["shown"]) == _norm(text)), None)
    if as_shown:  # агент завершил текстом для показа проверенного черновика (render=true) — это тот же черновик
        text = as_shown.get("draft", "")
    if not text.strip() and drafts:
        text, source = drafts[-1].get("draft", ""), "fallback_verified_draft"
    if text.strip():
        fv = check(text)
        shown, final_v, trace = fv.get("markdown") or text, fv.get("verification") or {}, fv.get("trace", "")
        if source != "fallback_verified_draft":
            source = (
                "verified_draft" if any(_norm(d.get("draft")) == _norm(text) for d in drafts) else "reverified_final"
            )
        if source == "reverified_final" and not final_v.get("ok") and drafts:
            # агент переписал проверенный черновик, и правка проверку не прошла: пользователю — проверенный черновик
            draft = drafts[-1].get("draft", "")
            fd = check(draft)
            if (fd.get("verification") or {}).get("ok"):
                rejected = final_v
                text, shown, final_v, trace = (
                    draft,
                    fd.get("markdown") or draft,
                    fd["verification"],
                    fd.get("trace", ""),
                )
                source = "fallback_verified_draft"
    return {
        "text": text,
        "shown": shown,
        "verification": final_v,
        "trace": trace,
        "source": source,
        "rejected": rejected,
    }


def finalize(answer: str, question: str, traces: list[str], history_ids: list[str] | None = None) -> dict:
    """Финал хода консоли. Агент завершает ход проверенным черновиком со ссылками [R#]/[W#]/[F#]; здесь показываемый
    текст проверяется заново по доказательствам ИМЕННО этого хода — выданным прогонами его инструментов (traces) и
    прошлыми репликами разговора (history_ids), — а метки, список источников и строку проверки ставит код, не модель.

    Возвращает pick_shown(…) и allowed — доказательства хода (ссылка вне них — чужая)."""
    allowed = sorted(set(history_ids or []) | set(ledger.items_of_runs(traces)))
    drafts = []
    for t in traces:
        run = runs.get_run(t) or {}
        st = run.get("state") or {}
        if run.get("source") == "mcp:verify_answer" and (st.get("verification") or {}).get("ok"):
            drafts.append({"draft": st.get("answer", ""), "shown": st.get("final_markdown", "")})

    def check(text: str) -> dict:
        run = run_verify(text, question, allowed=allowed or ["-"], tool="verify_final")
        if run.error:
            return {"verification": {"ok": False, "error": run.error[:200]}, "trace": run.run_id}
        st = run.state
        return {
            "markdown": st.get("final_markdown", ""),
            "verification": st.get("verification") or {},
            "trace": run.run_id,
        }

    return pick_shown(normalize_ids(answer), drafts, check) | {"allowed": allowed}

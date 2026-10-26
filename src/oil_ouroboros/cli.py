"""
Командная строка `oilo`: по функции на команду — ingest, reports, catalog, prices, forecast, tool, ask, smoke, demo,
eval, eval-retrieval, eval-report, compare.

Сервисы запускаются точкой входа контейнера (docker/entrypoint.sh): tools, console, gateway, encoder, bootstrap,
schedules, egress.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=1, default=str))


def cmd_ingest(a: argparse.Namespace) -> int:
    from .config import get_settings
    from .rag import ingest

    if get_settings().encoder_url:  # Docker: разбор и векторы — в контейнере кодировщика
        rep = {"busy": True}
        while rep.get("busy"):  # явный запрос: занят чужой загрузкой — дождаться её и запустить свою
            rep = ingest.run_via_encoder(a.recreate, a.only)
    else:
        rep = ingest.run(a.recreate, a.parser, a.only)
    _print_json(rep)
    return 0


def cmd_reports(a: argparse.Namespace) -> int:
    from .reports import watch

    if a.upload:
        item = watch.upload(Path(a.upload).read_bytes(), Path(a.upload).name)
        print(f"загружен: {item['series']} {item['edition']} → {item['file']}")
        if not a.no_index:
            print(json.dumps(watch.index_now(trigger="cli"), ensure_ascii=False))
    if a.check:
        res = watch.check(index=not a.no_index, trigger="cli")
        for x in res.get("items", []):
            status = watch.STATUS_RU.get(x["status"], x["status"])
            print(f"{x['series']:<10} {x['edition']}  {status:<28} {x.get('note', '')}")
        print("индекс:", json.dumps(res.get("index"), ensure_ascii=False))
    st = watch.status()
    print("\nвыпуск                 источник  файл  фрагментов")
    for d in st["catalog"]:
        present = "есть" if d["present"] else "нет "
        print(f"{d['series']:<10} {d['edition']:<11} {d['source']:<9} {present:<5} {d['chunks'] or '—'}")
    if st["pending"]:
        print("ждут индекса:", ", ".join(st["pending"]))
    return 0


def cmd_catalog(a: argparse.Namespace) -> int:
    from . import catalog

    try:
        c = catalog.load()
    except catalog.CatalogError as exc:
        print(exc)
        return 2
    if a.action == "check":
        print(f"каталог валиден: {c.ref} (файлов: {len(c.lock)})")
    elif a.action == "show":
        d = catalog.describe()
        print(d["ref"])
        for r in d["values"]:
            print(f"  {r['key']:<34} {str(r['value']):<28} [{r['layer']}] {r['description']}")
        for k, p in d["prompts"].items():
            print(f"  prompts/{k:<26} v{p['version']} #{p['sha']}  {p['purpose']}")
    else:
        other = catalog.load(slot=a.other or "current")
        print(f"{c.ref}  vs  {other.ref}")
        for f in sorted(set(c.lock) | set(other.lock)):
            x, y = c.lock.get(f), other.lock.get(f)
            if x != y:
                print(f"  {f:<30} {x or '—':<22} → {y or '—'}")
    return 0


def cmd_prices(a: argparse.Namespace) -> int:
    from .forecast import data

    if a.action == "refresh":
        _print_json(data.refresh())
    else:
        for b in data.SERIES:
            ps = data.load(b)
            print(b, ps.as_of, ps.last_price, ps.source_file)
    return 0


def cmd_forecast(a: argparse.Namespace) -> int:
    from .forecast import service

    r = service.forecast(a.benchmark, a.horizon, a.method)
    print(r.interpretation)
    for pt in r.path:
        print(pt)
    return 0


def cmd_tool(a: argparse.Namespace) -> int:
    import anyio

    from .mcp.server import call

    args = {}
    for kv in a.args:
        k, _, v = kv.partition("=")
        try:
            args[k] = json.loads(v)
        except json.JSONDecodeError:
            args[k] = v
    text, is_error = anyio.run(call, a.name, args)
    print(text)
    return 1 if is_error else 0


def cmd_ask(a: argparse.Namespace) -> int:
    from . import agent
    from .console import fingerprint, turns

    t0 = time.time()
    turn = turns.assemble(agent.ask(a.question), fingerprint.current())
    turns.save(turn, source="cli", started=t0)  # ход виден в консоли («Ходы»)
    if a.json:
        _print_json(turn)
    else:
        print(turn.get("answer", ""))
        calls = " → ".join(c["tool"] for c in turn.get("tool_calls", []))
        print(f"\n[{turn.get('ms', 0) / 1000:.1f} c · ${turn.get('cost_usd', 0):.4f} · инструменты: {calls or '—'}]")
    return 0 if turn.get("answer") else 1


def cmd_smoke(a: argparse.Namespace) -> int:
    from .console import smoke

    r = smoke.run(a.question) if a.question else smoke.run()
    for c in r["checks"]:
        print(f"  {'ok ' if c['ok'] else 'НЕТ'} {c['check']:<22} {c['detail']}")
    _print_json({k: v for k, v in r.items() if k != "checks"})
    return 0 if r["ok"] else 1


def cmd_demo(a: argparse.Namespace) -> int:
    from .console import demo

    rep = demo.export(Path(a.replay)) if a.export_only else demo.run(Path(a.out), Path(a.replay))
    print(json.dumps(rep, ensure_ascii=False))
    return 0


def cmd_eval(a: argparse.Namespace) -> int:
    from .evals import runner

    rec = runner.run(Path(a.set), a.repeats, a.label, a.only)
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(runner.report_markdown(rec), encoding="utf-8")
    _print_json({"eval_id": rec["eval_id"], "config": rec["config"], "summary": rec["summary"]})
    return 0


def cmd_eval_retrieval(a: argparse.Namespace) -> int:
    from .evals import retrieval

    rec = retrieval.run(Path(a.set), a.only, select=not a.no_select, plans_from=a.plans, label=a.label)
    if a.json:
        _print_json(rec)
    else:
        print(retrieval.report_text(rec))
    return 0


def cmd_eval_report(a: argparse.Namespace) -> int:
    from .evals import runner

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(runner.report_markdown(runner.load(a.eval_id)), encoding="utf-8")
    print(out)
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    from .evals import runner

    _print_json(runner.compare(runner.load(a.base), runner.load(a.cand)))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="oilo", description="Нефтегазовый аналитик на Ouroboros: инструменты, база отчётов, замеры"
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name: str, func, help: str) -> argparse.ArgumentParser:  # noqa: A002 — как у argparse
        sp = sub.add_parser(name, help=help)
        sp.set_defaults(func=func)
        return sp

    ing = add("ingest", cmd_ingest, "загрузить отчёты в Qdrant (в Docker — в контейнере кодировщика)")
    ing.add_argument("--recreate", action="store_true")
    ing.add_argument("--parser", default="auto", choices=["auto", "docling", "pypdf"])
    ing.add_argument("--only", nargs="*")

    rep = add("reports", cmd_reports, "база отчётов: выпуски по сериям, проверка новых, ручная загрузка")
    rep.add_argument("--check", action="store_true", help="проверить и скачать новые выпуски")
    rep.add_argument("--no-index", action="store_true", help="не добавлять скачанное в индекс")
    rep.add_argument("--upload", help="загрузить PDF вручную (серия и выпуск — по обложке)")

    cat = add("catalog", cmd_catalog, "каталог поведения config/: проверка, итоговые значения, сравнение слотов")
    cat.add_argument("action", choices=["check", "show", "diff"], nargs="?", default="check")
    cat.add_argument("other", nargs="?", help="diff: второй слот (например exp-topk8)")

    pr = add("prices", cmd_prices, "цены EIA: обновить или показать")
    pr.add_argument("action", choices=["refresh", "show"])

    fc = add("forecast", cmd_forecast, "прогноз из командной строки")
    fc.add_argument("--benchmark", default="brent")
    fc.add_argument("--horizon", type=int, default=3)
    fc.add_argument("--method", default="auto")

    tl = add(
        "tool", cmd_tool, 'вызвать инструмент агента напрямую (как MCP), например: oilo tool research question="…"'
    )
    tl.add_argument("name")
    tl.add_argument("args", nargs="*", help="ключ=значение (значение — JSON или строка)")

    ask = add("ask", cmd_ask, "задать вопрос агенту (Ouroboros) и показать ход")
    ask.add_argument("question")
    ask.add_argument("--json", action="store_true")

    sm = add(
        "smoke",
        cmd_smoke,
        "дымовой тест живого стека: один ход через Ouroboros, что видела модель "
        "(GigaChat Freemium — бесплатно, OpenRouter ~$0.01)",
    )
    sm.add_argument("--question", default="")

    dm = add(
        "demo", cmd_demo, "демо-диалоги через агента → markdown (GigaChat Freemium — бесплатно, OpenRouter ~$0.06)"
    )
    dm.add_argument("--out", default="var/demo_dialogs.md")
    dm.add_argument(
        "--replay",
        default="var/demo_turns.json",
        help="файл проигрывателя вкладки «Демо» (в репозитории — data/demo/turns.json)",
    )
    dm.add_argument(
        "--export-only", action="store_true", help="только записать файл проигрывателя из последнего прогона демо"
    )

    ev = add("eval", cmd_eval, "прогнать eval-набор через агента (Ouroboros)")
    ev.add_argument("--set", default="evals/datasets/dev.jsonl")
    ev.add_argument("--repeats", type=int, default=1)
    ev.add_argument("--label", default="")
    ev.add_argument("--only", nargs="*")
    ev.add_argument("--report", default="", help="записать отчёт прогона в markdown (например, var/eval_report.md)")

    evr = add(
        "eval-retrieval", cmd_eval_retrieval, "eval поиска и отбора по фактам кейсов — без агента (в контейнере tools)"
    )
    evr.add_argument("--set", default="evals/datasets/dev.jsonl")
    evr.add_argument("--only", nargs="*")
    evr.add_argument(
        "--plans", default="", help="планы запросов из прошлого прогона (id или файл): меряется поиск и отбор"
    )
    evr.add_argument("--no-select", action="store_true", help="без отбора LLM: только поиск (факты в кандидатах)")
    evr.add_argument("--label", default="")
    evr.add_argument("--json", action="store_true")

    er = add("eval-report", cmd_eval_report, "отчёт прогона eval в markdown")
    er.add_argument("eval_id", nargs="?", default="latest")
    er.add_argument("--out", default="var/eval_report.md")

    cmp = add("compare", cmd_compare, "сравнить два прогона eval")
    cmp.add_argument("base")
    cmp.add_argument("cand")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # консоль Windows (cp1251) не печатает «→», «≥»
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())

"""
Каталог поведения инструментов агента: всё, что меняет качество работы субагентов LangGraph, — вне кода,
в config/<слот>/ (YAML + промпты .md). Поведение самого агента (роль, правила ответа) — его память Ouroboros
(засев ouroboros/memory/: регламент knowledge/overview.md, identity.md); её версия входит в отпечаток агента
(console/fingerprint.py).

Код — механизм, каталог — поведение. Улучшение качества чаще всего правка каталога: её проще ревьюить, дешевле
проверить на eval и безопаснее отдавать самоулучшению (экспериментальный слот exp-*), чем правку кода.

Слои (поздний переопределяет ранний; слой каждого значения виден в /api/catalog и `oilo catalog show`):
  1. slot — config/<CATALOG_SLOT, по умолчанию current>/ (каталог — CONFIG_DIR, по умолчанию <корень>/config)
  2. env  — LLM_MODEL_SUBAGENT из окружения (быстрая смена или откат модели субагентов; LLM-профиль gigachat ставит
            сюда GigaChat-3-Ultra, на openrouter пусто — модель из llm.yaml)
  3. set  — CATALOG_SET="retrieval.top_k=8;web.max_results=4" (перебор параметров в eval без правки файлов);
            только разрешённые разделы (OVERLAY_SECTIONS)

Правила:
- строгая схема: неизвестный ключ, файл без схемы, неверный тип или диапазон — ошибка загрузки со списком всех
  проблем (файл и поле); сервис с невалидным каталогом не отвечает (готовность 503);
- строгий YAML: повтор ключа — ошибка; yes/no/on/off — строки, а не логические значения; даты — строки;
- промпты — .md с frontmatter (id, version, variables): переменные в тексте должны совпасть с объявленными;
- отпечаток fp (sha256 по итоговым значениям и текстам промптов, без комментариев и переводов строк) и lock
  (файл → version#sha12) пишутся в каждый трейс и каждый прогон eval: разница двух прогонов объясняется разницей
  двух lock.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import string
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

OVERLAY_SECTIONS = ("llm", "retrieval", "web", "research", "forecast", "reports")
PROMPTS = ("research_plan", "select_grade")


class CatalogError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("каталог невалиден:\n  " + "\n  ".join(problems))
        self.problems = problems


# ---------------------------------------------------------------------------------------------------------------
# строгий YAML
# ---------------------------------------------------------------------------------------------------------------


class _StrictLoader(yaml.SafeLoader):
    pass


# YAML 1.2 core: только true/false — логические; даты не превращаются в datetime
_StrictLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictLoader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$"), list("tf"))


def _mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    seen: set = set()
    for k_node, _ in node.value:
        if k_node.tag == "tag:yaml.org,2002:merge":
            raise yaml.constructor.ConstructorError(None, None, "ключ слияния << запрещён", k_node.start_mark)
        k = loader.construct_object(k_node, deep=deep)
        if k in seen:
            raise yaml.constructor.ConstructorError(None, None, f"повтор ключа {k!r}", k_node.start_mark)
        seen.add(k)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=_StrictLoader)  # noqa: S506 — SafeLoader с запретами


# ---------------------------------------------------------------------------------------------------------------
# схема
# ---------------------------------------------------------------------------------------------------------------


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Meta(_M):
    catalog_version: str
    agent: str
    changelog: tuple[str, ...] = ()


class Role(_M):
    model: str = Field(min_length=3)
    temperature: float = Field(0.0, ge=0, le=2)
    timeout_s: float = Field(90, gt=0, le=600)
    retries: int = Field(1, ge=0, le=5)
    reasoning_effort: str = Field("", pattern=r"^(|none|minimal|low|medium|high)$")  # пусто — по умолчанию модели
    hedge_after_s: float = Field(0, ge=0, le=120)  # нет ответа за столько секунд — дублирующий запрос (0 — выключено)


class LLM(_M):
    version: int
    subagent: Role


class Retrieval(_M):
    version: int
    prefetch: int = Field(ge=5, le=500)
    rerank_candidates: int = Field(ge=1, le=200)
    rerank_max_length: int = Field(ge=64, le=8192)
    top_k: int = Field(ge=1, le=50)
    latest_edition_bonus: float = Field(ge=0, le=1)


class Indexing(_M):
    version: int
    chunk_tokens: int = Field(ge=100, le=4000)


class WebPolicy(_M):
    needs_freshness: StrictBool  # строго true/false: «yes»/«on» — ошибка, а не тихое True
    sufficiency: tuple[Literal["sufficient", "partial", "insufficient"], ...]
    freshness_gap: StrictBool


class Web(_M):
    version: int
    max_results: int = Field(ge=1, le=30)
    min_tier_for_numbers: int = Field(ge=0, le=3)
    policy: WebPolicy


class Research(_M):
    version: int
    max_queries: int = Field(ge=1, le=10)
    max_web_queries: int = Field(ge=1, le=6)
    select_candidates: int = Field(ge=1, le=60)
    select_text_chars: int = Field(ge=200, le=6000)


class Verify(_M):
    version: int
    max_issues: int = Field(ge=1, le=50)
    max_checks: int = Field(ge=1, le=5)  # проверок черновика на ход: после последней verify_answer велит завершать
    neighbours_hours: float = Field(gt=0, le=168)


class Elasticity(_M):
    eps_s: float = Field(ge=0, le=2)
    eps_d: float = Field(le=0, ge=-2)
    source: str

    @model_validator(mode="after")
    def _not_both_zero(self):
        if self.eps_s + abs(self.eps_d) == 0:  # в формуле шока это знаменатель
            raise ValueError("eps_s и eps_d не могут быть оба равны 0")
        return self


class InventoryRule(_M):
    usd_per_mbd_low: float = Field(ge=0)
    usd_per_mbd_high: float = Field(ge=0)
    source: str
    attribution: str  # чья это оценка — попадает в метку расчёта: внешнее допущение, а не вывод модели

    @model_validator(mode="after")
    def _ordered(self):
        if self.usd_per_mbd_low > self.usd_per_mbd_high:
            raise ValueError("usd_per_mbd_low больше usd_per_mbd_high")
        return self


class UralsDiscount(_M):
    low: float = Field(ge=0)
    base: float = Field(ge=0)
    high: float = Field(ge=0)
    sources: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _ordered(self):
        if not self.low <= self.base <= self.high:
            raise ValueError("нужно low ≤ base ≤ high")
        return self


class Scenario(_M):
    world_supply_mbd: float = Field(gt=0)
    world_supply_source: str
    applicability_share: float = Field(gt=0, le=0.5)  # шок шире этой доли предложения — вне калибровки
    presets: dict[str, Elasticity] = Field(min_length=1)
    inventory_rule: InventoryRule
    urals_discount: UralsDiscount


class QuoteInstrument(_M):
    asset: str = Field(pattern=r"^[A-Za-z0-9]{1,12}$")  # код базового актива на срочном рынке Мосбиржи (ISS)
    title: str = Field(min_length=1)
    title_en: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    unit_en: str = Field(min_length=1)
    eia: str = ""  # ряд спота EIA (forecast/data.SERIES) или пусто — у сорта нет спота


class Quote(_M):
    roll_days: int = Field(ge=0, le=20)  # за сколько дней до экспирации котировка переходит на следующий контракт
    cache_s: int = Field(ge=0, le=3600)
    timeout_s: float = Field(gt=0, le=60)
    instruments: dict[str, QuoteInstrument] = Field(min_length=1)  # ключ — значение аргумента benchmark price_quote

    @model_validator(mode="after")
    def _keys(self):
        bad = [k for k in self.instruments if not re.fullmatch(r"[a-z][a-z0-9_]*", k)]
        if bad or "brent" not in self.instruments:
            raise ValueError(f"ключи сортов — латиница в нижнем регистре, нужен brent; неверные: {bad}")
        return self


class Forecast(_M):
    version: int
    backtest_windows: int = Field(ge=6, le=240)
    max_horizon: int = Field(ge=1, le=36)
    caveats: tuple[str, ...] = ()
    scenario: Scenario
    quote: Quote


class ReportSource(_M):
    series: str
    org: str
    report: str
    title: str
    kind: str = Field(pattern="^(pdf|html)$")
    page: str = ""
    license: str = ""
    lookback: int = Field(2, ge=1, le=12)
    detect: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    latest_url: str = ""
    schedule: dict[str, str] = Field(default_factory=dict)
    earliest_day: int = Field(1, ge=1, le=28)  # без графика: раньше этого числа выпуск месяца не ждём


class Reports(_M):
    version: int
    sources: tuple[ReportSource, ...]


class Thresholds(_M):
    tier1_min: float = Field(ge=0, le=1)
    tier2_min: float = Field(ge=0, le=1)
    deny_below: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self):
        if not self.deny_below <= self.tier2_min <= self.tier1_min:
            raise ValueError("нужно deny_below ≤ tier2_min ≤ tier1_min")
        return self


class PressRelease(_M):
    """Пресс-релизы через ленты: уровень 3 («не подтверждено»), даже на странице домена высокого уровня."""

    wires: tuple[str, ...] = ()  # домены лент пресс-релизов
    url_patterns: tuple[str, ...] = ()  # фрагменты адреса перепечатки релиза (markets.ft.com/data/announce, …)


class SourceTiers(_M):
    version: str
    thresholds: Thresholds
    tier0: tuple[str, ...] = ()
    tier1: tuple[str, ...] = ()
    tier2: tuple[str, ...] = ()
    state_affiliated: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    press_release: PressRelease = PressRelease()
    # для провайдеров без списка доменов (Brave, DuckDuckGo): операторы site: по ключевым доменам, по важности
    site_operators: tuple[str, ...] = Field(default=(), max_length=12)
    # суффиксы доменов органов власти и международных организаций: уровень 0 (если домен не гос. СМИ)
    government_suffixes: tuple[str, ...] = ()


class Prompt(_M):
    id: str
    version: int
    purpose: str = ""
    format: str = Field(pattern="^(python|plain)$")
    variables: tuple[str, ...] = ()
    text: str
    sha: str


class Catalog(_M):
    slot: str
    meta: Meta
    llm: LLM
    retrieval: Retrieval
    indexing: Indexing
    web: Web
    research: Research
    verify: Verify
    forecast: Forecast
    reports: Reports
    source_tiers: SourceTiers
    prompts: dict[str, Prompt]
    fp: str
    lock: dict[str, str]
    layers: dict[str, str]

    def prompt(self, name: str, **kw: object) -> str:
        p = self.prompts[name]
        return p.text.format(**kw) if p.format == "python" else p.text

    @property
    def ref(self) -> str:
        """Короткая ссылка на версию каталога для трейсов: слот@версия#fp."""
        return f"{self.slot}@{self.meta.catalog_version}#{self.fp}"


SECTIONS: dict[str, type[_M]] = {
    "meta": Meta,
    "llm": LLM,
    "retrieval": Retrieval,
    "indexing": Indexing,
    "web": Web,
    "research": Research,
    "verify": Verify,
    "forecast": Forecast,
    "reports": Reports,
    "source_tiers": SourceTiers,
}


# ---------------------------------------------------------------------------------------------------------------
# загрузка
# ---------------------------------------------------------------------------------------------------------------


def config_dir() -> Path:
    from .config import get_settings

    return Path(get_settings().config_dir)


def _sha(text: str) -> str:
    norm = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").lstrip("﻿").split("\n")).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _read(f: Path, rel: str, problems: list[str]) -> str | None:
    try:
        return f.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        problems.append(f"{rel}: файл не в кодировке UTF-8")
        return None


def _parse_prompt(name: str, raw: str, problems: list[str]) -> dict | None:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw.replace("\r\n", "\n").lstrip("﻿"), flags=re.S)
    if not m:
        problems.append(f"prompts/{name}.md: нет frontmatter (--- id, version, variables ---)")
        return None
    try:
        fm = load_yaml(m.group(1)) or {}
    except yaml.YAMLError as exc:
        problems.append(f"prompts/{name}.md: frontmatter: {exc}")
        return None
    if not isinstance(fm, dict):
        problems.append(f"prompts/{name}.md: frontmatter — не словарь ключей (id, version, variables)")
        return None
    text = m.group(2)
    if fm.get("format") == "python":
        try:
            used = sorted({f for _, f, _, _ in string.Formatter().parse(text) if f})
        except ValueError as exc:
            problems.append(f"prompts/{name}.md: шаблон: {exc}")
            return None
        if used != sorted(fm.get("variables") or []):
            problems.append(
                f"prompts/{name}.md: переменные в тексте {used} ≠ объявленным {sorted(fm.get('variables') or [])}"
            )
    if fm.get("id") != name:
        problems.append(f"prompts/{name}.md: id={fm.get('id')!r} не совпадает с именем файла")
    try:
        return Prompt.model_validate({**fm, "text": text, "sha": _sha(text)[:12]}).model_dump()
    except ValidationError as exc:
        problems += [f"prompts/{name}.md: {'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return None


def _deep_merge(base: dict, over: dict, path: str, layers: dict[str, str], layer: str, problems: list[str]) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        p = f"{path}.{k}" if path else k
        if v is None:
            problems.append(f"{layer}: {p}: null запрещён (удалить значение слоем нельзя)")
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v, p, layers, layer, problems)
        else:
            out[k] = v
            layers[p] = layer
    return out


def _apply_layer(raw: dict, over: dict, where: str, layer: str, layers: dict[str, str], problems: list[str]) -> None:
    """Слой CATALOG_SET: только разрешённые разделы (OVERLAY_SECTIONS), остальное — проблема."""
    if not isinstance(over, dict):
        problems.append(f"{where}: ожидается словарь разделов")
        return
    bad = [k for k in over if k not in OVERLAY_SECTIONS]
    if bad:
        problems.append(f"{where}: разделы {bad} нельзя менять слоем (можно: {', '.join(OVERLAY_SECTIONS)})")
    for k in [k for k in over if k in OVERLAY_SECTIONS]:
        if not isinstance(over[k], dict):
            problems.append(f"{where}: {k}: ожидается словарь ключей")
            continue
        raw[k] = _deep_merge(raw.get(k, {}), over[k], k, layers, layer, problems)


def _parse_set(spec: str) -> dict:
    """«retrieval.top_k=8;web.policy.freshness_gap=false» → вложенный словарь (значения — YAML-скаляры)."""
    out: dict = {}
    for part in filter(None, (x.strip() for x in spec.split(";"))):
        key, _, raw = part.partition("=")
        node = out
        keys = key.strip().split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
            if not isinstance(node, dict):
                raise ValueError(f"{key.strip()}: ключ {k} уже задан значением в этом же CATALOG_SET")
        if isinstance(node.get(keys[-1]), dict):
            raise ValueError(f"{key.strip()}: у ключа уже заданы вложенные значения в этом же CATALOG_SET")
        node[keys[-1]] = load_yaml(raw) if raw.strip() else raw
    return out


def load(slot: str | None = None, set_spec: str | None = None) -> Catalog:
    from .config import get_settings

    st = get_settings()
    root = config_dir()
    slot = slot or st.catalog_slot or "current"
    set_spec = st.catalog_set if set_spec is None else set_spec
    d = root / slot
    problems: list[str] = []
    if not d.is_dir():
        raise CatalogError([f"нет слота каталога {d}"])

    raw: dict[str, Any] = {}
    lock: dict[str, str] = {}
    known = {f"{name}.yaml" for name in SECTIONS}
    for f in sorted(d.glob("*.yaml")):
        if f.name not in known:
            problems.append(f"{f.name}: файл без схемы (переименуйте или добавьте раздел в catalog.py)")
            continue
        text = _read(f, f.name, problems)
        if text is None:
            continue
        try:
            parsed = load_yaml(text) or {}
        except yaml.YAMLError as exc:
            problems.append(f"{f.name}: {exc}")
            continue
        if not isinstance(parsed, dict):  # список или строка вместо разделов: слои и схема ждут словарь
            problems.append(f"{f.name}: ожидается словарь ключей, а не {type(parsed).__name__}")
            continue
        raw[f.stem] = parsed
        ver = (
            raw[f.stem].get("version", raw[f.stem].get("catalog_version", "")) if isinstance(raw[f.stem], dict) else ""
        )
        lock[f.name] = f"{ver}#{_sha(text)[:12]}"
    for name in SECTIONS:
        if name not in raw and f"{name}.yaml" not in [p.split(":")[0] for p in problems]:
            problems.append(f"{name}.yaml: нет файла")

    prompts: dict[str, dict] = {}
    pdir = d / "prompts"
    for f in sorted(pdir.glob("*.md")) if pdir.is_dir() else []:
        if f.stem not in PROMPTS:
            problems.append(f"prompts/{f.name}: промпт не используется кодом (известные: {', '.join(PROMPTS)})")
            continue
        text = _read(f, f"prompts/{f.name}", problems)
        p = _parse_prompt(f.stem, text, problems) if text is not None else None
        if p:
            prompts[f.stem] = p
            lock[f"prompts/{f.name}"] = f"{p.get('version', '')}#{p['sha']}"
    problems += [
        f"prompts/{n}.md: нет файла"
        for n in PROMPTS
        if n not in prompts and f"prompts/{n}.md" not in " ".join(problems)
    ]

    layers: dict[str, str] = {}
    if st.llm_model_subagent and "llm" in raw:
        raw["llm"] = _deep_merge(
            raw["llm"], {"subagent": {"model": st.llm_model_subagent}}, "llm", layers, "env", problems
        )
    if set_spec:
        try:
            _apply_layer(raw, _parse_set(set_spec), "CATALOG_SET", "set", layers, problems)
        except (yaml.YAMLError, ValueError) as exc:
            problems.append(f"CATALOG_SET: {exc}")

    data: dict[str, Any] = {}
    for name, model in SECTIONS.items():
        if name not in raw:
            continue
        try:
            data[name] = model.model_validate(raw[name])
        except ValidationError as exc:
            for e in exc.errors():
                problems.append(f"{name}.yaml: {'.'.join(str(x) for x in e['loc'])}: {e['msg']}")
    if problems:
        raise CatalogError(problems)

    # отпечаток — по итоговым значениям и нормализованным текстам промптов (sha без хвостовых пробелов и CRLF)
    effective = {k: v.model_dump(mode="json") for k, v in data.items()} | {
        "prompts": {k: v["sha"] for k, v in sorted(prompts.items())}
    }
    fp = hashlib.sha256(json.dumps(effective, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    return Catalog(
        slot=slot,
        **data,
        prompts={k: Prompt.model_validate(v) for k, v in prompts.items()},
        fp=fp,
        lock=lock,
        layers=layers,
    )


@lru_cache(maxsize=1)
def get() -> Catalog:
    return load()


def health() -> dict:
    try:
        c = get()
        return {"ok": True, "ref": c.ref}
    except CatalogError as exc:
        return {"ok": False, "problems": exc.problems}


def key_docs() -> dict[str, str]:
    """Описание каждого ключа — из комментария в той же строке YAML (один источник правды: значение + смысл)."""
    out: dict[str, str] = {}
    d = config_dir() / get().slot
    for sec in ("llm", "retrieval", "indexing", "web", "research", "verify", "forecast"):
        f = d / f"{sec}.yaml"
        if not f.is_file():
            continue
        stack: list[tuple[int, str]] = []
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(\s*)([A-Za-z_][\w-]*):\s*([^#]*?)\s*(?:#\s*(.*))?$", line)
            if not m or line.lstrip().startswith("#"):
                continue
            ind = len(m.group(1))
            while stack and stack[-1][0] >= ind:
                stack.pop()
            stack.append((ind, m.group(2)))
            if m.group(4):
                out[".".join([sec, *[k for _, k in stack]])] = m.group(4).strip()
    return out


def describe(texts: bool = False) -> dict:
    """Итоговые значения с указанием слоя — для карты архитектуры, MCP и CLI (texts — с текстами промптов)."""
    c = get()
    docs = key_docs()
    rows = []
    for sec in ("llm", "retrieval", "indexing", "web", "research", "verify", "forecast"):

        def walk(obj: Any, path: str) -> None:
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k != "version":
                        walk(v, f"{path}.{k}")
            else:
                rows.append(
                    {"key": path, "value": obj, "layer": c.layers.get(path, "slot"), "description": docs.get(path, "")}
                )

        walk(getattr(c, sec).model_dump(mode="json"), sec)
    return {
        "ref": c.ref,
        "slot": c.slot,
        "catalog_version": c.meta.catalog_version,
        "fp": c.fp,
        "lock": c.lock,
        "values": rows,
        "prompts": {
            k: {
                "version": p.version,
                "sha": p.sha,
                "purpose": p.purpose,
                "variables": list(p.variables),
                **({"text": p.text} if texts else {}),
            }
            for k, p in c.prompts.items()
        },
        "report_series": [r.series for r in c.reports.sources],
        "changelog": list(c.meta.changelog),
        "source_tiers_version": c.source_tiers.version,
    }

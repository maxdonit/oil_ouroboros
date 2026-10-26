"""
Оценка доверия к веб-источнику и фильтр «жёлтой прессы» (политика — config/current/source_tiers.yaml,
каталог поведения).

Порядок правил (первое сработавшее решает; код причины пишется в журнал решения):
  1. deny-список                              → deny (reason: curated_deny)
  2. лента пресс-релизов (prnewswire и т. п.)  → 3    (reason: press_release) — слова компании, а не проверка редакцией
  3. курируемый уровень 0                      → 0    (reason: curated_tier0) — первоисточник: его пресс-релиз — данные
  4. state_affiliated                         → 3    (reason: state_affiliated) — маркируется, не отбрасывается
  5. домен органа власти / междунар. орг.      → 0    (reason: government_domain; суффиксы — government_suffixes)
  6. адрес перепечатки релиза на домене СМИ    → 3    (reason: press_release; markets.ft.com/data/announce и т. п.)
  7. курируемый уровень 1 / 2                  → 1/2  (reason: curated_tierN)
  8. агрегат Lin et al. 2023 (pc1)             → 1/2/3 или deny по порогам (reason: pc1_*)
  9. иначе                                    → 3    (reason: unknown_domain)
Совпадение — по домену и всем его родительским доменам (news.bloomberg.com → bloomberg.com).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

from ..config import get_settings

TIER_TITLES = {0: "первичный источник", 1: "агентство уровня 1", 2: "деловая пресса", 3: "не подтверждено"}
REASON_TITLES = {
    "curated_deny": "в списке deny (жёлтая пресса, контент-фермы) — отбрасывается",
    "press_release": "лента или перепечатка пресс-релиза — слова компании, а не проверка редакцией",
    "curated_tier0": "курируемый список: первоисточник",
    "state_affiliated": "государственное СМИ: маркируется, уровень 3",
    "government_domain": "домен органа власти",
    "curated_tier1": "курируемый список: уровень 1",
    "curated_tier2": "курируемый список: уровень 2",
    "pc1_high": "рейтинг качества домена (Lin et al.) не ниже tier1_min",
    "pc1_medium": "рейтинг качества домена (Lin et al.) не ниже tier2_min",
    "pc1_low": "рейтинг качества домена (Lin et al.) ниже tier2_min",
    "pc1_deny": "рейтинг качества домена (Lin et al.) ниже deny_below — отбрасывается",
    "unknown_domain": "домена нет ни в списках, ни в рейтинге",
}


@dataclass(frozen=True)
class Verdict:
    domain: str
    tier: int | None  # None — отклонён
    reason: str
    pc1: float | None
    state_affiliated: bool = False

    @property
    def allowed(self) -> bool:
        return self.tier is not None


@dataclass(frozen=True)
class Policy:
    version: str
    thresholds: dict
    tiers: dict[str, int]
    state: frozenset[str]
    deny: frozenset[str]
    pc1: dict[str, float]
    wires: frozenset[str] = frozenset()
    release_urls: tuple[str, ...] = ()
    site_operators: tuple[str, ...] = ()
    government_suffixes: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def policy() -> Policy:
    from .. import catalog

    d = get_settings().data_dir
    # политика — в каталоге (config/current/source_tiers.yaml), агрегат pc1 — данные
    raw = catalog.get().source_tiers.model_dump()
    tiers: dict[str, int] = {}
    for n in (2, 1, 0):  # более высокий уровень перекрывает
        for dom in raw[f"tier{n}"]:
            tiers[dom.lower()] = n
    pc1: dict[str, float] = {}
    path = d / "source_quality" / "domain_pc1.csv"
    if path.is_file():
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    pc1[row["domain"].lower()] = float(row["pc1"])
                except (KeyError, ValueError):
                    continue
    return Policy(
        version=raw["version"],
        thresholds=raw["thresholds"],
        tiers=tiers,
        state=frozenset(x.lower() for x in raw["state_affiliated"]),
        deny=frozenset(x.lower() for x in raw["deny"]),
        pc1=pc1,
        wires=frozenset(x.lower() for x in raw["press_release"]["wires"]),
        release_urls=tuple(x.lower() for x in raw["press_release"]["url_patterns"]),
        site_operators=tuple(x.lower() for x in raw["site_operators"]),
        government_suffixes=tuple("." + x.lower().lstrip(".") for x in raw["government_suffixes"]),
    )


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or url).lower()
    return host[4:] if host.startswith("www.") else host


def _parents(domain: str) -> list[str]:
    parts = domain.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


def assess(url: str) -> Verdict:
    p = policy()
    dom = domain_of(url)
    chain = _parents(dom) or [dom]
    score = next((p.pc1[c] for c in chain if c in p.pc1), None)
    if any(c in p.deny for c in chain):
        return Verdict(dom, None, "curated_deny", score)
    if any(c in p.wires for c in chain):  # лента релизов: слова компании, а не проверка редакцией
        return Verdict(dom, 3, "press_release", score)
    tier = next((p.tiers[c] for c in chain if c in p.tiers), None)
    if tier == 0:  # первоисточник (регулятор, агентство, биржа): его пресс-релиз — первичные данные, а не перепечатка
        return Verdict(dom, 0, "curated_tier0", score)
    if any(c in p.state for c in chain):
        return Verdict(dom, 3, "state_affiliated", score, state_affiliated=True)
    if p.government_suffixes and dom.endswith(p.government_suffixes):
        return Verdict(dom, 0, "government_domain", score)
    # перепечатка пресс-релиза на домене СМИ не наследует его уровень: markets.ft.com/data/announce — не редакция FT
    if any(x in url.lower() for x in p.release_urls):
        return Verdict(dom, 3, "press_release", score)
    if tier is not None:
        return Verdict(dom, tier, f"curated_tier{tier}", score)
    if score is not None:
        t = p.thresholds
        if score >= t["tier1_min"]:
            return Verdict(dom, 1, "pc1_high", score)
        if score >= t["tier2_min"]:
            return Verdict(dom, 2, "pc1_medium", score)
        if score >= t["deny_below"]:
            return Verdict(dom, 3, "pc1_low", score)
        return Verdict(dom, None, "pc1_deny", score)
    return Verdict(dom, 3, "unknown_domain", None)

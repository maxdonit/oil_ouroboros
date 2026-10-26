"""
Статистика прогонов: перцентили, точный McNemar, интервал Уилсона (scipy). McNemar по кейсам: исход кейса — «верен
большинством прогонов»; точный тест — биномиальный по разногласиям b и c (n = b + c, p = 0,5).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scipy.stats import binomtest


def percentile(values: Sequence[float], p: float) -> float | None:
    """Перцентиль методом ближайшего ранга; пустая выборка — None."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(p / 100 * len(ordered)), 1)
    return float(ordered[min(rank, len(ordered)) - 1])


@dataclass(frozen=True)
class McNemar:
    n_fix: int  # base неверен, cand верен (c)
    n_break: int  # base верен, cand неверен (b)
    p_value: float
    both_ok: int = 0
    both_fail: int = 0

    @property
    def n_net(self) -> int:
        return self.n_fix - self.n_break

    def as_dict(self) -> dict[str, float | int]:
        return {
            "n_fix": self.n_fix,
            "n_break": self.n_break,
            "n_net": self.n_net,
            "both_ok": self.both_ok,
            "both_fail": self.both_fail,
            "p_value": round(self.p_value, 6),
        }


def mcnemar_exact(n_break: int, n_fix: int) -> float:
    """Двусторонний точный McNemar по числу разногласий b (break) и c (fix)."""
    n = n_break + n_fix
    return 1.0 if n == 0 else float(binomtest(min(n_break, n_fix), n, 0.5).pvalue)


def paired_outcomes(base: Mapping[str, bool], cand: Mapping[str, bool]) -> McNemar:
    """Таблица 2×2 по общим кейсам и точный p."""
    common = sorted(set(base) & set(cand))
    b = sum(1 for k in common if base[k] and not cand[k])
    c = sum(1 for k in common if cand[k] and not base[k])
    both_ok = sum(1 for k in common if base[k] and cand[k])
    both_fail = len(common) - b - c - both_ok
    return McNemar(n_fix=c, n_break=b, p_value=mcnemar_exact(b, c), both_ok=both_ok, both_fail=both_fail)


def wilson(k: int, n: int) -> tuple[float, float] | None:
    """95% интервал Уилсона доли верных по всем ходам прогонов."""
    if n <= 0:
        return None
    ci = binomtest(k, n).proportion_ci(confidence_level=0.95, method="wilson")
    return round(ci.low, 4), round(ci.high, 4)


def majority(outcomes: Sequence[bool]) -> bool:
    """Исход кейса по прогонам: верен, если верен строго больше чем в половине прогонов."""
    return sum(outcomes) * 2 > len(outcomes)

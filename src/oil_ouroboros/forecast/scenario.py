"""
Сценарный калькулятор шока предложения (OPEC+ ±N млн б/с) и пересчёт в Urals — прозрачные формулы, не чёрный ящик.

Импульсный канал (месяц-два, поток баррелей):
    чистый шок   s = N · c · (1 − o) / Q        N — объём, c — исполнение, o — компенсация, Q — мировое предложение
    цена         ln(P/P0) = s / (ε_S + |ε_D|)   краткосрочные эластичности; лог-форма: цена всегда > 0, а при малых
                                                шоках %ΔP ≈ s / (ε_S + |ε_D|), как в линейной записи
Пресеты эластичностей (литература; значения — в каталоге forecast.yaml → scenario):
    kilian_low   ε_S≈0.02, ε_D≈−0.13  — «низкие эластичности» (Kilian; Kilian–Murphy)
    cci          ε_S≈0.13, ε_D≈−0.13  — мета-оценка Caldara–Cavallo–Iacoviello (Fed IFDP 1173)
    bh           ε_S≈0.20, ε_D≈−0.35  — в духе Baumeister–Hamilton (AER 2019)
Канал запасов (пик через 6–12 мес. при устойчивом шоке) — правило банков: ≈ $10–20/барр. на 1 млн б/с
(Goldman Sachs, 2026: +$20 без ответа OPEC+, <$10 с компенсацией). Разрыв каналов в 3–10 раз реален и
объясняется в ответе, а не прячется. Заявления OPEC двигают цену и до изменения баррелей (Känzig, AER 2021).

Пределы: эластичности и правило запасов калиброваны на шоках до applicability_share мирового предложения
(каталог, ~5%). Шире — импульс считается с предупреждением «только порядок величины», а правило запасов не
экстраполируется (оценки нет). Компенсация (offset) уже вычтена из шока, поэтому с ней берётся только оценка «без
ответа OPEC+»: нижняя оценка правила и есть шок с компенсацией — иначе она учлась бы дважды.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


def params():
    """Числа сценариев — из каталога (config/current/forecast.yaml → scenario): эластичности, мировое предложение,
    правило канала запасов, дисконт Urals. Устаревают — правятся там, без правки кода."""
    from .. import catalog

    return catalog.get().forecast.scenario


@dataclass(frozen=True)
class ShockResult:
    delta_mbd: float
    compliance: float
    offset: float
    world_supply_mbd: float
    net_shock_share: float
    base_price: float
    impulse: dict[str, dict[str, float]]
    inventory_channel_usd: tuple[float, float] | None  # None — шок вне диапазона калибровки правила запасов
    explanation: str
    sources: list[str]
    within_calibration: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


def supply_shock(
    delta_mbd: float,
    base_price: float,
    compliance: float = 1.0,
    offset: float = 0.0,
) -> ShockResult:
    """
    delta_mbd < 0 — сокращение предложения (цена растёт), > 0 — рост предложения.
    compliance — доля фактического исполнения (0..1), offset — доля, компенсируемая свободными мощностями/SPR (0..1).
    """
    sc = params()
    world_supply_mbd = sc.world_supply_mbd  # каталог требует > 0
    if not 0 <= compliance <= 1 or not 0 <= offset <= 1:
        raise ValueError("compliance и offset — доли от 0 до 1")
    removed = -delta_mbd * compliance * (1 - offset)  # изъятые с рынка баррели, млн б/с: > 0 — дефицит, < 0 — излишек
    s = removed / world_supply_mbd
    within = abs(s) <= sc.applicability_share
    impulse = {}
    for name, p in sc.presets.items():
        growth = math.exp(s / (p.eps_s + abs(p.eps_d)))  # лог-форма: P = P0 · exp(s / (ε_S + |ε_D|)) > 0
        impulse[name] = {
            "pct": round((growth - 1) * 100, 2),
            "usd": round(base_price * (growth - 1), 2),
            "price": round(base_price * growth, 2),
            "eps_s": p.eps_s,
            "eps_d": p.eps_d,
        }
    rule = sc.inventory_rule
    # компенсация уже вычтена из шока: с ней — только оценка «без ответа OPEC+», иначе ответ учёлся бы дважды
    per_mbd = (
        (rule.usd_per_mbd_high, rule.usd_per_mbd_high) if offset > 0 else (rule.usd_per_mbd_low, rule.usd_per_mbd_high)
    )
    inv = tuple(sorted(round(removed * x, 2) for x in per_mbd)) if within else None
    pcts = sorted(abs(v["pct"]) for v in impulse.values())  # величина эффекта; направление — словом
    if removed > 0:
        shock_txt, direction = f"С рынка уходит {removed:.2f} млн б/с", "рост"
    elif removed < 0:
        shock_txt, direction = f"На рынок приходит дополнительно {-removed:.2f} млн б/с", "снижение"
    else:
        shock_txt, direction = "Чистого шока нет", "без изменения"
    explanation = (
        f"{shock_txt} ({abs(s) * 100:.2f}% мирового предложения {world_supply_mbd} млн б/с; "
        f"исполнение {compliance:.0%}, компенсация {offset:.0%}). Импульсный эффект на 1–2 месяца: "
        f"{direction} цены на {pcts[0]:.1f}…{pcts[-1]:.1f}% в зависимости от эластичностей. "
    )
    if inv is not None:
        explanation += (
            f"Если шок устойчив и истощает запасы, пик через 6–12 месяцев — по оценке {rule.attribution} (внешнее "
            f"допущение, не расчёт модели): {inv[0]:+.0f}…{inv[1]:+.0f} $/барр. Разница каналов — не ошибка: "
            "эластичности описывают месячный поток, банковская оценка — накопленный дефицит запасов. "
        )
    else:
        explanation += (
            f"Шок больше {sc.applicability_share:.0%} мирового предложения — вне диапазона, на котором калиброваны "
            f"эластичности и оценка {rule.attribution}: импульс — только порядок величины, "
            "оценки по каналу запасов нет. "
        )
    explanation += "Ожидания (заявления OPEC+) могут сдвинуть цену раньше, чем изменятся баррели."
    return ShockResult(
        delta_mbd=delta_mbd,
        compliance=compliance,
        offset=offset,
        world_supply_mbd=world_supply_mbd,
        net_shock_share=round(s, 5),
        base_price=round(base_price, 2),
        impulse=impulse,
        inventory_channel_usd=inv,
        explanation=explanation,
        sources=[p.source for p in sc.presets.values()] + [rule.source, sc.world_supply_source],
        within_calibration=within,
    )

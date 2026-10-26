"""
Детерминированная проверка ответа: каждая цифра ↔ процитированный источник, каждая ссылка ↔ известный источник.

Зачем: модели «рационализируют» цитаты постфактум — до 57% цитат в RAG не отражают реально использованный
источник (Wallat et al. 2025); генеративные поисковики цитируют верно лишь в ~74% случаев, а пере-сопоставление
цитат после генерации даёт +15% относительной точности (CiteFix, ACL 2025). Ошибки в финансовых ответах — «не та
строка, период или единица» (VeriFin), поэтому проверка цифр — код, а не LLM.

Алгоритм для каждого предложения ответа:
  1. ссылки [R#]/[W#]/[F#] → известны ли они (иначе unknown_citation);
  2. числа (без лет, дат, сезонов «2025/26», порядковых, длительностей «24 часа», номеров пунктов, уровней
     интервалов) → есть ли каждое в тексте процитированных источников:
     - десятичная запятая по-русски («0,2» — 0.2; «1,234» — 1234 или 1.234), неразрывный пробел — разделитель тысяч;
     - совпадение до половины последнего показанного знака («79,6» ≈ 79.62, «79,3» — нет);
     - единица: объём с масштабом (тыс. / млн / млрд б/с ↔ kb/d / mb/d), цена ($), проценты, п.п. — разные
       единицы не подтверждают друг друга; число источника без единицы (таблица) подтверждает только из
       процитированного источника;
     - направление: число со знаком или со словом прямо перед ним («+1,9», «выросли на 1,9») не подтверждается
       числом источника с обратным направлением («fell by 1.9», «−1.9»). Слово должно стоять прямо перед числом:
       «выше/ниже» в том же предложении часто относятся к другой величине (замер на сохранённых ответах);
     - «32 cents» — это $0,32; доля в результате расчёта (покрытие интервала 0.983) — это и 98,3%;
     - номера страниц, таблиц и рисунков («p. 12», «Table 3.1») и голые годы источника — не цифры;
  3. цифра из вопроса пользователя засчитывается, только если в предложении нет ссылок и в вопросе то же число
     той же единицы;
  4. в предложении с пометкой «(расчёт)» число, которое получается из подтверждённых цифр этого и двух предыдущих
     предложений той же единицы (разница, сумма, отношение, изменение в %), — derived_number;
  5. число без ссылки или не найденное в процитированном → ищем в других надёжных источниках хода с той же
     известной единицей: найдено — ссылка исправляется (CiteFix; ссылка, не подтвердившая ни одной цифры
     предложения, заменяется), не найдено — unverified_number;
  6. цифра, опирающаяся только на веб-источник уровня хуже допустимого, — weak_source.
Проверка находит число в источнике, но не понимает, о том ли показателе фраза: правильность фактов мерят кейсы eval.
Ссылки с кириллическими «Р»/«Ф» ([Р12]) приводятся к латинским ([R12]) до проверки. Разбор чисел и единиц —
evidence/numbers.py, ссылок и предложений — evidence/text.py.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .numbers import CALC_MARK, DERIVED_WINDOW, claim_numbers, derived, direction, evidence_values, matches, values
from .text import CITE, lang_of, normalize_ids, split_sentences, strip_own_sources


@dataclass
class Issue:
    type: str  # unknown_citation | unverified_number | uncited_number | wrong_citation | weak_source | derived_number
    sentence: str
    number: str = ""
    cited: list[str] = field(default_factory=list)
    suggestion: str = ""


@dataclass
class Claim:
    sentence: str
    cited: list[str]
    numbers: list[str]
    status: str  # verified | unverified | no_numbers | fixed
    support: dict[str, list[str]] = field(default_factory=dict)  # число → id источников, где найдено


@dataclass
class Verification:
    ok: bool
    numbers_total: int
    numbers_verified: int
    citations_total: int
    citations_unknown: int
    issues: list[Issue]
    claims: list[Claim]
    fixed_text: str
    # из подтверждённых: ссылка исправлена автоматически (число найдено в другом надёжном источнике хода)
    numbers_fixed: int = 0
    citations_foreign: int = 0  # ссылки на доказательства не из этого хода (allowed — список хода)

    def as_dict(self) -> dict:
        return asdict(self)


def verify(
    answer: str, evidence: dict[str, dict], question: str = "", allowed: set[str] | None = None
) -> Verification:
    """allowed — доказательства этого хода (консоль знает их точно); ссылка вне списка — foreign_citation (жёсткое)."""
    from .. import catalog

    answer = strip_own_sources(normalize_ids(answer))  # проверяется и показывается текст без своего списка агента
    ru = lang_of(answer) == "ru"  # запятая в числах ответа — десятичная
    max_tier = catalog.get().web.min_tier_for_numbers  # худший уровень веб-источника, подтверждающего цифру
    if allowed is not None:
        evidence = {k: v for k, v in evidence.items() if k in allowed}
    ev_vals = {eid: evidence_values(e.get("text", "")) for eid, e in evidence.items()}
    q_nums = [(tok, unit) for tok, _pos, unit in claim_numbers(question)]
    allowed_all = set(allowed) if allowed is not None else None
    answer_ids = {c.strip() for grp in CITE.findall(answer) for c in re.split(r"[,;]", grp)}

    def weak(eid: str) -> bool:
        return evidence[eid].get("kind") == "web" and evidence[eid].get("tier", 3) > max_tier

    def from_question(tok: str, unit: str) -> bool:  # то же число той же единицы, без масштаба
        return any(u == unit and matches(tok, {(v, u, "") for v in values(q, ru)}, unit, ru=ru) for q, u in q_nums)

    issues: list[Issue] = []
    claims: list[Claim] = []
    n_total = n_ok = n_fixed = c_total = c_unknown = c_foreign = 0
    fixed_text = answer
    recent: list[list[tuple[float, str]]] = []  # подтверждённые значения по предложениям — операнды «(расчёт)»
    for sent in split_sentences(answer):
        cited = [c.strip() for grp in CITE.findall(sent) for c in re.split(r"[,;]", grp)]
        c_total += len(cited)
        for c in cited:
            if c not in evidence:
                c_unknown += 1
                foreign = allowed_all is not None and c not in allowed_all
                c_foreign += int(foreign)
                hint = f"источник {c} не из этого хода" if foreign else f"нет источника {c}"
                issues.append(Issue("unknown_citation", sent, cited=cited, suggestion=hint))
        nums = claim_numbers(sent)
        if not nums:
            claims.append(Claim(sent, cited, [], "no_numbers"))
            recent.append([])
            continue
        support: dict[str, list[str]] = {}
        status = "verified"
        add_ids: list[str] = []
        ok_vals: list[tuple[float, str]] = []
        pending: list[tuple[str, str, str]] = []
        bare = CITE.sub(" ", sent)  # координаты чисел _claim_numbers
        for tok, pos, unit in nums:
            n_total += 1
            dirn = direction(tok, bare[max(0, pos - 60) : pos])
            found_in = [c for c in cited if c in ev_vals and matches(tok, ev_vals[c], unit, ru=ru, direction=dirn)]
            if not found_in:
                if not cited and from_question(tok, unit):
                    n_ok += 1
                    ok_vals += [(v, unit) for v in values(tok, ru)]
                    support[tok] = ["вопрос"]
                else:
                    pending.append((tok, unit, dirn))
                continue
            support[tok] = found_in
            if all(weak(c) for c in found_in):
                issues.append(
                    Issue(
                        "weak_source",
                        sent,
                        tok,
                        cited,
                        f"цифра опирается только на веб-источник уровня хуже {max_tier}",
                    )
                )
                status = "unverified"
            else:
                n_ok += 1
                ok_vals += [(v, unit) for v in values(tok, ru)]
        pool = [v for vals in recent[-(DERIVED_WINDOW - 1) :] for v in vals] + ok_vals
        calc = bool(CALC_MARK.search(sent))
        for tok, unit, dirn in pending:
            expr = derived(tok, unit, pool, ru) if calc else ""  # пометка «(расчёт)»: сначала воспроизвести расчёт
            if expr:
                n_ok += 1
                support[tok] = [f"расчёт: {expr}"]
                issues.append(Issue("derived_number", sent, tok, cited, f"расчёт из подтверждённых цифр: {expr}"))
                continue
            elsewhere = [
                eid
                for eid, vals in ev_vals.items()
                if matches(tok, vals, unit, strict=True, ru=ru, direction=dirn)
                and (evidence[eid].get("kind") != "tool" or eid in answer_ids)
            ]  # расчёт — только уже процитированный
            strong = [e for e in elsewhere if not weak(e)]
            if elsewhere and not strong:
                issues.append(
                    Issue("weak_source", sent, tok, cited, f"число есть только в веб-источнике уровня хуже {max_tier}")
                )
                status = "unverified"
            elif strong:
                # пере-сопоставление цитаты: число той же единицы есть в другом надёжном источнике — исправляем ссылку
                best = min(strong, key=lambda e: (evidence[e].get("kind") != "report", evidence[e].get("tier", 3)))
                issues.append(
                    Issue("wrong_citation" if cited else "uncited_number", sent, tok, cited, f"число есть в [{best}]")
                )
                add_ids.append(best)
                support[tok] = [best]
                n_ok += 1
                n_fixed += 1
                ok_vals += [(v, unit) for v in values(tok, ru)]
                status = "fixed" if status == "verified" else status
            else:
                reverse = dirn and any(matches(tok, ev_vals[c], unit, ru=ru) for c in cited if c in ev_vals)
                hint = (
                    "в источнике у этого числа обратное направление (рост ↔ снижение)"
                    if reverse
                    else "число не найдено ни в одном источнике"
                )
                issues.append(Issue("unverified_number", sent, tok, cited, hint))
                status = "unverified"
        recent.append(ok_vals)
        final_ids = cited + [e for e in add_ids if e not in cited]
        if add_ids:
            used = {i for ids in support.values() for i in ids}
            idle = [c for c in cited if c in evidence and c not in used]
            # ссылка, не подтвердившая ни одной цифры предложения, когда цифры нашлись в другом источнике, — ошибочная
            keep = [c for c in cited if c not in idle] if not (set(cited) & used) else cited
            final_ids = keep + [e for e in add_ids if e not in keep]
            new_sent = CITE.sub("", sent).rstrip(" .") if keep != cited else sent.rstrip(".")
            ids = "".join(f"[{e}]" for e in dict.fromkeys(final_ids) if keep != cited or e not in cited)
            fixed_text = fixed_text.replace(sent, f"{new_sent} {ids}" + ("." if sent.endswith(".") else ""), 1)
        claims.append(Claim(sent, final_ids, [t for t, _, _ in nums], status, support))
    hard = [i for i in issues if i.type in ("unknown_citation", "unverified_number", "weak_source")]
    return Verification(
        ok=not hard,
        numbers_total=n_total,
        numbers_verified=n_ok,
        citations_total=c_total,
        citations_unknown=c_unknown,
        issues=issues,
        claims=claims,
        fixed_text=fixed_text,
        numbers_fixed=n_fixed,
        citations_foreign=c_foreign,
    )

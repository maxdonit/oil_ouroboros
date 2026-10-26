from oil_ouroboros.evidence.numbers import claim_numbers
from oil_ouroboros.evidence.verify import verify


def numbers_in(text: str) -> list[tuple[str, int]]:
    return [(tok, pos) for tok, pos, _ in claim_numbers(text)]


EV = {
    "R1": {
        "kind": "report",
        "text": "World oil demand is forecast to fall by 2.5 mb/d y-o-y in 4Q26 and 2.6 mb/d on average "
        "in 2026. 2027 global oil use 620 kb/d below 2025 levels.",
    },
    "W1": {"kind": "web", "tier": 1, "text": "Brent rose to $126.55 on October 14"},
    "W3": {"kind": "web", "tier": 3, "text": "Some blog says Brent will hit $150"},
    "F1": {"kind": "tool", "tier": 0, "text": '{"median": 122.96, "lo80": 105.09}'},
}


def test_verified_numbers_with_decimal_comma_and_unit_scaling():
    v = verify("Спрос снизится на 2,6 млн б/с [R1]. В 2027 году — на 0,62 млн б/с ниже [R1].", EV)
    assert v.ok and v.numbers_total == 2 and v.numbers_verified == 2


def test_hallucinated_number_is_caught():
    v = verify("Добыча вырастет на 7,7 млн б/с [R1].", EV)
    assert not v.ok
    assert [i.type for i in v.issues] == ["unverified_number"]


def test_uncited_number_is_rematched_to_source():
    v = verify("Спрос в 4 квартале упадёт на 2,5 млн б/с.", EV)
    assert v.ok
    assert v.issues[0].type == "uncited_number"
    assert "[R1]" in v.fixed_text


def test_unknown_citation_and_weak_source():
    assert not verify("Пункт [R9].", EV).ok
    v = verify("Brent достигнет 150 $ [W3].", EV)
    assert any(i.type == "weak_source" for i in v.issues)


def test_numbers_in_skips_years_dates_quarters_and_horizons():
    toks = [t for t, _ in numbers_in("В 2026 году за 3 месяца, 14 октября, в 4Q26 цена 96 $/барр., 5 крупнейших, 25%")]
    assert toks == ["96", "25"]


def test_page_references_in_the_answer_are_not_figures():
    """«(с. 33, 44)» и «p. 12» — номера страниц, а «с 5,2 до 5,6» — данные: у «с» без точки это предлог."""
    v = verify("В EIA STEO (с. 33, 44) спрос — 2,6 млн б/с [R1].", EV)
    assert v.ok and v.numbers_total == 1
    assert [t for t, _ in numbers_in("Добыча выросла с 5,2 до 5,6 млн б/с.")] == ["5,2", "5,6"]
    assert [t for t, _ in numbers_in("See p. 12 and Table 3.1: demand 105.1 mb/d.")] == ["105.1"]
    assert [t for t, _ in numbers_in("с. 33, 44 млн б/с")] == ["44"]  # после страницы — цифра с единицей


def test_reversed_direction_does_not_confirm():
    """«выросли на 1,9» не подтверждается «fell by 1.9»; направление — только слово прямо перед числом или знак."""
    ev = {"R1": {"kind": "report", "text": "Global observed inventories fell by 1.9 mb/d in 3Q26."}}
    v = verify("Запасы выросли на 1,9 млн б/с [R1].", ev)
    assert not v.ok and "обратное направление" in v.issues[0].suggestion
    for ok in ("Запасы снизились на 1,9 млн б/с [R1].", "Запасы: −1,9 млн б/с [R1].", "Запасы — 1,9 млн б/с [R1]."):
        assert verify(ok, ev).ok, ok
    both = {"R1": {"kind": "report", "text": "Stocks fell by 1.9 mb/d in 3Q26, then rose by 1.9 mb/d in 4Q26."}}
    assert verify("Запасы выросли на 1,9 млн б/с [R1].", both).ok  # одно из вхождений согласуется
    assert not verify(
        "Добыча вырастет на +2,6 млн б/с [R1].", {"R1": {"kind": "report", "text": "Supply -2.6 mb/d"}}
    ).ok


def test_cents_and_shares_in_calculations_are_read_as_the_answer_writes_them():
    """«down 32 cents/MMBtu» — это $0,32; покрытие интервала 0.983 в результате расчёта — это 98,3%."""
    gas = {"R1": {"kind": "report", "text": "Henry Hub will average $3.16/MMBtu in 2027, down 32 cents/MMBtu (9%)."}}
    assert verify("Henry Hub подешевеет на $0,32/MMBtu (−9%) [R1].", gas).ok
    calc = {"F1": {"kind": "tool", "text": '{"median": 115.5, "coverage": {"80": 0.85, "95": 0.983}}'}}
    assert verify("Покрытие 95%-интервала на бэктесте — 98,3%, 80%-интервала — 85% [F1].", calc).ok
    assert not verify("Медиана — 98,3 $/барр. [F1].", calc).ok  # доля — проценты, а не цена


def test_barrels_per_day_spelled_b_d_is_a_volume():
    """«млн б/д» — объём, как «млн б/с»: цифра без ссылки находит источник, а не считается неподтверждённой."""
    ev = {"R8": {"kind": "report", "text": "The pipeline was shipping more than 5.0 million barrels per day (b/d)."}}
    v = verify("Трубопровод экспортировал более 5 млн б/д через Янбу.", ev)
    assert v.ok and [i.type for i in v.issues] == ["uncited_number"] and "[R8]" in v.fixed_text
    assert not verify("Добыча — 5 тыс. б/д [R8].", ev).ok  # масштаб тыс. ↔ млн учитывается и для «б/д»


def test_own_sources_section_at_the_end_is_neither_checked_nor_shown():
    """Список источников ставит система; свой список агента в конце черновика выпадает, номера страниц в нём — тоже."""
    for heading in ("**Источники:**", "### Sources", "Источники"):
        draft = (
            f"Спрос снизится на 2,6 млн б/с [R1].\n\n{heading}\n"
            "- [R1] Отчёт EIA STEO, октябрь 2026, с. 42\n1. [W1] Reuters"
        )
        v = verify(draft, EV)
        assert v.ok and v.numbers_total == 1 and v.fixed_text == "Спрос снизится на 2,6 млн б/с [R1]."
    middle = "Спрос снизится на 2,6 млн б/с [R1].\n\n**Источники**\n- [R1] — основной.\n\nВывод: спрос падает."
    assert "Источники" in verify(middle, EV).fixed_text  # раздел не в конце — часть ответа


def test_numbers_from_question_need_no_citation():
    v = verify("При сокращении на 2 млн б/с цена вырастет [F1].", EV, question="сокращение добычи на 2 млн б/с")
    assert v.ok


def test_marked_calculation_is_reproduced_from_verified_numbers():
    ev = {
        "R1": {"kind": "report", "text": "OPEC sees demand growth of 1.3 mb/d"},
        "R2": {"kind": "report", "text": "IEA sees demand growth of 0.7 mb/d"},
    }
    v = verify("OPEC ждёт роста на 1,3 млн б/с [R1], IEA — на 0,7 млн б/с [R2]; разница 0,6 млн б/с (расчёт).", ev)
    assert v.ok and v.numbers_verified == v.numbers_total == 3
    assert [i.type for i in v.issues] == ["derived_number"]
    # без пометки «(расчёт)» то же число — непроверенное, и выдуманное «расчётное» число не проходит
    assert not verify("OPEC ждёт 1,3 млн б/с [R1], IEA — 0,7 млн б/с [R2]; разница 0,6 млн б/с.", ev).ok
    assert not verify("OPEC ждёт 1,3 млн б/с [R1], IEA — 0,7 млн б/с [R2]; разница 4,4 млн б/с (расчёт).", ev).ok


def test_cyrillic_citation_ids_are_normalized():
    v = verify("Спрос снизится на 2,6 млн б/с [Р1].", EV)
    assert v.ok and v.citations_unknown == 0 and "[R1]" in v.fixed_text


AGENCIES = {
    "R3": {
        "kind": "report",
        "text": "IEA: world oil demand is set to fall by 2.6 mb/d in 2026. Refinery runs rise by 2.8 mb/d in 2027.",
    },
    "R22": {"kind": "report", "text": "OPEC: world oil demand growth of about 0.2 mb/d y-o-y in 2026."},
}


def test_decimal_comma_and_year_in_question_do_not_confirm_wrong_numbers():
    q = "Сравни прогнозы спроса на нефть в 2026 году от OPEC и IEA."
    v = verify("OPEC ждёт роста примерно на 0,2 млн б/с [R3].", AGENCIES, question=q)
    claim = v.claims[0]
    assert claim.support["0,2"] == ["R22"] and claim.status == "fixed"  # не «вопрос» (2026/1000) и не IEA
    assert not verify("Спрос вырастет на 2,0 млн б/с [R3].", AGENCIES, question=q).ok


def test_marked_gap_is_a_calculation_not_an_unrelated_figure_of_another_agency():
    """Регрессия демо «по отчётам»: разрыв 2,8 = 0,2 − (−2,6), а не «refinery runs 2.8 mb/d» из IEA."""
    text = (
        "IEA ожидает снижения спроса на 2,6 млн б/с [R3], OPEC — роста на 0,2 млн б/с [R22]. "
        "Разрыв между оценками — около 2,8 млн б/с (расчёт)."
    )
    v = verify(text, AGENCIES)
    assert v.ok and v.claims[1].support["2,8"][0].startswith("расчёт") and "[R3]" not in v.claims[1].sentence


def test_sign_and_unit_must_agree():
    ev = {"R1": {"kind": "report", "text": "Supply change: -2.6 mb/d. Brent averaged $70 in May."}}
    assert not verify("Предложение вырастет на +2,6 млн б/с [R1].", ev).ok  # «+» против «−2.6» источника
    assert verify("Предложение снизится на 2,6 млн б/с [R1].", ev).ok
    assert not verify("Запасы выросли на 70 млн барр. [R1].", ev).ok  # $70 — цена, не объём
    assert verify("Brent в мае — в среднем $70 [R1].", ev).ok


def test_interval_levels_are_method_parameters_not_figures():
    toks = [t for t, _ in numbers_in("Медиана 118,19 $/б, 80%-интервал 87,69–192,32 $/б; интервалы 50/80/95%; 95% CI")]
    assert toks == ["118,19", "87,69", "192,32"]


def test_day_ranges_are_dates_not_numbers():
    toks = [t for t, _ in numbers_in("Новостей за 15–21 октября нет; Reuters, 1–14 Oct 2026; спрос 2,6 млн б/с")]
    assert toks == ["2,6"]


def test_eval_reference_figures_match_with_volume_scaling():
    from oil_ouroboros.evidence.numbers import matches_reference

    toks = claim_numbers("Спрос ниже на 620 тыс. б/с и на 0,6%; Brent $96.")
    assert any(matches_reference(t, u, [0.62]) for t, _, u in toks)  # эталон в млн б/с, ответ в тыс. б/с
    assert any(matches_reference(t, u, [96]) for t, _, u in toks) and not any(
        matches_reference(t, u, [9.6]) for t, _, u in toks
    )


def test_page_table_numbers_and_wrong_units_do_not_confirm():
    ev = {
        "R1": {
            "kind": "report",
            "text": "OPEC MOMR p. 12. Table 3.1: demand 105.1 mb/d. Refinery runs rose 2.5% y-o-y.",
        }
    }
    assert not verify("Доля ОПЕК+ составляет 12% [R1].", ev).ok  # «p. 12» — номер страницы
    assert not verify("Brent стоит $3.1 [R1].", ev).ok  # «Table 3.1» — номер таблицы
    assert not verify("Загрузка НПЗ выросла на 2,5 п.п. [R1].", ev).ok  # п.п. — не %
    assert verify("Загрузка НПЗ выросла на 2,5% [R1].", ev).ok


def test_volume_scale_and_rounding_tolerance():
    assert not verify("Рост 2,6 млн б/с [R1].", {"R1": {"kind": "report", "text": "Growth of 2.6 kb/d"}}).ok
    ev = {"R1": {"kind": "report", "text": "Demand growth 1.3 mb/d. Brent averaged $79.62 in September."}}
    assert verify("Рост 1300 тыс. б/с [R1].", ev).ok and not verify("Рост 1,3 тыс. б/с [R1].", ev).ok
    assert not verify(
        "Рост 1,234 тыс. б/с [R1].", {"R1": {"kind": "report", "text": "Growth 1.234 mb/d"}}
    ).ok  # по-русски 1.234 тыс.
    assert verify("Brent в сентябре — $79,6 [R1].", ev).ok and not verify("Brent в сентябре — $79,3 [R1].", ev).ok


def test_wrong_citation_is_replaced_not_kept_next_to_the_right_one():
    ev = {
        "R1": {"kind": "report", "text": "IEA: demand grows by 0.7 mb/d."},
        "R2": {"kind": "report", "text": "OPEC: demand grows by 1.3 mb/d."},
    }
    v = verify("Спрос вырастет на 1,3 млн б/с [R1].", ev)
    assert v.ok and "[R2]" in v.fixed_text and "[R1]" not in v.fixed_text


def test_question_numbers_do_not_launder_cited_claims_and_need_the_same_unit():
    ev = {"R1": {"kind": "report", "text": "OPEC+ kept quotas unchanged."}}
    q = "Почему ОПЕК+ сократила добычу на 5 млн б/с?"
    assert not verify("ОПЕК+ сократила добычу на 5 млн б/с [R1].", ev, question=q).ok  # ссылка на отчёт без этой цифры
    assert verify("Вы спрашиваете о сокращении на 5 млн б/с.", ev, question=q).ok  # без ссылки — цифра из вопроса
    assert not verify("Добыча выросла на 80 тыс. б/с.", ev, question="Будет ли Brent выше 80?").ok


def test_only_explicit_calculation_mark_allows_derived_numbers():
    ev = {"R1": {"kind": "report", "text": "Supply 105.1 mb/d, demand 106.3 mb/d."}}
    assert not verify("По расчётам ОПЕК, профицит составит 1,2 млн б/с.", ev).ok
    assert verify("Предложение 105,1 млн б/с [R1], спрос 106,3 млн б/с [R1]; дефицит 1,2 млн б/с (расчёт).", ev).ok


def test_non_breaking_thousands_and_non_figures_in_answers():
    ev = {"R1": {"kind": "report", "text": "Output 1,234 kb/d"}}
    assert verify("Добыча 1 234 тыс. б/с [R1].", ev).ok and verify("Добыча 1 234 тыс. б/с [R1].", ev).ok
    toks = [
        t for t, _ in numbers_in("За последние 24 часа, за 30 дней, в сезоне 2025/26, на 10-й месяц — 3,5 млн б/с")
    ]
    assert toks == ["3,5"]
    assert [t for t, _ in numbers_in("Brent подешевеет на $5, WTI — на $7")] == ["5", "7"]


def test_calculation_json_units_and_ranges():
    """Результат расчётного модуля — JSON: единица по имени поля; начало диапазона берёт единицу его конца."""
    f = (
        '{"path": [{"h": 3, "median": 115.96, "lo80": 98.79}], '
        '"scenario": {"delta_mbd": -2.0, "inventory_channel_usd": [20.0, 40.0]}}'
    )
    ev = {"F1": {"kind": "tool", "tier": 0, "text": f}, "R1": {"kind": "report", "text": "Demand 102.3 mb/d"}}
    assert verify("Медиана через 3 месяца — 115,96 $/б [F1].", ev).ok
    assert verify("Канал запасов: +20…+40 $/б [F1].", ev).ok
    assert not verify("Добыча снизится на 115,96 млн б/с [F1].", ev).ok  # цена — не объём
    # без ссылки число расчёта не пере-сопоставляется, пока ответ не ссылается на этот расчёт
    assert not verify("Медиана через 3 месяца — 115,96 $/б.", ev).ok
    assert verify("Медиана через 3 месяца — 115,96 $/б. Нижняя граница 80%-интервала — 98,79 $/б [F1].", ev).ok

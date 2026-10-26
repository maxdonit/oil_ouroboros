import pytest

from oil_ouroboros.web.credibility import assess

# адрес → (уровень, код причины): порядок правил — web/credibility.py; None — отброшен
TIERS = [
    ("https://www.eia.gov/outlooks/steo/", 0, "curated_tier0"),
    ("https://home.treasury.gov/news/press-releases/sb0123", 0, "curated_tier0"),  # релиз регулятора — первоисточник
    ("https://www.cmegroup.com/press-releases/2026/10/x.html", 0, "curated_tier0"),
    ("https://www.consilium.europa.eu/en/press/press-releases/2026/10/01/x/", 0, "government_domain"),
    ("https://www.maritime.dot.gov/msci/2026-001", 0, "government_domain"),
    ("https://www.meti.go.jp/english/press/x.html", 0, "government_domain"),
    ("https://ppac.gov.in/content/x", 0, "government_domain"),
    ("https://www.customs.gov.cn/x", 0, "government_domain"),
    ("https://www.ukmto.org/indian-ocean/alerts", 0, "curated_tier0"),
    ("https://rigcount.bakerhughes.com/na-rig-count", 0, "curated_tier0"),
    ("https://spimex.com/markets/oil_products/", 0, "curated_tier0"),
    ("http://government.ru/news/1/", 0, "curated_tier0"),
    ("https://www.spa.gov.sa/w2k", 3, "state_affiliated"),  # гос. СМИ на госдомене — маркируется, не уровень 0
    ("https://tass.ru/ekonomika/1", 3, "state_affiliated"),
    ("https://www.reuters.com/business/energy/x", 1, "curated_tier1"),
    (
        "https://markets.ft.com/data/announce/detail?dockey=600-202610211000PR_NEWS_USPRX____LN77572-1",
        3,
        "press_release",
    ),
    ("https://www.prnewswire.com/news-releases/oil-outlook-302.html", 3, "press_release"),
    ("https://www.ft.com/content/509c0ecf-625e-4473-96cf-e249edb7c86e", 1, "curated_tier1"),
    ("https://www.kommersant.ru/doc/1", 2, "curated_tier2"),
    ("https://www.dailymail.co.uk/x", None, "curated_deny"),
    ("https://totally-unknown-domain-xyz.com/", 3, "unknown_domain"),
]


@pytest.mark.parametrize(("url", "tier", "reason"), TIERS)
def test_domain_tier_table(url, tier, reason):
    v = assess(url)
    assert (v.tier, v.reason) == (tier, reason)


def test_curated_tiers_and_parent_domains():
    assert assess("https://www.eia.gov/outlooks/steo/").tier == 0
    assert assess("https://www.reuters.com/business/energy/x").tier == 1
    assert assess("https://news.bloomberg.com/a").tier == 1  # родительский домен
    assert assess("https://www.kommersant.ru/doc/1").tier == 2


def test_yellow_press_is_denied():
    for url in ("https://www.dailymail.co.uk/x", "https://www.kp.ru/daily/x", "https://www.zerohedge.com/x"):
        v = assess(url)
        assert v.tier is None and not v.allowed, url


def test_state_media_is_marked_not_dropped():
    v = assess("https://tass.ru/ekonomika/1")
    assert v.tier == 3 and v.state_affiliated


def test_pc1_dataset_drives_unknown_domains():
    assert assess("https://www.infowars.com/x").reason in ("curated_deny", "pc1_deny")
    assert assess("https://totally-unknown-domain-xyz.com/").reason == "unknown_domain"


def test_press_releases_do_not_inherit_the_host_tier():
    release = "https://markets.ft.com/data/announce/detail?dockey=600-202610211000PR_NEWS_USPRX____LN77572-1"
    assert (assess(release).tier, assess(release).reason) == (3, "press_release")
    assert assess("https://www.prnewswire.com/news-releases/oil-outlook-302.html").tier == 3
    assert (
        assess("https://www.ft.com/content/509c0ecf-625e-4473-96cf-e249edb7c86e").tier == 1
    )  # редакция FT — как была


def test_site_operator_pass_uses_key_domains_not_alphabetical_ones():
    from oil_ouroboros.web import search

    ops = search._site_operators(sorted(["aramco.com", "bis.org", "cbr.ru"] * 5))
    assert ops[:3] == ["reuters.com", "bloomberg.com", "ft.com"] and {"iea.org", "eia.gov", "opec.org"} <= set(ops)
    assert len(ops) <= 12


def test_report_editions_from_the_base_are_not_cited_as_web():
    """Выпуск, который уже есть в базе отчётов, — это отчёт: в веб-доказательства его страница не попадает. Новый
    выпуск, которого в базе ещё нет, и прочие страницы сайтов агентств из веба проходят."""
    from oil_ouroboros.web import search

    in_base = search.report_urls_in_base()
    seen = lambda url: search.norm_url(url) in in_base  # noqa: E731
    assert seen("https://www.iea.org/reports/oil-market-report-october-2026")  # IEA OMR 2026-10 — в базе
    assert seen("https://www.iea.org/reports/oil-market-report-september-2026/")
    assert seen("https://www.eia.gov/outlooks/steo/archives/sep26.pdf")  # EIA STEO 2026-09 — в базе
    assert not seen("https://www.iea.org/reports/oil-market-report-november-2026")  # нового выпуска в базе нет
    assert not seen("https://www.eia.gov/outlooks/steo/report/global_oil.php")
    assert not seen("https://www.eia.gov/todayinenergy/detail.php?id=1")
    assert not seen("https://www.opec.org/monthly-oil-market-report.html")  # страница серии — не выпуск

// «Источники»: что подключено к агенту, насколько это свежо и насколько ему верить — отчёты агентств (база, индекс,
// наблюдатель выпусков), веб-поиск с уровнями доверия к доменам, цены EIA расчётного модуля и котировки сортов с
// Мосбиржи; как добавить источник (docs/guide/sources.md).

import { h, mount, fmt, empty, errorBox, loading, badge, kpi, collapsible } from "./util.js";
import { api } from "./api.js";
import { renderMarkdown } from "./md.js";

// статусы пунктов проверки сайтов, которые не требуют внимания (остальные выдаёт _try наблюдателя: blocked, not_found, …)
const OK = ["present", "downloaded", "scheduled"];
const STATUS_CLS = { present: "b-ok", indexed: "b-ok", downloaded: "b-ok", uploaded: "b-ok", queued: "b-info", scheduled: "b-muted", error: "b-bad", started: "b-muted", done: "b-ok" };
const SOURCE_RU = { catalog: "в репозитории", watch: "наблюдатель", upload: "вручную" };
const TRIGGER_RU = { startup: "при старте", schedule: "по расписанию", cli: "командой" };
const SUFF_RU = { partial: "частичными", insufficient: "недостаточными" };
const TIER_CLS = { 0: "b-ok", 1: "b-ok", 2: "b-info", 3: "b-warn" };
const HOWTO_DOC = "guide/sources.md";

let els = {};

const age = (iso) => {
  const n = Math.floor((Date.now() - new Date(iso).getTime()) / 864e5);
  return isNaN(n) ? "" : n <= 0 ? "сегодня" : `${n} дн. назад`;
};
const ago = (t) => {
  const m = Math.round((Date.now() - new Date(typeof t === "number" ? t * 1000 : t).getTime()) / 60000);
  return isNaN(m) ? "—" : m < 60 ? `${Math.max(m, 0)} мин назад` : m < 2880 ? `${Math.round(m / 60)} ч назад` : `${Math.round(m / 1440)} дн. назад`;
};
const head = (title, sub) => h("div", { class: "panel-head" }, h("h3", null, title), sub ? h("span", { class: "muted small" }, sub) : null);
const plural = (n, [one, few, many]) =>
  (n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? few : many);
const domains = (list) => (list && list.length
  ? collapsible(`${list.length} ${plural(list.length, ["домен", "домена", "доменов"])}`, () => h("div", { class: "small mono" }, list.join(", ")))
  : "—");

export async function init(root) {
  els.body = h("div");
  mount(root,
    h("div", { class: "page-head" }, h("h2", null, "Источники"),
      h("p", null, "Что агент знает и откуда. Отчёты агентств — приоритетный источник (уровень 0); в веб агент идёт, когда отчётов " +
        "не хватает или нужна свежесть, и пускает в контекст только домены, прошедшие фильтр доверия; цены EIA — данные расчётов " +
        "прогноза; текущие цены — котировки фьючерсов Мосбиржи. Всё на странице — из того, что работает сейчас: каталога, индекса, наблюдателя выпусков и журнала доказательств.")),
    els.body);
  await load();
}

async function load() {
  mount(els.body, loading());
  let d;
  try {
    d = await api.sources();
  } catch (e) {
    return mount(els.body, errorBox(e));
  }
  mount(els.body, summary(d), reports(d.reports), web(d.web), prices(d.prices, d.quote || {}), howto());
}

function summary(d) {
  const r = d.reports, cat = r.catalog || [];
  const inIndex = cat.filter((x) => x.present && x.chunks);
  const problems = (r.last?.items || []).filter((x) => !OK.includes(x.status));
  const brent = (d.prices.series || []).find((x) => x.benchmark === "brent") || {};
  return h("div", { class: "kpis" },
    kpi("Отчёты в индексе", `${inIndex.length} из ${cat.length}`, `выпусков · ${fmt.int(inIndex.reduce((a, x) => a + x.chunks, 0))} фрагментов`,
      inIndex.length === cat.length ? "ok" : "warn"),
    kpi("Наблюдатель выпусков", r.last ? ago(r.last.finished) : "не запускался",
      problems.length ? `требуют внимания: ${problems.length}` : "ожидаемые выпуски на месте", problems.length ? "warn" : "ok"),
    kpi("Веб-поиск", d.web.provider, `цифры — только уровни 0–${d.web.min_tier_for_numbers}`),
    kpi("Цены EIA", brent.as_of ? fmt.date(brent.as_of) : "—", brent.as_of ? `последнее наблюдение Brent · ${age(brent.as_of)}` : brent.error || ""),
    quoteKpi(((d.quote || {}).instruments || []).find((x) => x.benchmark === "brent") || {}));
}

// предыдущий рабочий день (пн–пт) по московскому времени (UTC+3) — свежее него котировка в норме: в выходные торгов нет
const prevWorkday = () => {
  const d = new Date(Date.now() + 3 * 3600e3);
  do d.setUTCDate(d.getUTCDate() - 1); while (d.getUTCDay() === 0 || d.getUTCDay() === 6);
  return d.toISOString().slice(0, 10);
};
// цена котировки: 2 знака, если их хватает (116,67), иначе 3 (2,628 у газа)
const price = (x) => fmt.num(x, Math.abs(x * 100 - Math.round(x * 100)) < 1e-6 ? 2 : 3);
const quoteOld = (q) => q.status === "stale" || (q.quote && q.quote.time_msk.slice(0, 10) < prevWorkday());

function quoteKpi(q) {
  if (!q.quote) return kpi("Котировка Brent", "—", q.error ? `биржа не ответила: ${q.error}` : "ещё не запрашивалась", q.error ? "warn" : "");
  return kpi("Котировка Brent", `${price(q.quote.price)} $`, `${q.quote.contract} · ${q.quote.time_ru}${q.status === "stale" ? " · устарела" : ""}`,
    quoteOld(q) ? "warn" : "ok");
}

function reports(r) {
  const cat = r.catalog || [], rules = r.series || [], items = r.last?.items || [];
  const pending = new Set(r.pending || []);
  const status = (s, note) => badge(r.statuses?.[s] || s, STATUS_CLS[s] || "b-warn", note || null);
  const edition = (x) => h("div", { class: "small" },
    h("span", { class: "mono" }, x.edition), " · опубл. ", fmt.date(x.published),
    x.published ? h("span", { class: "muted" }, ` (${age(x.published)})`) : null, " · ",
    !x.present ? badge("нет файла", "b-bad") : x.chunks ? h("span", { class: "muted" }, `${fmt.int(x.chunks)} фрагм.`)
      : pending.has(x.doc_id) ? badge("ждёт индекса", "b-info") : badge("нет в индексе", "b-warn"),
    " ", badge(SOURCE_RU[x.source] || x.source, "b-muted", x.file));
  const names = [...new Set([...rules.map((s) => s.series), ...cat.map((x) => x.series)])];
  const rows = names.map((name) => {
    const rule = rules.find((s) => s.series === name);
    const docs = cat.filter((x) => x.series === name).sort((a, b) => b.edition.localeCompare(a.edition));
    const checked = items.filter((x) => x.series === name);
    return h("tr", null,
      h("td", null, h("b", null, name), h("div", { class: "muted small" }, rule?.title || docs[0]?.title || ""),
        rule?.page ? h("div", { class: "small" }, h("a", { href: rule.page, target: "_blank", rel: "noopener" }, "страница серии")) : null),
      h("td", null, docs.length ? docs.map(edition) : h("span", { class: "muted small" }, "нет выпусков")),
      h("td", null, checked.length
        ? checked.map((x) => h("div", { class: "small" }, h("span", { class: "mono" }, x.edition), " ", status(x.status, x.note)))
        : h("span", { class: "muted small" }, rule ? "ещё не проверялась" : "без правила наблюдателя: выпуски добавляются вручную")),
      h("td", { class: "small" }, rule?.next_release
        ? [h("span", { class: "mono" }, rule.next_release.edition), h("div", { class: "muted" }, rule.next_release.date)] : "—"),
      h("td", { class: "small" }, badge("уровень 0", "b-ok"), h("div", { class: "muted" }, "первичный источник"),
        h("div", { class: "muted" }, rule?.license || docs[0]?.license || "")));
  });
  const last = r.last?.finished;
  return h("section", { class: "panel" },
    head("Отчёты агентств", `в базе держатся последние выпуски каждой серии (lookback), правила — config/current/reports.yaml`),
    h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["серия", "в базе: выпуск · опубликован · индекс · откуда", "последняя проверка сайта", "следующий выпуск",
        "достоверность"].map((x) => h("th", null, x)))),
      h("tbody", null, rows))),
    h("p", { class: "small" }, r.running?.active ? [badge("идёт проверка", "b-info"), " "] : null,
      "Наблюдатель проверяет сайты агентств ", r.every_hours > 0 ? `каждые ${fmt.num(r.every_hours, 1)} ч` : "только при старте и командой",
      last ? `; последняя проверка — ${fmt.ts(last)} (${TRIGGER_RU[r.last.trigger] || r.last.trigger})` : "",
      r.next_run ? `, следующая — ${fmt.ts(r.next_run)}` : "", ". Скачанное ",
      r.auto_index ? "сразу добавляется в индекс." : "добавляется в индекс командой.",
      " Выпуск за проверкой от ботов (Cloudflare) не обходится — его загружают вручную (см. «Как добавить источник»).",
      pending.size ? ` Ждут индекса: ${[...pending].join(", ")}.` : ""),
    (r.journal || []).length ? collapsible(`Журнал наблюдателя — последние ${r.journal.length} событий`,
      () => h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("tbody", null, r.journal.map((j) => h("tr", null,
        h("td", { class: "mono small nowrap" }, fmt.ts(j.ts)),
        h("td", { class: "small nowrap" }, j.action, j.series ? ` · ${j.series} ${j.edition || ""}` : j.doc_id ? ` · ${j.doc_id}` : ""),
        h("td", null, status(j.status)),
        h("td", { class: "small" }, j.note || j.file || ""))))))) : null);
}

function web(w) {
  const t = w.tiers || {}, th = t.thresholds || {}, pol = w.policy || {}, max = w.min_tier_for_numbers;
  const when = [
    pol.needs_freshness && "вопрос про свежие события («сейчас», «на этой неделе», после выхода отчётов)",
    (pol.sufficiency || []).length && `оценщик достаточности счёл отчёты ${pol.sufficiency.map((x) => SUFF_RU[x] || x).join(" или ")}`,
    pol.freshness_gap && "между датой отчётов и вопросом — разрыв",
  ].filter(Boolean);
  const numbers = (tier) => (tier <= max ? badge("подтверждают цифры", "b-ok") : badge("только фон, без цифр", "b-warn"));
  const tierRow = (tier, what, who) => h("tr", null,
    h("td", { class: "nowrap" }, badge(`уровень ${tier}`, TIER_CLS[tier]), h("div", { class: "muted small" }, w.titles?.[tier] || "")),
    h("td", { class: "small" }, what), h("td", { class: "small" }, who), h("td", null, numbers(tier)));
  return h("section", { class: "panel" },
    head("Веб-поиск", `провайдер ${w.provider} · до ${w.max_results} источников в контексте`),
    h("p", { class: "small" }, "Агент идёт в веб, если ", when.join("; ") || "так решил оценщик",
      ". Каждый домен получает уровень доверия; в ответе цифры подтверждаются только источниками уровней 0–", max,
      ", уровень 3 можно упомянуть как фон, домены из deny не попадают в контекст. Метка в ответе: ",
      h("code", null, "[Источник: домен, web, дата, уровень N]"), "."),
    h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["уровень", "что это", "какие домены", "в ответе"].map((x) => h("th", null, x)))),
      h("tbody", null,
        tierRow(0, "первичные данные: агентства, регуляторы, статистика, биржи", [domains(t.tier0),
          h("div", { class: "muted" }, "и домены органов власти: ", (t.government_suffixes || []).map((x) => "." + x).join(" "))]),
        tierRow(1, "ценовые агентства под принципами IOSCO и ведущие информагентства", [domains(t.tier1),
          h("div", { class: "muted" }, `или рейтинг качества домена ≥ ${th.tier1_min}`)]),
        tierRow(2, "качественная деловая и отраслевая пресса", [domains(t.tier2), h("div", { class: "muted" }, `или рейтинг ≥ ${th.tier2_min}`)]),
        tierRow(3, "не подтверждено: государственные СМИ (маркируются), пресс-релизы, неизвестные домены", [
          h("div", null, "государственные СМИ: "), domains(t.state_affiliated),
          h("div", null, "ленты пресс-релизов: "), domains(t.press_release?.wires),
          h("div", { class: "muted" }, `или рейтинг ≥ ${th.deny_below}, или домена нет ни в списках, ни в рейтинге`)]),
        h("tr", null, h("td", null, badge("deny", "b-bad")), h("td", { class: "small" }, "жёлтая пресса, контент-фермы, конспирология"),
          h("td", { class: "small" }, domains(t.deny), h("div", { class: "muted" }, `или рейтинг < ${th.deny_below}`)),
          h("td", null, badge("отбрасывается", "b-bad")))))),
    h("p", { class: "small muted" }, `Курируемые списки важнее рейтинга. Рейтинг — агрегат экспертных оценок качества новостных доменов ` +
      `(Lin et al., PNAS Nexus 2023): ${fmt.int(w.rated_domains)} доменов. Политика доверия — версия ${t.version}, ` +
      `config/current/source_tiers.yaml; версия пишется в журнал каждого решения фильтра.`),
    domainCheck(),
    h("h4", { class: "sec-h" }, "Домены, которые попадали в доказательства ответов"),
    (w.used || []).length ? h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["домен", "уровень", "фрагментов", "последний раз"].map((x) => h("th", null, x)))),
      h("tbody", null, w.used.map((x) => h("tr", null,
        h("td", { class: "mono" }, x.domain || "—"),
        h("td", null, x.tier === null || x.tier === undefined ? "—" : badge(`уровень ${x.tier}`, TIER_CLS[x.tier], w.titles?.[x.tier])),
        h("td", { class: "mono" }, fmt.int(x.n)), h("td", { class: "small" }, fmt.ts(x.last))))))) : empty("Веб в ответах ещё не использовался"));
}

function domainCheck() {
  const out = h("span", { class: "small" });
  const inp = h("input", {
    class: "input", placeholder: "reuters.com или ссылка на статью", style: "max-width: 320px", onkeydown: (e) => e.key === "Enter" && go()
  });
  async function go() {
    const v = inp.value.trim();
    if (!v) return;
    mount(out, "…");
    try {
      const x = await api.sourceDomain(v);
      mount(out, badge(x.allowed ? `уровень ${x.tier} — ${x.title}` : "отклонён", x.allowed ? TIER_CLS[x.tier] : "b-bad"), " ",
        h("span", { class: "mono" }, x.domain), ": ", x.reason, x.pc1 === null || x.pc1 === undefined ? "" : ` (рейтинг ${fmt.num(x.pc1, 2)})`);
    } catch (e) {
      mount(out, errorBox(e));
    }
  }
  return h("div", { class: "row" }, h("b", { class: "small" }, "Проверить домен:"), inp,
    h("button", { class: "btn btn-sm", onclick: go }, "Проверить"), out);
}

function prices(p, q) {
  const res = Object.entries(p.result || {});
  return h("section", { class: "panel" },
    head("Цены EIA", "прогноз, сценарии, история цен; спот рядом с котировками"),
    h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["ряд", "последнее наблюдение", "цена", "откуда", "достоверность"].map((x) => h("th", null, x)))),
      h("tbody", null, (p.series || []).map((x) => h("tr", null,
        h("td", null, h("b", null, { brent: "Brent", wti: "WTI", henry_hub: "газ Henry Hub" }[x.benchmark] || x.benchmark),
          h("div", { class: "muted small" }, h("a", { href: x.url, target: "_blank", rel: "noopener" }, x.title))),
        h("td", null, x.as_of ? [fmt.date(x.as_of), h("div", { class: "muted small" }, age(x.as_of))]
          : h("span", { class: "text-bad small" }, x.error || "нет данных")),
        h("td", { class: "mono" }, x.last_price === undefined ? "—" : `${fmt.num(x.last_price, 2)} ${x.unit || "$/барр."}`),
        h("td", { class: "small" }, x.as_of ? (x.snapshot ? "снимок в репозитории (data/prices)" : "скачано с eia.gov") : "—"),
        h("td", { class: "small" }, badge("уровень 0", "b-ok"), h("div", { class: "muted" }, "официальная статистика EIA, public domain"))))))),
    h("p", { class: "small" }, "Обновление ",
      p.every_hours > 0 ? `каждые ${fmt.num(p.every_hours, 1)} ч` : "выключено (только командой oilo prices refresh)",
      p.last_refresh ? `; последнее — ${fmt.ts(p.last_refresh)}` : "", p.next_refresh ? `, следующее — ${fmt.ts(p.next_refresh)}` : "", ".",
      res.length ? h("span", { class: "muted" }, ` Итог: ${res.map(([k, v]) => `${k} — ${v}`).join(", ")}.`) : "",
      p.skipped ? h("span", { class: "muted" }, ` Отложено: ${p.skipped}.`) : "",
      " EIA выкладывает дневные цены раз в неделю, поэтому последнее наблюдение отстаёт до недели от сегодняшней даты. " +
        "Urals — от Brent с дисконтом из каталога (forecast.scenario.urals_discount)."),
    quotesTable(q));
}

// котировки сортов (инструмент price_quote): последние сохранённые, без запроса к бирже
function quotesTable(q) {
  const rows = q.instruments || [];
  return [
    h("h4", null, "Котировки Мосбиржи"),
    h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["сорт", "контракт", "цена", "время сделки", "состояние"].map((x) => h("th", null, x)))),
      h("tbody", null, rows.map((r) => h("tr", null,
        h("td", null, h("b", null, r.title), h("div", { class: "muted small mono" }, r.benchmark)),
        h("td", { class: "mono" }, r.quote ? h("a", { href: r.quote.url, target: "_blank", rel: "noopener" }, r.quote.contract) : "—"),
        h("td", { class: "mono" }, r.quote ? `${price(r.quote.price)} ${r.unit}` : "—"),
        h("td", { class: "small" }, r.quote ? [r.quote.time_ru, h("div", { class: "muted" }, r.quote.kind)] : "—"),
        h("td", { class: "small" },
          !r.quote ? (r.error ? h("span", { class: "text-bad" }, `биржа не ответила: ${r.error}`) : "ещё не запрашивалась")
            : [quoteOld(r) ? badge("устарела", "b-warn") : badge("свежая", "b-ok"),
              r.fetched_at ? h("div", { class: "muted" }, `скачана ${ago(r.fetched_at)}`) : ""])))))),
    h("p", { class: "small" }, "Фьючерс ближайшего месяца на ",
      h("a", { href: "https://iss.moex.com/iss/engines/futures/markets/forts/securities", target: "_blank", rel: "noopener" }, "Мосбирже (ISS)"),
      `: задержка ${q.delay_min ?? 15} мин, переход на следующий контракт за ${q.roll_days ?? 3} дн. до экспирации, кэш ${q.cache_s ?? 300} с. `,
      "Котировка появляется после первого вопроса агенту о текущей цене; сорта — каталог forecast.quote.instruments."),
  ];
}

// «Как добавить источник» — документ docs/guide/sources.md (одна точка правды), тем же рендером, что и документация
function howto() {
  const body = h("div", null, loading());
  loadDoc(body, HOWTO_DOC);
  return h("section", { class: "panel" }, head("Как добавить источник", `docs/${HOWTO_DOC}`), body);
}

async function loadDoc(host, path) {
  try {
    // из документа — раздел «Как добавить источник» и дальше: обзор источников вкладка уже показывает данными,
    // а заголовок раздела — у панели
    const doc = await api.doc(path);
    const part = doc.split(/^## Как добавить источник\s*$/m);
    mount(host, renderMarkdown(part.length > 1 ? part[1] : doc.replace(/^#\s.*\n+/, ""), { headingShift: 1, idPrefix: "howto-" }));
  } catch (e) {
    mount(host, errorBox(e, "Документ не найден."));
  }
}

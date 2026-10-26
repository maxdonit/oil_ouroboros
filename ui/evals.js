// «Качество»: прогоны eval-набора через настоящего агента (Ouroboros → инструменты) — метрики с доверительным
// интервалом, pass^k, доли по категориям и проверкам, провалы с причинами и ссылками на ходы; сравнение двух версий
// (точный McNemar по парным исходам кейсов, дельты, разница отпечатков версии агента).

import { h, mount, clear, fmt, empty, errorBox, loading, badge, kpi, collapsible, toast, jsonTree } from "./util.js";
import { api } from "./api.js";

export const CHECK_TITLES = {
  no_error: "ход без ошибки и с ответом",
  scope: "отказ ровно тогда, когда вопрос вне компетенции",
  research_first: "первым — research, список отчётов или расчёт (в том числе текущая котировка)",
  route_web: "веб-поиск вызван (или нет) как ожидалось",
  tools: "вызваны обязательные инструменты",
  tool_args: "аргументы расчёта (сорт, горизонт, знак шока) верные",
  no_tools: "лишние расчёты не вызваны",
  facts: "в ответе есть ожидаемые числа",
  cites_report: "есть ссылки на отчёты",
  cites_web: "есть ссылки на веб-источники",
  cites_tool: "есть ссылки на расчёты",
  cites_orgs: "процитированы нужные агентства",
  verified: "финальная проверка: цифры подтверждены, нет чужих и неизвестных ссылок",
  answer_is_checked_text: "ответ пользователю = проверенный черновик агента",
  source_quality: "не процитированы источники ниже допустимого уровня",
  labels: "метки источников [Отчёт …] / [Источник: …], без сырых id",
  persona_clean: "ответ от лица аналитика (без «Ouroboros»)",
  language: "ответ на языке вопроса",
  admits_missing: "честно говорит, что данных нет",
  must_include: "в ответе есть обязательная фраза",
  must_not_include: "в ответе нет запрещённых фраз",
};
export const CATEGORY_TITLES = {
  report: "по отчётам",
  web: "веб / свежие данные",
  combined: "отчёты + веб",
  forecast: "прогноз",
  scenario: "сценарий",
  out_of_scope: "вне компетенции",
  conflict: "конфликт источников",
  history: "история цен",
  quote: "текущая цена",
  followup: "уточнение в диалоге",
  honesty: "честность (нет в базе)",
  advice: "запрос инвестсовета",
};

let els = {};
let list = [];
let current = null;

export async function init(root) {
  els.list = h("div", { class: "eval-list" });
  els.detail = h("div");
  els.compare = h("div");
  els.base = h("select", { class: "input", "aria-label": "База" });
  els.mcp = h("div");
  els.cand = h("select", { class: "input", "aria-label": "Кандидат" });
  mount(root,
    h("div", { class: "page-head" }, h("h2", null, "Качество (eval)"),
      h("p", null, "Каждый кейс — настоящий ход агента. Проверки детерминированные, по одному типу ошибки каждая; причина провала " +
        "показывает класс ошибки. Запуск — кнопкой «Запустить» ниже или ", h("code", { class: "md-code" }, "docker compose exec console oilo eval"), ".")),
    h("div", { class: "split" },
      h("section", { class: "panel" },
        h("div", { class: "panel-head" }, h("h3", null, "Прогоны eval"), h("span", { class: "spacer" }),
          h("button", { class: "btn btn-sm", onclick: loadList }, "↻")),
        els.list),
      h("div", { class: "stack" },
        h("section", { class: "panel" }, els.detail),
        h("section", { class: "panel" },
          h("div", { class: "panel-head" }, h("h3", null, "Сравнение: база → кандидат"),
            h("span", { class: "muted small" }, "McNemar по парным исходам кейсов")),
          h("div", { class: "row" }, h("label", { class: "row small" }, "база", els.base), h("span", null, "→"),
            h("label", { class: "row small" }, "кандидат", els.cand),
            h("button", { class: "btn btn-primary btn-sm", onclick: doCompare }, "Сравнить")),
          els.compare))),
    h("section", { class: "panel" }, els.mcp));
  mount(els.detail, empty("Выберите прогон eval слева"));
  drawRunner();
  await loadList();
}

async function drawRunner() {
  const only = h("input", { class: "input", placeholder: "кейсы или категории через запятую (пусто — весь набор)", style: { minWidth: "360px" } });
  const label = h("input", { class: "input", placeholder: "метка прогона" });
  const status = h("span", { class: "muted small" });
  const poll = async () => {
    try {
      const st = await api.evalStatus();
      status.textContent = st.active ? `идёт: ${st.label || ""} с ${fmt.ts(st.started)}`
        : st.error ? `ошибка: ${st.error}` : st.eval_id ? `готово: ${st.eval_id}` : "";
      if (st.active) setTimeout(poll, 10000);
      else if (st.eval_id) loadList();
    } catch (e) {
      status.textContent = e.message;
    }
  };
  mount(els.mcp, h("div", { class: "panel-head" }, h("h3", null, "Запуск eval")),
    h("div", { class: "row" }, only, label, h("button", { class: "btn btn-primary btn-sm", onclick: async () => {
      try {
        await api.evalStart({ only: only.value ? only.value.split(",").map((x) => x.trim()).filter(Boolean) : null, label: label.value });
        toast("eval запущен: кейсы идут через агента, ~1 мин на кейс", "info");
        poll();
      } catch (e) {
        toast(e.message, "bad");
      }
    } }, "Запустить"), status),
    h("p", { class: "muted small" }, "Стоимость: на GigaChat Freemium — бесплатно (токены), на OpenRouter ~$0.01 на кейс. Пока идёт eval, наблюдатель отчётов не меняет индекс."));
  poll();
}

export async function show(params) {
  const id = params.rest[0];
  if (id && (!current || current.eval_id !== id)) openEval(id);
  const b = params.query.get("base");
  const c = params.query.get("cand");
  if (b && c) {
    els.base.value = b;
    els.cand.value = c;
    doCompare();
  }
}

async function loadList() {
  mount(els.list, loading());
  try {
    list = await api.evals();
  } catch (e) {
    mount(els.list, errorBox(e));
    return;
  }
  clear(els.list);
  if (!list.length) {
    els.list.appendChild(empty("Прогонов eval пока нет", "Запустите набор кнопкой выше или: docker compose exec console oilo eval"));
    mount(els.detail, empty("Нет данных"));
  }
  for (const r of list) {
    const sm = r.summary || {};
    const ci = sm.pass_rate_ci95;
    els.list.appendChild(h("a", {
      class: `run-item ${current && current.eval_id === r.eval_id ? "active" : ""}`,
      href: `#/quality/${encodeURIComponent(r.eval_id)}`, "data-id": r.eval_id
    },
      h("div", { class: "run-q" }, h("b", null, r.label || "без метки"), " ", h("span", { class: "muted mono small" }, r.eval_id)),
      h("div", { class: "run-meta" },
        h("span", { class: "pass-big" }, fmt.pct(sm.pass_rate, 0)),
        ci ? h("span", { class: "muted mono" }, `[${fmt.pct(ci[0], 0)}; ${fmt.pct(ci[1], 0)}]`) : null,
        badge(`${sm.cases ?? "?"} кейсов × ${r.repeats}`, "b-muted"),
        h("span", { class: "muted" }, r.dataset),
        h("span", { class: "muted" }, fmt.ts(r.started)))));
  }
  const opts = (sel, idx) => {
    clear(sel);
    list.forEach((r, i) => sel.appendChild(h("option", { value: r.eval_id, selected: i === idx },
      `${r.label || "—"} · ${r.eval_id} · ${fmt.pct((r.summary || {}).pass_rate, 0)}`)));
  };
  opts(els.base, Math.min(1, list.length - 1));
  opts(els.cand, 0);
  if (!current && list.length && !location.hash.includes("/quality/")) openEval(list[0].eval_id);
}

async function openEval(id) {
  mount(els.detail, loading("Загрузка прогона eval…"));
  for (const el of els.list.querySelectorAll(".run-item")) el.classList.toggle("active", el.dataset.id === id);
  try {
    current = await api.evalRun(id);
    renderEval(current);
  } catch (e) {
    mount(els.detail, errorBox(e));
  }
}

export function bars(obj, opts = {}) {
  const entries = Object.entries(obj || {});
  if (!entries.length) return empty(opts.emptyText || "нет данных");
  const max = opts.max ?? Math.max(...entries.map(([, v]) => v || 0), 1e-9);
  return h("div", { class: "bars" }, entries.map(([k, v]) =>
    h("div", { class: "bar-row", title: opts.titles && opts.titles[k] ? opts.titles[k] : k },
      h("div", { class: "bar-k" }, opts.label ? opts.label(k) : k),
      h("div", { class: "bar-track" }, h("div", {
        class: `bar-fill ${opts.cls ? opts.cls(v) : ""}`, style: { width: `${Math.max(0, Math.min(1, (v || 0) / max)) * 100}%` }
      })),
      h("div", { class: "bar-v mono" }, opts.fmt ? opts.fmt(v) : String(v)))));
}

function renderEval(rec) {
  const sm = rec.summary || {};
  const passK = Object.keys(sm).find((k) => k.startsWith("pass^"));
  const ci = sm.pass_rate_ci95;
  const failClass = (v) => (v >= 0.9 ? "ok" : v >= 0.7 ? "mid" : "low");
  let onlyFails = false;
  const tableHost = h("div");
  const drawTable = () => mount(tableHost, casesTable(rec.results || [], onlyFails));
  drawTable();
  mount(els.detail,
    h("div", { class: "panel-head" },
      h("h3", null, rec.label || rec.eval_id),
      h("span", { class: "mono muted small" }, rec.eval_id),
      badge(rec.dataset || "—", "b-muted"),
      badge(`повторов: ${rec.repeats}`, "b-muted"),
      badge(`версия агента: ${(rec.config || {}).agent_version || "—"}`, "b-info"),
      h("span", { class: "muted small" }, `${fmt.ts(rec.started)} · ${rec.duration_s ?? "—"} с`)),
    h("div", { class: "kpis" },
      kpi("Доля пройденных", fmt.pct(sm.pass_rate, 1), ci ? `95% ДИ Уилсона: ${fmt.pct(ci[0], 0)}–${fmt.pct(ci[1], 0)}` : ""),
      passK ? kpi(passK, fmt.pct(sm[passK], 1), "кейс пройден во всех повторах") : null,
      kpi("Цифры подтверждены",
        sm.numbers_verified_rate === null || sm.numbers_verified_rate === undefined ? "—" : fmt.pct(sm.numbers_verified_rate, 1),
        "доля чисел ответа, найденных в источниках"),
      kpi("Стоимость ответа", fmt.usd(sm.cost_usd_per_answer), `всего ${fmt.usd(sm.cost_usd_total)}`),
      kpi("Латентность p95", fmt.sec(sm.latency_p95_s), `p50 ${fmt.sec(sm.latency_p50_s)}`),
      kpi("Раундов агента", fmt.num(sm.rounds_mean, 2), "в среднем на ответ"),
      kpi("Кейсов / прогонов", `${sm.cases ?? "—"} / ${sm.runs ?? "—"}`),
      kpi("Ошибок", String(sm.errors ?? 0), "", sm.errors ? "bad" : "")),
    h("div", { class: "grid-3" },
      h("div", null, h("h4", { class: "sec-h" }, "По категориям"),
        bars(sm.by_category, { max: 1, fmt: (v) => fmt.pct(v, 0), cls: failClass, label: (k) => CATEGORY_TITLES[k] || k, titles: CATEGORY_TITLES })),
      h("div", null, h("h4", { class: "sec-h" }, "По проверкам"),
        bars(sm.by_check, { max: 1, fmt: (v) => fmt.pct(v, 0), cls: failClass, titles: CHECK_TITLES })),
      h("div", null, h("h4", { class: "sec-h" }, "Провалы по проверкам"),
        bars(sm.failures_by_check, { fmt: (v) => String(v), cls: () => "low", titles: CHECK_TITLES, emptyText: "провалов нет" }))),
    collapsible("Отпечаток версии агента", () => jsonTree(rec.config || {}, { depth: 2 })),
    h("div", { class: "panel-head", style: { marginTop: "12px" } },
      h("h4", { class: "sec-h", style: { margin: 0 } }, `Кейсы (${(rec.results || []).length})`),
      h("label", { class: "row small" },
        h("input", { type: "checkbox", onchange: (e) => { onlyFails = e.target.checked; drawTable(); } }), "только провалы")),
    tableHost);
}

function casesTable(results, onlyFails) {
  const rows = results.filter((r) => !onlyFails || !r.passed);
  if (!rows.length) return empty(onlyFails ? "Провалов нет" : "Результатов нет");
  return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl cases-tbl" },
    h("thead", null, h("tr", null, ["кейс", "категория", "выборка", "итог", "проверки", "время", "$", "раундов", "цифры", "инструменты", "ход"]
      .map((t) => h("th", null, t)))),
    h("tbody", null, rows.map((r) => {
      const failed = (r.checks || []).filter((c) => !c.ok);
      return h("tr", { class: r.passed ? "" : "row-fail" },
        h("td", { class: "mono" }, r.case_id),
        h("td", null, CATEGORY_TITLES[r.category] || r.category),
        h("td", null, badge(r.split || "—", r.split === "holdout" ? "b-warn" : "b-muted", r.split === "holdout" ? "скрытая выборка (holdout)" : "")),
        h("td", null, r.passed ? badge("пройден", "b-ok") : badge("провал", "b-bad")),
        h("td", { class: "checks-cell" },
          failed.length
            ? failed.map((c) => h("div", { class: "check-fail" },
              h("span", { class: "chip chip-bad", title: CHECK_TITLES[c.name] || c.name }, c.name), " ", h("span", { class: "small" }, c.detail)))
            : h("span", { class: "muted small" }, `все ${(r.checks || []).length} ок`),
          r.error ? h("div", { class: "text-bad small" }, r.error) : null),
        h("td", { class: "mono nowrap" }, fmt.ms(r.ms)),
        h("td", { class: "mono" }, fmt.usd(r.cost_usd)),
        h("td", { class: "mono" }, r.llm_calls),
        h("td", { class: "mono" }, `${r.numbers_verified}/${r.numbers_total}`),
        h("td", { class: "small mono path-cell" }, (r.path || []).filter((p) => p !== "finish_task").join(" → ") || "—"),
        h("td", null, r.run_id ? h("a", { href: `#/turns/${encodeURIComponent(r.run_id)}` }, "ход") : "—"));
    }))));
}

// ---------------------------------------------------------------------------------------------------------
// сравнение
// ---------------------------------------------------------------------------------------------------------

async function doCompare() {
  const b = els.base.value;
  const c = els.cand.value;
  if (!b || !c) return toast("Выберите базу и кандидата", "warn");
  if (b === c) return toast("База и кандидат совпадают", "warn");
  mount(els.compare, loading("Сравнение…"));
  try {
    const r = await api.evalCompare(b, c);
    renderCompare(r);
  } catch (e) {
    mount(els.compare, errorBox(e));
  }
}

function delta(a, b, pct = true, invert = false) {
  if (a === null || a === undefined || b === null || b === undefined) return h("span", { class: "muted" }, "—");
  const d = b - a;
  const good = invert ? d < 0 : d > 0;
  const cls = Math.abs(d) < 1e-12 ? "muted" : good ? "delta-up" : "delta-down";
  return h("span", { class: `mono ${cls}` }, `${d > 0 ? "+" : ""}${pct ? (d * 100).toFixed(1) + " п.п." : fmt.num(d, 5)}`);
}

function renderCompare(r) {
  const mc = r.mcnemar || {};
  const p = mc.p_value;
  const verdict = p === undefined || p === null ? "—"
    : mc.n_net > 0 && p < 0.05 ? "улучшение статистически значимо"
    : mc.n_net < 0 && p < 0.05 ? "ухудшение статистически значимо" : "различие не значимо (p ≥ 0.05)";
  const vcls = mc.n_net > 0 && p < 0.05 ? "ok" : mc.n_net < 0 && p < 0.05 ? "bad" : "";
  const table = (obj, titles) =>
    h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["", "база", "кандидат", "Δ"].map((t) => h("th", null, t)))),
      h("tbody", null, Object.entries(obj || {}).map(([k, v]) =>
        h("tr", null, h("td", { title: titles[k] || "" }, titles === CATEGORY_TITLES ? titles[k] || k : k),
          h("td", { class: "mono" }, fmt.pct(v.base, 0)), h("td", { class: "mono" }, fmt.pct(v.cand, 0)), h("td", null, delta(v.base, v.cand)))))));
  mount(els.compare,
    h("div", { class: "kpis" },
      kpi("Исправлено (n_fix)", String(mc.n_fix ?? "—"), "провал в базе → успех у кандидата", "ok"),
      kpi("Сломано (n_break)", String(mc.n_break ?? "—"), "успех в базе → провал у кандидата", mc.n_break ? "bad" : ""),
      kpi("Итог (net)", String(mc.n_net ?? "—")),
      kpi("p (точный McNemar)", p === undefined || p === null ? "—" : fmt.fixed(p, 4), verdict, vcls),
      kpi("Оба ок / оба провал", `${mc.both_ok ?? "—"} / ${mc.both_fail ?? "—"}`, `общих кейсов: ${r.common_cases ?? "—"}`),
      kpi("Доля пройденных", `${fmt.pct((r.pass_rate || {}).base, 0)} → ${fmt.pct((r.pass_rate || {}).cand, 0)}`),
      kpi("Стоимость ответа", `${fmt.usd((r.cost_usd_per_answer || {}).base)} → ${fmt.usd((r.cost_usd_per_answer || {}).cand)}`),
      kpi("Латентность p95", `${fmt.sec((r.latency_p95_s || {}).base)} → ${fmt.sec((r.latency_p95_s || {}).cand)}`)),
    h("p", { class: "muted small" }, "Значимы только несовпадающие пары кейсов: если кандидат одни кейсы чинит, а другие ломает, " +
      "доля пройденных может не измениться, а сломанное — важно."),
    h("div", { class: "grid-2" },
      h("div", null, h("h4", { class: "sec-h" }, `Исправленные кейсы (${(r.fixed_cases || []).length})`),
        (r.fixed_cases || []).length ? h("div", { class: "chips-inline" }, r.fixed_cases.map((c) => h("span", { class: "chip chip-ok" }, c)))
          : h("p", { class: "muted small" }, "нет")),
      h("div", null, h("h4", { class: "sec-h" }, `Сломанные кейсы (${(r.broken_cases || []).length})`),
        (r.broken_cases || []).length ? h("div", { class: "chips-inline" }, r.broken_cases.map((c) => h("span", { class: "chip chip-bad" }, c)))
          : h("p", { class: "muted small" }, "нет"))),
    h("div", { class: "grid-2" },
      h("div", null, h("h4", { class: "sec-h" }, "По категориям"), table(r.by_category, CATEGORY_TITLES)),
      h("div", null, h("h4", { class: "sec-h" }, "По проверкам"), table(r.by_check, CHECK_TITLES))),
    h("h4", { class: "sec-h" }, "Разница конфигураций"),
    r.config_diff && Object.keys(r.config_diff).length
      ? h("table", { class: "tbl" }, h("thead", null, h("tr", null, ["параметр", "база", "кандидат"].map((t) => h("th", null, t)))),
          h("tbody", null, Object.entries(r.config_diff).map(([k, [a, b]]) => h("tr", null,
            h("td", { class: "mono" }, k), h("td", { class: "mono" }, String(a ?? "—")), h("td", { class: "mono" }, String(b ?? "—"))))))
      : h("p", { class: "muted small" }, "Конфигурации совпадают (разница — только в случайности модели или данных)."));
}

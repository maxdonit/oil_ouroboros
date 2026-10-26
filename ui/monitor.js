// «Мониторинг»: только наблюдение. Метрики ходов агента по версиям (отпечаток агента): доли онлайн-проверок,
// деньги и время по слоям, кэш промпта, ошибки инструментов; бюджет LLM-шлюза и расход по вызывающим.

import { h, mount, fmt, empty, errorBox, loading, badge, kpi } from "./util.js";
import { api } from "./api.js";
import { CHECK_INFO } from "./turnview.js";

let els = {};
let hours = 24 * 7;
let source = "chat";

export async function init(root) {
  els.body = h("div");
  const hsel = h("select", { class: "input", onchange: (e) => { hours = +e.target.value; load(); } },
    [[24, "сутки"], [24 * 7, "неделя"], [24 * 30, "месяц"], [0, "всё время"]].map(([v, t]) => h("option", { value: v, selected: v === hours }, t)));
  const ssel = h("select", { class: "input", onchange: (e) => { source = e.target.value; load(); } },
    [["chat", "чат"], ["demo", "демо"], ["eval", "eval"], ["smoke", "smoke"], ["cli", "oilo ask"], ["all", "всё"]].map(([v, t]) => h("option", { value: v, selected: v === source }, t)));
  mount(root,
    h("div", { class: "page-head" }, h("h2", null, "Мониторинг"),
      h("p", null, "Консоль только наблюдает: агент и его инструменты работают сами, а здесь видно, как часто ответы проходят " +
        "проверку, сколько стоят и как быстро приходят — по каждой версии агента. Версия меняется, когда меняется регламент, " +
        "память агента, его модели или параметры инструментов.")),
    h("div", { class: "row" }, h("label", { class: "row small" }, "период ", hsel), h("label", { class: "row small" }, "ходы ", ssel),
      h("button", { class: "btn btn-sm", onclick: load }, "↻")),
    els.body);
  await load();
}

const SHARE_KEYS = ["answered", "research_first", "verify_called", "verify_ok", "answer_is_checked_text", "citations_in_turn", "labels",
  "persona_clean", "language_ok"];

async function load() {
  mount(els.body, loading());
  let m;
  try {
    m = await api.metrics({ hours, source });
  } catch (e) {
    return mount(els.body, errorBox(e));
  }
  const a = m.all || {};
  const b = m.budget || {};
  const cls = (v) => (v === undefined ? "" : v >= 0.95 ? "ok" : v >= 0.8 ? "" : "bad");
  const versions = Object.entries(m.by_version || {}).sort((x, y) => (y[1].turns || 0) - (x[1].turns || 0));
  mount(els.body,
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "Сводка"), badge(`${a.turns || 0} ходов`, "b-muted")),
      a.turns ? h("div", { class: "kpis" },
        kpi("Проверка пройдена", fmt.pct(a.verify_ok, 0), "финальная проверка консоли по доказательствам хода", cls(a.verify_ok)),
        kpi("Ответ = проверенный черновик", fmt.pct(a.answer_is_checked_text, 0), "агент не переписал текст после проверки",
          cls(a.answer_is_checked_text)),
        kpi("Сначала отчёты", fmt.pct(a.research_first, 0), "первый инструмент — research, список отчётов или расчёт", cls(a.research_first)),
        kpi("Отказы", fmt.pct(a.refused, 0), "доля вопросов вне компетенции"),
        kpi("Ходы с ошибкой инструмента", fmt.pct(a.tool_error_turns, 0), "", a.tool_error_turns ? "bad" : ""),
        kpi("Стоимость хода", fmt.usd(a.cost_avg_usd), `всего ${fmt.usd(a.cost_total_usd)}; агент ${fmt.pct(a.cost_agent_share, 0)}`),
        kpi("Время p50 / p95", `${fmt.ms(a.ms_p50)} / ${fmt.ms(a.ms_p95)}`),
        kpi("Раундов агента", fmt.num(a.rounds_avg, 1), `кэш промпта ${fmt.pct(a.cache_share, 0)}`))
        : empty("Ходов за период нет", "Задайте вопрос во вкладке «Чат»."),
      b.budget_usd !== undefined ? h("p", { class: "small" },
        `Бюджет LLM-шлюза: потрачено ${fmt.usd(b.spent_usd)} из ${fmt.usd(b.budget_usd)}; отказов по лимиту: ${b.refused ?? 0}; ` +
          `по вызывающим: `,
        Object.entries(b.by_caller || {}).map(([k, v]) => badge(`${k} ${fmt.usd(v)}`, "b-muted"))) : (b.error ? errorBox(b.error) : null)),
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "По версиям агента"),
        h("span", { class: "muted small" }, "отпечаток: регламент, память, модели, шаблон хода, инструменты, база отчётов")),
      versions.length ? h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null,
          ["версия", "ходов", ...SHARE_KEYS.map((k) => (CHECK_INFO[k] || [k])[0]), "$ / ход", "p95"].map((x) => h("th", null, x)))),
        h("tbody", null, versions.map(([v, r]) => h("tr", null, h("td", { class: "mono" }, v), h("td", { class: "mono" }, r.turns),
          ...SHARE_KEYS.map((k) => h("td", { class: `mono ${r[k] !== undefined && r[k] < 0.8 ? "val-false" : ""}` }, fmt.pct(r[k], 0))),
          h("td", { class: "mono" }, fmt.usd(r.cost_avg_usd)), h("td", { class: "mono" }, fmt.ms(r.ms_p95))))))) : empty("Нет данных")),
    h("p", { class: "muted small" },
      "Онлайн-проверки не требуют эталона. Правильность фактов меряет eval («Качество») на наборе вопросов с эталонными цифрами."));
}

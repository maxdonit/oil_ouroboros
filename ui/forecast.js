// «Прогноз»: расчётный модуль напрямую — история цены и прогноз с веером интервалов 50/80/95, честная
// карточка скилла против random walk (тест Диболда–Мариано), покрытие интервалов на бэктесте и сценарный
// калькулятор шока предложения с прозрачной формулой.

import { h, s, mount, clear, fmt, errorBox, loading, debounce, collapsible, kpi } from "./util.js";
import { api } from "./api.js";

const METHOD_TITLES = {
  auto: "auto — лучший по бэктесту на горизонте",
  ensemble: "ensemble — комбинация ETS+ARIMA+Theta",
  ets: "ets — AutoETS",
  arima: "arima — AutoARIMA",
  theta: "theta — AutoTheta",
  drift: "drift — random walk со сносом",
  rw: "rw — random walk (no-change)",
};
const METHOD_SHORT = { rw: "RW", drift: "RW+снос", ets: "ETS", arima: "ARIMA", theta: "Theta", ensemble: "Комбинация" };
const PRESET_TITLES = {
  kilian_low: "Низкие эластичности (Kilian)",
  cci: "Мета-оценка CCI (ФРС)",
  bh: "Baumeister–Hamilton",
};
const BENCH = [["brent", "Brent"], ["wti", "WTI"], ["urals", "Urals"]];
const RANGES = [["2018", "с 2018"], ["5", "5 лет"], ["2", "2 года"]];

const st = { benchmark: "brent", horizon: 3, method: "auto", range: "2018", fc: null, hist: {}, shock: null, metric: "rel" };
let els = {};
let fcAbort = null;
let shAbort = null;

export async function init(root) {
  els.bench = seg(BENCH, st.benchmark, (v) => { st.benchmark = v; refresh(); });
  els.hz = h("input", { type: "range", min: 1, max: 12, step: 1, value: st.horizon, "aria-label": "Горизонт, месяцев" });
  els.hzVal = h("b", { class: "mono" }, `${st.horizon} мес.`);
  els.hz.addEventListener("input", () => { els.hzVal.textContent = `${els.hz.value} мес.`; });
  els.hz.addEventListener("change", () => { st.horizon = +els.hz.value; refresh(); });
  els.method = h("select", { class: "input", "aria-label": "Метод", onchange: (e) => { st.method = e.target.value; refresh(); } },
    Object.entries(METHOD_TITLES).map(([k, t]) => h("option", { value: k, selected: k === st.method }, t)));
  els.range = seg(RANGES, st.range, (v) => { st.range = v; drawChart(); });
  els.chart = h("div", { class: "fc-chart" });
  els.side = h("div", { class: "fc-side" });
  els.interp = h("div", { class: "fc-interp" });
  els.skill = h("div");
  els.coverage = h("div");
  els.shock = h("div");
  els.status = h("span", { class: "muted small" });
  mount(root,
    h("div", { class: "page-head" }, h("h2", null, "Прогноз цены нефти"),
      h("p", null, "Тот же расчётный модуль, который вызывает аналитик: цифры считает код, модель их только интерпретирует.")),
    h("section", { class: "panel" },
      h("div", { class: "fc-controls" },
        h("div", { class: "field" }, h("span", { class: "field-label" }, "Бенчмарк"), els.bench),
        h("div", { class: "field fc-hz" }, h("span", { class: "field-label" }, "Горизонт: ", els.hzVal), els.hz),
        h("div", { class: "field" }, h("span", { class: "field-label" }, "Метод"), els.method),
        h("div", { class: "field" }, h("span", { class: "field-label" }, "Окно графика"), els.range),
        h("button", { class: "btn", onclick: refresh }, "Пересчитать"),
        els.status),
      h("div", { class: "fc-main" }, h("div", { class: "fc-chart-wrap" }, els.chart), els.side),
      els.interp),
    h("div", { class: "fc-grid2" },
      h("section", { class: "panel" }, els.skill),
      h("section", { class: "panel" }, els.coverage)),
    h("section", { class: "panel" }, els.shock));
  buildShock();
  refresh();
}

export function show() {}

function seg(items, value, onChange) {
  const el = h("div", { class: "seg", role: "group" });
  for (const [v, t] of items)
    el.appendChild(h("button", { type: "button", class: v === value ? "active" : "", onclick: (e) => {
      [...el.children].forEach((b) => b.classList.toggle("active", b === e.currentTarget));
      onChange(v);
    } }, t));
  return el;
}

async function refresh() {
  if (fcAbort) fcAbort.abort();
  const ctl = (fcAbort = new AbortController());
  const t0 = performance.now();
  els.status.textContent = "";
  els.chart.classList.add("is-loading");
  if (!st.fc)
    mount(els.chart, loading("Расчёт прогона… первый запрос после запуска сервера может идти до ~40 с (компиляция и бэктест 60 окон), " +
      "дальше — из кэша."));
  try {
    const [fc, hist] = await Promise.all([
      api.forecast({ benchmark: st.benchmark, horizon: st.horizon, method: st.method }, ctl.signal),
      st.hist[st.benchmark] ? Promise.resolve(st.hist[st.benchmark])
        : api.history({ benchmark: st.benchmark, start: "2018-01-01", freq: "M" }, ctl.signal),
    ]);
    if (ctl.signal.aborted) return;
    st.hist[st.benchmark] = hist;
    st.fc = fc;
    els.status.textContent = `рассчитано за ${fmt.ms(performance.now() - t0)}`;
    drawChart();
    drawSide();
    drawSkill();
    drawCoverage();
    scheduleShock();
  } catch (e) {
    if (e.name === "AbortError") return;
    mount(els.chart, errorBox(e, "Проверьте, что в data/prices есть ряды EIA и сервер видит каталог данных."));
    clear(els.side);
    clear(els.interp);
  } finally {
    if (fcAbort === ctl) els.chart.classList.remove("is-loading");
  }
}

// ---------------------------------------------------------------------------------------------------------
// график
// ---------------------------------------------------------------------------------------------------------

function niceStep(range, count) {
  const raw = range / Math.max(count, 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const f = raw / mag;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
}

const T = (d) => Date.parse(d + "T00:00:00Z");

function drawChart() {
  const fc = st.fc;
  const hist = st.hist[st.benchmark];
  if (!fc || !hist) return;
  let pts = hist.points.map((p) => ({ t: T(p.date), date: p.date, v: p.price }));
  if (st.range !== "2018") {
    const cutoff = T(fc.last_month_end) - (+st.range) * 365.25 * 864e5;
    pts = pts.filter((p) => p.t >= cutoff);
  }
  const anchor = { t: T(fc.last_month_end), date: fc.last_month_end, v: fc.last_price };
  const path = fc.path.map((p) => ({ ...p, t: T(p.date) }));
  const W = 900;
  const H = 340;
  const m = { l: 50, r: 18, t: 16, b: 30 };
  const xs = [...pts.map((p) => p.t), anchor.t, ...path.map((p) => p.t)];
  const ys = [...pts.map((p) => p.v), anchor.v, ...path.flatMap((p) => [p.lo95, p.hi95])];
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  let y0 = Math.min(...ys);
  let y1 = Math.max(...ys);
  const pad = (y1 - y0) * 0.06 || 5;
  y0 = Math.max(0, y0 - pad);
  y1 += pad;
  const step = niceStep(y1 - y0, 6);
  y0 = Math.floor(y0 / step) * step;
  y1 = Math.ceil(y1 / step) * step;
  const X = (t) => m.l + ((t - x0) / (x1 - x0 || 1)) * (W - m.l - m.r);
  const Y = (v) => H - m.b - ((v - y0) / (y1 - y0 || 1)) * (H - m.t - m.b);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "fc-svg", role: "img", "aria-label": `${fc.benchmark} история и прогноз` });
  const grid = s("g", { class: "fc-grid" });
  for (let v = y0; v <= y1 + 1e-9; v += step) {
    grid.appendChild(s("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }));
    grid.appendChild(s("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end", class: "fc-axis" }, fmt.num(v, 0)));
  }
  const yStart = new Date(x0).getUTCFullYear();
  const yEnd = new Date(x1).getUTCFullYear();
  for (let y = yStart; y <= yEnd + 1; y++) {
    const t = Date.UTC(y, 0, 1);
    if (t < x0 || t > x1) continue;
    grid.appendChild(s("line", { x1: X(t), x2: X(t), y1: m.t, y2: H - m.b, class: "v" }));
    grid.appendChild(s("text", { x: X(t), y: H - m.b + 16, "text-anchor": "middle", class: "fc-axis" }, String(y)));
  }
  svg.appendChild(grid);
  svg.appendChild(s("text", { x: m.l, y: m.t - 4, class: "fc-axis" }, "$/барр."));
  // веер интервалов: 95 → 80 → 50
  const band = (lo, hi, cls) => {
    const top = [anchor, ...path].map((p, i) => `${X(p.t)},${Y(i === 0 ? anchor.v : p[hi])}`);
    const bot = [anchor, ...path].map((p, i) => `${X(p.t)},${Y(i === 0 ? anchor.v : p[lo])}`).reverse();
    return s("polygon", { points: [...top, ...bot].join(" "), class: `fc-band ${cls}` });
  };
  svg.append(band("lo95", "hi95", "b95"), band("lo80", "hi80", "b80"), band("lo50", "hi50", "b50"));
  // история
  if (pts.length)
    svg.appendChild(s("path", { d: "M" + [...pts, anchor].map((p) => `${X(p.t).toFixed(1)},${Y(p.v).toFixed(1)}`).join(" L"), class: "fc-hist" }));
  // медиана
  svg.appendChild(s("path", {
    d: "M" + [anchor, ...path].map((p, i) => `${X(p.t).toFixed(1)},${Y(i === 0 ? anchor.v : p.median).toFixed(1)}`).join(" L"),
    class: "fc-median"
  }));
  for (const p of path) svg.appendChild(s("circle", { cx: X(p.t), cy: Y(p.median), r: 3.2, class: "fc-dot" }));
  // отсечка данных
  const xa = X(anchor.t);
  svg.appendChild(s("line", { x1: xa, x2: xa, y1: m.t, y2: H - m.b, class: "fc-cut" }));
  svg.appendChild(s("text", { x: xa - 4, y: m.t + 10, "text-anchor": "end", class: "fc-axis fc-cut-label" }, `данные по ${fc.as_of}`));
  // прямые подписи
  const last = path[path.length - 1];
  if (last) {
    svg.appendChild(s("text", {
      x: Math.min(X(last.t) + 6, W - m.r - 2), y: Y(last.median) - 6, "text-anchor": X(last.t) > W - 80 ? "end" : "start", class: "fc-direct"
    }, `медиана ${fmt.fixed(last.median, 1)}`));
  }
  // наведение
  const cross = s("line", { class: "fc-cross", y1: m.t, y2: H - m.b, x1: -10, x2: -10 });
  const dot = s("circle", { r: 4.5, class: "fc-hover-dot", cx: -10, cy: -10 });
  svg.append(cross, dot);
  const tip = h("div", { class: "fc-tip", hidden: true });
  const all = [...pts.map((p) => ({ kind: "hist", ...p })), { kind: "anchor", ...anchor }, ...path.map((p) => ({ kind: "fc", ...p, v: p.median }))];
  const hit = s("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, class: "fc-hit" });
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const sx = ((ev.clientX - r.left) / r.width) * W;
    let best = all[0];
    for (const p of all) if (Math.abs(X(p.t) - sx) < Math.abs(X(best.t) - sx)) best = p;
    cross.setAttribute("x1", X(best.t));
    cross.setAttribute("x2", X(best.t));
    dot.setAttribute("cx", X(best.t));
    dot.setAttribute("cy", Y(best.v));
    clear(tip);
    tip.append(h("div", { class: "fc-tip-h" }, best.date,
      best.kind === "fc" ? ` · прогноз h=${best.h}` : best.kind === "anchor" ? " · последнее наблюдение" : ""));
    if (best.kind === "fc")
      tip.append(
        h("div", null, "медиана ", h("b", null, fmt.fixed(best.median, 2))),
        h("div", { class: "muted" }, `50%: ${fmt.fixed(best.lo50, 1)}–${fmt.fixed(best.hi50, 1)}`),
        h("div", { class: "muted" }, `80%: ${fmt.fixed(best.lo80, 1)}–${fmt.fixed(best.hi80, 1)}`),
        h("div", { class: "muted" }, `95%: ${fmt.fixed(best.lo95, 1)}–${fmt.fixed(best.hi95, 1)}`));
    else tip.append(h("div", null, "цена ", h("b", null, fmt.fixed(best.v, 2)), " $/барр."));
    tip.hidden = false;
    const px = (X(best.t) / W) * r.width;
    tip.style.left = `${Math.min(px + 12, r.width - 170)}px`;
    tip.style.top = `${(Y(best.v) / H) * r.height - 10}px`;
  };
  hit.addEventListener("mousemove", move);
  hit.addEventListener("mouseleave", () => {
    tip.hidden = true;
    cross.setAttribute("x1", -10);
    cross.setAttribute("x2", -10);
    dot.setAttribute("cx", -10);
  });
  svg.appendChild(hit);
  const legend = h("div", { class: "legend fc-legend" },
    h("span", { class: "legend-item" }, h("span", { class: "fc-sw hist" }), `история (${hist.source})`),
    h("span", { class: "legend-item" }, h("span", { class: "fc-sw median" }), `медиана: ${fc.method_title}`),
    h("span", { class: "legend-item" }, h("span", { class: "fc-sw b50" }), "50%"),
    h("span", { class: "legend-item" }, h("span", { class: "fc-sw b80" }), "80%"),
    h("span", { class: "legend-item" }, h("span", { class: "fc-sw b95" }), "95% (conformal по ошибкам бэктеста)"));
  mount(els.chart, h("div", { class: "fc-svg-wrap" }, svg, tip), legend);
}

function drawSide() {
  const fc = st.fc;
  const last = fc.path[fc.path.length - 1] || {};
  const chg = fc.last_price ? (last.median / fc.last_price - 1) * 100 : 0;
  mount(els.side,
    kpi("Последняя цена", `${fmt.fixed(fc.last_price, 2)}`, `$/барр., на ${fc.as_of}`),
    kpi(`Медиана через ${fc.horizon_months} мес.`, fmt.fixed(last.median, 2), `${chg >= 0 ? "+" : ""}${chg.toFixed(1)}% · ${last.date || ""}`),
    kpi("80% интервал", `${fmt.fixed(last.lo80, 1)}–${fmt.fixed(last.hi80, 1)}`, `95%: ${fmt.fixed(last.lo95, 1)}–${fmt.fixed(last.hi95, 1)}`),
    kpi("Метод", fc.method_title, st.method === "auto" ? `выбран автоматически (наименьшая ошибка на h=${fc.horizon_months})` : "задан вручную"),
    h("div", { class: "muted small" }, "Источник: ", fc.data_source),
    fc.urals_discount ? h("div", { class: "small warn-line" },
      `Urals = Brent − дисконт $${fc.urals_discount.base} (сценарий ${fc.urals_discount.low}–${fc.urals_discount.high}). `,
      collapsible("источники дисконта", () => h("ul", { class: "pp-list" }, (fc.urals_discount.sources || []).map((x) => h("li", null, x)))))
      : null);
  mount(els.interp,
    h("div", { class: "fc-interp-text" }, h("b", null, "Интерпретация (детерминированный текст из чисел, не LLM): "), fc.interpretation),
    fc.caveats && fc.caveats.length ? h("ul", { class: "caveats" }, fc.caveats.map((c) => h("li", null, c))) : null,
    collapsible("Помесячный путь прогона", () =>
      h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["месяц", "h", "медиана", "50%", "80%", "95%"].map((t) => h("th", null, t)))),
        h("tbody", null, fc.path.map((p) => h("tr", null,
          h("td", null, p.date), h("td", { class: "mono" }, p.h), h("td", { class: "mono" }, fmt.fixed(p.median, 2)),
          h("td", { class: "mono" }, `${fmt.fixed(p.lo50, 1)}–${fmt.fixed(p.hi50, 1)}`),
          h("td", { class: "mono" }, `${fmt.fixed(p.lo80, 1)}–${fmt.fixed(p.hi80, 1)}`),
          h("td", { class: "mono" }, `${fmt.fixed(p.lo95, 1)}–${fmt.fixed(p.hi95, 1)}`))))))),
    fc.all_methods_last ? collapsible(`Все методы на горизонте ${fc.horizon_months} мес. (медиана)`, () =>
      h("div", { class: "chips-inline" }, Object.entries(fc.all_methods_last).map(([k, v]) =>
        h("span", { class: `chip ${k === fc.method ? "chip-on" : ""}` }, `${METHOD_SHORT[k] || k}: ${fmt.fixed(v, 2)}`)))) : null);
}

// ---------------------------------------------------------------------------------------------------------
// скилл и покрытие
// ---------------------------------------------------------------------------------------------------------

function cellColor(rel) {
  if (rel === null || rel === undefined) return null;
  const d = Math.max(-1, Math.min(1, (rel - 1) / 0.08));
  const pct = Math.round(Math.abs(d) * 34 + 6);
  return d < 0 ? `color-mix(in srgb, var(--good) ${pct}%, var(--surface))` : d > 0 ? `color-mix(in srgb, var(--bad) ${pct}%, var(--surface))` : null;
}

function drawSkill() {
  const fc = st.fc;
  const skill = fc.skill || [];
  const methods = [...new Set(skill.map((r) => r.method))];
  const hs = [...new Set(skill.map((r) => r.h))].sort((a, b) => a - b);
  const by = {};
  for (const r of skill) by[`${r.method}|${r.h}`] = r;
  const metricSeg = seg([["rel", "RMSE / RW"], ["dm", "p (DM)"], ["hit", "попадание в направление"]], st.metric,
    (v) => { st.metric = v; drawSkill(); });
  const val = (r) => {
    if (!r) return "—";
    if (st.metric === "rel") return fmt.fixed(r.rel_rmse_vs_rw, 3);
    if (st.metric === "dm") return r.dm_p_vs_rw === null || r.dm_p_vs_rw === undefined ? "—" : fmt.fixed(r.dm_p_vs_rw, 2);
    return r.hit_rate === null || r.hit_rate === undefined ? "—" : fmt.pct(r.hit_rate, 0);
  };
  const table = h("table", { class: "tbl skill-tbl" },
    h("thead", null, h("tr", null, h("th", null, "метод \\ горизонт, мес."),
      hs.map((x) => h("th", { class: `num ${x === fc.horizon_months ? "hcur" : ""}` }, String(x))))),
    h("tbody", null, methods.map((m) => h("tr", { class: m === fc.method ? "row-chosen" : "" },
      h("th", { class: "skill-m", title: METHOD_TITLES[m] || m }, METHOD_SHORT[m] || m,
        m === fc.method ? h("span", { class: "badge b-info" }, "выбран") : null),
      hs.map((x) => {
        const r = by[`${m}|${x}`];
        const best = fc.best_method_by_h && fc.best_method_by_h[String(x)] === m;
        const sig = r && r.dm_p_vs_rw !== null && r.dm_p_vs_rw !== undefined && r.dm_p_vs_rw < 0.1;
        return h("td", {
          class: `num mono ${best ? "best" : ""} ${sig ? "sig" : ""} ${x === fc.horizon_months ? "hcur" : ""}`,
          style: st.metric === "rel" && r && m !== "rw" ? { background: cellColor(r.rel_rmse_vs_rw) } : null,
          title: r ? `${METHOD_SHORT[m] || m}, h=${x}: RMSE(log) ${r.rmse_log}; к RW ${r.rel_rmse_vs_rw}; p(DM) ${r.dm_p_vs_rw ?? "—"}; ` +
            `попадание в направление ${r.hit_rate ?? "—"}; окон ${r.n}` : "",
        }, val(r));
      })))));
  mount(els.skill,
    h("div", { class: "panel-head" }, h("h3", null, "Скилл против random walk (бэктест, скользящее начало)"), h("span", { class: "spacer" }),
      metricSeg),
    h("p", { class: "panel-sub" }, "RMSE / RW < 1 — метод точнее наивного «завтра как сегодня» (зелёный), > 1 — хуже (красный). " +
      "Жирным — различие значимо по тесту Диболда–Мариано (p < 0.1). Рамка — лучший метод на горизонте. Прогноз нефти очень трудно " +
      "обыграть (Alquist–Kilian–Vigfusson), поэтому эта таблица показывается всегда."),
    h("div", { class: "tbl-wrap" }, table),
    h("div", { class: "muted small" }, `Окон бэктеста: ${skill[0] ? skill[0].n : "—"}.`));
}

function drawCoverage() {
  const cov = st.fc.coverage || {};
  const rows = Object.entries(cov).sort((a, b) => +a[0] - +b[0]);
  const W = 440;
  const rowH = 34;
  const m = { l: 112, r: 46 };
  const svg = s("svg", { viewBox: `0 0 ${W} ${rows.length * rowH + 26}`, class: "cov-svg", role: "img", "aria-label": "Покрытие интервалов" });
  const X = (v) => m.l + v * (W - m.l - m.r);
  [0, 0.25, 0.5, 0.75, 1].forEach((v) => {
    svg.appendChild(s("line", { x1: X(v), x2: X(v), y1: 4, y2: rows.length * rowH + 6, class: "cov-grid" }));
    svg.appendChild(s("text", { x: X(v), y: rows.length * rowH + 20, "text-anchor": "middle", class: "fc-axis" }, `${v * 100}%`));
  });
  rows.forEach(([lvl, act], i) => {
    const y = 8 + i * rowH;
    const nom = +lvl / 100;
    const diff = act - nom;
    const cls = Math.abs(diff) <= 0.05 ? "ok" : diff < 0 ? "under" : "over";
    svg.appendChild(s("text", { x: m.l - 8, y: y + 14, "text-anchor": "end", class: "cov-label" }, `${lvl}%-интервал`));
    svg.appendChild(s("rect", { x: X(0), y, width: X(act) - X(0), height: 18, rx: 4, class: `cov-bar ${cls}` }));
    svg.appendChild(s("line", { x1: X(nom), x2: X(nom), y1: y - 3, y2: y + 21, class: "cov-nom" }));
    svg.appendChild(s("text", { x: X(act) + 6, y: y + 14, class: "cov-val" }, fmt.pct(act, 0)));
  });
  mount(els.coverage,
    h("div", { class: "panel-head" }, h("h3", null, "Покрытие интервалов: заявлено и фактически")),
    h("p", { class: "panel-sub" }, "Доля отложенных окон бэктеста, где факт попал в интервал выбранного метода. Чёрточка — номинал; " +
      "честный интервал попадает примерно в номинал (±5 п.п.)."),
    svg,
    h("div", { class: "legend small" },
      h("span", { class: "legend-item" }, h("span", { class: "cov-sw ok" }), "около номинала"),
      h("span", { class: "legend-item" }, h("span", { class: "cov-sw under" }), "интервал узковат (покрытие ниже)"),
      h("span", { class: "legend-item" }, h("span", { class: "cov-sw over" }), "интервал с запасом")),
    h("h4", { class: "sec-h" }, "Лучший метод по горизонтам"),
    h("div", { class: "chips-inline" }, Object.entries(st.fc.best_method_by_h || {}).map(([k, v]) =>
      h("span", { class: "chip" }, `h=${k}: ${METHOD_SHORT[v] || v}`))));
}

// ---------------------------------------------------------------------------------------------------------
// сценарный калькулятор
// ---------------------------------------------------------------------------------------------------------

function slider(label, min, max, step, value, fmtv) {
  const out = h("b", { class: "mono" }, fmtv(value));
  const inp = h("input", { type: "range", min, max, step, value, "aria-label": label });
  inp.addEventListener("input", () => { out.textContent = fmtv(+inp.value); });
  return { el: h("div", { class: "field" }, h("span", { class: "field-label" }, label, ": ", out), inp), inp };
}

function buildShock() {
  els.d = slider("Изменение предложения, млн б/с", -5, 5, 0.1, -1.5, (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`);
  els.c = slider("Исполнение (доля)", 0, 1, 0.05, 1, (v) => `${Math.round(v * 100)}%`);
  els.o = slider("Компенсация другими (доля)", 0, 1, 0.05, 0, (v) => `${Math.round(v * 100)}%`);
  els.shockOut = h("div", { class: "shock-out" });
  for (const x of [els.d, els.c, els.o]) x.inp.addEventListener("change", scheduleShock);
  mount(els.shock,
    h("div", { class: "panel-head" }, h("h3", null, "Сценарный калькулятор: шок предложения (ОПЕК+ ±N млн б/с)"), h("span", { class: "spacer" }),
      h("button", { class: "btn btn-sm", onclick: runShock }, "Рассчитать")),
    h("p", { class: "panel-sub" }, "Поверх медианы базового прогноза (метод «комбинация», тот же бенчмарк и горизонт). " +
      "Отрицательное значение — сокращение добычи (цена растёт)."),
    h("div", { class: "shock-grid" },
      h("div", { class: "shock-controls" }, els.d.el, els.c.el, els.o.el,
        h("div", { class: "formula" },
          h("div", null, "чистый шок ", h("i", null, "s"), " = N · c · (1 − o) / Q"),
          h("div", null, "%ΔP ≈ ", h("i", null, "s"), " / (ε", h("sub", null, "S"), " + |ε", h("sub", null, "D"), "|)"),
          h("div", { class: "muted small" },
            "N — объём, c — исполнение, o — компенсация, Q — мировое предложение; ε — краткосрочные эластичности."))),
      els.shockOut));
}

const scheduleShock = debounce(runShock, 450);

async function runShock() {
  if (shAbort) shAbort.abort();
  const ctl = (shAbort = new AbortController());
  const params = {
    delta_mbd: +els.d.inp.value, benchmark: st.benchmark, horizon: st.horizon, compliance: +els.c.inp.value, offset: +els.o.inp.value
  };
  els.shockOut.classList.add("is-loading");
  if (!st.shock) mount(els.shockOut, loading("Расчёт сценария…"));
  try {
    const r = await api.shock(params, ctl.signal);
    if (ctl.signal.aborted) return;
    st.shock = r;
    drawShock(r);
  } catch (e) {
    if (e.name === "AbortError") return;
    mount(els.shockOut, errorBox(e));
  } finally {
    if (shAbort === ctl) els.shockOut.classList.remove("is-loading");
  }
}

function drawShock(r) {
  const sc = r.scenario || {};
  const imp = sc.impulse || {};
  const base = r.baseline_median;
  const inv = sc.inventory_channel_usd; // null — шок вне диапазона калибровки правила запасов: оценки нет
  const vals = [base, ...(r.baseline_interval80 || []), ...Object.values(imp).map((x) => x.price), ...(inv ? [base + inv[0], base + inv[1]] : [])]
    .filter((x) => typeof x === "number");
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  const pad = (hi - lo) * 0.08 || 3;
  lo -= pad;
  hi += pad;
  const W = 560;
  const m = { l: 160, r: 20 };
  const X = (v) => m.l + ((v - lo) / (hi - lo || 1)) * (W - m.l - m.r);
  const rows = [
    ["База (80% интервал)", "base"],
    ...Object.keys(imp).map((k) => [PRESET_TITLES[k] || k, k]),
    ...(inv ? [["Канал запасов (6–12 мес.)", "inv"]] : []),
  ];
  const rowH = 28;
  const H = rows.length * rowH + 30;
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "shock-svg", role: "img", "aria-label": "Сценарий: влияние на цену" });
  const step = niceStep(hi - lo, 5);
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    svg.appendChild(s("line", { x1: X(v), x2: X(v), y1: 4, y2: H - 22, class: "cov-grid" }));
    svg.appendChild(s("text", { x: X(v), y: H - 8, "text-anchor": "middle", class: "fc-axis" }, fmt.num(v, 0)));
  }
  svg.appendChild(s("line", { x1: X(base), x2: X(base), y1: 2, y2: H - 22, class: "shock-base" }));
  rows.forEach(([title, key], i) => {
    const y = 10 + i * rowH;
    svg.appendChild(s("text", { x: m.l - 8, y: y + 12, "text-anchor": "end", class: "cov-label" }, title));
    if (key === "base") {
      const [a, b] = r.baseline_interval80 || [base, base];
      svg.appendChild(s("rect", { x: X(a), y: y + 2, width: Math.max(1, X(b) - X(a)), height: 14, rx: 4, class: "shock-band" }));
      svg.appendChild(s("circle", { cx: X(base), cy: y + 9, r: 5, class: "shock-dot base" }));
    } else if (key === "inv") {
      const a = base + Math.min(inv[0], inv[1]);
      const b = base + Math.max(inv[0], inv[1]);
      svg.appendChild(s("rect", { x: X(a), y: y + 2, width: Math.max(2, X(b) - X(a)), height: 14, rx: 4, class: "shock-inv" }));
    } else {
      const p = imp[key];
      svg.appendChild(s("line", { x1: X(base), x2: X(p.price), y1: y + 9, y2: y + 9, class: "shock-stem" }));
      svg.appendChild(s("circle", { cx: X(p.price), cy: y + 9, r: 5, class: "shock-dot" }));
      svg.appendChild(s("text", {
        x: X(p.price) + (p.price >= base ? 9 : -9), y: y + 13, "text-anchor": p.price >= base ? "start" : "end", class: "cov-val"
      }, `${p.pct > 0 ? "+" : ""}${p.pct}%`));
    }
  });
  mount(els.shockOut,
    h("div", { class: "mini-tiles" },
      [["База (медиана)", `${fmt.fixed(base, 2)} $`], ["Чистый шок, доля предложения", fmt.pct(sc.net_shock_share, 2)],
        ["Мировое предложение", `${sc.world_supply_mbd} млн б/с`],
        ["Канал запасов", inv ? `${inv[0] > 0 ? "+" : ""}${inv[0]}…${inv[1] > 0 ? "+" : ""}${inv[1]} $` : "вне калибровки"]]
        .map(([k, v]) => h("div", { class: "mini-tile" }, h("div", { class: "mt-k" }, k), h("div", { class: "mt-v" }, v)))),
    svg,
    h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["пресет эластичностей", "ε_S", "ε_D", "%ΔP", "Δ $/барр.", "цена"].map((t) => h("th", null, t)))),
      h("tbody", null, Object.entries(imp).map(([k, p]) => h("tr", null,
        h("td", null, PRESET_TITLES[k] || k), h("td", { class: "mono" }, p.eps_s), h("td", { class: "mono" }, p.eps_d),
        h("td", { class: "mono" }, `${p.pct > 0 ? "+" : ""}${p.pct}%`), h("td", { class: "mono" }, `${p.usd > 0 ? "+" : ""}${p.usd}`),
        h("td", { class: "mono" }, fmt.fixed(p.price, 2))))))),
    h("p", { class: "shock-expl" }, sc.explanation || ""),
    collapsible(`Источники (${(sc.sources || []).length})`, () => h("ul", { class: "pp-list" }, (sc.sources || []).map((x) => h("li", null, x)))),
    h("div", { class: "muted small" },
      `Данные по ${r.as_of} · горизонт ${r.horizon_months} мес. · бенчмарк ${String(r.benchmark || st.benchmark).toUpperCase()}`));
}


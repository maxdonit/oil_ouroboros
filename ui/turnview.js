// Отображение хода агента (общее для «Чата», «Ходов» и «Качества»): шаги — раунды Ouroboros и вызовы
// инструментов, онлайн-проверки, деньги и время по слоям, проверка цифр, процитированные источники.

import { h, fmt, badge, collapsible, empty, kvTable, jsonTree } from "./util.js";
import { renderMarkdown } from "./md.js";

export const TOOL_TITLES = {
  research: "исследование: отчёты → достаточность → веб",
  search_reports: "поиск по отчётам",
  search_web: "веб-поиск",
  forecast_price: "прогноз цены",
  supply_shock_scenario: "сценарий шока предложения",
  price_history: "история цен",
  price_quote: "текущая котировка",
  verify_answer: "проверка ответа",
  list_reports: "список отчётов",
  finish_task: "завершение хода",
  knowledge_read: "чтение регламента",
  knowledge_list: "список знаний",
};

export const CHECK_INFO = {
  answered: ["ответ получен", "задача агента завершилась с ответом"],
  research_first: ["сначала отчёты", "первым — research, список отчётов или расчёт (в том числе текущая котировка) — правило приоритета источников"],
  verify_called: ["проверка вызвана", "агент отдал черновик на verify_answer"],
  verify_ok: ["проверка пройдена", "все цифры подтверждены источниками, неизвестных ссылок нет"],
  answer_is_checked_text: ["ответ = проверенный текст",
    "агент завершил ход ровно тем черновиком, что прошёл verify_answer (не переписал его после проверки)"],
  labels: ["метки источников", "в тексте нет сырых id [R#] — только метки [Отчёт …] / [Источник: …]"],
  citations_in_turn: ["источники этого хода", "все процитированные R#/W#/F# получены инструментами в этом ходе"],
  persona_clean: ["без «Ouroboros»", "ответ от лица аналитика, без внутренней кухни агента"],
  language_ok: ["язык вопроса", "ответ на языке вопроса"],
  refused: ["отказ", "вопрос вне компетенции — вежливый отказ без инструментов"],
};

export function answerMarkdown(text) {
  return renderMarkdown(text || "", { chips: true, breaks: true, headingShift: 2, idPrefix: "a-" });
}

export function checksChips(checks) {
  if (!checks) return null;
  const items = [];
  for (const [k, [title, hint]] of Object.entries(CHECK_INFO)) {
    if (!(k in checks)) continue;
    if (k === "refused") {
      if (checks.refused) items.push(h("span", { class: "chip", title: hint }, "отказ вне компетенции"));
      continue;
    }
    if (checks.refused && k !== "answered") continue;
    items.push(h("span", { class: `chip ${checks[k] ? "chip-ok" : "chip-bad"}`, title: hint }, `${checks[k] ? "✓" : "✗"} ${title}`));
  }
  if (checks.finish_held)
    items.push(h("span", { class: "chip chip-warn",
      title: "Ouroboros держал завершение после finish_task — ход закрыла консоль проверенным текстом" }, "завершён консолью"));
  if (checks.final_rewrite_rejected)
    items.push(h("span", { class: "chip chip-warn",
      title: "агент переписал проверенный черновик, правка не прошла финальную проверку — пользователю показан проверенный черновик" },
      "правка агента отклонена"));
  if (checks.tool_errors)
    items.push(h("span", { class: "chip chip-bad", title: "вызовы инструментов с ошибкой" }, `ошибок инструментов: ${checks.tool_errors}`));
  if ((checks.builtin_tools || []).filter((t) => !["finish_task"].includes(t)).length)
    items.push(h("span", { class: "chip chip-warn", title: "встроенные инструменты Ouroboros, кроме finish_task" },
      `встроенные: ${checks.builtin_tools.filter((t) => t !== "finish_task").join(", ")}`));
  return h("div", { class: "chips-inline" }, items);
}

export function costLine(t) {
  return h("span", { class: "muted small" },
    `${fmt.ms(t.ms)} · ${fmt.usd(t.cost_usd)} (агент ${fmt.usd(t.cost_agent_usd)}, субагенты ${fmt.usd(t.cost_tools_usd)}) · ` +
      `раундов ${t.rounds ?? "—"}`,
    t.tokens_in ? ` · вход ${fmt.int(t.tokens_in)} ток., из кэша ${fmt.pct(t.tokens_in ? (t.tokens_cached || 0) / t.tokens_in : 0, 0)}` : "");
}

/** Хронология хода: раунды LLM агента и вызовы инструментов вперемешку, по времени. */
export function timeline(t, opts = {}) {
  const rows = [];
  for (const r of t.llm_rounds || []) rows.push({ ts: r.ts, kind: "llm", r });
  for (const c of t.tool_calls || []) rows.push({ ts: c.ts, kind: "tool", c });
  rows.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
  if (!rows.length) return empty("Шагов нет", "Ход ещё не начался или журнал Ouroboros недоступен.");
  const t0 = Date.parse(rows[0].ts) || 0;
  const rel = (ts) => {
    const x = Date.parse(ts);
    return x && t0 ? `+${((x - t0) / 1000).toFixed(1)} с` : "";
  };
  return h("ol", { class: "tl" }, rows.map((row) => {
    if (row.kind === "llm") {
      const r = row.r;
      return h("li", { class: "tl-llm" },
        h("span", { class: "tl-t mono small" }, rel(row.ts)),
        badge(`раунд ${r.round ?? "?"}`, "b-muted"),
        h("span", { class: "small" }, ` ${r.model || ""} · вход ${fmt.int(r.tokens_in)} (кэш ${fmt.int(r.tokens_cached)}) → ` +
          `${fmt.int(r.tokens_out)} ток. · ${fmt.usd(r.cost_usd)}`),
        r.category && r.category !== "main" ? badge(r.category, "b-info") : null);
    }
    const c = row.c;
    const run = c.run || {};
    const title = TOOL_TITLES[c.tool] || c.tool;
    const head = h("span", null,
      h("span", { class: "tl-t mono small" }, rel(row.ts)),
      badge(c.mcp ? "MCP" : "встроенный", c.mcp ? "b-ok" : "b-warn"),
      h("b", null, ` ${c.tool}`), h("span", { class: "muted small" }, ` — ${title}`),
      h("span", { class: "small" }, ` · ${fmt.ms(c.ms)}`), c.ok ? null : badge("ошибка", "b-bad"),
      run.path && run.path.length ? h("span", { class: "muted small mono" }, ` · граф: ${run.path.join(" → ")}`) : null,
      run.cost_usd ? h("span", { class: "muted small" }, ` · субагенты ${fmt.usd(run.cost_usd)}`) : null);
    return h("li", { class: `tl-tool ${c.ok ? "" : "tl-bad"}` },
      collapsible(head, () => toolDetail(c, opts), false, "tl-col"));
  }));
}

function toolDetail(c, opts) {
  const run = c.run || {};
  return h("div", { class: "stack" },
    h("div", null, h("h5", { class: "sec-h" }, "Аргументы (как их передал агент)"), jsonTree(c.args || {}, { depth: 2 })),
    run.nodes && run.nodes.length ? h("div", null, h("h5", { class: "sec-h" }, "Узлы графа LangGraph"),
      h("table", { class: "tbl" }, h("thead", null, h("tr", null, ["узел", "мс", "итог узла"].map((x) => h("th", null, x)))),
        h("tbody", null, run.nodes.map((n) => h("tr", null, h("td", { class: "mono" }, n.node), h("td", { class: "mono" }, fmt.int(n.ms)),
          h("td", { class: "small" }, n.summary ? h("code", { class: "md-code" }, fmt.short(JSON.stringify(n.summary), 220)) : "—")))))) : null,
    run.llm && run.llm.length ? h("div", null, h("h5", { class: "sec-h" }, "LLM субагентов"),
      h("ul", { class: "small" }, run.llm.map((l) => h("li", null,
        `${l.purpose}: ${l.model} · ${fmt.ms(l.ms)} · ${fmt.int(l.tokens_in)}→${fmt.int(l.tokens_out)} ток. · ${fmt.usd(l.cost_usd)}`)))) : null,
    run.grade ? h("div", null, h("h5", { class: "sec-h" }, "Достаточность отчётов"), kvTable(run.grade)) : null,
    run.web_reasons ? h("div", null, h("h5", { class: "sec-h" }, "Решение о вебе"),
      h("p", { class: "small" }, run.web_reasons.length ? `веб: ${run.web_reasons.join(", ")}` : "веб не нужен")) : null,
    run.verification ? verificationBox(run.verification) : null,
    run.decisions && run.decisions.length ? collapsible(`Журнал решений (${run.decisions.length})`, () => decisionsTable(run.decisions)) : null,
    c.preview ? h("pre", { class: "pre small" }, c.preview) : null,
    c.trace && opts.traceLink !== false ? h("p", { class: "small" }, "trace ", h("code", null, c.trace), " · ",
      h("a", { href: `#/turns/tool/${encodeURIComponent(c.trace)}` }, "полный прогон инструмента (промпты субагентов)")) : null);
}

export function decisionsTable(ds) {
  return h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
    h("thead", null, h("tr", null, ["узел", "решение", "выбрано", "коды причин", "политика"].map((x) => h("th", null, x)))),
    h("tbody", null, ds.map((d) => h("tr", null, h("td", { class: "mono small" }, d.node), h("td", { class: "small" }, d.type),
      h("td", { class: "small" }, fmt.short(String(d.chosen ?? ""), 80)), h("td", { class: "small mono" }, (d.reason_codes || []).join(", ")),
      h("td", { class: "small" }, fmt.short(d.policy || "", 120)))))));
}

export function verificationBox(v) {
  const issues = v.issues || [];
  return h("div", null,
    h("h5", { class: "sec-h" }, `Проверка ответа: ${v.ok ? "пройдена" : "не пройдена"} — цифр ${v.numbers_verified ?? 0}/${v.numbers_total ?? 0}, ` +
      `ссылок ${(v.citations_total || 0) - (v.citations_unknown || 0)}/${v.citations_total || 0}`),
    issues.length ? h("ul", { class: "small" }, issues.slice(0, 20).map((i) =>
      h("li", { class: ["unverified_number", "unknown_citation", "weak_source"].includes(i.type) ? "text-bad" : "" },
        h("b", null, i.type), ` «${fmt.short(i.sentence || "", 140)}» ${i.number || ""} — ${i.suggestion || ""}`)))
      : h("p", { class: "muted small" }, "замечаний нет"));
}

export function evidenceList(t) {
  const ev = t.evidence || {};
  const ids = Object.keys(ev);
  if (!ids.length) return empty("Источников в ответе нет");
  return h("div", { class: "stack" }, ids.map((id) => {
    const e = ev[id];
    const kind = { report: "отчёт", web: "веб", tool: "расчёт" }[e.kind] || e.kind;
    return collapsible(h("span", null, badge(id, "b-muted"), " ", badge(kind, e.kind === "report" ? "b-ok" : e.kind === "web" ? "b-info" : "b-warn"),
      ` ${e.label}`, e.kind === "web" && e.tier !== undefined ? h("span", { class: "muted small" }, ` · уровень ${e.tier}`) : null),
    () => h("div", null, e.url ? h("p", { class: "small" }, h("a", { href: e.url, target: "_blank", rel: "noopener noreferrer" }, e.url)) : null,
      h("pre", { class: "pre small" }, e.text || "")));
  }));
}

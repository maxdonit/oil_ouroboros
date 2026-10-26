// «Параметры»: всё, что определяет поведение агента, с версиями — основа будущего самоулучшения.
// Агент (Ouroboros): регламент и память (identity, overview, patterns) с журналами изменений, настройки (модели,
// режимы), шаблон хода и отключённые встроенные инструменты. Инструменты (LangGraph): каталог config/ с отпечатком.

import { h, mount, fmt, empty, errorBox, loading, badge, collapsible, jsonTree } from "./util.js";
import { api } from "./api.js";
import { renderMarkdown } from "./md.js";

let els = {};

export async function init(root) {
  els.body = h("div");
  mount(root,
    h("div", { class: "page-head" }, h("h2", null, "Параметры и версии"),
      h("p", null, "Здесь видно, из чего складывается версия агента и что можно улучшать. Поведение агента — его память " +
        "(регламент knowledge/overview.md, identity.md): её агент может улучшать сам штатным knowledge_write, каждое изменение — " +
        "в журнале. Поведение инструментов — каталог config/ (промпты субагентов, параметры поиска, веб-политика, прогноз) " +
        "со строгой схемой и отпечатком.")),
    els.body);
  await load();
}

async function load() {
  mount(els.body, loading());
  let p;
  try {
    p = await api.params();
  } catch (e) {
    return mount(els.body, errorBox(e));
  }
  const fp = p.fingerprint || {};
  const cat = p.tools_catalog || {};
  const files = fp.files || {};
  const memHost = h("div");
  mount(els.body,
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "Версия агента"), h("code", { class: "md-code" }, fp.agent_version || "—"),
        (fp.modified_by_agent || []).length ? badge(`агент изменил: ${fp.modified_by_agent.join(", ")}`, "b-warn")
          : badge("память = засев из репозитория", "b-ok")),
      h("table", { class: "tbl" }, h("tbody", null,
        [["Ouroboros", fp.ouroboros], ["модели и режимы", JSON.stringify(fp.settings || {})], ["шаблон хода (sha)", fp.task_template],
         ["каталог инструментов", fp.catalog], ["MCP-инструменты (sha)", fp.mcp_tools],
         ["база отчётов (sha)", `${fp.corpus || "—"} · ${fmt.int(fp.index_chunks)} фрагм.`],
         ["бюджеты (не версия)", JSON.stringify(fp.budgets || {})]].map(([k, v]) =>
          h("tr", null, h("td", { class: "small" }, k), h("td", { class: "mono small" }, v ?? "—")))))),
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "Память агента (Ouroboros)"),
        h("span", { class: "muted small" }, "sha файлов; просмотр — по кнопке")),
      h("table", { class: "tbl" }, h("tbody", null, Object.entries(files).map(([f, sha]) => h("tr", null,
        h("td", { class: "mono small" }, f), h("td", { class: "mono small" }, sha || "нет файла"),
        h("td", null, sha ? h("button", { class: "btn btn-xs", onclick: async () => {
          mount(memHost, loading());
          try {
            const m = await api.memoryFile(f);
            mount(memHost, h("div", null, h("h4", { class: "sec-h" }, f), renderMarkdown(m.text || "")));
          } catch (e) {
            mount(memHost, errorBox(e));
          }
        } }, "показать") : null))))),
      memHost,
      collapsible("Журналы изменений памяти (identity, knowledge)", () => journals(p.journals || {}))),
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "Ход: что консоль передаёт агенту")),
      h("pre", { class: "pre small" }, p.turn_rules || ""),
      collapsible(`Отключённые встроенные инструменты Ouroboros (${(p.disabled_tools || []).length})`,
        () => h("p", { class: "small mono" }, (p.disabled_tools || []).join(", ")))),
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "Каталог инструментов (config/)"),
        h("code", { class: "md-code" }, cat.ref || cat.error || "—")),
      cat.values ? h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["параметр", "значение", "слой", "описание"].map((x) => h("th", null, x)))),
        h("tbody", null, cat.values.map((r) => h("tr", null,
          h("td", { class: "mono small" }, r.key), h("td", { class: "mono small" }, fmt.short(JSON.stringify(r.value), 80)),
          h("td", null, badge(r.layer, r.layer === "slot" ? "b-muted" : "b-warn")), h("td", { class: "small" }, r.description)))))) : null,
      cat.prompts ? collapsible("Промпты субагентов", () => jsonTree(cat.prompts, { depth: 2 })) : null,
      cat.lock ? collapsible("Файлы каталога (version#sha)", () => jsonTree(cat.lock, { depth: 1 })) : null),
    collapsible("Окружение консоли (секреты скрыты)", () => h("table", { class: "tbl" }, h("tbody", null,
      (p.console_settings || []).map((r) => h("tr", null,
        h("td", { class: "mono small" }, r.name), h("td", { class: "mono small" }, r.value), h("td", { class: "small" }, r.description)))))));
}

function journals(js) {
  const entries = Object.entries(js);
  if (!entries.length) return empty("Журналов нет");
  return h("div", null, entries.map(([f, rows]) => h("div", null, h("h5", { class: "sec-h" }, `${f} (${rows.length})`),
    rows.length ? h("ul", { class: "small" }, rows.slice(0, 30).map((r) =>
      h("li", null, `${fmt.ts(r.ts || r.at)} · ${r.topic || r.action || ""} · ${r.task_id || ""} `,
      h("code", { class: "md-code" }, fmt.short(JSON.stringify(r), 200))))) : h("p", { class: "muted small" }, "изменений не было"))));
}

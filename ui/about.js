// «Архитектура»: интерактивная карта системы (сервисы и связи — из docker-compose.yml) и графы инструментов LangGraph
// (из кода: узлы, параметры каталога, наложение реального хода), ход ответа (docs/guide/how_it_works.md), статичная
// схема (ui/architecture.svg, она же в README), развёртывание, MCP-инструменты, правила хода, документация.

import { h, mount, errorBox, loading, collapsible, badge } from "./util.js";
import { api } from "./api.js";
import { renderMarkdown } from "./md.js";
import { systemMap } from "./sysmap.js";
import { toolGraphs } from "./graphview.js";

const FLOW_DOC = "guide/how_it_works.md";

const DOCS = [
  ["_root/README.md", "README: что это, запуск, примеры, архитектура, требования"],
  ["architecture/DESIGN.md", "Архитектура: решения, особенности Ouroboros, данные, риски"],
  ["guide/how_it_works.md", "Как работает ход агента — по шагам"],
  ["guide/sources.md", "Источники: что подключено и как добавить новый"],
  ["guide/owner.md", "Канал владельца: UI Ouroboros, Telegram, утренняя сводка"],
  ["guide/operations.md", "Эксплуатация: готовность, логи, деньги, резервная копия, обновление, сбои"],
  ["guide/parameters.md", "Справочник параметров: что делает, почему так, что будет при изменении"],
  ["requirements.md", "Требования к агенту"],
  ["adr/0001-ouroboros-as-agent.md", "ADR-0001: агент — Ouroboros, инструменты — LangGraph"],
  ["demo_dialogs.md", "Демо-диалоги (реальные прогоны)"],
  ["report.md", "Отчёт: решения, качество, ограничения, что улучшить"],
  ["eval_report.md", "Полный прогон eval (42 кейса, профиль openrouter)"],
  ["_root/config/README.md", "Каталог параметров инструментов"],
];

let els = {};

export async function init(root) {
  els.body = h("div");
  els.doc = h("div");
  mount(root, h("div", { class: "page-head" }, h("h2", null, "Архитектура")), els.body,
    h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", null, "Документация")),
      h("div", { class: "chips-inline" }, DOCS.map(([p, t]) => h("button", { class: "chip", onclick: () => openDoc(p) }, t))), els.doc));
  await load();
}

const openDoc = (path) => showDoc(els.doc, path);

async function showDoc(host, path, opts = {}) {
  mount(host, loading());
  try {
    const text = await api.doc(path);
    // документ внутри своей панели — без собственного заголовка: он уже в шапке панели
    mount(host, renderMarkdown(opts.dropTitle ? text.replace(/^#\s.*\n+/, "") : text, { headingShift: 1, ...opts }));
  } catch (e) {
    mount(host, errorBox(e, "Документ не найден."));
  }
}

// те же графы списком — для печати и чтения без мыши
const flow = (g) => h("div", null, h("h4", { class: "sec-h" }, g.tools.join(" · ")),
  h("ol", { class: "flow" }, g.nodes.map((n) =>
    h("li", null, h("b", { class: "mono" }, n.id), " — ", n.title, " ", badge(n.kind_title, n.kind === "llm" ? "b-info" : "b-muted")))));

async function load() {
  mount(els.body, loading());
  let a;
  try {
    a = await api.about();
  } catch (e) {
    return mount(els.body, errorBox(e));
  }
  const g = a.graphs || {};
  const flowDoc = h("div");
  showDoc(flowDoc, FLOW_DOC, { idPrefix: "flow-", dropTitle: true });
  const graphsPanel = h("section", { class: "panel", id: "arch-graphs" },
    h("div", { class: "panel-head" }, h("h3", null, "Графы инструментов (LangGraph)"),
      h("span", { class: "muted small" }, "из кода сервиса tools: узлы, параметры, наложение реального хода")),
    g.error || !g.graphs ? errorBox(g.error || "графы недоступны") : [toolGraphs(g.graphs),
      collapsible("Те же графы списком", () => h("div", { class: "grid-2" }, g.graphs.map(flow)))]);
  mount(els.body,
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "Карта системы"),
        h("span", { class: "muted small" }, "сервисы и связи — из docker-compose.yml (x-role, x-why, x-links)")),
      systemMap(a.deployment || {}, { onTools: () => graphsPanel.scrollIntoView({ behavior: "smooth", block: "start" }) })),
    graphsPanel,
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "Ход ответа"), h("span", { class: "muted small" }, `docs/${FLOW_DOC}`)),
      flowDoc),
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "Схема"), h("span", { class: "muted small" }, "статичная картинка — та же, что в README")),
      h("a", { href: "/ui/architecture.svg", target: "_blank", rel: "noopener" },
        h("img", { class: "arch-img", src: "/ui/architecture.svg", alt: "Архитектура: консоль, Ouroboros, инструменты LangGraph, шлюз, данные" }))),
    deployment(a.deployment || {}),
    h("section", { class: "panel" },
      h("div", { class: "panel-head" }, h("h3", null, "MCP-инструменты агента"),
        h("span", { class: "muted small" }, "так их видит Ouroboros: mcp_oil__<имя>")),
      h("table", { class: "tbl" }, h("tbody", null, (g.mcp_tools || []).map((t) =>
        h("tr", null, h("td", { class: "mono" }, t.name), h("td", { class: "small" }, t.description)))))),
    h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", null, "Правила хода (текст задачи)")),
      h("pre", { class: "pre small" }, a.turn_rules || "")));
}

const list = (xs) => (xs && xs.length ? xs.join(", ") : "—");

function deployment(d) {
  const svc = d.services || [];
  return h("section", { class: "panel" },
    h("div", { class: "panel-head" }, h("h3", null, "Развёртывание"), h("span", { class: "muted small" }, "из docker-compose.yml")),
    h("p", { class: "small" },
      "Ядро запускает ", h("code", null, "docker compose up -d"), "; сервисы «по профилю» включаются переменной ",
      h("code", null, "COMPOSE_PROFILES"), " в .env: owner — канал владельца, gigachat — модели GigaChat; шаблон .env включает оба. Как запустить — README, «Быстрый старт»."),
    h("p", { class: "small" }, h("b", null, "Порядок запуска"), " (по depends_on): ",
      (d.order || []).map((step, i) => [i ? " → " : "", step.join(" · ")])),
    h("div", { class: "tbl-wrap" },
      h("table", { class: "tbl" },
        h("thead", null, h("tr", null, ["контейнер", "что делает", "зачем отдельно", "образ", "когда работает", "сети", "порт наружу",
          "тома", "ждёт", "лимиты"].map((x) => h("th", null, x)))),
        h("tbody", null, svc.map((c) => h("tr", null,
          h("td", { class: "mono" }, c.name, c.healthcheck ? badge("healthcheck", "b-muted") : null),
          h("td", { class: "small" }, c.role),
          h("td", { class: "small" }, c.why),
          h("td", { class: "small mono" }, c.image, c.own_build ? badge("свой", "b-info") : null),
          h("td", { class: "small" }, c.when),
          h("td", { class: "small mono" }, list(c.networks)),
          h("td", { class: "small mono" }, list(c.ports)),
          h("td", { class: "small mono" }, list(c.volumes)),
          h("td", { class: "small mono" }, list(c.depends_on)),
          h("td", { class: "small" }, c.limits || "—")))))),
    h("div", { class: "grid-2" },
      h("div", null, h("h4", { class: "sec-h" }, "Сети"),
        h("table", { class: "tbl" }, h("tbody", null, (d.networks || []).map((n) => h("tr", null,
          h("td", { class: "mono" }, n.name, n.internal ? badge("без выхода наружу", "b-warn") : null), h("td", { class: "small" }, n.why)))))),
      h("div", null, h("h4", { class: "sec-h" }, "Тома (данные между перезапусками)"),
        h("table", { class: "tbl" }, h("tbody", null, (d.volumes || []).map((v) => h("tr", null,
          h("td", { class: "mono" }, v.name), h("td", { class: "small" }, v.why))))))));
}

// Графы инструментов (LangGraph) для вкладки «Архитектура»: узлы и рёбра — из кода (/api/graphs): вид узла, «как и
// почему» (docstring функции узла), параметры каталога config/ с текущими значениями, ветвления с подписью. Щелчок по
// узлу — его паспорт; поверх графа можно наложить реальный ход из журнала консоли: пройденный путь и время узлов.

import { h, s, mount, badge, fmt } from "./util.js";
import { api } from "./api.js";
import { wrap2 } from "./sysmap.js";

const COL_W = 196;
const ROW_H = 96;
const NODE_W = 160;
const NODE_H = 70;
const PAD_X = 16;
const PAD_TOP = 58;

const KIND_CLS = { llm: "gv-llm", code: "gv-code", db: "gv-db", web: "gv-web", calc: "gv-calc" };

// слой узла — длина самого длинного пути от начала; узлы одного слоя — друг под другом
function layout(nodes, edges) {
  const layer = Object.fromEntries(nodes.map((n) => [n.id, 0]));
  for (let i = 0; i < nodes.length; i++) {
    for (const e of edges) layer[e.to] = Math.max(layer[e.to], layer[e.from] + 1);
  }
  const rowsUsed = {};
  return Object.fromEntries(nodes.map((n) => {
    const l = layer[n.id];
    const r = (rowsUsed[l] = (rowsUsed[l] ?? -1) + 1);
    return [n.id, { x: PAD_X + l * COL_W, y: PAD_TOP + r * ROW_H, layer: l }];
  }));
}

const firstParagraph = (text) => String(text || "").split(/\n\s*\n/)[0].replace(/\s+/g, " ").trim();
const short = (text, n) => (text.length > n ? text.slice(0, n - 1) + "…" : text);

/** Просмотр графов: graphs — список из /api/graphs.graphs. */
export function toolGraphs(graphs) {
  const state = { gi: 0, selected: null, overlay: null };
  const host = h("div", { class: "gv-canvas" });
  const side = h("aside", { class: "sm-side" });
  const head = h("div", { class: "gv-head small" });
  const overlaySel = h("select", { class: "input", "aria-label": "Наложить реальный ход", onchange: (e) => applyOverlay(e.target.value) });
  const overlayInfo = h("span", { class: "muted small" });
  const tabs = h("div", { class: "chips-inline" });
  let turns = null; // журнал ходов (лениво; не загрузился — наложения нет)

  const graph = () => graphs[state.gi];

  function drawTabs() {
    mount(tabs, graphs.map((g, i) => h("button", {
      class: `chip${i === state.gi ? " chip-on" : ""}`, title: g.title,
      onclick: () => { state.gi = i; state.selected = null; state.overlay = null; drawTabs(); draw(); loadOverlayOptions(); },
    }, g.tools.join(" · "))));
  }

  function draw() {
    const g = graph();
    const pos = layout(g.nodes, g.edges);
    const layers = Math.max(...Object.values(pos).map((p) => p.layer)) + 1;
    const rows = Math.max(...Object.values(pos).map((p) => (p.y - PAD_TOP) / ROW_H)) + 1;
    const width = PAD_X * 2 + (layers - 1) * COL_W + NODE_W;
    const height = PAD_TOP + rows * ROW_H;
    const path = state.overlay ? state.overlay.path : [];
    const ms = state.overlay ? state.overlay.ms : {};
    const svg = s("svg", { class: "gv-svg", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `Граф ${g.id}` },
      s("defs", null, s("marker", { id: "gv-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7,
        orient: "auto-start-reverse" }, s("path", { d: "M0,0 L10,5 L0,10 z", class: "sm-arrowhead" }))));
    const passed = (a, b) => path.some((x, i) => x === a && path[i + 1] === b);
    for (const e of g.edges) {
      const p = pos[e.from];
      const q = pos[e.to];
      const skip = q.layer - p.layer > 1 && p.y === q.y; // обход через слой — дугой сверху
      const d = skip
        ? `M${p.x + NODE_W / 2},${p.y} C${p.x + NODE_W / 2},${p.y - 46} ${q.x + NODE_W / 2},${q.y - 46} ${q.x + NODE_W / 2},${q.y}`
        : `M${p.x + NODE_W},${p.y + NODE_H / 2} C${p.x + NODE_W + 18},${p.y + NODE_H / 2} ${q.x - 18},${q.y + NODE_H / 2} ${q.x},${q.y + NODE_H / 2}`;
      const cls = ["sm-edge", e.kind === "branch" ? "gv-branch" : "", path.length ? (passed(e.from, e.to) ? "on" : "dim") : ""];
      svg.append(s("path", { d, class: cls.filter(Boolean).join(" "), "marker-end": "url(#gv-arrow)" },
        s("title", null, e.label || `${e.from} → ${e.to}`)));
      if (e.label) {
        // подпись: над дугой обхода или под прямым ребром (между соседними узлами для неё нет места)
        const lx = skip ? (p.x + q.x + NODE_W) / 2 : (p.x + NODE_W + q.x) / 2;
        const ly = skip ? p.y - 38 : Math.max(p.y, q.y) + NODE_H + 16;
        svg.append(s("text", { class: "sm-edge-label", x: lx, y: ly, "text-anchor": "middle" }, short(e.label, 44)));
      }
    }
    for (const n of g.nodes) {
      const { x, y } = pos[n.id];
      const inPath = path.includes(n.id);
      const cls = ["gv-node", KIND_CLS[n.kind] || "gv-code", state.selected === n.id ? "sel" : "", path.length && !inPath ? "dim" : ""];
      svg.append(s("g", {
        class: cls.filter(Boolean).join(" "), tabindex: 0, role: "button", "aria-label": `${n.id}: ${n.title}`,
        onclick: () => select(n.id), onkeydown: (ev) => (ev.key === "Enter" || ev.key === " ") && (ev.preventDefault(), select(n.id)),
      },
      s("rect", { x, y, width: NODE_W, height: NODE_H, rx: 10 }),
      s("text", { class: "gv-kind", x: x + NODE_W - 10, y: y + 17, "text-anchor": "end" }, n.kind_title),
      s("text", { class: "sm-name", x: x + 12, y: y + 22 }, n.id),
      wrap2(n.title.split(/[:(]/)[0], 24).map((line, i) => s("text", { class: "sm-sub", x: x + 12, y: y + 39 + i * 13 }, line)),
      inPath && ms[n.id] !== undefined ? s("text", { class: "gv-ms", x: x + NODE_W - 10, y: y + NODE_H - 8, "text-anchor": "end" },
        fmt.ms(ms[n.id])) : null));
    }
    mount(host, svg);
    mount(head, h("span", null, h("b", null, g.title), " — инструменты MCP: ", h("span", { class: "mono" }, g.tools.join(", ")),
      " · код: ", h("span", { class: "mono" }, g.file)));
    passport();
  }

  function select(id) {
    state.selected = state.selected === id ? null : id;
    draw();
  }

  function passport() {
    const g = graph();
    const n = g.nodes.find((x) => x.id === state.selected);
    if (!n) {
      return mount(side, h("div", { class: "sm-legend" },
        h("b", null, "Как читать"),
        h("ul", { class: "small" },
          [["llm", "вызов модели (LLM)"], ["code", "код: детерминированно, без LLM"], ["db", "векторная база (Qdrant)"],
            ["web", "веб-поиск"], ["calc", "расчётный модуль"]].map(([k, t]) => h("li", null, h("span", { class: `sm-dot ${KIND_CLS[k]}` }), t)),
          h("li", null, h("span", { class: "sm-dot gv-branch-dot" }), "пунктир — ветвление по решению кода")),
        h("p", { class: "small muted" }, "Щёлкните узел — что он делает, как и почему, какие параметры каталога на него влияют.")));
    }
    const out = g.edges.filter((e) => e.from === n.id);
    const t = state.overlay && state.overlay.ms[n.id] !== undefined ? state.overlay : null;
    mount(side,
      h("div", { class: "sm-pass-head" }, h("h4", { class: "mono" }, n.id),
        h("button", { class: "btn btn-xs btn-ghost", onclick: () => select(n.id), title: "Закрыть паспорт" }, "×")),
      h("div", { class: "row" }, badge(n.kind_title, n.kind === "llm" ? "b-info" : "b-muted"),
        t ? badge(`в наложенном ходе: ${fmt.ms(t.ms[n.id])}`, "b-ok") : null),
      h("h5", null, "Что делает"), h("p", { class: "small" }, n.title),
      n.how ? [h("h5", null, "Как и почему"), h("p", { class: "small" }, firstParagraph(n.how))] : null,
      n.params.length ? [h("h5", null, "Параметры каталога (config/current)"),
        h("table", { class: "kv small" }, h("tbody", null, n.params.map((p) =>
          h("tr", null, h("th", { class: "mono" }, p.key), h("td", { class: "mono" }, typeof p.value === "object" ? JSON.stringify(p.value) : String(p.value))))))]
        : h("p", { class: "small muted" }, "Параметров каталога нет: поведение задаёт код."),
      out.length ? [h("h5", null, "Дальше"), h("ul", { class: "sm-links" }, out.map((e) => h("li", null,
        h("button", { class: "btn-link mono", onclick: () => select(e.to) }, `→ ${e.to}`), " ",
        h("span", { class: "small" }, e.kind === "branch" ? e.label || "ветвление" : ""))))] : null,
      h("p", { class: "small muted" }, "код: ", h("span", { class: "mono" }, n.code)));
  }

  async function loadOverlayOptions() {
    const g = graph();
    try {
      turns = turns || (await api.turns(60));
    } catch {
      overlaySel.parentElement && overlaySel.parentElement.remove(); // журнал ходов недоступен
      return;
    }
    const rows = turns.filter((r) => (r.tools || []).some((t) => g.tools.includes(t))).slice(0, 25);
    mount(overlaySel, h("option", { value: "" }, rows.length ? "— наложить реальный ход —" : "— ходов с этим инструментом нет —"),
      rows.map((r) => h("option", { value: r.turn_id }, `${fmt.ts(r.started)} · ${short(r.question || "", 60)}`)));
    mount(overlayInfo, "");
  }

  async function applyOverlay(turnId) {
    state.overlay = null;
    mount(overlayInfo, "");
    if (turnId) {
      const g = graph();
      try {
        const t = await api.turn(turnId);
        const call = (t.tool_calls || []).find((c) => g.tools.includes(c.tool) && (c.run || {}).path);
        if (call) {
          const ms = Object.fromEntries((call.run.nodes || []).map((x) => [x.node, x.ms]));
          state.overlay = { path: call.run.path, ms };
          mount(overlayInfo, `${call.tool}: ${call.run.path.join(" → ")} · ${fmt.ms(call.run.ms)}`);
        } else {
          mount(overlayInfo, "в этом ходе у вызова нет трейса графа");
        }
      } catch (e) {
        mount(overlayInfo, `ход не загрузился: ${e.message || e}`);
      }
    }
    draw();
  }

  drawTabs();
  draw();
  loadOverlayOptions();
  return h("div", { class: "sm" }, tabs, head,
    h("div", { class: "sm-toolbar" }, h("label", { class: "sm-toggle" }, "Наложить ход:", overlaySel), overlayInfo),
    h("div", { class: "sm-body" }, host, side));
}

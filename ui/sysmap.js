// Карта системы для вкладки «Архитектура» в двух режимах — по данным docker-compose.yml:
// - «Связи»: кто к кому обращается (x-role, x-why, x-links, x-externals) — для разработчика;
// - «Развёртывание»: шаг запуска (depends_on), сети, тома, порты на хосте, проверки здоровья, ресурсы и имена
//   секретов — для эксплуатации.
// Наведение подсвечивает связи элемента, щелчок открывает его паспорт справа. Переключатели профилей owner и
// gigachat сначала стоят так, как включено в этом развёртывании (COMPOSE_PROFILES); сняв их, можно читать одно ядро.

import { h, s, mount, badge } from "./util.js";

const COL_W = 196;
const ROW_H = 92;
const NODE_W = 160;
const NODE_H = 64;
const DEP_H = 78; // узел в режиме «Развёртывание»: имя, образ, порт / проверка / ресурсы
const PAD_X = 18;
const PAD_TOP = 40;

// режим «Связи»: место на карте [колонка, ряд]; колонки — слева направо по пути запроса пользователя
const PLACE = {
  user: [0, 0], console: [1, 0], bootstrap: [1, 1.45], ouroboros: [2, 0.7], tools: [3, 0], gateway: [3, 1.6],
  qdrant: [4, 0], encoder: [4, 1], internet: [5, 0.5], openrouter: [5, 1.6], gpt2giga: [4, 2.6], gigachat: [5, 2.6],
  owner: [0, 3.2], "ouroboros-ui": [1, 2.85], schedules: [1, 3.95], egress: [2, 2.6], telegram: [2, 3.95],
};
const COLUMNS = ["Люди", "Вход", "Агент", "Инструменты и LLM", "Данные и модели", "Внешние сервисы"];
const GROUP = {
  user: "ext", owner: "ext", internet: "ext", openrouter: "ext", gigachat: "ext", telegram: "ext",
  console: "entry", "ouroboros-ui": "entry", ouroboros: "agent", bootstrap: "agent", schedules: "agent",
  egress: "agent", tools: "tools", gateway: "llm", gpt2giga: "llm", qdrant: "data", encoder: "data",
};
const GROUP_TITLES = {
  entry: "вход: консоль, UI владельца", agent: "агент и его засев", tools: "инструменты (LangGraph)", llm: "LLM",
  data: "данные и модели", ext: "люди и внешние сервисы",
};
// режим «Развёртывание»: полосы по сетям сервиса
const BANDS = [
  ["agent", "Сеть agent — без выхода в интернет"],
  ["both", "Сети agent и oilo"],
  ["oilo", "Сеть oilo — с выходом в интернет"],
];
const EXT_PROFILE = { owner: "owner", telegram: "owner", gigachat: "gigachat" };
const PROFILES = [
  ["owner", "профиль owner", "канал владельца: UI Ouroboros, Telegram, утренняя сводка"],
  ["gigachat", "профиль gigachat", "модели GigaChat через gpt2giga"],
];
const CONDITION = {
  healthy: "ждёт здоровья (healthcheck)", started: "ждёт старта", completed_successfully: "ждёт успешного завершения",
};

const short = (text, n = 30) => {
  const t = String(text || "").split(/[:;(]/)[0].trim();
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
};
// подпись узла в две строки по словам; лишнее — многоточием
export const wrap2 = (text, per = 25) => {
  const words = String(text || "").replace(/\s+/g, " ").trim().split(" ");
  const lines = [""];
  for (const w of words) {
    const cur = lines[lines.length - 1];
    if (!cur || (cur + " " + w).length <= per) lines[lines.length - 1] = cur ? cur + " " + w : w;
    else if (lines.length < 2) lines.push(w);
    else {
      lines[1] = (lines[1] + " " + w).slice(0, per - 1) + "…";
      break;
    }
  }
  return lines.map((l) => (l.length > per ? l.slice(0, per - 1) + "…" : l));
};
// кривая между двумя прямоугольниками; at(t) — точка на ней (для подписи)
function curve(p, q, pw, ph, qw, qh) {
  let pts;
  if (Math.abs(p.x - q.x) > 8) {
    const [x1, x2] = p.x < q.x ? [p.x + pw, q.x] : [p.x, q.x + qw];
    const [y1, y2] = [p.y + ph / 2, q.y + qh / 2];
    const dx = (x2 - x1) * 0.45;
    pts = [[x1, y1], [x1 + dx, y1], [x2 - dx, y2], [x2, y2]];
  } else {
    const [y1, y2] = p.y < q.y ? [p.y + ph, q.y] : [p.y, q.y + qh];
    const [x1, x2] = [p.x + pw / 2, q.x + qw / 2];
    pts = [[x1, y1], [x1, y1], [x2, y2], [x2, y2]];
  }
  const at = (k) => [0, 1].map((i) => (1 - k) ** 3 * pts[0][i] + 3 * (1 - k) ** 2 * k * pts[1][i]
    + 3 * (1 - k) * k ** 2 * pts[2][i] + k ** 3 * pts[3][i]);
  return { d: `M${pts[0]} C${pts[1]} ${pts[2]} ${pts[3]}`, at };
}
const arrowDefs = () => s("defs", null, s("marker", { id: "sm-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7,
  markerHeight: 7, orient: "auto-start-reverse" }, s("path", { d: "M0,0 L10,5 L0,10 z", class: "sm-arrowhead" })));

/** Карта: d — deployment из /api/about; onTools — что делать по кнопке «графы инструментов» в паспорте tools. */
export function systemMap(d, { onTools } = {}) {
  const svc = Object.fromEntries((d.services || []).map((x) => [x.name, x]));
  const ext = Object.fromEntries((d.externals || []).map((x) => [x.id, x]));
  const vols = Object.fromEntries((d.volumes || []).map((v) => [v.name, v]));
  const links = (d.links || []).filter((l) => (svc[l.from] || ext[l.from]) && (svc[l.to] || ext[l.to]));
  const on = new Set(d.profiles_on || []);
  const state = { mode: "links", owner: on.has("owner"), gigachat: on.has("gigachat"), labels: false, hover: null, selected: null };
  const refs = { nodes: {}, edges: [] };

  const profileOf = (id) => (svc[id] ? (svc[id].when.match(/по профилю (\w+)/) || [])[1] : EXT_PROFILE[id]) || "";
  const visible = (id) => !profileOf(id) || state[profileOf(id)];
  const nameOf = (id) => (svc[id] ? id : ext[id] ? (ext[id].title || id).split(":")[0] : id.replace(/^vol:/, ""));
  // запасное место для сервиса, которого нет в PLACE: нижний ряд слева направо
  let spare = 0;
  const place = Object.fromEntries([...Object.keys(svc), ...Object.keys(ext)].map((id) => [id, PLACE[id] || [spare++, 5]]));

  const svgHost = h("div", { class: "sm-canvas" });
  const side = h("aside", { class: "sm-side" });
  const modeBtn = (mode, title) => h("button", {
    class: "chip", dataset: { mode }, title,
    onclick: () => { state.mode = mode; state.selected = null; state.hover = null; syncToolbar(); draw(); },
  }, mode === "links" ? "Связи" : "Развёртывание");
  const modes = h("span", { class: "chips-inline" },
    modeBtn("links", "кто к кому обращается и что передаёт"),
    modeBtn("deploy", "шаг запуска, сети, тома, порты, проверки здоровья, ресурсы, секреты"));
  const labelsToggle = h("label", { class: "sm-toggle" },
    h("input", { type: "checkbox", onchange: (e) => { state.labels = e.target.checked; paint(); } }), "подписи всех связей");
  const toolbar = h("div", { class: "sm-toolbar" }, modes,
    PROFILES.map(([key, title, hint]) => h("label", { class: "sm-toggle", title: hint },
      h("input", { type: "checkbox", checked: state[key], onchange: (e) => { state[key] = e.target.checked; draw(); } }), title)),
    labelsToggle, h("span", { class: "muted small" }, "наведите — связи элемента; щёлкните — паспорт"));

  function syncToolbar() {
    for (const b of modes.children) b.classList.toggle("chip-on", b.dataset.mode === state.mode);
  }

  // ---------------------------------------------------------------- общее для двух режимов
  function node(id, x, y, height, cls, lines, label) {
    const g = s("g", {
      class: cls, tabindex: 0, role: "button", "aria-label": label,
      onmouseenter: () => { state.hover = id; paint(); }, onmouseleave: () => { state.hover = null; paint(); },
      onclick: () => select(id), onkeydown: (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), select(id)),
    }, s("rect", { x, y, width: NODE_W, height, rx: 10 }), lines);
    refs.nodes[id] = g;
    return g;
  }

  function edge(a, b, r, text, cls = "") {
    const path = s("path", { d: r.d, class: `sm-edge ${cls}`.trim(), "marker-end": cls.includes("sm-mount") ? null : "url(#sm-arrow)" },
      s("title", null, `${nameOf(a)} → ${nameOf(b)}${text ? ": " + text : ""}`));
    const label = s("text", { class: "sm-edge-label hidden", "text-anchor": "middle" }, short(text, 36));
    refs.edges.push({ a, b, path, label, at: r.at, base: `sm-edge ${cls}`.trim(), text });
    return [path, label];
  }

  function draw() {
    refs.nodes = {};
    refs.edges = [];
    labelsToggle.style.display = state.mode === "links" ? "" : "none";
    if (state.selected && !state.selected.startsWith("vol:") && !visible(state.selected)) state.selected = null;
    mount(svgHost, state.mode === "links" ? drawLinks() : drawDeploy());
    paint();
    passport();
  }

  function paint() {
    const focus = state.hover || state.selected;
    const near = new Set([focus]);
    for (const e of refs.edges) {
      const on = !!focus && (e.a === focus || e.b === focus);
      if (on) near.add(e.a).add(e.b);
      e.path.setAttribute("class", `${e.base}${on ? " on" : focus ? " dim" : ""}`);
      // подписи — при наведении на элемент (при выборе они в паспорте) или все сразу по переключателю
      const shown = e.text && ((on && state.hover === focus) || (state.mode === "links" && state.labels && !focus));
      e.label.setAttribute("class", `sm-edge-label${shown ? "" : " hidden"}`);
      const [lx, ly] = e.at(!on ? 0.5 : e.a === focus ? 0.72 : 0.28);
      e.label.setAttribute("x", lx);
      e.label.setAttribute("y", ly - 5);
    }
    for (const [id, g] of Object.entries(refs.nodes)) {
      g.classList.toggle("sel", state.selected === id);
      g.classList.toggle("dim", !!focus && !near.has(id));
    }
  }

  function select(id) {
    state.selected = state.selected === id ? null : id;
    paint();
    passport();
  }

  // ---------------------------------------------------------------- режим «Связи»
  function drawLinks() {
    const ids = [...Object.keys(svc), ...Object.keys(ext)].filter(visible);
    const xy = (id) => ({ x: PAD_X + place[id][0] * COL_W, y: PAD_TOP + place[id][1] * ROW_H });
    const rows = Math.max(...ids.map((id) => place[id][1])) + 1;
    const width = PAD_X * 2 + (COLUMNS.length - 1) * COL_W + NODE_W;
    const svg = s("svg", { class: "sm-svg", viewBox: `0 0 ${width} ${PAD_TOP + rows * ROW_H}`, role: "img",
      "aria-label": "Карта связей сервисов" }, arrowDefs(),
    COLUMNS.map((title, i) => s("text", { class: "sm-col", x: PAD_X + i * COL_W + NODE_W / 2, y: 18, "text-anchor": "middle" }, title)));
    const edges = links.filter((l) => visible(l.from) && visible(l.to))
      .map((l) => edge(l.from, l.to, curve(xy(l.from), xy(l.to), NODE_W, NODE_H, NODE_W, NODE_H), l.what));
    svg.append(...edges.map((e) => e[0]));
    for (const id of ids) {
      const { x, y } = xy(id);
      const item = svc[id];
      const sub = short(item ? item.role : (ext[id].title || "").split(":").slice(1).join(":"), 60);
      const cls = ["sm-node", `sm-${GROUP[id] || (item ? "other" : "ext")}`,
        profileOf(id) || (item && item.when === "разово") ? "sm-dash" : ""].filter(Boolean).join(" ");
      svg.append(node(id, x, y, NODE_H, cls, [s("text", { class: "sm-name", x: x + 12, y: y + 22 }, nameOf(id)),
        wrap2(sub).map((line, i) => s("text", { class: "sm-sub", x: x + 12, y: y + 39 + i * 13 }, line))], `${nameOf(id)}: ${sub}`));
    }
    svg.append(...edges.map((e) => e[1]));
    return svg;
  }

  // ---------------------------------------------------------------- режим «Развёртывание»
  // шаг запуска — слой по depends_on среди видимых сервисов (сервисы профилей — тоже по своим зависимостям)
  function steps(ids) {
    const step = Object.fromEntries(ids.map((id) => [id, 0]));
    for (let i = 0; i < ids.length; i++) {
      for (const id of ids) {
        for (const dep of svc[id].depends || []) if (dep.name in step) step[id] = Math.max(step[id], step[dep.name] + 1);
      }
    }
    return step;
  }

  const bandOf = (item) => {
    const n = new Set(item.networks || []);
    return n.has("agent") && n.has("oilo") ? "both" : n.has("agent") ? "agent" : "oilo";
  };

  function drawDeploy() {
    const ids = Object.keys(svc).filter(visible);
    const step = steps(ids);
    const nSteps = Math.max(...Object.values(step)) + 1;
    // ряды полосы: сколько сервисов одного шага в одной сети
    const cell = {};
    const pos = {};
    for (const id of ids.sort((a, b) => step[a] - step[b] || a.localeCompare(b))) {
      const key = `${bandOf(svc[id])}:${step[id]}`;
      pos[id] = { band: bandOf(svc[id]), col: step[id], row: (cell[key] = (cell[key] ?? -1) + 1) };
    }
    const bandRows = Object.fromEntries(BANDS.map(([b]) => [b, Math.max(0, ...ids.filter((id) => pos[id].band === b).map((id) => pos[id].row + 1))]));
    const rowH = DEP_H + 22;
    let y = PAD_TOP;
    const bandTop = {};
    const bandRects = [];
    for (const [b, title] of BANDS) {
      if (!bandRows[b]) continue;
      bandTop[b] = y + 22;
      const hgt = 22 + bandRows[b] * rowH;
      bandRects.push(s("rect", { class: `sm-band sm-band-${b}`, x: 4, y, width: PAD_X * 2 + (nSteps - 1) * COL_W + NODE_W - 8, height: hgt - 6, rx: 10 }),
        s("text", { class: "sm-band-title", x: 14, y: y + 15 }, title));
      y += hgt;
    }
    const usedVols = Object.keys(vols).filter((v) => ids.some((id) => (svc[id].volumes || []).includes(v)));
    const volTop = y + 22;
    const volW = 132;
    const volGap = Math.max(volW + 12, ((nSteps - 1) * COL_W + NODE_W) / Math.max(usedVols.length, 1));
    const width = Math.max(PAD_X * 2 + (nSteps - 1) * COL_W + NODE_W, PAD_X * 2 + usedVols.length * volGap);
    const height = volTop + 60;
    const xy = (id) => (id.startsWith("vol:")
      ? { x: PAD_X + usedVols.indexOf(id.slice(4)) * volGap, y: volTop }
      : { x: PAD_X + pos[id].col * COL_W, y: bandTop[pos[id].band] + pos[id].row * rowH });
    const svg = s("svg", { class: "sm-svg", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Развёртывание: шаги запуска, сети, тома" },
      arrowDefs(), bandRects,
      Array.from({ length: nSteps }, (_, i) => s("text", { class: "sm-col", x: PAD_X + i * COL_W + NODE_W / 2, y: 18, "text-anchor": "middle" },
        i === 0 ? "шаг 1 · без зависимостей" : `шаг ${i + 1}`)),
      usedVols.length ? [s("rect", { class: "sm-band sm-band-vol", x: 4, y: volTop - 22, width: width - 8, height: 76, rx: 10 }),
        s("text", { class: "sm-band-title", x: 14, y: volTop - 7 }, "Тома — данные между перезапусками")] : null);
    const edges = [];
    for (const id of ids) {
      for (const dep of svc[id].depends || []) {
        if (!pos[dep.name]) continue;
        const p = xy(dep.name);
        edges.push(edge(dep.name, id, curve(p, xy(id), NODE_W, DEP_H, NODE_W, DEP_H), CONDITION[dep.condition] || dep.condition, "sm-dep"));
      }
      for (const v of svc[id].volumes || []) {
        if (!usedVols.includes(v)) continue;
        edges.push(edge(id, `vol:${v}`, curve(xy(id), xy(`vol:${v}`), NODE_W, DEP_H, volW, 34), "", "sm-mount"));
      }
    }
    svg.append(...edges.map((e) => e[0]));
    for (const id of ids) {
      const { x, y: ny } = xy(id);
      const it = svc[id];
      const facts = [it.ports.length ? `порт ${it.ports.map((p) => p.split(":").slice(-2)[0]).join(", ")}` : "",
        it.health ? "health" : "", [it.mem, it.cpus && `${it.cpus} CPU`].filter(Boolean).join(" · "),
        it.secrets.length ? `секретов ${it.secrets.length}` : ""].filter(Boolean).join(" · ");
      const cls = ["sm-node", `sm-${GROUP[id] || "other"}`, profileOf(id) || it.when === "разово" ? "sm-dash" : ""].filter(Boolean).join(" ");
      svg.append(node(id, x, ny, DEP_H, cls, [
        s("text", { class: "sm-name", x: x + 12, y: ny + 21 }, id),
        s("text", { class: "sm-sub mono-sm", x: x + 12, y: ny + 38 }, short(it.image + (it.own_build ? " · свой" : ""), 30)),
        wrap2(facts, 27).map((line, i) => s("text", { class: "sm-facts", x: x + 12, y: ny + 54 + i * 13 }, line)),
      ], `${id}: ${facts}`));
    }
    for (const v of usedVols) {
      const { x, y: vy } = xy(`vol:${v}`);
      const g = s("g", {
        class: "sm-node sm-vol", tabindex: 0, role: "button", "aria-label": `том ${v}`,
        onmouseenter: () => { state.hover = `vol:${v}`; paint(); }, onmouseleave: () => { state.hover = null; paint(); },
        onclick: () => select(`vol:${v}`), onkeydown: (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), select(`vol:${v}`)),
      }, s("rect", { x, y: vy, width: volW, height: 34, rx: 14 }), s("text", { class: "sm-sub", x: x + 10, y: vy + 21 }, short(v, 20)));
      refs.nodes[`vol:${v}`] = g;
      svg.append(g);
    }
    svg.append(...edges.map((e) => e[1]));
    return svg;
  }

  // ---------------------------------------------------------------- паспорт
  const goto = (id, text) => h("button", { class: "btn-link mono", onclick: () => select(id) }, text);
  const linkRow = (l, out) => h("li", null, goto(out ? l.to : l.from, `${out ? "→" : "←"} ${nameOf(out ? l.to : l.from)}`),
    " ", h("span", { class: "small" }, l.what));
  const kv = (rows) => h("table", { class: "kv small" }, h("tbody", null, rows.filter(([, v]) => v && (!Array.isArray(v) || v.length))
    .map(([k, v]) => h("tr", null, h("th", null, k), h("td", { class: "mono" }, Array.isArray(v) ? v.join(", ") : v)))));

  function legend() {
    if (state.mode === "deploy") {
      return h("div", { class: "sm-legend" }, h("b", null, "Как читать"),
        h("ul", { class: "small" },
          h("li", null, "колонки — шаг запуска: сервис стартует, когда готовы те, кого он ждёт (depends_on)"),
          h("li", null, "полосы — сети: agent без выхода в интернет, oilo — с выходом"),
          h("li", null, "внизу — тома: данные, которые переживают перезапуск и пересборку"),
          h("li", null, h("span", { class: "sm-dot sm-dash" }), "пунктир — профиль или разовый сервис")),
        h("p", { class: "small muted" }, "В узле: образ, порт на хосте, health — есть проверка здоровья, лимиты памяти и CPU, ",
          "число секретов. Щёлкните — команда, пользователь, проверка, имена секретов. Эксплуатация — docs/guide/operations.md."));
    }
    return h("div", { class: "sm-legend" }, h("b", null, "Как читать"),
      h("ul", { class: "small" },
        Object.entries(GROUP_TITLES).map(([g, t]) => h("li", null, h("span", { class: `sm-dot sm-${g}` }), t)),
        h("li", null, h("span", { class: "sm-dot sm-dash" }), "пунктир — профиль или разовый сервис")),
      h("p", { class: "small muted" }, "Стрелка — кто к кому обращается и что передаёт. Описание сервисов и связей — ",
        "поля x-role, x-why и x-links в docker-compose.yml."));
  }

  function passport() {
    const id = state.selected;
    if (!id) return mount(side, legend());
    const head = h("div", { class: "sm-pass-head" }, h("h4", null, nameOf(id)),
      h("button", { class: "btn btn-xs btn-ghost", onclick: () => select(id), title: "Закрыть паспорт" }, "×"));
    if (id.startsWith("vol:")) {
      const v = id.slice(4);
      const users = Object.keys(svc).filter((n) => (svc[n].volumes || []).includes(v));
      return mount(side, head, badge("том", "b-muted"), h("h5", null, "Что хранит"), h("p", { class: "small" }, vols[v]?.why || "—"),
        h("h5", null, "Подключают"), h("ul", { class: "sm-links" }, users.map((n) => h("li", null, goto(n, n)))),
        h("p", { class: "small muted" }, "Резервная копия и восстановление — docs/guide/operations.md."));
    }
    const item = svc[id];
    if (!item) return mount(side, head, h("p", { class: "small" }, ext[id].title));
    const badges = h("div", { class: "row" }, badge(item.when, item.when === "постоянно" ? "b-ok" : "b-muted"),
      item.healthcheck ? badge("healthcheck", "b-muted") : null, item.own_build ? badge("свой образ", "b-info") : null);
    if (state.mode === "deploy") {
      const waits = (item.depends || []).map((x) => h("li", null, goto(x.name, `← ${x.name}`), " ",
        h("span", { class: "small" }, CONDITION[x.condition] || x.condition)));
      const waiters = Object.keys(svc).filter((n) => (svc[n].depends || []).some((x) => x.name === id))
        .map((n) => h("li", null, goto(n, `→ ${n}`)));
      return mount(side, head, badges, h("p", { class: "small" }, item.role),
        kv([["образ", item.image + (item.own_build ? " (собирается из репозитория)" : "")], ["команда", item.command],
          ["пользователь", item.user], ["перезапуск", item.restart], ["сети", item.networks], ["порт на хосте", item.ports],
          ["тома", item.volumes], ["память", item.mem], ["CPU", item.cpus]]),
        waits.length ? [h("h5", null, "Ждёт перед стартом"), h("ul", { class: "sm-links" }, waits)] : null,
        waiters.length ? [h("h5", null, "Его ждут"), h("ul", { class: "sm-links" }, waiters)] : null,
        item.health ? [h("h5", null, "Проверка здоровья"), h("p", { class: "small mono" }, item.health.test),
          h("p", { class: "small muted" }, `каждые ${item.health.interval || "—"}, на старт ${item.health.start_period || "—"}`)] : null,
        h("h5", null, "Секреты (только имена)"),
        item.secrets.length ? h("p", { class: "small mono" }, item.secrets.join(", "))
          : h("p", { class: "small muted" }, item.env_file ? "в окружении нет: чужие секреты .env обнулены" : "в окружении нет"));
    }
    const out = links.filter((l) => l.from === id && visible(l.to));
    const inc = links.filter((l) => l.to === id && visible(l.from));
    return mount(side, head, badges,
      h("h5", null, "Что делает"), h("p", { class: "small" }, item.role), h("h5", null, "Зачем отдельно"), h("p", { class: "small" }, item.why),
      id === "tools" && onTools ? h("button", { class: "btn btn-sm", onclick: onTools }, "Графы инструментов внутри ↓") : null,
      out.length ? [h("h5", null, "Обращается к"), h("ul", { class: "sm-links" }, out.map((l) => linkRow(l, true)))] : null,
      inc.length ? [h("h5", null, "К нему обращаются"), h("ul", { class: "sm-links" }, inc.map((l) => linkRow(l, false)))] : null,
      kv([["образ", item.image + (item.own_build ? " (свой)" : "")], ["сети", item.networks], ["порт наружу", item.ports],
        ["тома", item.volumes], ["лимиты", item.limits]]));
  }

  syncToolbar();
  draw();
  return h("div", { class: "sm" }, toolbar, h("div", { class: "sm-body" }, svgHost, side));
}

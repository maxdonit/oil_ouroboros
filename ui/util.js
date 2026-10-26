// Общие помощники консоли: построение DOM без innerHTML, форматирование, тосты, состояния загрузки и ошибок,
// плитки показателей, таблицы и JSON-дерево.
// Весь текст из API выводится через textContent — так в консоль нельзя «внедрить» разметку.

const SVG_NS = "http://www.w3.org/2000/svg";

function applyAttrs(el, attrs, isSvg) {
  if (!attrs) return;
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.setAttribute("class", Array.isArray(v) ? v.filter(Boolean).join(" ") : v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "value" && !isSvg) el.value = v;
    else if (k === "checked" && !isSvg) el.checked = !!v;
    else if (k === "selected" && !isSvg) el.selected = !!v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
}

function appendKids(el, kids) {
  for (const c of kids) {
    if (c === null || c === undefined || c === false || c === "") continue;
    if (Array.isArray(c)) appendKids(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  }
}

/** HTML-элемент: h("div", {class: "x", onclick: fn}, "текст", child, [дети]) */
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs && (attrs instanceof Node || Array.isArray(attrs) || typeof attrs !== "object")) {
    kids.unshift(attrs);
    attrs = null;
  }
  applyAttrs(el, attrs, false);
  appendKids(el, kids);
  return el;
}

/** SVG-элемент */
export function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  applyAttrs(el, attrs, true);
  appendKids(el, kids);
  return el;
}

export function clear(el) {
  while (el && el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function mount(el, ...kids) {
  clear(el);
  appendKids(el, kids);
  return el;
}

// ---------------------------------------------------------------------------------------------------------
// форматирование
// ---------------------------------------------------------------------------------------------------------

const nf0 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

export const fmt = {
  int: (v) => (v === null || v === undefined || isNaN(v) ? "—" : nf0.format(v)),
  num: (v, d = 2) =>
    v === null || v === undefined || isNaN(v)
      ? "—"
      : new Intl.NumberFormat("ru-RU", { maximumFractionDigits: d, minimumFractionDigits: 0 }).format(v),
  fixed: (v, d = 2) => (v === null || v === undefined || isNaN(v) ? "—" : Number(v).toFixed(d)),
  pct: (v, d = 1) => (v === null || v === undefined || isNaN(v) ? "—" : `${nf2.format(+(v * 100).toFixed(d))}%`),
  usd: (v) => {
    if (v === null || v === undefined || isNaN(v)) return "—";
    if (v === 0) return "$0";
    if (Math.abs(v) < 0.01) return "$" + Number(v).toPrecision(2);
    return "$" + Number(v).toFixed(v < 1 ? 4 : 2);
  },
  ms: (v) => {
    if (v === null || v === undefined || isNaN(v)) return "—";
    if (v < 1000) return `${Math.round(v)} мс`;
    if (v < 60000) return `${(v / 1000).toFixed(v < 10000 ? 2 : 1)} с`;
    return `${Math.floor(v / 60000)} мин ${Math.round((v % 60000) / 1000)} с`;
  },
  sec: (v) => (v === null || v === undefined || isNaN(v) ? "—" : `${Number(v).toFixed(2)} с`),
  ts: (t) => {
    if (!t) return "—";
    const d = new Date(typeof t === "number" ? t * 1000 : t);
    if (isNaN(d)) return String(t);
    return d.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  },
  date: (t) => {
    if (!t) return "—";
    const d = new Date(typeof t === "number" ? t * 1000 : t);
    return isNaN(d) ? String(t) : d.toLocaleDateString("ru-RU");
  },
  short: (str, n = 120) => {
    const t = String(str ?? "");
    return t.length > n ? t.slice(0, n - 1) + "…" : t;
  },
};

export function debounce(fn, ms) {
  let t = 0;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// Режим консоли: простой (чат и прогноз) или администратора (все вкладки). Паролей нет — режим выбирает интерфейс
// кнопкой в шапке, он хранится в localStorage (oil.mode); сервер о нём не знает.
export const SIMPLE_TABS = ["chat", "forecast"];
export const mode = { admin: false };

/** Видна ли вкладка в текущем режиме (ссылки на скрытые вкладки не показываем). */
export const canOpen = (tab) => mode.admin || SIMPLE_TABS.includes(tab);

export const store = {
  get(key, dflt = null) {
    try {
      const v = window.localStorage.getItem("oil." + key);
      return v === null ? dflt : JSON.parse(v);
    } catch {
      return dflt;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem("oil." + key, JSON.stringify(value));
    } catch {
      /* приватный режим / запрет хранилища — не критично */
    }
  },
};

export function isObj(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// ---------------------------------------------------------------------------------------------------------
// уведомления, состояния загрузки и ошибок
// ---------------------------------------------------------------------------------------------------------

export function toast(message, kind = "info", ms = 5000) {
  let host = document.getElementById("toasts");
  if (!host) {
    host = h("div", { id: "toasts", class: "toasts", role: "status", "aria-live": "polite" });
    document.body.appendChild(host);
  }
  const el = h("div", { class: `toast toast-${kind}` }, h("span", { class: "toast-msg" }, String(message)));
  const close = h("button", { class: "toast-x", "aria-label": "Закрыть", onclick: () => el.remove() }, "×");
  el.appendChild(close);
  host.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

export function loading(text = "Загрузка…") {
  return h("div", { class: "state state-loading" }, h("span", { class: "spinner" }), text);
}

export function empty(text, hint) {
  return h("div", { class: "state state-empty" }, h("div", { class: "state-title" }, text), hint ? h("div", { class: "state-hint" }, hint) : null);
}

export function errorBox(err, hint) {
  const msg = err && err.message ? err.message : String(err);
  return h(
    "div",
    { class: "state state-error", role: "alert" },
    h("div", { class: "state-title" }, "Ошибка"),
    h("div", { class: "state-msg" }, msg),
    hint ? h("div", { class: "state-hint" }, hint) : null
  );
}

export function badge(text, cls = "", title) {
  return h("span", { class: `badge ${cls}`, title: title || null }, text);
}

/** Плитка показателя: подпись, значение, пояснение; cls — цвет значения (ok / warn / bad). */
export function kpi(k, v, sub, cls = "") {
  return h("div", { class: `kpi ${cls}` },
    h("div", { class: "kpi-k" }, k), h("div", { class: "kpi-v" }, v), sub ? h("div", { class: "kpi-s" }, sub) : null);
}

/** Таблица «ключ → значение» для произвольного объекта (неизвестные поля API показываются как есть). */
export function kvTable(obj, opts = {}) {
  if (!isObj(obj) || !Object.keys(obj).length) return empty(opts.emptyText || "нет данных");
  const rows = Object.entries(obj).map(([k, v]) =>
    h("tr", null, h("th", { class: "kv-k" }, opts.labels && opts.labels[k] ? opts.labels[k] : k), h("td", { class: "kv-v" }, valueNode(v, opts)))
  );
  return h("table", { class: "kv" }, h("tbody", null, rows));
}

export function valueNode(v, opts = {}) {
  if (v === null || v === undefined) return h("span", { class: "muted" }, "—");
  if (typeof v === "boolean") return h("span", { class: v ? "val-true" : "val-false" }, v ? "да" : "нет");
  if (typeof v === "number") return h("span", { class: "mono" }, fmt.num(v, 4));
  if (typeof v === "string") {
    if (/^https?:\/\//.test(v)) return h("a", { href: v, target: "_blank", rel: "noopener noreferrer" }, v);
    return h("span", null, v);
  }
  if (Array.isArray(v) && v.every((x) => x === null || ["string", "number", "boolean"].includes(typeof x))) {
    if (!v.length) return h("span", { class: "muted" }, "[]");
    return h("span", { class: "chips-inline" }, v.map((x) => h("span", { class: "chip chip-plain" }, String(x))));
  }
  if (isObj(v) && opts.nested !== false && Object.keys(v).length <= 12 && Object.values(v).every((x) => !isObj(x) && !Array.isArray(x)))
    return kvTable(v, opts);
  return jsonTree(v, { depth: 1 });
}

// ---------------------------------------------------------------------------------------------------------
// JSON-дерево (трейсы, результаты задач, параметры)
// ---------------------------------------------------------------------------------------------------------

function preview(v) {
  if (Array.isArray(v)) return `[${v.length}]`;
  if (isObj(v)) return `{${Object.keys(v).length}}`;
  return "";
}

function leaf(v) {
  if (v === null) return h("span", { class: "j-null" }, "null");
  if (typeof v === "boolean") return h("span", { class: "j-bool" }, String(v));
  if (typeof v === "number") return h("span", { class: "j-num" }, String(v));
  const str = String(v);
  if (str.length > 280) {
    const span = h("span", { class: "j-str" }, JSON.stringify(str.slice(0, 280) + "…"));
    const more = h(
      "button",
      {
        class: "btn btn-xs btn-link",
        onclick: (e) => {
          e.stopPropagation();
          span.textContent = JSON.stringify(str);
          more.remove();
        },
      },
      `ещё ${str.length - 280} симв.`
    );
    return h("span", null, span, " ", more);
  }
  return h("span", { class: "j-str" }, JSON.stringify(str));
}

/** opts.depth — сколько уровней раскрыто сразу. */
export function jsonTree(value, opts = {}) {
  const maxDepth = opts.depth ?? 1;
  function node(key, v, path, depth) {
    const keyEl = key !== null ? h("span", { class: "j-key" }, String(key)) : null;
    if (Array.isArray(v) || isObj(v)) {
      const entries = Array.isArray(v) ? v.map((x, i) => [i, x]) : Object.entries(v);
      const det = h("details", { class: "j-node" });
      if (depth < maxDepth) det.open = true;
      const summary = h("summary", null, keyEl, keyEl ? ": " : null, h("span", { class: "j-prev" }, preview(v)));
      det.appendChild(summary);
      let rendered = false;
      const renderKids = () => {
        if (rendered) return;
        rendered = true;
        const box = h("div", { class: "j-kids" });
        const limit = 200;
        entries.slice(0, limit).forEach(([k, x]) => box.appendChild(node(k, x, path === "" ? String(k) : `${path}.${k}`, depth + 1)));
        if (entries.length > limit) box.appendChild(h("div", { class: "muted" }, `… ещё ${entries.length - limit}`));
        det.appendChild(box);
      };
      if (det.open) renderKids();
      det.addEventListener("toggle", () => det.open && renderKids());
      return det;
    }
    return h("div", { class: "j-leaf" }, keyEl, keyEl ? ": " : null, leaf(v));
  }
  const root = h("div", { class: "jtree" });
  if (Array.isArray(value) || isObj(value)) {
    const entries = Array.isArray(value) ? value.map((x, i) => [i, x]) : Object.entries(value);
    if (!entries.length) root.appendChild(h("span", { class: "muted" }, Array.isArray(value) ? "[]" : "{}"));
    for (const [k, x] of entries) root.appendChild(node(k, x, String(k), 1));
  } else root.appendChild(leaf(value));
  return root;
}

/** Плашка с разворачиваемым содержимым, рендерится лениво. */
export function collapsible(title, render, open = false, cls = "") {
  const det = h("details", { class: `coll ${cls}` }, h("summary", null, title));
  let done = false;
  const go = () => {
    if (done || !det.open) return;
    done = true;
    try {
      appendKids(det, [h("div", { class: "coll-body" }, render())]);
    } catch (e) {
      console.error(e);
      det.appendChild(errorBox(e));
    }
  };
  det.addEventListener("toggle", go);
  if (open) {
    det.open = true;
    go();
  }
  return det;
}


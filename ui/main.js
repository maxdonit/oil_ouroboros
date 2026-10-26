// Точка входа консоли: вкладки (hash-маршруты), индикатор здоровья системы, тема оформления.
// Каждая вкладка — отдельный ES-модуль: загружается при первом открытии и дальше живёт в фоне (ход агента не
// прерывается при переключении вкладок).

import { h, mount, clear, store, toast, errorBox, loading, mode, canOpen } from "./util.js";
import { api } from "./api.js";

const ALL_VIEWS = [
  { id: "chat", title: "Чат", mod: "./chat.js", hint: "Вопрос аналитику (агент Ouroboros) с живым ходом: инструменты, проверка, источники" },
  { id: "demo", title: "Демо", mod: "./demo.js", hint: "Записанные ходы агента по шагам — 7 сценариев без LLM, без денег и без агента" },
  { id: "turns", title: "Ходы", mod: "./turns.js",
    hint: "Трейс каждого хода: раунды Ouroboros, вызовы инструментов, графы LangGraph, вызовы LLM, деньги" },
  { id: "monitor", title: "Мониторинг", mod: "./monitor.js",
    hint: "Метрики по версиям агента: проверка ответов, источники, ошибки, деньги, время, бюджет" },
  { id: "quality", title: "Качество", mod: "./evals.js", hint: "Прогоны eval через агента: проверки по кейсам, сравнение версий (McNemar)" },
  { id: "params", title: "Параметры", mod: "./params.js",
    hint: "Что определяет поведение: регламент и память агента, настройки, каталог инструментов, версии" },
  { id: "forecast", title: "Прогноз", mod: "./forecast.js", hint: "Расчётный модуль напрямую: прогноз с интервалами, скилл против RW, сценарии" },
  { id: "sources", title: "Источники", mod: "./sources.js",
    hint: "Что подключено, насколько свежо и достоверно: отчёты агентств и наблюдатель, веб по уровням доверия, цены EIA, котировки Мосбиржи; " +
      "как добавить источник" },
  { id: "about", title: "Архитектура", mod: "./about.js",
    hint: "Схема, ход ответа, развёртывание (контейнеры, сети, тома), графы инструментов, документация" },
];

const loaded = {};
const sections = {};
let navLinks = {};

function parseHash() {
  const raw = (location.hash || "").replace(/^#\/?/, "");
  const [pathPart, queryPart] = raw.split("?");
  const parts = pathPart.split("/").filter((x) => x !== "");
  const view = ALL_VIEWS.find((v) => v.id === parts[0]) ? parts[0] : "chat";
  const rest = parts.slice(1).map((p) => {
    try {
      return decodeURIComponent(p);
    } catch {
      return p;
    }
  });
  return { view, rest, query: new URLSearchParams(queryPart || "") };
}

async function route() {
  const r = parseHash();
  if (!canOpen(r.view)) setMode(true); // ссылка на вкладку администратора включает его режим: преград всё равно нет
  const def = ALL_VIEWS.find((v) => v.id === r.view);
  for (const [id, a] of Object.entries(navLinks)) {
    a.classList.toggle("active", id === r.view);
    if (id === r.view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  for (const [id, sec] of Object.entries(sections)) sec.hidden = id !== r.view;
  let sec = sections[r.view];
  if (!sec) {
    sec = h("section", { class: `view view-${r.view}`, id: `view-${r.view}`, "aria-label": def.title });
    sections[r.view] = sec;
    document.getElementById("views").appendChild(sec);
    sec.appendChild(loading(`Загрузка вкладки «${def.title}»…`));
  }
  document.title = `${def.title} — Нефтегазовый аналитик на Ouroboros`;
  try {
    if (!loaded[r.view]) {
      loaded[r.view] = (async () => {
        const mod = await import(def.mod);
        clear(sec);
        await mod.init(sec);
        return mod;
      })();
    }
    const mod = await loaded[r.view];
    if (parseHash().view !== r.view) return;
    if (mod.show) await mod.show({ rest: r.rest, query: r.query });
  } catch (e) {
    console.warn("вкладка", r.view, e);
    delete loaded[r.view];
    mount(sec, errorBox(e, "Вкладку не удалось открыть. Обновите страницу; если ошибка повторится — проверьте журнал сервера."));
  }
}

function buildNav() {
  const nav = document.getElementById("nav");
  navLinks = {};
  const views = ALL_VIEWS.filter((v) => canOpen(v.id));
  mount(nav, views.map((v) => (navLinks[v.id] = h("a", { href: `#/${v.id}`, class: "nav-link", title: v.hint }, v.title))));
  const btn = document.getElementById("mode");
  btn.textContent = mode.admin ? "Простой режим" : "Режим администратора";
  btn.title = mode.admin
    ? "Оставить только чат и прогноз"
    : "Показать все вкладки: демо, ходы, мониторинг, качество, параметры, источники, архитектура (пароль не нужен)";
}

/** Простой режим (чат и прогноз) или режим администратора (все вкладки); выбор запоминается в браузере. */
function setMode(admin) {
  mode.admin = admin;
  store.set("mode", admin ? "admin" : "simple");
  buildNav();
}

function initMode() {
  mode.admin = store.get("mode", "simple") === "admin";
  document.getElementById("mode").addEventListener("click", () => {
    setMode(!mode.admin);
    if (!canOpen(parseHash().view)) location.hash = "#/chat"; // со скрытой вкладки — в чат
    else route();
  });
}

const THEMES = ["auto", "light", "dark"];
const THEME_TITLES = { auto: "авто", light: "светлая", dark: "тёмная" };

function applyTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  const btn = document.getElementById("theme");
  if (btn) {
    btn.textContent = `Тема: ${THEME_TITLES[t] || "авто"}`;
    btn.title = "Переключить тему: авто (как в системе) → светлая → тёмная";
  }
}

function initTheme() {
  let t = store.get("theme", "auto");
  if (!THEMES.includes(t)) t = "auto";
  applyTheme(t);
  document.getElementById("theme").addEventListener("click", () => {
    t = THEMES[(THEMES.indexOf(t) + 1) % THEMES.length];
    store.set("theme", t);
    applyTheme(t);
  });
}

function dot(ok) {
  return h("span", { class: `dot ${ok === true ? "dot-ok" : ok === false ? "dot-bad" : "dot-warn"}` });
}

async function refreshHealth() {
  const box = document.getElementById("health");
  try {
    const hl = await api.health();
    const t = hl.tools || {};
    const g = hl.gateway || {};
    const o = hl.ouroboros || {};
    const idx = typeof t.index_chunks === "number" ? `${t.index_chunks} фрагм.` : "—";
    const spent = typeof g.spent_usd === "number" ? `$${g.spent_usd.toFixed(3)} / $${g.budget_usd}` : "—";
    mount(box,
      h("span", { class: "health-item", title: o.error ? `Ouroboros недоступен: ${o.error}` : "Агент Ouroboros отвечает" }, dot(!o.error), "агент"),
      h("span", { class: "health-item", title: `Сервис инструментов: ${t.status || t.error || "—"}; каталог ${(t.catalog || {}).ref || "—"}` },
        dot(t.status === "ok" ? true : t.error ? false : null), "инструменты ", h("b", null, idx)),
      h("span", { class: "health-item", title: "Веб-поиск (провайдер; при исчерпании лимита — резерв)" },
        "веб ", h("b", null, t.web_provider || "—")),
      h("span", { class: "health-item", title: "Деньги LLM: потрачено / жёсткий лимит шлюза" },
        dot(g.status === "ok" ? true : g.error ? false : null), "LLM ", h("b", null, spent)));
    box.title = `Система: ${hl.status}`;
  } catch (e) {
    mount(box, h("span", { class: "dot dot-bad" }), h("span", { class: "health-item text-bad" }, "консоль недоступна"));
    box.title = e.message;
  }
}

function initGlobalErrors() {
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    if (r && r.name === "AbortError") {
      e.preventDefault();
      return;
    }
    toast("Ошибка: " + (r && r.message ? r.message : String(r)), "bad");
  });
}

function start() {
  initMode();
  buildNav();
  initTheme();
  initGlobalErrors();
  refreshHealth();
  setInterval(refreshHealth, 30000);
  window.addEventListener("hashchange", route);
  route();
}

start();

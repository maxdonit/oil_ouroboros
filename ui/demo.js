// «Демо»: записанные ходы агента (data/demo/turns.json — настоящие прогоны `oilo demo`) проигрываются по шагам:
// раунды Ouroboros, вызовы инструментов и узлы их графов LangGraph, проверка цифр, затем ответ с метками источников.
// Без LLM, без денег и без агента: работает и на чистом деплое без ключа LLM.

import { h, mount, clear, empty, errorBox, loading, fmt, badge } from "./util.js";
import { api } from "./api.js";
import { TOOL_TITLES, answerMarkdown, checksChips, costLine, timeline, verificationBox, evidenceList } from "./turnview.js";

const PACE = { slow: 1400, normal: 700, fast: 220 }; // мс на шаг; узлы графа — четверть шага
const st = { list: [], recorded: "", i: null, run: 0, pace: "normal" };
const els = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function init(root) {
  els.list = h("div", { class: "stack" });
  els.stage = h("div", { class: "demo-stage" });
  els.side = h("div", { class: "stack" });
  els.head = h("p");
  els.pace = h("select", { class: "input", "aria-label": "Темп", onchange: (e) => { st.pace = e.target.value; } },
    h("option", { value: "slow" }, "медленно"), h("option", { value: "normal", selected: true }, "обычно"), h("option", { value: "fast" }, "быстро"));
  els.instant = h("button", { class: "btn btn-sm", type: "button", onclick: () => st.i !== null && play(st.i, true) }, "Показать сразу");
  mount(root,
    h("div", { class: "page-head" }, h("h2", null, "Демо"), els.head),
    h("div", { class: "split" },
      h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h3", null, "Сценарии")), els.list),
      h("div", { class: "stack" },
        h("section", { class: "panel" },
          h("div", { class: "panel-head" }, h("h3", null, "Ход ответа"), h("span", { class: "spacer" }), els.pace, els.instant),
          els.stage),
        h("section", { class: "panel" }, els.side))));
  mount(els.list, loading());
  try {
    const r = await api.demoList();
    st.list = r.turns || [];
    st.recorded = r.recorded || "";
  } catch (e) {
    return mount(els.list, errorBox(e));
  }
  const v = (st.list[0] || {}).agent_version;
  els.head.textContent = st.list.length
    ? `Настоящие прогоны агента (oilo demo, ${st.recorded}${v ? `, версия агента ${v}` : ""}), ` +
      `проигрываются по записи — без LLM, без денег и без агента.`
    : "";
  renderList();
  idle();
}

export function show({ rest } = {}) {
  const i = Number(rest && rest[0]);
  if (Number.isInteger(i) && i >= 1 && i <= st.list.length && st.i !== i - 1) play(i - 1);
}

function renderList() {
  if (!st.list.length)
    return mount(els.list, empty("Записей демо нет",
      "Запишите их: docker compose exec console oilo demo, затем файл var/demo_turns.json → data/demo/turns.json."));
  mount(els.list, st.list.map((d) => h("button", {
    class: `demo-item ${st.i === d.i ? "active" : ""}`, type: "button", onclick: () => { location.hash = `#/demo/${d.i + 1}`; }
  },
    h("b", null, `${d.i + 1}. ${d.title || "сценарий"}`),
    h("div", { class: "small" }, d.question),
    h("div", { class: "muted small" }, `${d.tools.join(" → ") || "без инструментов"} · ${fmt.ms(d.ms)} · ${fmt.usd(d.cost_usd)}`))));
}

function idle() {
  mount(els.stage, empty("Выберите сценарий слева",
    "Ход агента проиграется по шагам: раунды Ouroboros, инструменты и узлы их графов, проверка цифр, ответ."));
  mount(els.side, h("h3", null, "Подробный ход"),
    empty("Появится после ответа", "Хронология с токенами и деньгами, проверка цифр, процитированные источники."));
}

function summaryText(s) {
  if (s === null || s === undefined) return "";
  if (typeof s !== "object") return String(s);
  return Object.entries(s).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`).join("; ");
}

function steps(t) {
  const rows = [];
  for (const r of t.llm_rounds || []) rows.push({ ts: r.ts, kind: "llm", r });
  for (const c of t.tool_calls || []) rows.push({ ts: c.ts, kind: "tool", c });
  return rows.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
}

function answerBubble(t) {
  return h("div", { class: "bubble bubble-a" }, answerMarkdown(t.answer || "(нет ответа)"),
    h("div", { class: "answer-meta" }, checksChips(t.checks), costLine(t)));
}

async function play(i, instant = false) {
  const run = ++st.run; // новый запуск останавливает прежний
  const alive = () => run === st.run;
  st.i = i;
  renderList();
  idle();
  mount(els.stage, loading());
  let t;
  let prev = null;
  try {
    t = await api.demoTurn(i);
    const p = st.list[i - 1];
    if (p && t.conversation_id && p.conversation_id === t.conversation_id) prev = await api.demoTurn(i - 1); // уточнение «А WTI?»
  } catch (e) {
    return mount(els.stage, errorBox(e));
  }
  if (!alive()) return;
  clear(els.stage);
  if (prev) {
    els.stage.appendChild(h("p", { class: "muted small" }, "Тот же разговор: предыдущая реплика"));
    els.stage.appendChild(h("div", { class: "msg-q" }, h("div", { class: "bubble bubble-q" }, prev.question)));
    els.stage.appendChild(h("div", { class: "msg-a demo-prev" }, answerBubble(prev)));
  }
  els.stage.appendChild(h("div", { class: "msg-q" }, h("div", { class: "bubble bubble-q" }, t.question)));
  const progress = h("span", null, "Вопрос ушёл агенту: задача Ouroboros с правилами хода…");
  const live = h("div", { class: "bubble bubble-a pending" }, h("span", { class: "spinner" }), progress);
  const list = h("ol", { class: "demo-steps" });
  const wrap = h("div", { class: "msg-a" }, list, live);
  els.stage.appendChild(wrap);
  const rows = steps(t);
  const t0 = Date.parse((rows[0] || {}).ts) || 0;
  const done = [];
  const pause = async (k = 1) => { if (!instant) await sleep(PACE[st.pace] * k); return alive(); };
  for (const row of rows) {
    if (!(await pause())) return;
    const sec = t0 ? `+${((Date.parse(row.ts) - t0) / 1000).toFixed(1)} с` : "";
    if (row.kind === "llm") {
      const r = row.r;
      list.appendChild(h("li", { class: "demo-llm" }, h("span", { class: "mono small muted" }, sec), " ", badge(`раунд ${r.round ?? "?"}`, "b-muted"),
        h("span", { class: "small" },
          ` агент рассуждает · на входе ${fmt.int(r.tokens_in)} ток., из кэша ${fmt.int(r.tokens_cached)} · ${fmt.usd(r.cost_usd)}`)));
      progress.textContent = `${sec} · агент рассуждает…`;
      continue;
    }
    const c = row.c;
    const run_ = c.run || {};
    const li = h("li", { class: "demo-tool" }, h("span", { class: "mono small muted" }, sec), " ",
      badge(c.mcp ? "MCP" : "встроенный", c.mcp ? "b-ok" : "b-warn"),
      h("b", null, ` ${c.tool}`), h("span", { class: "muted small" }, ` — ${TOOL_TITLES[c.tool] || c.tool} · ${fmt.ms(c.ms)}`));
    list.appendChild(li);
    if (c.tool !== "finish_task") done.push(c.tool);
    progress.textContent = `${sec} · ${done.join(" → ") || "завершение хода"}`;
    if ((run_.nodes || []).length) {
      const ol = h("ol", { class: "demo-nodes small" });
      li.appendChild(ol);
      for (const n of run_.nodes) {
        if (!(await pause(0.25))) return;
        ol.appendChild(h("li", null, h("span", { class: "mono" }, n.node),
          n.summary ? h("span", { class: "muted" }, ` — ${fmt.short(summaryText(n.summary), 150)}`) : null));
      }
    }
    if (run_.web_reasons)
      li.appendChild(h("div", { class: "small" },
        run_.web_reasons.length ? `решение о вебе: нужен (${run_.web_reasons.join(", ")})` : "решение о вебе: отчётов достаточно"));
    if (c.tool === "verify_answer" && run_.verification) {
      const v = run_.verification;
      li.appendChild(h("div", { class: `small ${v.ok ? "" : "text-bad"}` },
        `проверка: ${v.ok ? "пройдена" : "не пройдена"} — цифр ${v.numbers_verified ?? 0}/${v.numbers_total ?? 0}`));
    }
  }
  if (!(await pause())) return;
  wrap.replaceChild(answerBubble(t), live);
  mount(els.side, h("h3", null, "Подробный ход"), checksChips(t.checks), costLine(t),
    h("h4", { class: "sec-h" }, "Шаги"), timeline(t, { traceLink: false }),
    t.verification && Object.keys(t.verification).length ? verificationBox(t.verification) : null,
    h("h4", { class: "sec-h" }, `Источники ответа (${Object.keys(t.evidence || {}).length})`), evidenceList(t));
}

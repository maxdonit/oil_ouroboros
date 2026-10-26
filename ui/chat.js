// «Чат»: вопрос старшему аналитику. Агент — Ouroboros; каждый вопрос — его задача, которую сервер ведёт в фоне,
// а консоль показывает живой ход: какие инструменты агент вызвал, сколько это стоило, прошла ли проверка цифр.
// Разговор многоходовой: последние реплики сервер передаёт агенту в тексте задачи; «Новый разговор» сбрасывает их.

import { h, mount, clear, fmt, toast, empty, errorBox, store, canOpen } from "./util.js";
import { api } from "./api.js";
import { answerMarkdown, checksChips, costLine, timeline, evidenceList, verificationBox, TOOL_TITLES } from "./turnview.js";

const EXAMPLES = [
  "Как IEA оценивает изменение мирового спроса на нефть в 2026 году?",
  "Что EIA прогнозирует по Brent до конца 2026 года и какие свежие события могут изменить прогноз?",
  "Спрогнозируй цену Brent на 3 месяца",
  "Оцени диапазон цены Brent при сокращении добычи ОПЕК+ на 2 млн б/с",
  "Какие последние новости о решениях ОПЕК+ на этой неделе?",
  "Посоветуй рецепт борща",
];

const st = { conv: store.get("conv", ""), turns: [], running: null, selected: null };
let els = {};

export async function init(root) {
  els.transcript = h("div", { class: "transcript" });
  els.conv = h("span", { class: "thread-info muted small" });
  els.input = h("textarea", {
    class: "input chat-q", rows: 2, "aria-label": "Вопрос",
    placeholder: "Вопрос старшему аналитику нефтегазового рынка… (Enter — отправить, Shift+Enter — новая строка)",
    onkeydown: (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        send();
      }
    },
  });
  els.send = h("button", { class: "btn btn-primary", type: "submit" }, "Спросить");
  els.side = h("div", { class: "why-host" });
  mount(root,
    h("div", { class: "chat-main" },
      h("section", { class: "panel chat-col" },
        h("div", { class: "panel-head" }, h("h3", null, "Разговор"), els.conv,
          h("button", { class: "btn btn-sm", onclick: newConversation, title: "Начать новый разговор: агент не увидит прошлых реплик" },
            "Новый разговор")),
        els.transcript,
        h("form", { class: "chat-form", onsubmit: (e) => { e.preventDefault(); send(); } }, els.input, h("div", { class: "chat-btns" }, els.send))),
      h("section", { class: "panel why-col" }, els.side)));
  if (st.conv) {
    try {
      const turns = (await api.conversation(st.conv)).filter(Boolean);
      st.turns = turns.map((t) => ({
        question: t.question, status: ["completed", "completed_by_console"].includes(t.status) ? "done" : t.status, turn: t
      }));
      st.selected = st.turns[st.turns.length - 1] || null;
    } catch {
      st.turns = []; // разговор не прочитался (сбой сети или консоли) — начинаем новый
      st.conv = "";
      store.set("conv", "");
    }
  }
  renderTranscript();
  renderSide();
}

export function show() {
  setTimeout(() => els.input && els.input.focus(), 0);
}

function newConversation() {
  if (st.running) return toast("Дождитесь ответа на текущий вопрос", "warn");
  st.conv = "";
  store.set("conv", "");
  st.turns = [];
  st.selected = null;
  renderTranscript();
  renderSide();
  els.input.focus();
}

function renderTranscript() {
  els.conv.textContent = st.conv ? `разговор ${st.conv} · реплик: ${st.turns.length}` : "новый разговор";
  clear(els.transcript);
  if (!st.turns.length) {
    els.transcript.appendChild(h("div", { class: "chat-empty" },
      h("div", { class: "chat-empty-title" }, "Старший аналитик нефтегазового рынка"),
      h("p", { class: "muted" }, "Агент — Ouroboros. Его инструменты — субагенты LangGraph: поиск по отчётам OPEC / IEA / EIA " +
        "(приоритет), веб с фильтром жёлтой прессы, расчётный модуль прогноза, котировки Мосбиржи (Brent, WTI, газ), " +
        "проверка каждой цифры по источнику. " +
        "Ход агента виден справа."),
      h("div", { class: "examples" }, EXAMPLES.map((q) =>
        h("button", { class: "chip chip-example", type: "button", onclick: () => { els.input.value = q; send(); } }, q)))));
    return;
  }
  for (const t of st.turns) els.transcript.appendChild(turnEl(t));
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function turnEl(t) {
  const a = h("div", { class: `msg-a ${st.selected === t ? "selected" : ""}` });
  t.el = a;
  fillAnswer(t);
  return h("div", { class: "turn" }, h("div", { class: "msg-q" }, h("div", { class: "bubble bubble-q" }, t.question)), a);
}

function fillAnswer(t) {
  const a = t.el;
  if (!a) return;
  clear(a);
  a.classList.toggle("selected", st.selected === t);
  if (t.status === "running") {
    a.appendChild(h("div", { class: "bubble bubble-a pending" },
      h("span", { class: "spinner" }), h("span", null, t.progress || "Агент принял вопрос…")));
    return;
  }
  const turn = t.turn || {};
  if (!turn.answer) {
    a.appendChild(h("div", { class: "bubble bubble-a" }, errorBox(turn.error || `ход завершился без ответа (статус: ${turn.status || t.status})`,
      canOpen("turns") ? "Подробности — во вкладке «Ходы»." : "")));
    return;
  }
  a.appendChild(h("div", { class: "bubble bubble-a" },
    answerMarkdown(turn.answer),
    h("div", { class: "answer-meta" }, checksChips(turn.checks), costLine(turn)),
    h("div", { class: "answer-actions" },
      h("button", { class: "btn btn-xs", type: "button", onclick: () => select(t) }, "Ход ответа"),
      canOpen("turns") ? h("a", { class: "btn btn-xs btn-ghost", href: `#/turns/${encodeURIComponent(turn.turn_id)}` }, "Полный трейс") : null)));
}

function select(t) {
  st.selected = t;
  for (const x of st.turns) if (x.el) x.el.classList.toggle("selected", x === t);
  renderSide();
}

function renderSide() {
  const t = st.selected;
  clear(els.side);
  if (!t) {
    els.side.appendChild(h("div", { class: "why-empty" }, h("h3", null, "Ход ответа"),
      empty("Здесь появится ход агента", "Раунды Ouroboros и вызовы инструментов по порядку; для каждого инструмента — " +
        "граф LangGraph, решения, деньги. Затем — проверка цифр и источники.")));
    return;
  }
  if (t.status === "running") {
    els.side.appendChild(h("div", null, h("h3", null, "Ход ответа"), h("p", { class: "muted small" }, t.progress || "…"),
      t.liveCalls && t.liveCalls.length ? h("ol", { class: "tl" }, t.liveCalls.map((c) => h("li", { class: "tl-tool" },
        h("b", null, c.tool), h("span", { class: "muted small" }, ` — ${TOOL_TITLES[c.tool] || ""} · ${fmt.ms(c.ms)}`),
        c.ok ? null : h("span", { class: "text-bad" }, " ошибка")))) : null));
    return;
  }
  const turn = t.turn || {};
  els.side.appendChild(h("div", { class: "stack" },
    h("h3", null, "Ход ответа"),
    checksChips(turn.checks), costLine(turn),
    h("h4", { class: "sec-h" }, "Шаги"), timeline(turn, { traceLink: canOpen("turns") }),
    turn.verification && Object.keys(turn.verification).length ? verificationBox(turn.verification) : null,
    h("h4", { class: "sec-h" }, `Источники ответа (${Object.keys(turn.evidence || {}).length})`), evidenceList(turn)));
}

async function send() {
  const question = els.input.value.trim();
  if (!question) return els.input.focus();
  if (st.running) return toast("Агент ещё отвечает на предыдущий вопрос", "warn");
  els.input.value = "";
  const t = { question, status: "running", progress: "Вопрос отправлен агенту…" };
  st.turns.push(t);
  st.selected = t;
  renderTranscript();
  renderSide();
  els.send.disabled = true;
  st.running = t;
  try {
    const r = await api.ask(question, st.conv || undefined);
    st.conv = r.conversation_id;
    store.set("conv", st.conv);
    const started = performance.now();
    for (;;) {
      await new Promise((res) => setTimeout(res, 1500));
      let p;
      try {
        p = await api.turnProgress(r.turn_id);
      } catch (e) {
        if (performance.now() - started > 600000) throw e;
        continue;
      }
      if (p.done) {
        t.turn = p.turn;
        t.status = ["completed", "completed_by_console"].includes(p.turn.status) ? "done" : "error";
        break;
      }
      const sec = ((performance.now() - started) / 1000).toFixed(0);
      const calls = p.tool_calls || [];
      t.liveCalls = calls;
      t.progress = calls.length ? `${sec} с · ${calls.map((c) => c.tool).join(" → ")}`
        : `${sec} с · ${p.stage === "accepted" ? "агент думает…" : "в очереди…"}`;
      const span = t.el && t.el.querySelector(".bubble.pending span:last-child");
      if (span) span.textContent = t.progress;
      if (st.selected === t) renderSide();
    }
  } catch (e) {
    t.status = "error";
    t.turn = { error: e.message };
    toast(e.message, "bad");
  } finally {
    st.running = null;
    els.send.disabled = false;
    renderTranscript();
    renderSide();
  }
}

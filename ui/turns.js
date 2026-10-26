// «Ходы»: журнал ходов агента (чат и eval) и трейс каждого хода:
// - раунды Ouroboros (модель, токены, кэш, деньги) и вызовы инструментов вперемешку;
// - для каждого инструмента — узлы графа LangGraph, вызовы LLM субагентов, решения, проверка;
// - вызовы LLM из журнала шлюза с телами запросов (что именно агент отправил модели);
// - полный прогон инструмента: промпты и ответы субагентов.

import { h, mount, clear, fmt, empty, errorBox, loading, badge, collapsible, jsonTree } from "./util.js";
import { api } from "./api.js";
import { answerMarkdown, checksChips, costLine, timeline, evidenceList, verificationBox, decisionsTable } from "./turnview.js";

let els = {};
let list = [];
let source = "";

export async function init(root) {
  els.list = h("div", { class: "eval-list" });
  els.detail = h("div");
  els.src = h("select", { class: "input", onchange: (e) => { source = e.target.value; loadList(); } },
    h("option", { value: "" }, "все"), h("option", { value: "chat" }, "чат"), h("option", { value: "demo" }, "демо"),
    h("option", { value: "eval" }, "eval"), h("option", { value: "smoke" }, "smoke"), h("option", { value: "cli" }, "oilo ask"));
  mount(root,
    h("div", { class: "page-head" }, h("h2", null, "Ходы агента"),
      h("p", null, "Каждый ход — задача Ouroboros. Её шаги собраны из журналов агента, трейсов инструментов и журнала LLM-шлюза: " +
        "что агент решил вызвать, что сделал каждый граф LangGraph и сколько это стоило.")),
    h("div", { class: "split" },
      h("section", { class: "panel" },
        h("div", { class: "panel-head" }, h("h3", null, "Ходы"), els.src, h("span", { class: "spacer" }),
          h("button", { class: "btn btn-sm", onclick: loadList }, "↻")),
        els.list),
      h("section", { class: "panel" }, els.detail)));
  mount(els.detail, empty("Выберите ход слева"));
  await loadList();
}

export async function show(params) {
  const [a, b] = params.rest;
  if (a === "tool" && b) return openToolRun(b);
  if (a) return openTurn(a);
}

async function loadList() {
  mount(els.list, loading());
  try {
    list = await api.turns(200, source);
  } catch (e) {
    return mount(els.list, errorBox(e));
  }
  clear(els.list);
  if (!list.length) return els.list.appendChild(empty("Ходов пока нет", "Задайте вопрос во вкладке «Чат» или запустите eval."));
  for (const r of list) {
    const ok = r.checks && r.checks.answered && (r.checks.refused || r.checks.verify_ok);  // показанный текст прошёл финальную проверку
    els.list.appendChild(h("a", { class: "run-item", href: `#/turns/${encodeURIComponent(r.turn_id)}`, "data-id": r.turn_id },
      h("div", { class: "run-q" }, fmt.short(r.question, 140)),
      h("div", { class: "run-meta" },
        badge(r.source, r.source === "eval" ? "b-info" : "b-muted"),
        badge(r.status === "completed_by_console" ? "завершён консолью" : r.status,
          ["completed", "completed_by_console"].includes(r.status) ? (ok ? "b-ok" : "b-warn") : "b-bad"),
        h("span", { class: "mono small" }, (r.tools || []).filter((t) => t !== "finish_task").join(" → ") || "без инструментов"),
        h("span", { class: "muted small" }, `${fmt.ms(r.ms)} · ${fmt.usd(r.cost_usd)} · ${fmt.ts(r.started)}`),
        r.agent_version ? h("span", { class: "muted mono small", title: "версия агента (отпечаток)" }, r.agent_version) : null)));
  }
}

async function openTurn(id) {
  mount(els.detail, loading("Загрузка хода…"));
  for (const el of els.list.querySelectorAll(".run-item")) el.classList.toggle("active", el.dataset.id === id);
  let t;
  try {
    t = await api.turn(id);
  } catch (e) {
    return mount(els.detail, errorBox(e));
  }
  const fp = t.fingerprint || {};
  const llmHost = h("div");
  mount(els.detail,
    h("div", { class: "panel-head" }, h("h3", null, fmt.short(t.question, 120)), badge(t.status, ["completed", "completed_by_console"].includes(t.status) ? "b-ok" : "b-bad")),
    h("p", { class: "muted small" },
      `ход ${t.turn_id} · задача Ouroboros ${t.task_id || "—"} · разговор ${t.conversation_id || "—"} · версия агента `,
      h("code", null, fp.agent_version || "—"),
      fp.modified_by_agent && fp.modified_by_agent.length ? badge(`изменено агентом: ${fp.modified_by_agent.join(", ")}`, "b-warn") : null),
    checksChips(t.checks), costLine(t),
    t.error ? errorBox(t.error) : null,
    collapsible("Ответ пользователю", () => answerMarkdown(t.answer), true),
    h("h4", { class: "sec-h" }, "Шаги хода"), timeline(t),
    t.verification && Object.keys(t.verification).length ? verificationBox(t.verification) : null,
    collapsible(`Источники ответа (${Object.keys(t.evidence || {}).length})`, () => evidenceList(t)),
    collapsible("Вызовы LLM агента (журнал шлюза, тела запросов)", () => {
      loadLlm(llmHost, t);
      return llmHost;
    }),
    collapsible("Результат задачи Ouroboros", () => jsonTree(t.task || {}, { depth: 2 })),
    collapsible("Отпечаток версии агента", () => jsonTree(fp, { depth: 2 })));
}

async function loadLlm(host, t) {
  mount(host, loading());
  const end = (t.started || 0) + (t.ms || 0) / 1000;
  try {
    const started = Date.parse((t.llm_rounds || [])[0]?.ts || "") / 1000 || (Date.now() / 1000 - 3600);
    const r = await api.llmCalls({ since: started - 5, caller: "ouroboros", limit: 200 });
    const rows = (r.calls || []).filter((c) => !end || c.ts <= end + 60).reverse();
    if (!rows.length) return mount(host, empty("В журнале шлюза нет вызовов за время хода"));
    mount(host, h("div", { class: "tbl-wrap" }, h("table", { class: "tbl" },
      h("thead", null, h("tr", null, ["время", "модель", "вход (кэш)", "выход", "$", "мс", "тело"].map((x) => h("th", null, x)))),
      h("tbody", null, rows.map((c) => {
        const bodyHost = h("div");
        return [h("tr", null, h("td", { class: "small" }, fmt.ts(c.ts)), h("td", { class: "mono small" }, c.model),
          h("td", { class: "mono" }, `${fmt.int(c.tokens_in)} (${fmt.int(c.tokens_cached)})`), h("td", { class: "mono" }, fmt.int(c.tokens_out)),
          h("td", { class: "mono" }, fmt.usd(c.cost)), h("td", { class: "mono" }, fmt.int(c.ms)),
          h("td", null, h("button", { class: "btn btn-xs", onclick: async () => {
            mount(bodyHost, loading());
            try {
              const b = await api.llmCall(c.id);
              mount(bodyHost, llmBody(b));
            } catch (e) {
              mount(bodyHost, errorBox(e));
            }
          } }, "показать"))), h("tr", null, h("td", { colspan: 7 }, bodyHost))];
      }).flat()))));
  } catch (e) {
    mount(host, errorBox(e, "Журнал шлюза недоступен (нужен токен console)."));
  }
}

function llmBody(b) {
  const msgs = ((b.request || {}).messages || []);
  const sizes = msgs.map((m) => (typeof m.content === "string" ? m.content.length : JSON.stringify(m.content || "").length));
  return h("div", { class: "stack" },
    h("p", { class: "small muted" }, `сообщений: ${msgs.length}; символов: ${fmt.int(sizes.reduce((a, x) => a + x, 0))}; ` +
      `инструментов в запросе: ${((b.request || {}).tools || []).length}`),
    h("ol", { class: "small" }, msgs.map((m, i) => h("li", null, collapsible(`${m.role} · ${fmt.int(sizes[i])} симв.`,
      () => h("pre", { class: "pre small" }, typeof m.content === "string" ? m.content : JSON.stringify(m.content, null, 1)))))),
    collapsible("Ответ модели", () => jsonTree(b.response || {}, { depth: 3 })));
}

async function openToolRun(trace) {
  mount(els.detail, loading("Загрузка прогона инструмента…"));
  let r;
  try {
    r = await api.toolRun(trace);
  } catch (e) {
    return mount(els.detail, errorBox(e));
  }
  const ev = r.events || [];
  const st = r.state || {};
  mount(els.detail,
    h("div", { class: "panel-head" }, h("h3", null, `Прогон инструмента ${r.source || ""}`),
      badge(r.status || "—", r.status === "error" ? "b-bad" : "b-ok")),
    h("p", { class: "muted small" }, `trace ${r.run_id} · ${fmt.ms(r.ms)} · ${fmt.usd((r.usage || {}).cost_usd)} · ${fmt.ts(r.started)}`),
    r.error ? errorBox(r.error) : null,
    h("h4", { class: "sec-h" }, "События графа"),
    h("ol", { class: "tl" }, ev.filter((e) => ["node_start", "node_end", "node_error", "llm_call", "run_error"].includes(e.type)).map((e) =>
      e.type === "llm_call"
        ? h("li", { class: "tl-llm" }, collapsible(h("span", null, badge("LLM", "b-info"),
          ` ${e.node} · ${e.purpose} · ${e.model} · ${fmt.ms(e.ms)} · ${fmt.int(e.tokens_in)}→${fmt.int(e.tokens_out)} · ${fmt.usd(e.cost_usd)}`),
            () => h("div", null, (e.request || []).map((m) => collapsible(`${m.role}`, () => h("pre", { class: "pre small" }, m.content))),
              h("h5", { class: "sec-h" }, "Ответ"), h("pre", { class: "pre small" }, (e.response || {}).content || ""))))
        : h("li", { class: e.type === "node_error" ? "tl-bad" : "" },
            badge(e.type === "node_start" ? "старт" : e.type === "node_end" ? "конец" : "ошибка",
              e.type === "node_end" ? "b-ok" : e.type === "node_start" ? "b-muted" : "b-bad"),
            ` ${e.node || ""} `, e.title ? h("span", { class: "muted small" }, e.title) : null,
            e.ms ? h("span", { class: "small" }, ` · ${fmt.ms(e.ms)}`) : null,
            e.summary ? h("code", { class: "md-code small" }, ` ${fmt.short(JSON.stringify(e.summary), 240)}`) : null,
            e.error ? h("span", { class: "text-bad small" }, ` ${e.error}`) : null))),
    st.verification ? verificationBox(st.verification) : null,
    st.decisions && st.decisions.length ? collapsible(`Журнал решений (${st.decisions.length})`, () => decisionsTable(st.decisions), true) : null,
    st.result ? collapsible("Что получил агент (текст результата)", () => h("pre", { class: "pre small" }, st.result)) : null,
    collapsible("Полное состояние графа (JSON)", () => jsonTree(st, { depth: 1 })));
}

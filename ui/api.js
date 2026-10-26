// Доступ к API консоли: JSON-запросы с понятными ошибками. Ход агента идёт в фоне на сервере (задача Ouroboros),
// консоль опрашивает его прогресс — без постоянного соединения.

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function parseError(res) {
  let detail = "";
  let body = null;
  try {
    const text = await res.text();
    try {
      body = JSON.parse(text);
      detail = typeof body.detail === "string" ? body.detail : body.error || JSON.stringify(body.detail ?? body);
      // ошибка, переданная через прокси, бывает JSON внутри строки: {"detail": "{\"detail\": \"…\"}"} — разворачиваем
      for (let i = 0; i < 2 && /^\s*\{/.test(detail); i++) {
        try {
          const inner = JSON.parse(detail);
          if (inner && (typeof inner.detail === "string" || typeof inner.error === "string")) detail = inner.detail || inner.error;
          else break;
        } catch {
          break;
        }
      }
    } catch {
      detail = text.slice(0, 300);
    }
  } catch {
    /* тело недоступно */
  }
  const prefix = res.status === 404 ? "не найдено" : `HTTP ${res.status}`;
  return new ApiError(detail ? `${prefix}: ${detail}` : prefix, res.status, body);
}

export async function request(method, path, body, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: opts.signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(`сервер недоступен (${e.message})`, 0);
  }
  if (!res.ok) throw await parseError(res);
  if (opts.text) return res.text();
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

export const get = (path, opts) => request("GET", path, undefined, opts);
export const post = (path, body = {}, opts) => request("POST", path, body, opts);

export function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) if (v !== undefined && v !== null && v !== "") u.set(k, v);
  const t = u.toString();
  return t ? "?" + t : "";
}

export const api = {
  health: () => get("/api/health"),
  ask: (question, conversation_id) => post("/api/chat", { question, conversation_id }),
  turnProgress: (id) => get(`/api/chat/${encodeURIComponent(id)}`),
  conversation: (id) => get(`/api/conversations/${encodeURIComponent(id)}`),
  turns: (limit = 100, source) => get("/api/turns" + qs({ limit, source })),
  turn: (id) => get(`/api/turns/${encodeURIComponent(id)}`),
  toolRun: (trace) => get(`/api/tool_runs/${encodeURIComponent(trace)}`),
  llmCalls: (p = {}) => get("/api/llm" + qs(p)),
  llmCall: (id) => get(`/api/llm/${encodeURIComponent(id)}`),
  metrics: (p = {}) => get("/api/metrics" + qs(p)),
  params: () => get("/api/params"),
  memoryFile: (file) => get("/api/params/memory" + qs({ file })),
  forecast: (p, signal) => get("/api/forecast" + qs(p), { signal }),
  history: (p, signal) => get("/api/forecast/history" + qs(p), { signal }),
  shock: (p, signal) => get("/api/forecast/scenario" + qs(p), { signal }),
  evals: () => get("/api/evals"),
  evalStatus: () => get("/api/evals/status"),
  evalStart: (body) => post("/api/evals/run", body),
  evalRun: (id) => get(`/api/evals/${encodeURIComponent(id)}`),
  evalCompare: (a, b) => get("/api/evals/compare" + qs({ base: a, cand: b })),
  sources: () => get("/api/sources"),
  sourceDomain: (url) => get("/api/sources/domain" + qs({ url })),
  about: () => get("/api/about"),
  demoList: () => get("/api/demo"),
  demoTurn: (i) => get(`/api/demo/${encodeURIComponent(i)}`),
  doc: (path) => get("/docs/" + path.split("/").map(encodeURIComponent).join("/"), { text: true }),
};

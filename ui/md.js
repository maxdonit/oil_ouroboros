// Безопасный рендер Markdown → DOM (без innerHTML): заголовки, абзацы, списки (вложенные), цитаты, код,
// таблицы, ссылки, жирный/курсив/зачёркнутый. Любой HTML в исходнике выводится как текст.
// Опция chips: метки источников из ответа агента ([Отчёт …], [Источник: …, web, …], [Расчёт: …], в ответе на
// английском — [Report …], [Source: …], [Calculation: …]; [R1]) превращаются в цветные «фишки», по клику — обратный
// вызов onChip({label, kind, id}).

const LIST_RE = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const FENCE_RE = /^(\s{0,3})(`{3,}|~{3,})\s*([^\s`]*)?.*$/;
const HEADING_RE = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const HR_RE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/;
const QUOTE_RE = /^\s{0,3}>\s?/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-{1,}:?\s*(?:\|\s*:?-{1,}:?\s*)*\|?\s*$/;
const ID_LIST_RE = /^(?:[RWF]\d+)(?:\s*[,;]\s*[RWF]\d+)*$/;
// начало метки по виду фишки — как метки ставит сервер (graphs/render.py), по-русски и в ответе на английском;
// «Расчётный модуль …» — прежний вид метки расчёта в записанных ходах
const LABEL_KINDS = [
  ["report", /^(?:Отчёт|Report) /],
  ["web", /^(?:Источник|Source):/],
  ["tool", /^(?:Расчёт:|Calculation:|Расчётный модуль|Котировка:|Quote:)/],
  ["bad", /^[RWF]\d+: (?:источник не найден|source not found)/],
];
const labelKind = (t) => (LABEL_KINDS.find(([, re]) => re.test(t)) || [])[0];

function el(tag, attrs, kids) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  if (kids) for (const k of kids) e.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
  return e;
}

const indentOf = (l) => (/^\s*/.exec(l)[0] || "").replace(/\t/g, "    ").length;
const isBlank = (l) => !l || !l.trim();

export function safeHref(href) {
  // браузер выбрасывает управляющие символы и пробелы из схемы («java\tscript:» — это javascript:), поэтому схема
  // определяется так же, как её прочтёт браузер; разрешены только http(s), mailto, относительные ссылки и якоря
  const t = String(href || "").trim();
  if (!t) return null;
  const probe = t.replace(/[\u0000-\u0020\u007f-\u009f]+/g, "");
  if (/^(https?:|mailto:)/i.test(probe)) return t;
  if (/^[a-z][a-z0-9+.-]*:/i.test(probe) || /^\/\//.test(probe)) return null; // javascript:, data:, //чужой-хост
  return t;
}

export function slugify(text, used) {
  let base =
    String(text)
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, "-")
      .replace(/^-+|-+$/g, "") || "section";
  let id = base;
  let n = 2;
  while (used && used.has(id)) id = `${base}-${n++}`;
  if (used) used.add(id);
  return id;
}

export function chipKind(label) {
  if (/^R\d+$/.test(label)) return "report";
  if (/^W\d+$/.test(label)) return "web";
  if (/^F\d+$/.test(label)) return "tool";
  return labelKind(label) || "bad";
}

/** Текст в скобках → массив меток или null, если это не метки. Метки в группе разделены «; », но «; » бывает и
 * внутри метки («Расчёт: сценарий -2 млн б/с, Brent; запасы — оценка …») — такой хвост остаётся в своей метке. */
export function splitLabels(text) {
  const out = [];
  for (const p of text.split(/;\s+/)) {
    if (labelKind(p)) out.push(p);
    else if (out.length) out[out.length - 1] += "; " + p;
    else return null;
  }
  return out.length ? out : null;
}

const KIND_TAG = { report: "отчёт", web: "web", tool: "расчёт", bad: "нет" };

export function chip(label, opts = {}) {
  const kind = opts.kind || chipKind(label);
  let tier = "";
  const m = /(?:уровень|tier) (\d)/.exec(label);
  if (kind === "web" && m) tier = ` tier-${m[1]}`;
  const c = el("span", { class: `src-chip src-${kind}${tier}`, title: opts.title || label });
  c.appendChild(el("span", { class: "src-tag" }, [KIND_TAG[kind] || kind]));
  c.appendChild(el("span", { class: "src-text" }, [label]));
  if (opts.onChip) {
    c.setAttribute("role", "button");
    c.setAttribute("tabindex", "0");
    const fire = (e) => {
      e.preventDefault();
      opts.onChip({ label, kind, id: /^[RWF]\d+$/.test(label) ? label : null });
    };
    c.addEventListener("click", fire);
    c.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && fire(e));
  }
  return c;
}

function findClosing(text, open, close, from) {
  let depth = 0;
  for (let i = from; i < text.length; i++) {
    const c = text[i];
    if (c === "\\") {
      i++;
      continue;
    }
    if (c === open) depth++;
    else if (c === close) {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

/** Строка → массив DOM-узлов (инлайн-разметка). */
export function inline(text, opts = {}) {
  const out = [];
  let buf = "";
  const flush = () => {
    if (!buf) return;
    const parts = buf.split("\n");
    parts.forEach((p, i) => {
      if (i > 0) out.push(opts.breaks ? el("br") : document.createTextNode(" "));
      if (p) out.push(document.createTextNode(p));
    });
    buf = "";
  };
  const n = text.length;
  let i = 0;
  while (i < n) {
    const c = text[i];
    const prev = i > 0 ? text[i - 1] : "";
    if (c === "\\" && i + 1 < n) {
      if (text[i + 1] === "\n") {
        flush();
        out.push(el("br"));
        i += 2;
        continue;
      }
      if (/[\\`*_{}\[\]()#+\-.!|~<>]/.test(text[i + 1])) {
        buf += text[i + 1];
        i += 2;
        continue;
      }
    }
    if (c === " " && text.startsWith("  \n", i)) {
      flush();
      out.push(el("br"));
      i += 3;
      continue;
    }
    if (c === "`") {
      let k = 0;
      while (text[i + k] === "`") k++;
      const fence = "`".repeat(k);
      const end = text.indexOf(fence, i + k);
      if (end !== -1) {
        flush();
        out.push(el("code", { class: "md-code" }, [text.slice(i + k, end).replace(/^ (.*) $/, "$1")]));
        i = end + k;
        continue;
      }
      buf += fence;
      i += k;
      continue;
    }
    if ((c === "*" || c === "_") && text[i + 1] === c && text[i + 2] && text[i + 2] !== " ") {
      const mark = c + c;
      const end = text.indexOf(mark, i + 2);
      if (end > i + 2 && text[end - 1] !== " " && (c === "*" || !/[\p{L}\p{N}]/u.test(text[end + 2] || ""))) {
        flush();
        out.push(el("strong", null, inline(text.slice(i + 2, end), opts)));
        i = end + 2;
        continue;
      }
    }
    if ((c === "*" || c === "_") && text[i + 1] && text[i + 1] !== " " && text[i + 1] !== c) {
      // «_» внутри слов и «*» между цифрами (2*3*4) — не разметка
      const leftOk = c === "*" ? !(/\d/.test(prev) && /\d/.test(text[i + 1])) : !/[\p{L}\p{N}]/u.test(prev);
      if (leftOk) {
        let end = -1;
        for (let j = i + 1; j < n; j++) {
          if (text[j] === "\n" && text[j + 1] === "\n") break;
          if (text[j] === c && text[j - 1] !== " " && text[j + 1] !== c && text[j - 1] !== c) {
            if (c === "_" && /[\p{L}\p{N}]/u.test(text[j + 1] || "")) continue;
            end = j;
            break;
          }
        }
        if (end > i + 1) {
          flush();
          out.push(el("em", null, inline(text.slice(i + 1, end), opts)));
          i = end + 1;
          continue;
        }
      }
    }
    if (c === "~" && text[i + 1] === "~") {
      const end = text.indexOf("~~", i + 2);
      if (end > i + 2) {
        flush();
        out.push(el("del", null, inline(text.slice(i + 2, end), opts)));
        i = end + 2;
        continue;
      }
    }
    if (c === "<") {
      const br = /^<br\s*\/?>/i.exec(text.slice(i, i + 6));
      if (br) {
        flush();
        out.push(el("br"));
        i += br[0].length;
        continue;
      }
      const auto = /^<(https?:\/\/[^\s<>]+)>/.exec(text.slice(i));
      if (auto) {
        flush();
        out.push(makeLink(auto[1], [auto[1]], opts));
        i += auto[0].length;
        continue;
      }
    }
    if ((c === "[" || (c === "!" && text[i + 1] === "[")) && !(c === "[" && text[i + 1] === "^")) {
      const img = c === "!";
      const start = img ? i + 1 : i;
      const close = findClosing(text, "[", "]", start);
      if (close !== -1) {
        const inner = text.slice(start + 1, close);
        if (text[close + 1] === "(") {
          const pclose = findClosing(text, "(", ")", close + 1);
          if (pclose !== -1) {
            let href = text.slice(close + 2, pclose).trim();
            href = href.replace(/\s+["'(].*["')]$/, "").replace(/^<|>$/g, "");
            flush();
            const local = img && /^(?:\.\.\/)*\/?ui\/[\w.-]+\.(?:svg|png)$/.test(href); // картинки консоли (схема); чужие — не грузим
            if (local) out.push(el("img", { class: "arch-img", src: "/ui/" + href.split("ui/").pop(), alt: inner || "" }));
            else if (img) out.push(el("span", { class: "md-img", title: href }, ["[изображение: " + (inner || href) + "]"]));
            else out.push(makeLink(href, inline(inner, { ...opts, chips: false }), opts));
            i = pclose + 1;
            continue;
          }
        }
        if (!img && opts.chips) {
          const t = inner.trim();
          if (ID_LIST_RE.test(t)) {
            flush();
            t.split(/\s*[,;]\s*/).forEach((id) => out.push(chip(id, opts)));
            i = close + 1;
            continue;
          }
          const parts = splitLabels(t);
          if (parts) {
            flush();
            parts.forEach((p) => out.push(chip(p, opts)));
            i = close + 1;
            continue;
          }
        }
      }
    }
    if (c === "h" && !/[\p{L}\p{N}]/u.test(prev)) {
      const m = /^https?:\/\/[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'"»)]/.exec(text.slice(i, i + 2048));
      if (m) {
        flush();
        out.push(makeLink(m[0], [m[0]], opts));
        i += m[0].length;
        continue;
      }
    }
    buf += c;
    i++;
  }
  flush();
  return out;
}

function makeLink(href, kids, opts) {
  let target = safeHref(href);
  let internal = false;
  if (target && opts.resolveLink) {
    const r = opts.resolveLink(target);
    if (r === null) target = null;
    else if (r) {
      target = r.href;
      internal = !!r.internal;
    }
  }
  if (!target) return el("span", { class: "md-badlink", title: "ссылка отключена: " + href }, kids);
  const a = el("a", { href: target }, kids);
  if (/^https?:/i.test(target) && !internal) {
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
  }
  return a;
}

function splitRow(line) {
  let t = line.trim();
  if (t.startsWith("|")) t = t.slice(1);
  if (t.endsWith("|") && !t.endsWith("\\|")) t = t.slice(0, -1);
  const cells = [];
  let cur = "";
  let inCode = false;
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (c === "\\" && t[i + 1] === "|") {
      cur += "|";
      i++;
      continue;
    }
    if (c === "`") inCode = !inCode;
    if (c === "|" && !inCode) {
      cells.push(cur.trim());
      cur = "";
    } else cur += c;
  }
  cells.push(cur.trim());
  return cells;
}

function isBlockStart(line, next) {
  return (
    FENCE_RE.test(line) && /^(\s{0,3})(`{3,}|~{3,})/.test(line)
  ) || HEADING_RE.test(line) || HR_RE.test(line) || QUOTE_RE.test(line) || LIST_RE.test(line)
    || (line.includes("|") && next !== undefined && TABLE_SEP_RE.test(next) && next.includes("-"));
}

function parseBlocks(lines, opts, ctx) {
  const out = [];
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i];
    if (isBlank(line)) {
      i++;
      continue;
    }
    // код
    const fm = /^(\s{0,3})(`{3,}|~{3,})\s*([^\s`]*)/.exec(line);
    if (fm) {
      const fence = fm[2];
      const body = [];
      i++;
      while (i < n && !(lines[i].trim().startsWith(fence[0].repeat(fence.length))
        && lines[i].trim().replace(new RegExp(`^\\${fence[0]}+`), "").trim() === "")) {
        body.push(lines[i]);
        i++;
      }
      i++;
      const code = el("code", fm[3] ? { class: "lang-" + fm[3].replace(/[^\w-]/g, "") } : null, [body.join("\n")]);
      const preEl = el("pre", { class: "md-pre" }, [code]);
      if (fm[3] === "mermaid")
        out.push(el("figure", { class: "md-mermaid" }, [
          el("figcaption", null, ["Диаграмма Mermaid — исходный текст (консоль работает офлайн и не рисует Mermaid)"]),
          preEl,
        ]));
      else out.push(preEl);
      continue;
    }
    // заголовок
    const hm = HEADING_RE.exec(line);
    if (hm) {
      const level = hm[1].length;
      const hEl = el("h" + Math.min(6, level + (opts.headingShift || 0)), null, inline(hm[2], opts));
      if (opts.headingIds !== false) {
        const id = slugify(hEl.textContent, ctx.ids);
        hEl.id = (opts.idPrefix || "") + id;
        if (opts.toc) opts.toc.push({ level, text: hEl.textContent, id: hEl.id });
      }
      out.push(hEl);
      i++;
      continue;
    }
    if (HR_RE.test(line)) {
      out.push(el("hr"));
      i++;
      continue;
    }
    // цитата
    if (QUOTE_RE.test(line)) {
      const body = [];
      while (i < n && !isBlank(lines[i]) && (QUOTE_RE.test(lines[i]) || body.length)) {
        if (!QUOTE_RE.test(lines[i]) && isBlockStart(lines[i], lines[i + 1])) break;
        body.push(lines[i].replace(QUOTE_RE, ""));
        i++;
      }
      out.push(el("blockquote", null, parseBlocks(body, opts, ctx)));
      continue;
    }
    // таблица
    if (line.includes("|") && i + 1 < n && TABLE_SEP_RE.test(lines[i + 1]) && lines[i + 1].includes("-")) {
      const head = splitRow(line);
      const aligns = splitRow(lines[i + 1]).map((c) =>
        (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : null));
      i += 2;
      const rows = [];
      while (i < n && !isBlank(lines[i]) && lines[i].includes("|")) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      const cell = (tag, txt, k) => {
        const c = el(tag, aligns[k] ? { style: `text-align:${aligns[k]}` } : null, inline(txt, opts));
        return c;
      };
      const thead = el("thead", null, [el("tr", null, head.map((t, k) => cell("th", t, k)))]);
      const tbody = el("tbody", null, rows.map((r) => el("tr", null, head.map((_, k) => cell("td", r[k] ?? "", k)))));
      out.push(el("div", { class: "md-table-wrap" }, [el("table", { class: "md-table" }, [thead, tbody])]));
      continue;
    }
    // список
    if (LIST_RE.test(line)) {
      const [list, next] = parseList(lines, i, opts, ctx);
      out.push(list);
      i = next;
      continue;
    }
    // абзац
    const para = [];
    while (i < n && !isBlank(lines[i])) {
      if (para.length && isBlockStart(lines[i], lines[i + 1])) break;
      para.push(para.length ? lines[i].trimStart() : lines[i].trim());
      i++;
    }
    out.push(el("p", null, inline(para.join("\n"), opts)));
  }
  return out;
}

function parseList(lines, i, opts, ctx) {
  const n = lines.length;
  const first = LIST_RE.exec(lines[i]);
  const base = indentOf(first[1]);
  const ordered = /\d/.test(first[2]);
  const list = el(ordered ? "ol" : "ul", { class: "md-list" });
  if (ordered) {
    const startNum = parseInt(first[2], 10);
    if (startNum !== 1) list.setAttribute("start", String(startNum));
  }
  let loose = false;
  const items = [];
  while (i < n) {
    const m = LIST_RE.exec(lines[i]);
    if (!m || Math.abs(indentOf(m[1]) - base) > 1 || /\d/.test(m[2]) !== ordered) break;
    const contentIndent = indentOf(m[1]) + m[2].length + 1;
    const body = [m[3]];
    i++;
    while (i < n) {
      const l = lines[i];
      if (isBlank(l)) {
        let j = i + 1;
        while (j < n && isBlank(lines[j])) j++;
        if (j < n && indentOf(lines[j]) > base && !(LIST_RE.test(lines[j]) && indentOf(lines[j]) <= base + 1)) {
          body.push("");
          loose = true;
          i++;
          continue;
        }
        break;
      }
      const ind = indentOf(l);
      if (ind > base + 1) {
        body.push(l.replace(/^\s+/, (sp) => " ".repeat(Math.max(0, sp.replace(/\t/g, "    ").length - contentIndent))));
        i++;
        continue;
      }
      if (LIST_RE.test(l) || isBlockStart(l, lines[i + 1])) break;
      body.push(l.trim()); // ленивое продолжение абзаца
      i++;
    }
    items.push(body);
    // пустая строка между пунктами одного списка
    if (i < n && isBlank(lines[i])) {
      let j = i;
      while (j < n && isBlank(lines[j])) j++;
      const m2 = j < n ? LIST_RE.exec(lines[j]) : null;
      if (m2 && Math.abs(indentOf(m2[1]) - base) <= 1 && /\d/.test(m2[2]) === ordered) {
        loose = true;
        i = j;
      }
    }
  }
  for (const body of items) {
    const li = el("li");
    const kids = parseBlocks(body, opts, ctx);
    for (const k of kids) {
      if (!loose && k.tagName === "P") while (k.firstChild) li.appendChild(k.firstChild);
      else li.appendChild(k);
    }
    list.appendChild(li);
  }
  return [list, i];
}

/**
 * Markdown → <div class="md">. opts: chips, onChip, breaks (перевод строки = <br>), resolveLink(href) →
 * {href, internal} | null, toc (массив, заполняется заголовками), idPrefix, headingShift.
 */
export function renderMarkdown(src, opts = {}) {
  const root = el("div", { class: "md" + (opts.className ? " " + opts.className : "") });
  const lines = String(src ?? "").replace(/\r\n?/g, "\n").split("\n");
  const ctx = { ids: new Set() };
  for (const node of parseBlocks(lines, opts, ctx)) root.appendChild(node);
  return root;
}

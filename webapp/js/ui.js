// Small DOM + formatting helpers shared by every view.

/** h("div.card#id", {attrs|on*}, ...children) — tiny hyperscript. */
export function h(tag, attrs, ...children) {
  const [, name = "div", rest = ""] = tag.match(/^([a-z0-9-]*)(.*)$/i);
  const el = document.createElement(name || "div");
  for (const m of rest.matchAll(/([.#])([\w-]+)/g)) {
    if (m[1] === ".") el.classList.add(m[2]); else el.id = m[2];
  }
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "class") el.className += " " + v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Inline SVG icon from a small built-in set (stroke icons, 24px grid). */
const ICONS = {
  home: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  ticket: "M4 7a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-3a2 2 0 0 0 0-4zM14 5v14",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zm10 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  layers: "M12 3l9 5-9 5-9-5 9-5zm-9 9l9 5 9-5M3 16l9 5 9-5",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4",
  user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9a7 7 0 0 1 14 0",
  check: "M5 12l5 5 9-10",
  x: "M6 6l12 12M18 6L6 18",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  alert: "M12 3l10 18H2zM12 10v5M12 18v.5",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 11v6M12 7.5v.5",
  spark: "M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6",
  search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm10 2l-4.5-4.5",
  upload: "M12 16V4M7 9l5-5 5 5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  download: "M12 4v12M7 11l5 5 5-5M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  play: "M7 4l13 8-13 8z",
  pause: "M7 4h4v16H7zM13 4h4v16h-4z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 7v5l3 2",
  cpu: "M6 6h12v12H6zM9 9h6v6H9zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4",
  db: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zm0 0v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  lock: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4",
  send: "M4 12l16-8-6 16-2-7z",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  chevron: "M9 6l6 6-6 6",
  filter: "M4 5h16l-6 8v6l-4-2v-4z",
  trend: "M3 17l6-6 4 4 8-8M15 7h6v6",
  box: "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10",
  sliders: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4",
  chat: "M4 5h16v11H9l-5 4z",
  star: "M12 3l2.8 5.8 6.2.9-4.5 4.4 1 6.3L12 17.5 6.5 20.4l1-6.3L3 9.7l6.2-.9z",
};
export function icon(name, cls = "") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("width", "16"); // default size; CSS rules override
  svg.setAttribute("height", "16");
  svg.style.flex = "none";
  if (cls) svg.setAttribute("class", cls);
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", ICONS[name] || ICONS.info);
  svg.append(p);
  return svg;
}

// ---- formatting ----------------------------------------------------------
export const fmtInt = (v) => (v == null || !isFinite(v) ? "–" : Math.round(v).toLocaleString());
export const fmtNum = (v, d = 1) => (v == null || !isFinite(v) ? "–" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
export const fmtPct = (v, d = 1) => (v == null || !isFinite(v) ? "–" : (v * 100).toFixed(d) + "%");
export function fmtDur(hours) {
  if (hours == null || !isFinite(hours)) return "–";
  if (hours < 1) return Math.round(hours * 60) + "m";
  if (hours < 48) return hours.toFixed(1) + "h";
  return (hours / 24).toFixed(1) + "d";
}
export function fmtDate(ts, withTime = false) {
  if (ts == null) return "–";
  const d = typeof ts === "number" ? new Date(ts < 1e12 ? ts * 1000 : ts) : new Date(ts);
  const opt = { year: "numeric", month: "short", day: "2-digit" };
  if (withTime) Object.assign(opt, { hour: "2-digit", minute: "2-digit", hour12: false });
  return d.toLocaleString(undefined, opt);
}
export function timeAgo(ms) {
  const s = Math.round((Date.now() - ms) / 1000);
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

// ---- common bits ----------------------------------------------------------
/** The persistent "advisory / human decision required" cue. */
export function advisory(text = "Advisory — human decision required") {
  return h("span.advisory", { title: "AI output is a recommendation only. No action is taken automatically." }, icon("user"), text);
}
export function aiTag(text = "Local AI") {
  return h("span.ai-tag", icon("spark"), text);
}
export function statusPill(level, text) {
  const ic = { good: "check", warning: "alert", serious: "alert", critical: "alert", info: "info" }[level] || "info";
  return h(`span.pill.${level}`, icon(ic), text);
}
export function kpi({ label, value, unit, foot, icon: ic }) {
  return h("div.card.kpi",
    h("div.k-label", ic ? icon(ic) : null, label),
    h("div.k-value", value, unit ? h("small", unit) : null),
    foot ? h("div.k-foot", foot) : null);
}
/** Delta chip. `goodWhen` = "down" | "up" decides colour; always shows an arrow + sign. */
export function delta(change, { goodWhen = "down", fmt = (v) => fmtPct(Math.abs(v)) } = {}) {
  if (change == null || !isFinite(change)) return h("span.delta.flat", "–");
  const up = change > 0;
  const cls = Math.abs(change) < 1e-9 ? "flat" : (up === (goodWhen === "up") ? "good" : "bad");
  return h(`span.delta.${cls}`, up ? "▲" : "▼", fmt(change));
}

export function toast(text, kind = "") {
  let host = $(".toasts");
  if (!host) document.body.append((host = h("div.toasts")));
  const t = h(`div.toast${kind ? "." + kind : ""}`, text);
  host.append(t);
  setTimeout(() => t.remove(), 3800);
}

/** Right-side drawer. Returns { body, close }. */
export function drawer(title, { subtitle, actions = [] } = {}) {
  closeOverlays();
  const close = () => { scrim.remove(); panel.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => e.key === "Escape" && close();
  const scrim = h("div.scrim", { onclick: close });
  const body = h("div.drawer-body");
  const panel = h("aside.drawer", { role: "dialog", "aria-label": title },
    h("div.drawer-head",
      h("div.stack", { style: { gap: "2px" } }, h("h3", title), subtitle ? h("span.small.muted", subtitle) : null),
      h("div.row", { style: { marginLeft: "auto" } }, ...actions,
        h("button.btn.ghost.sm", { onclick: close, "aria-label": "Close" }, icon("x")))),
    body);
  document.body.append(scrim, panel);
  document.addEventListener("keydown", onKey);
  return { body, close };
}

/** Simple modal with a form body; resolves with the value of onSubmit or null on cancel. */
export function modal(title, build) {
  closeOverlays();
  return new Promise((resolve) => {
    const done = (v) => { scrim.remove(); box.remove(); resolve(v); };
    const scrim = h("div.scrim", { onclick: () => done(null) });
    const box = h("div.modal", { role: "dialog", "aria-label": title }, h("h3", title));
    build(box, done);
    document.body.append(scrim, box);
    box.querySelector("textarea, input, select")?.focus();
  });
}

export function closeOverlays() {
  $$(".scrim, .drawer, .modal").forEach((n) => n.remove());
}

export function downloadFile(name, text, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function toCSV(rows, cols) {
  cols = cols || Object.keys(rows[0] || {});
  const esc = (v) => {
    const s = v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
}

/** Seeded PRNG for reproducible synthetic data. */
export function rng(seed = 1) {
  let a = seed >>> 0; // mulberry32
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

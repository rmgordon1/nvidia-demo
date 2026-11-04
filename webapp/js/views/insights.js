// IT Insights — managers ask questions about the incident archive in plain English. The local
// LLM writes a read-only SQL query (shown for transparency), the server runs it, and the
// answer is summarized from the returned rows only.
import { h, icon, aiTag, advisory, toast, fmtNum, toCSV, downloadFile } from "../ui.js";
import * as store from "../store.js";
import { callApi, chatModel } from "../ai/config.js";
import { lineChart, hbarChart, SERIES } from "../charts.js";

const EXAMPLES = [
  "What are the top 5 issues at the Southwest Production Facility last quarter?",
  "MTTR in hours for MES Support by month in 2026",
  "Which support groups bounce tickets the most?",
  "How many phishing reports did we get per quarter?",
  "Which OT products had the most high or critical incidents this year?",
  "How many incidents were reopened, by assigned group?",
];

export async function render(root) {
  const input = h("input", { type: "text", placeholder: "Ask about the incident archive, e.g. \"Top 5 issues at HQ last month\"", "aria-label": "Question" });
  const go = h("button.btn.primary", { type: "submit" }, icon("spark"), "Ask");
  const history = h("div.stack");
  const form = h("form.card.ins-ask", { onsubmit: (e) => { e.preventDefault(); ask(input.value); } },
    h("div.card-head", aiTag("Ask the archive"), h("span.sub", "Helix incidents Nov 2024 – Oct 2026 · read-only")),
    h("div.itsm-chatbar", input, go),
    h("div.itsm-chips", h("span.small.muted", "Try:"), ...EXAMPLES.map((q) => h("button.btn.sm.ghost.itsm-chip", { type: "button", title: q, onclick: () => { input.value = q; ask(q); } }, q.length > 44 ? q.slice(0, 42) + "…" : q))));

  root.append(
    h("div.view-head", h("div.titles", h("h2", "IT Insights"),
      h("p.lede", "Ask operational questions in plain English. A local model writes a read-only SQL query over the incident archive. You see the query, the rows and a short summary drawn only from those rows."))),
    form, history);

  async function ask(q) {
    q = (q || "").trim();
    if (q.length < 5) { toast("Ask a full question", "bad"); return; }
    go.disabled = true;
    const card = h("div.card.ins-result", h("div.card-head", h("b", q), h("span.end.row.small.muted", h("span.spinner"), "writing query…")));
    history.prepend(card);
    const t0 = performance.now();
    try {
      const r = await callApi("/analytics/ask", { question: q, model: chatModel() });
      renderResult(card, q, r, performance.now() - t0);
      store.audit({ module: "itsm", action: "insights.answered", subject: "analytics",
        detail: { question: q, sql: r.sql, rows: r.rows.length, ms: r.ms },
        ai: { model: r.model, version: "vllm-local", mode: "api", at: new Date().toISOString() } });
    } catch (err) {
      card.querySelector(".card-head .end").replaceChildren(h("span.pill.critical", "error"), " ", err.message);
    } finally {
      go.disabled = false;
    }
  }

  function renderResult(card, q, r, ms) {
    const chartBox = h("div.ins-chart");
    const table = h("div.table-wrap.ins-table", h("table.data",
      h("thead", h("tr", ...r.columns.map((c) => h("th", c)))),
      h("tbody", ...r.rows.slice(0, 50).map((row) => h("tr", ...row.map((v) => h(typeof v === "number" ? "td.r" : "td", v == null ? "—" : typeof v === "number" ? fmtNum(v, Number.isInteger(v) ? 0 : 1) : String(v))))))));
    card.replaceChildren(
      h("div.card-head", h("b", q), advisory("Verify before reporting"),
        h("span.end.row", h("span.chip", `${(ms / 1000).toFixed(1)}s · ${r.model || "local LLM"}`),
          r.rows.length ? h("button.btn.ghost.sm", { type: "button", title: "Download CSV", onclick: () => downloadFile("insight.csv", toCSV(r.rows.map((row) => Object.fromEntries(row.map((v, i) => [r.columns[i], v]))), r.columns), "text/csv") }, icon("download")) : null)),
      h("p.ins-answer", r.answer || "—"),
      chartBox,
      r.rows.length ? table : h("div.empty-state", "No rows returned."),
      h("details.ins-sql", h("summary.small.muted", "SQL query"), h("pre", r.sql || "")));
    drawChart(chartBox, r);
  }
}

function drawChart(el, r) {
  if (r.rows.length < 2 || r.columns.length < 2) return;
  const numCols = r.columns.map((_, i) => r.rows.every((row) => row[i] == null || typeof row[i] === "number"));
  const vi = numCols.findIndex((isNum, i) => isNum && i > 0);
  if (vi < 0 || numCols[0]) return;
  const labels = r.rows.map((row) => String(row[0] ?? "—"));
  const temporal = labels.every((l) => /^\d{4}(-\d{2}|-Q\d)$/.test(l));
  if (temporal && labels.length >= 3) {
    lineChart(el, { x: labels, height: 220, xFmt: (v) => v, tipTitle: (v) => v, yFmt: (v) => fmtNum(v, 1),
      series: [{ name: r.columns[vi], values: r.rows.map((row) => row[vi]), color: SERIES[0], points: true, area: true }] });
  } else {
    hbarChart(el, { items: r.rows.slice(0, 12).map((row) => ({ label: String(row[0] ?? "—"), value: row[vi] || 0 })),
      valueName: r.columns[vi], fmt: (v) => fmtNum(v, Number.isInteger(v) ? 0 : 1), rowH: 26 });
  }
}

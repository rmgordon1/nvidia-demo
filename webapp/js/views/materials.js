// Material Accountability — raw material → finished output ("what's next" phase).
// Yield / spoilage vs standard, automatic variance attribution, reconciliation
// ledger, plan-driven procurement forecast and NL Q&A over ERP / MES exports.
import { h, icon, advisory, aiTag, statusPill, kpi, toast, drawer, modal, downloadFile, toCSV, fmtInt, fmtNum, fmtPct } from "../ui.js";
import { lineChart, barChart, SERIES } from "../charts.js";
import * as store from "../store.js";
import { getMaterialsData, dayDate, DAYS } from "../data/materials.js";
import { attributeVariance, forecast, ask } from "../ai/materials.js";

const fmt$ = (v) => (v < 0 ? "−$" : "$") + fmtInt(Math.abs(v));
const fmtQty = (v, unit) => (unit === "kg" ? fmtNum(v, 0) + " kg" : fmtInt(v) + " " + unit);
const compact = (v) => (Math.abs(v) >= 1e6 ? (v / 1e6).toFixed(2) + "M" : Math.abs(v) >= 1e4 ? (v / 1e3).toFixed(1) + "k" : fmtInt(v));
const fmtQtyShort = (v, unit) => compact(v) + " " + (unit === "sheets" ? "sh" : unit);
const DECISIONS_KEY = "materials.decisions";
const DRIVER = {
  "lot":        { label: "Material lot",  icon: "box" },
  "line-shift": { label: "Line × shift",  icon: "clock" },
  "line":       { label: "Line",          icon: "cpu" },
  "usage-rate": { label: "Usage rate",    icon: "trend" },
};
const ASSIGNEES = ["Production engineering", "Supply chain / vendor quality", "Line supervisor", "Finance / cost accounting"];
const SUGGESTED = [
  "Why was ink consumption over standard on Line 2 last week?",
  "Which substrate lot caused spoilage problems?",
  "What is driving spoilage on Line 3?",
  "What do we need to order in the next 12 weeks?",
];

export async function render(root) {
  const data = getMaterialsData();
  const matColor = Object.fromEntries(data.materials.map((m, i) => [m.code, SERIES[i]]));
  const state = { days: 30, line: "all", shift: "all", material: "all", ledgerMat: "INK-G1", fcMat: "INK-G1", recTab: "runs", recPage: 0, recQuery: "" };
  const decisions = () => store.get(DECISIONS_KEY, {});
  let att = null;
  const fc = forecast(data, data.plan);

  // ---------------------------------------------------------------- header
  root.append(
    h("div.view-head",
      h("div.titles",
        h("div.row.wrap", h("h2", "Material Accountability"), h("span.pill", icon("clock"), "WIP · later development"),
          h("span.pill", icon("db"), "Source: ERP / MES export (synthetic)")),
        h("p.lede", "Raw material to finished output: yield and spoilage against standard, automatic variance attribution to a material, lot, line or shift, and plan-driven procurement forecasting.")),
      h("div.actions",
        h("button.btn", { onclick: exportFindings }, icon("download"), "Export findings"))),
    h("div.placeholder-note", "Analytics are transparent placeholders in ", h("code", "js/ai/materials.js"),
      " (robust z-scores + contribution analysis over the synthetic exports in ", h("code", "js/data/materials.js"),
      "). Swap ", h("code", "attributeVariance()"), ", ", h("code", "forecast()"), " and ", h("code", "ask()"), " for the local model service; the data shapes stay the same."),
  );

  // ---------------------------------------------------------------- filters (one row, above charts)
  const rangeGroup = h("div.btn-group", { role: "group", "aria-label": "Date range" },
    [7, 30, 60].map((d) => h("button", { type: "button", "data-d": d, onclick: () => { state.days = d; refresh(); } }, `${d} d`)));
  const sel = (label, key, opts) => {
    const s = h("select", { "aria-label": label, onchange: () => { state[key] = s.value; refresh(); } },
      h("option", { value: "all" }, `All ${label.toLowerCase()}s`), opts.map(([v, t]) => h("option", { value: v }, t)));
    return h("label.mat-filter", h("span", label), s);
  };
  root.append(h("div.card.mat-filters",
    h("label.mat-filter", h("span", "Range"), rangeGroup),
    sel("Line", "line", data.lines.map((l) => [l, `Line ${l}`])),
    sel("Shift", "shift", data.shifts.map((s) => [s, `Shift ${s}`])),
    sel("Material", "material", data.materials.map((m) => [m.code, `${m.code} · ${m.name}`])),
    h("span.small.muted.mat-asof", icon("clock"), `Data through ${data.endDate} · ${fmtInt(data.runs.length)} runs · ${fmtInt(data.txns.length)} inventory transactions`)));

  // ---------------------------------------------------------------- sections (filled by refresh)
  const kpiRow = h("div.mat-kpis");
  const spoilChart = h("div"), costChart = h("div");
  const spoilCaption = h("span.sub");
  const findingsBody = h("div");
  const findingsSub = h("span.sub");
  const ledgerBody = h("div");
  const ledgerSel = h("select", { "aria-label": "Ledger material", onchange: () => { state.ledgerMat = ledgerSel.value; renderLedger(); } },
    data.materials.map((m) => h("option", { value: m.code }, m.code)));
  ledgerSel.value = state.ledgerMat;
  const scenarioBody = h("div.mat-scenarios");

  root.append(
    kpiRow,
    h("div.grid.g-2",
      h("div.card",
        h("div.card-head", h("h3", "Spoilage rate vs allowance"), spoilCaption),
        spoilChart),
      h("div.card",
        h("div.card-head", h("h3", "Actual vs standard material cost"), h("span.sub", "standard = BOM rate × good output × (1 + allowance)")),
        costChart)),
    h("div.card",
      h("div.card-head", h("h3", "Variance attribution"), aiTag("Local AI · placeholder"), findingsSub, h("div.end", advisory())),
      findingsBody),
  );

  // Ask the data + reconciliation
  const askInput = h("input", { type: "text", placeholder: "Ask about yield, spoilage, variance or procurement…", "aria-label": "Question" });
  const askBtn = h("button.btn.primary", { type: "submit" }, icon("send"), "Ask");
  const answers = h("div.mat-answers");
  root.append(h("div.grid.g-2",
    h("div.card",
      h("div.card-head", h("h3", "Material reconciliation"), h("span.sub", "plant-wide ledger for the selected range"), h("div.end", ledgerSel)),
      ledgerBody),
    h("div.card.mat-ask",
      h("div.card-head", h("h3", "Ask the data"), aiTag("Local LLM · placeholder"), h("div.end", advisory())),
      h("form.row", { onsubmit: (e) => { e.preventDefault(); runAsk(askInput.value); } }, askInput, askBtn),
      h("div.row.wrap.mat-suggest", SUGGESTED.map((q) => h("button.btn.sm", { type: "button", onclick: () => { askInput.value = q; runAsk(q); } }, q))),
      answers)));

  // Forecast
  const fcGroup = h("div.btn-group", { role: "group", "aria-label": "Forecast material" },
    data.materials.map((m) => h("button", { type: "button", "data-m": m.code, onclick: () => { state.fcMat = m.code; renderForecast(); } }, m.code)));
  const fcChart = h("div"), fcSide = h("div.stack"), fcTable = h("div");
  root.append(h("div.card",
    h("div.card-head", h("h3", "Procurement forecast — next 12 weeks"), aiTag("Plan-driven"), h("span.sub", "vs historical-average method"), h("div.end", advisory())),
    h("div.row.wrap", { style: { marginBottom: "12px" } }, fcGroup),
    h("div.grid.g-main-side", h("div", fcChart), fcSide),
    h("div.divider"),
    h("h4", { style: { margin: "4px 0 10px" } }, "Reorder recommendations"),
    fcTable));

  // Raw records
  const recTabs = h("div.btn-group", { role: "tablist" },
    [["runs", "Production runs"], ["pos", "Purchase orders"], ["lots", "Lots"], ["txns", "Inventory transactions"]].map(([k, t]) =>
      h("button", { type: "button", "data-k": k, onclick: () => { state.recTab = k; state.recPage = 0; renderRecords(); } }, t)));
  const recSearch = h("input", { type: "search", placeholder: "Filter by id, lot, line…", "aria-label": "Filter records", oninput: () => { state.recQuery = recSearch.value.trim().toLowerCase(); state.recPage = 0; renderRecords(); } });
  const recBody = h("div");
  root.append(h("div.card",
    h("div.card-head", h("h3", "Source records"), h("span.sub", "ERP / MES export shape · respects the filters above"),
      h("div.end", recSearch, h("button.btn.sm", { onclick: exportRecords }, icon("download"), "CSV"))),
    h("div", { style: { marginBottom: "10px" } }, recTabs),
    recBody));

  // Scenario key (evaluator aid)
  root.append(h("details.card.mat-key",
    h("summary", h("h3", "Known-answer scenarios built into the synthetic data"), h("span.sub", "evaluator view — checks whether the AI found them")),
    scenarioBody));

  // ================================================================= renderers
  function refresh() {
    rangeGroup.querySelectorAll("button").forEach((b) => b.classList.toggle("on", +b.dataset.d === state.days));
    att = attributeVariance(data, state);
    renderKpis();
    renderSpoilChart();
    renderCostChart();
    renderFindings();
    renderLedger();
    renderRecords();
    renderScenarios();
  }

  function filteredRuns() {
    return data.runs.filter((x) => x.day >= DAYS - state.days && (state.line === "all" || x.line === state.line) && (state.shift === "all" || x.shift === state.shift));
  }

  function renderKpis() {
    const runs = filteredRuns();
    const printed = runs.reduce((a, x) => a + x.printed, 0), good = runs.reduce((a, x) => a + x.good, 0);
    const spoil = (printed - good) / printed;
    const allowed = data.allowedSpoilage / (1 + data.allowedSpoilage);
    const over = att.totals.std ? att.totals.varianceCost / att.totals.std : 0;
    kpiRow.replaceChildren(
      kpi({ label: "Good output", icon: "box", value: compact(good), unit: "sheets", foot: `${fmtInt(printed)} printed · ${state.days} d` }),
      kpi({ label: "Yield", icon: "trend", value: fmtPct(good / printed), foot: "good ÷ printed sheets" }),
      kpi({ label: "Spoilage", icon: "alert", value: fmtPct(spoil), foot: [spoil > allowed ? statusPill("warning", "Over allowance") : statusPill("good", "Within allowance"), h("span", `allowed ${fmtPct(allowed)}`)] }),
      kpi({ label: "Material variance", icon: "layers", value: fmt$(att.totals.varianceCost), foot: `actual − standard · ${over >= 0 ? "+" : ""}${fmtPct(over)} vs standard` }),
      kpi({ label: "Variance explained", icon: "spark", value: fmtPct(att.explainedPct, 0), foot: `${att.findings.length} findings · auto-attributed` }),
      kpi({ label: "Reconciliation time", icon: "clock", value: "3", unit: "min", foot: "vs ~3 days manual spreadsheet cycle" }),
    );
  }

  function renderSpoilChart() {
    const runs = filteredRuns();
    const start = DAYS - state.days;
    const x = Array.from({ length: state.days }, (_, i) => dayDate(start + i));
    const byLine = state.line === "all";
    const keys = byLine ? data.lines.filter((l) => runs.some((r) => r.line === l)) : data.shifts.filter((s) => runs.some((r) => r.shift === s));
    const series = keys.map((k, i) => {
      const vals = x.map((_, di) => {
        const rs = runs.filter((r) => r.day === start + di && (byLine ? r.line === k : r.shift === k));
        const p = rs.reduce((a, r) => a + r.printed, 0);
        return p ? rs.reduce((a, r) => a + r.spoiled, 0) / p : null;
      });
      return { name: byLine ? `Line ${k}` : `Shift ${k}`, values: vals, color: SERIES[byLine ? data.lines.indexOf(k) : i], fmt: (v) => fmtPct(v, 2) };
    });
    spoilCaption.textContent = byLine ? "daily, by line" : `daily, Line ${state.line} by shift`;
    const allowed = data.allowedSpoilage / (1 + data.allowedSpoilage);
    lineChart(spoilChart, {
      x, series, height: 230, yMin: 0, yFmt: (v) => (v * 100).toFixed(0) + "%",
      xFmt: (d) => d.slice(5), tipTitle: (d) => d, label: "Daily spoilage rate",
      refs: [{ y: allowed, label: `Allowed ${fmtPct(allowed)}` }],
    });
  }

  function renderCostChart() {
    const mats = data.materials.filter((m) => state.material === "all" || m.code === state.material);
    const by = Object.fromEntries(mats.map((m) => [m.code, { std: 0, act: 0 }]));
    for (const r of att.records) { by[r.m.code].std += r.std * r.m.unitCost; by[r.m.code].act += r.actual * r.m.unitCost; }
    barChart(costChart, {
      labels: mats.map((m) => m.code), height: 230, label: "Actual vs standard cost by material",
      yFmt: (v) => "$" + (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? Math.round(v / 1e3) + "k" : Math.round(v)),
      tipTitle: (lb, i) => `${lb} · ${mats[i].name}`,
      series: [
        { name: "Standard", values: mats.map((m) => by[m.code].std), color: "var(--series-1)", fmt: fmt$ },
        { name: "Actual", values: mats.map((m) => by[m.code].act), color: "var(--series-2)", fmt: fmt$ },
      ],
    });
    costChart.append(h("div.mat-var-chips", mats.map((m) => {
      const v = by[m.code].act - by[m.code].std, p = by[m.code].std ? v / by[m.code].std : 0;
      return h("span.chip", { title: `${m.name}: actual − standard` }, `${m.code} ${p >= 0 ? "+" : ""}${(p * 100).toFixed(1)}% · ${fmt$(v)}`);
    })));
  }

  function decisionPill(f) {
    const d = decisions()[f.id];
    if (!d) return statusPill("warning", "Needs review");
    if (d.decision === "confirmed") return statusPill("good", "Confirmed");
    if (d.decision === "assigned") return statusPill("info", `Assigned · ${d.assignee}`);
    return h("span.pill", icon("x"), "Dismissed");
  }

  function renderFindings() {
    findingsSub.textContent = `${fmtPct(att.explainedPct, 0)} of gross variance explained · ${fmt$(att.totals.attributed)} attributed of ${fmt$(att.totals.grossAbs)}`;
    if (!att.findings.length) {
      findingsBody.replaceChildren(h("div.empty-state", "No statistically significant variance drivers in this selection. Remaining variance is within normal run-to-run noise."));
      return;
    }
    const maxShare = Math.max(...att.findings.map((f) => f.share), 0.01);
    findingsBody.replaceChildren(h("div.table-wrap.mat-findings", h("table.data",
      h("thead", h("tr", h("th", "#"), h("th", "Finding"), h("th", "Driver"), h("th", "Material"), h("th.r", "Impact"), h("th", "Share of variance"), h("th", "Confidence"), h("th", "Review"))),
      h("tbody", att.findings.map((f, i) => h("tr.clickable", { onclick: () => openFinding(f) },
        h("td.num.muted", i + 1),
        h("td.wrap", h("div.mat-ftitle", f.title), h("div.small.muted", f.id)),
        h("td", h("span.pill", icon(DRIVER[f.driver].icon), DRIVER[f.driver].label)),
        h("td", f.material === "all" ? h("span.muted", "All materials") : h("span.row", h("i.dot", { style: { background: matColor[f.material] } }), f.material)),
        h("td.r", h("b", fmt$(f.varianceCost))),
        h("td", h("div.mat-meter", h("div.bar-meter", h("i", { style: { width: (100 * f.share / maxShare).toFixed(0) + "%" } })), h("span.small.num", fmtPct(f.share, 0)))),
        h("td", h("div.mat-meter", h("div.bar-meter", h("i", { style: { width: (f.confidence * 100).toFixed(0) + "%", background: "var(--series-3)" } })), h("span.small.num", fmtPct(f.confidence, 0)))),
        h("td", h("span.row", decisionPill(f), h("span.mat-chev", icon("chevron"))))))))));
  }

  // ---- finding drawer
  function openFinding(f) {
    const { body } = drawer(f.title, { subtitle: `${DRIVER[f.driver].label} · ${f.id}` });
    const m = data.materials.find((x) => x.code === f.material);
    body.append(
      h("div.row.wrap", aiTag(f.provenance.model), advisory()),
      h("p", f.summary),
      h("dl.kv",
        h("dt", "Driver"), h("dd", DRIVER[f.driver].label),
        h("dt", "Material"), h("dd", f.material === "all" ? "All materials (extra sheets printed)" : `${f.material} · ${m?.name || ""}`),
        f.lines || f.line ? [h("dt", "Line(s)"), h("dd", (f.lines || [f.line]).join(", ") + (f.shift ? ` · Shift ${f.shift}` : ""))] : null,
        f.lot ? [h("dt", "Lot / vendor"), h("dd", `${f.lot} · ${f.vendor} (${data.vendors[f.vendor] || ""})`)] : null,
        f.weeks ? [h("dt", "Week(s)"), h("dd", f.weeks.join(", "))] : null,
        h("dt", "Estimated impact"), h("dd", h("b", fmt$(f.varianceCost)), f.driver === "usage-rate" ? ` · ${fmtQty(f.varianceQty, m.unit)} over` : ` · ${fmtInt(f.varianceQty)} excess sheets`),
        h("dt", "Share of variance"), h("dd", fmtPct(f.share, 1)),
        h("dt", "Confidence"), h("dd", `${fmtPct(f.confidence, 0)} (robust z = ${f.z.toFixed(1)})`),
        h("dt", "Runs involved"), h("dd", fmtInt(f.runIds.length)),
        h("dt", "Model"), h("dd.small.t2", `${f.provenance.model} · v${f.provenance.version} · ${f.provenance.mode}`)),
    );
    const chartEl = h("div");
    body.append(h("div.card", h("div.card-head", h("h3", f.driver === "usage-rate" ? `${f.material} usage per 1,000 printed sheets` : "Spoilage: affected runs vs all other runs"), h("span.sub", "daily")), chartEl));
    drawEvidenceChart(f, chartEl);
    body.append(h("div.stack",
      h("h4", "Evidence"),
      h("ul.mat-evidence", f.evidence.map((e) => h("li", h("span.chip", e.type), h("span.mono.small", e.id), h("span.t2.small", e.text))))));
    const ids = new Set(f.runIds);
    const runs = data.runs.filter((x) => ids.has(x.id)).slice(0, 12);
    body.append(h("div.table-wrap", h("table.data",
      h("thead", h("tr", h("th", "Run"), h("th", "Substrate lot"), h("th.r", "Printed"), h("th.r", "Spoiled"), h("th.r", "Spoil %"), f.material !== "all" && f.material !== "SUB-100" ? h("th.r", f.material) : null)),
      h("tbody", runs.map((x) => h("tr",
        h("td.mono", x.id), h("td.mono", x.subLot), h("td.r", fmtInt(x.printed)), h("td.r", fmtInt(x.spoiled)), h("td.r", fmtPct(x.spoiled / x.printed, 1)),
        f.material !== "all" && f.material !== "SUB-100" ? h("td.r", fmtNum(x.actual[f.material], 1)) : null))))),
      f.runIds.length > 12 ? h("p.small.muted", `Showing 12 of ${f.runIds.length} runs.`) : null);

    // review
    const history = h("div.stack");
    const drawHistory = () => {
      const recs = store.auditList().filter((a) => a.module === "materials" && a.subject === f.id).reverse();
      history.replaceChildren(...(recs.length ? recs.map((a) => h("div.mat-hist",
        h("span.small.muted", new Date(a.ts).toLocaleString()), h("b", a.decision), a.detail?.assignee ? h("span.small", "→ " + a.detail.assignee) : null,
        a.reason ? h("span.small.t2", "“" + a.reason + "”") : null, h("span.small.muted", a.user))) : [h("span.small.muted", "No review decisions yet.")]));
    };
    drawHistory();
    body.append(h("div.card.mat-review",
      h("div.card-head", h("h3", "Analyst review"), h("span.sub", "the AI does not act — record your decision")),
      h("div.row.wrap",
        h("button.btn.good", { onclick: () => review(f, "confirmed").then(drawHistory) }, icon("check"), "Confirm"),
        h("button.btn", { onclick: () => review(f, "assigned").then(drawHistory) }, icon("user"), "Assign for investigation"),
        h("button.btn.bad", { onclick: () => review(f, "dismissed").then(drawHistory) }, icon("x"), "Dismiss")),
      h("div.divider"), h("h4", "Decision history"), history));
  }

  function drawEvidenceChart(f, el) {
    const ids = new Set(f.runIds);
    const days = [...new Set(f.runIds.map((id) => data.runs.find((x) => x.id === id).day))];
    const lo = Math.max(0, Math.min(...days) - 7), hi = Math.min(DAYS - 1, Math.max(...days) + 7);
    const x = Array.from({ length: hi - lo + 1 }, (_, i) => dayDate(lo + i));
    const agg = (pred, val) => x.map((_, i) => {
      const rs = data.runs.filter((r) => r.day === lo + i && pred(r));
      const den = rs.reduce((a, r) => a + r.printed, 0);
      return rs.length && den ? rs.reduce((a, r) => a + val(r), 0) / den : null;
    });
    if (f.driver === "usage-rate") {
      const m = data.materials.find((mm) => mm.code === f.material);
      lineChart(el, {
        x, height: 200, xFmt: (d) => d.slice(5), tipTitle: (d) => d, yFmt: (v) => v.toFixed(2),
        refs: [{ y: m.stdPer1000, label: `Standard ${m.stdPer1000}` }],
        series: [
          { name: `Line ${f.line}`, values: agg((r) => r.line === f.line, (r) => r.actual[f.material] * 1000), color: "var(--series-2)" },
          { name: "Other lines", values: agg((r) => r.line !== f.line, (r) => r.actual[f.material] * 1000), color: "var(--series-1)" },
        ],
      });
    } else {
      lineChart(el, {
        x, height: 200, yMin: 0, xFmt: (d) => d.slice(5), tipTitle: (d) => d, yFmt: (v) => (v * 100).toFixed(0) + "%",
        series: [
          { name: "Affected runs", values: agg((r) => ids.has(r.id), (r) => r.spoiled), color: "var(--series-2)", fmt: (v) => fmtPct(v, 2) },
          { name: "All other runs", values: agg((r) => !ids.has(r.id), (r) => r.spoiled), color: "var(--series-1)", fmt: (v) => fmtPct(v, 2) },
        ],
      });
    }
  }

  async function review(f, decision) {
    const result = await modal(decision === "confirmed" ? "Confirm finding" : decision === "assigned" ? "Assign for investigation" : "Dismiss finding", (box, done) => {
      const who = h("select", ASSIGNEES.map((a) => h("option", a)));
      const note = h("textarea", { rows: 3, placeholder: decision === "confirmed" ? "Optional note (e.g. corrective action)" : "Reason (required)" });
      const err = h("p.small", { style: { color: "#ff9a9a" }, hidden: true }, "Please enter a reason.");
      box.append(
        h("p.small.t2", f.title),
        decision === "assigned" ? h("label.field", "Assign to", who) : null,
        h("label.field", decision === "confirmed" ? "Note" : "Reason", note), err,
        h("div.row", { style: { justifyContent: "flex-end" } },
          h("button.btn.ghost", { onclick: () => done(null) }, "Cancel"),
          h("button.btn.primary", { onclick: () => {
            if (decision !== "confirmed" && !note.value.trim()) { err.hidden = false; return; }
            done({ reason: note.value.trim(), assignee: decision === "assigned" ? who.value : undefined });
          } }, "Record decision")));
    });
    if (!result) return;
    const all = decisions();
    all[f.id] = { decision, reason: result.reason, assignee: result.assignee, by: store.currentUser(), ts: Date.now() };
    store.set(DECISIONS_KEY, all);
    store.audit({
      module: "materials", action: "finding.reviewed", subject: f.id, decision, reason: result.reason,
      detail: { title: f.title, driver: f.driver, varianceCost: Math.round(f.varianceCost), assignee: result.assignee },
      ai: { ...f.provenance, confidence: +f.confidence.toFixed(2), sources: f.evidence.map((e) => e.id) },
    });
    toast(`Finding ${decision} — recorded in the audit trail.`, decision === "dismissed" ? "" : "good");
    renderFindings();
  }

  // ---- reconciliation ledger
  function renderLedger() {
    const m = data.materials.find((x) => x.code === state.ledgerMat);
    const startDate = dayDate(DAYS - state.days);
    const tx = data.txns.filter((t) => t.material === m.code);
    const sumT = (pred) => tx.filter(pred).reduce((a, t) => a + t.qty, 0);
    const inP = (t) => t.date >= startDate;
    const opening = data.stock[m.code].opening + sumT((t) => !inP(t) && t.type === "Receipt") - sumT((t) => !inP(t) && t.type === "Issue") + sumT((t) => !inP(t) && t.type === "Return");
    const rec = sumT((t) => inP(t) && t.type === "Receipt"), iss = sumT((t) => inP(t) && t.type === "Issue"), ret = sumT((t) => inP(t) && t.type === "Return");
    const closing = opening + rec - iss + ret;
    const balanced = Math.abs(closing - data.stock[m.code].closing) < 1;

    const runs = data.runs.filter((x) => x.day >= DAYS - state.days);
    const rate = m.stdPer1000 / 1000;
    const good = runs.reduce((a, x) => a + x.good, 0), printed = runs.reduce((a, x) => a + x.printed, 0);
    const consumed = runs.reduce((a, x) => a + x.actual[m.code], 0);
    const stdGood = rate * good, allowance = rate * good * data.allowedSpoilage;
    const excessSpoil = rate * (printed - good * (1 + data.allowedSpoilage));
    const rateVar = consumed - rate * printed;
    const plantAtt = attributeVariance(data, { days: state.days, material: m.code });
    const attributed = plantAtt.findings.reduce((a, f) => a + (f.driver === "usage-rate" ? f.varianceQty : f.varianceQty * rate), 0);
    const unexplained = excessSpoil + rateVar - attributed;

    const q = (v) => fmtQty(v, m.unit);
    const maxBar = Math.max(stdGood, 1);
    const bar = (v, color) => h("div.mat-wbar", h("i", { style: { width: Math.max(0.5, (Math.abs(v) / maxBar) * 100) + "%", background: color } }));
    const row = (label, v, opts = {}) => h(`div.mat-lrow${opts.total ? ".total" : ""}`,
      h("span", opts.sign ? h("b.mat-sign", opts.sign) : null, label),
      opts.bar ? bar(v, opts.color) : h("span"),
      h("span.num", q(v)), h("span.num.muted", fmt$(v * m.unitCost)));
    ledgerBody.replaceChildren(
      h("div.row.wrap", { style: { marginBottom: "8px" } },
        h("span.row", h("i.dot", { style: { background: matColor[m.code] } }), h("b", `${m.code}`), h("span.t2", m.name)),
        balanced ? statusPill("good", "Ledger balances") : statusPill("critical", "Out of balance")),
      h("div.mat-ledger",
        h("div.mat-lhead", h("span", "Inventory movement"), h("span"), h("span", "Quantity"), h("span", "Value")),
        row("Opening stock", opening), row("Receipts", rec, { sign: "+" }), row("Issued to production", iss, { sign: "−" }), row("Returns", ret, { sign: "+" }),
        row("Closing stock", closing, { total: true, sign: "=" }),
        h("div.mat-lhead", h("span", "Where the consumed material went"), h("span"), h("span"), h("span")),
        row("Standard for good output", stdGood, { bar: true, color: "var(--series-1)" }),
        row("Allowed spoilage", allowance, { bar: true, color: "var(--series-3)", sign: "+" }),
        row("Explained variance (findings)", attributed, { bar: true, color: "var(--series-2)", sign: "+" }),
        row("Unexplained residual", unexplained, { bar: true, color: "var(--series-7)", sign: "+" }),
        row("Net consumed (issued − returns)", consumed, { total: true, sign: "=" })),
      h("p.small.muted", { style: { marginTop: "8px" } }, `${state.days}-day window from ${startDate}. Every figure traces to inventory transactions and production runs in the source records below.`));
  }

  // ---- forecast
  function renderForecast() {
    fcGroup.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.m === state.fcMat));
    const f = fc.find((x) => x.material === state.fcMat);
    const weeks = data.plan.map((p) => p.week);
    const markers = f.arrivals.map((a, i) => (a ? { x: i, y: Math.max(0, f.stockPlan[i]), r: 5, color: "var(--text)", label: "PO" } : null)).filter(Boolean);
    lineChart(fcChart, {
      x: weeks, height: 250, yMin: 0, label: "Projected stock",
      xFmt: (w) => "W" + w, tipTitle: (w) => `Week ${w} · from ${data.plan[w - 1].start}`,
      yFmt: (v) => (v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "k" : Math.round(v)),
      refs: [{ y: f.safety, label: "Safety stock (2 wks)" }, ...(f.shortfallWeek ? [{ x: f.shortfallWeek - 1, label: "Plan-driven shortfall" }] : [])],
      markers,
      series: [
        { name: "Projected stock — plan-driven", values: f.stockPlan.map((v) => Math.max(0, v)), color: "var(--series-1)", fmt: (v) => fmtQty(v, f.unit) },
        { name: "Projected stock — historical average", values: f.stockNaive.map((v) => Math.max(0, v)), color: "var(--series-2)", dash: true, fmt: (v) => fmtQty(v, f.unit) },
      ],
    });
    fcSide.replaceChildren(
      h("dl.kv",
        h("dt", "Material"), h("dd", `${f.material} · ${f.name}`),
        h("dt", "On hand"), h("dd", fmtQty(f.onHand, f.unit)),
        h("dt", "Weeks of cover"), h("dd", `${f.coverWeeks.toFixed(1)} wks at planned rate`),
        h("dt", "Shortfall (plan)"), h("dd", f.shortfallWeek ? `Week ${f.shortfallWeek}` : "None in 12 weeks"),
        h("dt", "Shortfall (hist. avg)"), h("dd", f.naiveShortfallWeek ? `Week ${f.naiveShortfallWeek}` : h("span.muted", "Not detected")),
        h("dt", "Open POs"), h("dd", data.pos.filter((p) => p.status === "Open" && p.material === f.material).map((p) => h("span.chip", { title: `${fmtQty(p.qty, p.unit)} due ${p.deliveryDate}` }, p.po)))),
      f.rec
        ? h("div.mat-rec",
          h("div.row", statusPill(f.rec.status, f.rec.statusText), advisory("Recommendation")),
          h("p", `Order ${fmtQty(f.rec.qty, f.unit)} from ${f.rec.vendor} by `, h("b", f.rec.orderBy), ` to arrive by ${f.rec.arriveBy} (${f.rec.leadWeeks}-week lead time).`),
          f.naiveShortfallWeek !== f.shortfallWeek ? h("p.small.t2", f.naiveShortfallWeek ? `The historical-average method flags this ${f.naiveShortfallWeek - f.shortfallWeek} week(s) later — after the lead-time window.` : "The historical-average method does not flag this: it ignores the planned production increase from week 5.") : null,
          h("button.btn.sm", { onclick: () => draftReq(f) }, icon("edit"), "Draft requisition"))
        : h("div.mat-rec", statusPill("good", "No action needed"), h("p.small.t2", "Projected stock stays above safety stock for the 12-week horizon.")),
    );
    fcTable.replaceChildren(h("div.table-wrap", h("table.data",
      h("thead", h("tr", h("th", "Material"), h("th.r", "On hand"), h("th.r", "Cover"), h("th", "Short · plan"), h("th", "Short · hist."), h("th", "Order by"), h("th.r", "Qty"), h("th", "Vendor · lead"), h("th", "Status"))),
      h("tbody", [...fc].sort((a, b) => (a.rec ? a.rec.daysLeft : 1e9) - (b.rec ? b.rec.daysLeft : 1e9)).map((x) => h("tr.clickable", { onclick: () => { state.fcMat = x.material; renderForecast(); }, class: x.material === state.fcMat ? "selected" : "" },
        h("td", h("span.row", h("i.dot", { style: { background: matColor[x.material] } }), x.material)),
        h("td.r", fmtQtyShort(x.onHand, x.unit)), h("td.r", x.coverWeeks.toFixed(1) + " wk"),
        h("td", x.shortfallWeek ? `W${x.shortfallWeek}` : "—"), h("td", x.naiveShortfallWeek ? `W${x.naiveShortfallWeek}` : h("span.muted", x.shortfallWeek ? "missed" : "—")),
        h("td", x.rec ? x.rec.orderBy : "—"), h("td.r", x.rec ? fmtQtyShort(x.rec.qty, x.unit) : "—"), h("td", `${x.rec ? x.rec.vendor : data.materials.find((m) => m.code === x.material).vendor} · ${data.materials.find((m) => m.code === x.material).leadWeeks} wk`),
        h("td", x.rec ? statusPill(x.rec.status, x.rec.statusText) : statusPill("good", "Covered"))))))));
  }

  function draftReq(f) {
    store.audit({
      module: "materials", action: "requisition.drafted", subject: f.material, decision: "drafted",
      detail: { qty: f.rec.qty, unit: f.unit, vendor: f.rec.vendor, orderBy: f.rec.orderBy, writeBack: false },
      ai: { ...f.provenance },
    });
    toast(`Requisition draft for ${f.material} recorded. No ERP write-back during the pilot.`, "good");
  }

  // ---- ask the data
  async function runAsk(question) {
    question = question.trim();
    if (!question) return;
    askBtn.disabled = true;
    const pending = h("div.mat-answer", h("div.mat-q", icon("user"), question), h("div.row.muted.small", h("span.spinner"), "Querying local model over ERP / MES exports…"));
    answers.prepend(pending);
    try {
      const res = await ask(question, data, { days: 60 });
      pending.replaceChildren(
        h("div.mat-q", icon("user"), question),
        h("div.mat-a", res.answer.split("\n").map((p) => h("p", p))),
        res.citations.length ? h("div.row.wrap.small", h("span.muted", "Sources:"), res.citations.map((c) => h("span.chip", { title: "Record id in the source exports" }, c))) : null,
        h("div.row.wrap.small.muted", advisory(), `${res.provenance.model} · ${res.provenance.mode}`));
      store.audit({ module: "materials", action: "question.answered", subject: question, ai: { ...res.provenance, sources: res.citations } });
    } catch (err) {
      pending.replaceChildren(h("div.mat-q", icon("user"), question), h("p.small", { style: { color: "#ff9a9a" } }, "Error: " + err.message));
    } finally {
      askBtn.disabled = false;
      askInput.value = "";
    }
  }

  // ---- records
  function recordRows() {
    const startDate = dayDate(DAYS - state.days);
    const f = state;
    const matOk = (code) => f.material === "all" || code === f.material;
    let rows, cols;
    if (state.recTab === "runs") {
      rows = filteredRuns().map((x) => ({ run_id: x.id, date: x.date, line: x.line, shift: x.shift, planned: x.planned, printed: x.printed, good: x.good, spoiled: x.spoiled,
        spoil_pct: +(100 * x.spoiled / x.printed).toFixed(2), substrate_lot: x.subLot,
        ...Object.fromEntries(data.materials.filter((m) => matOk(m.code) && m.code !== "SUB-100").map((m) => [m.code + "_issued", x.actual[m.code]])) }));
    } else if (state.recTab === "pos") {
      rows = data.pos.filter((p) => matOk(p.material)).map((p) => ({ po_number: p.po, vendor: p.vendor, material: p.material, qty: p.qty, unit: p.unit, unit_cost: p.unitCost, order_date: p.orderDate, delivery_date: p.deliveryDate, status: p.status, lot: p.lot }));
    } else if (state.recTab === "lots") {
      rows = data.lots.filter((l) => matOk(l.material)).map((l) => ({ lot_id: l.lot, material: l.material, vendor: l.vendor, po_number: l.po, received: l.received, qty: l.qty, unit: l.unit }));
    } else {
      rows = data.txns.filter((t) => t.date >= startDate && matOk(t.material) && (f.line === "all" || !t.line || t.line === f.line) && (f.shift === "all" || !t.shift || t.shift === f.shift))
        .map((t) => ({ txn_id: t.id, date: t.date, type: t.type, material: t.material, qty: t.qty, unit: t.unit, lot: t.lot, reference: t.ref, line: t.line, shift: t.shift }));
    }
    if (state.recQuery) rows = rows.filter((r) => Object.values(r).some((v) => String(v).toLowerCase().includes(state.recQuery)));
    cols = rows.length ? Object.keys(rows[0]) : [];
    return { rows, cols };
  }

  function renderRecords() {
    recTabs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.k === state.recTab));
    const { rows, cols } = recordRows();
    const PAGE = 15, pages = Math.max(1, Math.ceil(rows.length / PAGE));
    state.recPage = Math.min(state.recPage, pages - 1);
    const pageRows = rows.slice(state.recPage * PAGE, state.recPage * PAGE + PAGE);
    const isNum = (c) => rows.length && typeof rows[0][c] === "number";
    recBody.replaceChildren(
      rows.length ? h("div.table-wrap.mat-records", h("table.data",
        h("thead", h("tr", cols.map((c) => h(isNum(c) ? "th.r" : "th", c)))),
        h("tbody", pageRows.map((r) => h("tr", cols.map((c) => h(isNum(c) ? "td.r" : /id|lot|po|reference/.test(c) ? "td.mono" : "td", typeof r[c] === "number" ? r[c].toLocaleString() : r[c] ?? "")))))))
        : h("div.empty-state", "No records match."),
      h("div.row.mat-pager",
        h("span.small.muted", `${fmtInt(rows.length)} records`),
        h("div.row", { style: { marginLeft: "auto" } },
          h("button.btn.sm", { disabled: state.recPage === 0, onclick: () => { state.recPage--; renderRecords(); } }, "Prev"),
          h("span.small.t2", `Page ${state.recPage + 1} / ${pages}`),
          h("button.btn.sm", { disabled: state.recPage >= pages - 1, onclick: () => { state.recPage++; renderRecords(); } }, "Next"))));
  }

  function exportRecords() {
    const { rows, cols } = recordRows();
    if (!rows.length) return toast("Nothing to export.");
    downloadFile(`bcp-${state.recTab}-${state.days}d.csv`, toCSV(rows, cols), "text/csv");
  }

  function exportFindings() {
    const d = decisions();
    const rows = att.findings.map((f) => ({ id: f.id, title: f.title, driver: f.driver, material: f.material, line: f.line || (f.lines || []).join(" "), shift: f.shift || "", lot: f.lot || "",
      variance_cost: Math.round(f.varianceCost), share: +f.share.toFixed(3), confidence: +f.confidence.toFixed(2), review: d[f.id]?.decision || "pending", model: f.provenance.model, model_version: f.provenance.version }));
    downloadFile(`bcp-variance-findings-${state.days}d.csv`, toCSV(rows), "text/csv");
    store.audit({ module: "materials", action: "findings.exported", subject: `${rows.length} findings`, detail: { filters: { ...state } } });
  }

  // ---- scenario key
  function renderScenarios() {
    const full = attributeVariance(data, { days: 60 });
    scenarioBody.replaceChildren(...data.scenarios.map((s) => {
      const e = s.expect;
      const hit = full.findings.find((f) => f.driver === e.driver && (!e.material || f.material === e.material) && (!e.line || f.line === e.line) && (!e.shift || f.shift === e.shift) && (!e.lot || f.lot === e.lot));
      const inView = hit && att.findings.some((f) => f.id === hit.id);
      return h("div.mat-scn",
        h("div.row.wrap", h("span.chip", s.id), h("b", s.title)),
        h("div.small.t2", s.where), h("div.small.muted", s.truth),
        h("div.row.wrap", hit ? statusPill("good", "Detected") : statusPill("critical", "Not detected"),
          hit ? h("span.small.mono.t2", hit.id) : null,
          hit && !inView ? h("span.small.muted", "(outside current filters)") : null));
    }));
  }

  refresh();
  renderForecast();
}

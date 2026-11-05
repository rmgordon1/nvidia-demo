// Governance & Audit — every AI output and reviewer decision, traceable from
// input to decision. Admin role only.
import { h, icon, kpi, fmtDate, fmtPct, fmtInt, drawer, modal, toast, downloadFile, toCSV, statusPill } from "../ui.js";
import { barChart } from "../charts.js";
import * as store from "../store.js";
import { AI } from "../ai/config.js";

const MODULES = { itsm: "ITSM", vision: "CV inspection", materials: "Materials", system: "System" };
const DECISIONS = ["accepted", "edited", "rejected", "pass", "reject", "reinspect", "confirmed", "dismissed", "assigned"];
const PAGE = 25;

export async function render(root) {
  const state = { module: "", decision: "", role: "", q: "", page: 0 };

  const kpis = h("div.grid.g-4");
  const chart = h("div");
  const tableHost = h("div");
  const pager = h("div.row.small.muted");

  const filters = h("div.field-row.au-filters",
    h("label.field", "Module", sel(state, "module", [["", "All modules"], ...Object.entries(MODULES)], refresh)),
    h("label.field", "Decision", sel(state, "decision", [["", "Any"], ...DECISIONS.map((d) => [d, d])], refresh)),
    h("label.field", "Role", sel(state, "role", [["", "All roles"], ...Object.entries(store.ROLES).map(([k, r]) => [k, r.label])], refresh)),
    h("label.field", "Search", h("input", { type: "search", placeholder: "Subject, action, reason, user…", oninput: (e) => { state.q = e.target.value.toLowerCase(); state.page = 0; refresh(); } })));

  root.append(
    h("div.view-head",
      h("div.titles", h("div.row.wrap", h("h2", "Governance & Audit"), h("span.pill", icon("clock"), "WIP · later development")),
        h("p.lede", "Trace every recommendation, defect flag and variance finding from the AI output to the human decision, with model provenance. Stored locally in this browser for the demo.")),
      h("div.actions",
        h("button.btn", { onclick: () => exportAs("csv") }, icon("download"), "Export CSV"),
        h("button.btn", { onclick: () => exportAs("json") }, icon("download"), "Export JSON"),
        h("button.btn.ghost", { onclick: seed, title: "Add synthetic reviewer activity for demonstration" }, icon("refresh"), "Load sample activity"),
        h("button.btn.bad", { onclick: clearAll }, icon("x"), "Clear log"))),
    kpis,
    h("section.card",
      h("div.card-head", h("h3", "Audit trail"), h("div.end", pager)),
      filters,
      tableHost),
    h("div.grid.g-3",
      h("section.card", h("div.card-head", h("h3", "Decisions by module")), chart),
      h("section.card", h("div.card-head", h("h3", "Model registry"), h("span.sub", `Mode: ${AI.MODE}`)), registry()),
      h("section.card", h("div.card-head", h("h3", "Policy controls")), policies())),
  );

  function filtered() {
    return store.auditList().slice().sort((a, b) => b.ts - a.ts).filter((e) =>
      (!state.module || e.module === state.module) &&
      (!state.decision || e.decision === state.decision) &&
      (!state.role || e.role === state.role) &&
      (!state.q || JSON.stringify([e.subject, e.action, e.reason, e.user, e.decision, e.detail]).toLowerCase().includes(state.q)));
  }

  function refresh() {
    const all = store.auditList();
    const decided = all.filter((e) => e.decision);
    const overrides = decided.filter((e) => ["rejected", "edited", "pass", "dismissed"].includes(e.decision));
    kpis.replaceChildren(
      kpi({ label: "Events recorded", icon: "db", value: fmtInt(all.length), foot: all.length ? `since ${fmtDate(all[0].ts, true)}` : "No events yet" }),
      kpi({ label: "Human decisions", icon: "user", value: fmtInt(decided.length), foot: "accept · reject · edit · disposition" }),
      kpi({ label: "AI overridden by reviewer", icon: "edit", value: decided.length ? fmtPct(overrides.length / decided.length, 0) : "–", foot: "rejected, edited, or passed against an AI flag" }),
      kpi({ label: "AI outputs with provenance", icon: "link", value: all.length ? fmtPct(all.filter((e) => e.ai).length / all.length, 0) : "–", foot: "model + version + sources recorded" }));

    const rows = filtered();
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    state.page = Math.min(state.page, pages - 1);
    const slice = rows.slice(state.page * PAGE, state.page * PAGE + PAGE);
    tableHost.replaceChildren(slice.length
      ? h("div.table-wrap.au-table",
        h("table.data",
          h("thead", h("tr", ["Time", "User", "Module", "Action", "Subject", "Decision", "Reason", "Model"].map((c) => h("th", c)))),
          h("tbody", slice.map((e) => h("tr.clickable", { onclick: () => detail(e) },
            h("td.mono.small", shortTime(e.ts)),
            h("td.small", e.user.replace(" (demo)", "")),
            h("td.small", MODULES[e.module] || e.module),
            h("td", e.action),
            h("td.mono", e.subject || "–"),
            h("td", e.decision ? decisionPill(e.decision) : h("span.muted", "–")),
            h("td.au-reason.small", e.reason || ""),
            h("td.small.muted", e.ai ? e.ai.model : "–"))))))
      : h("div.empty-state", store.auditList().length ? "No events match these filters." : "The audit log is empty. Decisions made in the ITSM, CV and Materials modules are recorded here — or load sample activity."));
    pager.replaceChildren(
      `${fmtInt(rows.length)} events`,
      h("button.btn.sm.ghost", { disabled: state.page === 0, onclick: () => { state.page--; refresh(); } }, "‹ Prev"),
      `${state.page + 1} / ${pages}`,
      h("button.btn.sm.ghost", { disabled: state.page >= pages - 1, onclick: () => { state.page++; refresh(); } }, "Next ›"));

    const mods = Object.keys(MODULES).filter((m) => m !== "system");
    const cats = [["Accepted / confirmed", ["accepted", "reject", "confirmed"]], ["Edited / re-inspect / assigned", ["edited", "reinspect", "assigned"]], ["Rejected / overridden", ["rejected", "pass", "dismissed"]]];
    chart.replaceChildren();
    barChart(chart, {
      labels: mods.map((m) => MODULES[m]), stacked: true, height: 190,
      series: cats.map(([name, ds], k) => ({ name, color: ["var(--series-1)", "var(--series-4)", "var(--series-8)"][k], values: mods.map((m) => decided.filter((e) => e.module === m && ds.includes(e.decision)).length) })),
      yFmt: (v) => fmtInt(v),
    });
  }

  function detail(e) {
    const d = drawer(e.action, { subtitle: `${e.id} · ${fmtDate(e.ts, true)}` });
    d.body.append(
      h("dl.kv",
        h("dt", "User"), h("dd", `${e.user} (${store.ROLES[e.role]?.label || e.role})`),
        h("dt", "Module"), h("dd", MODULES[e.module] || e.module),
        h("dt", "Subject"), h("dd.mono", e.subject || "–"),
        h("dt", "Decision"), h("dd", e.decision ? decisionPill(e.decision) : "–"),
        h("dt", "Reason"), h("dd", e.reason || "–")),
      e.ai ? h("div.stack", h("h4", "AI provenance"), h("dl.kv",
        Object.entries(e.ai).flatMap(([k, v]) => [h("dt", k), h("dd.mono.small", typeof v === "object" ? JSON.stringify(v) : String(v))]))) : null,
      e.detail ? h("div.stack", h("h4", "Detail"), h("pre.au-json", JSON.stringify(e.detail, null, 2))) : null);
  }

  function exportAs(kind) {
    const rows = filtered();
    if (!rows.length) return toast("Nothing to export.");
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    if (kind === "json") downloadFile(`bep-audit-${stamp}.json`, JSON.stringify(rows, null, 2), "application/json");
    else downloadFile(`bep-audit-${stamp}.csv`, toCSV(rows.map((e) => ({ ...e, ts: new Date(e.ts).toISOString() })),
      ["id", "ts", "user", "role", "module", "action", "subject", "decision", "reason", "ai", "detail"]), "text/csv");
    toast(`Exported ${rows.length} events.`, "good");
  }

  async function clearAll() {
    const ok = await modal("Clear the audit log?", (box, done) => box.append(
      h("p.t2", "This removes every recorded event from this browser. Export first if you need to keep them."),
      h("div.row", { style: { justifyContent: "flex-end" } },
        h("button.btn.ghost", { onclick: () => done(false) }, "Cancel"),
        h("button.btn.bad", { onclick: () => done(true) }, "Clear log"))));
    if (!ok) return;
    store.clearAudit();
    store.audit({ module: "system", action: "audit.cleared", subject: "audit-log" });
    toast("Audit log cleared.");
  }

  const onAudit = () => refresh();
  window.addEventListener("audit", onAudit);
  refresh();
  return () => window.removeEventListener("audit", onAudit);
}

// ---------------------------------------------------------------------------
function shortTime(ts) {
  const d = new Date(ts);
  return d.toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function sel(state, key, options, onChange) {
  return h("select", { onchange: (e) => { state[key] = e.target.value; state.page = 0; onChange(); } },
    options.map(([v, l]) => h("option", { value: v }, l)));
}

function decisionPill(d) {
  const level = ["accepted", "confirmed", "reject"].includes(d) ? "good" : ["rejected", "dismissed", "pass"].includes(d) ? "critical" : "warning";
  return h(`span.pill.${level}`, d);
}

function registry() {
  return h("div.table-wrap", h("table.data",
    h("thead", h("tr", h("th", "Model"), h("th.r", "Version"))),
    h("tbody", Object.values(AI.models).map((m) => h("tr",
      h("td.wrap", { style: { minWidth: "0" } }, h("div.small", m.name), h("div.small.muted", m.runtime)),
      h("td.r.mono.small", m.version))))));
}

function policies() {
  const items = [
    ["good", "External API calls", "Blocked — AI layer only calls the local /api/ route"],
    ["good", "Autonomous actions", "None — sheet disposition, ticket fixes and variance actions require a person"],
    ["good", "Write-back to ServiceNow / ERP / line systems", "Disabled for the pilot"],
    ["good", "Provenance on AI output", "Model, version and source records attached to each decision"],
    ["warning", "Identity", "Demo role switcher only — map to agency SSO / PIV roles in discovery"],
    ["warning", "Retention", "Browser-local storage — move to an accredited store before pilot"],
  ];
  return h("ul.au-policies", items.map(([lvl, t, d]) => h("li", statusPill(lvl, lvl === "good" ? "Enforced" : "To do"), h("div", h("b", t), h("p.small.muted", d)))));
}

// Synthetic activity so the log is demoable before anyone has clicked through.
function seed() {
  const r = Math.random;
  const pick = (a) => a[Math.floor(r() * a.length)];
  const prov = (key) => ({ model: AI.models[key].name, version: AI.models[key].version, mode: "demo-seed", confidence: +(0.55 + r() * 0.4).toFixed(2) });
  const events = [];
  for (let i = 0; i < 18; i++) {
    const decision = pick(["accepted", "accepted", "accepted", "rejected", "edited"]);
    events.push({ role: "agent", module: "itsm", action: "recommendation." + decision, decision,
      subject: "INC00000087" + String(3400 + Math.floor(r() * 600)),
      reason: decision === "accepted" ? "" : pick(["Steps already tried by caller", "Wrong CI suggested", "Reordered steps"]),
      ai: { ...prov("llm"), sources: ["INC000000873417", "INC000000873521"] } });
  }
  for (let i = 0; i < 16; i++) {
    const decision = pick(["reject", "reject", "reject", "pass", "reinspect"]);
    events.push({ role: "qa", module: "vision", action: "sheet.dispositioned", decision,
      subject: "SHT-" + String(1 + Math.floor(r() * 160)).padStart(5, "0"),
      reason: decision === "pass" ? "Within plate tolerance, not a defect" : "", ai: prov("anomaly") });
  }
  for (let i = 0; i < 6; i++) {
    const decision = pick(["confirmed", "confirmed", "assigned", "dismissed"]);
    events.push({ role: "analyst", module: "materials", action: "finding.reviewed", decision,
      subject: "VAR-" + String(1 + i).padStart(3, "0"),
      reason: decision === "assigned" ? "Assigned to Line 2 supervisor" : "", ai: prov("variance") });
  }
  const current = store.role();
  const now = Date.now();
  events.forEach((e) => (e.ts = now - Math.floor(r() * 3 * 86400000)));
  for (const { role, ...e } of events.sort((a, b) => a.ts - b.ts)) {
    store.set("role", role); // attribute each event to the role that would have made it
    store.audit({ ...e, detail: { seeded: true } });
  }
  store.set("role", current);
  toast(`Added ${events.length} sample events.`, "good");
}

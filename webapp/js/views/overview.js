// Mission Overview — headline metrics for all three use cases.
import { h, icon, kpi, delta, fmtDur, fmtPct, fmtInt, timeAgo, statusPill } from "../ui.js";
import { lineChart, hbarChart, SERIES } from "../charts.js";
import * as store from "../store.js";
import { loadTickets, loadCV } from "../data/loaders.js";
import { aiMetrics, rulesMetrics, getThreshold } from "../data/cv-metrics.js";
import { CUTOVER as ITSM_CUTOVER } from "../ai/itsm.js";

// ServiceNow cutover month (set in js/ai/itsm.js). History before it is the Helix archive.
const CUTOVER = ITSM_CUTOVER.date.slice(0, 7);
const PROJECT_MONTHS = 8;

export async function render(root) {
  const [tk, cv, mat] = await Promise.all([
    loadTickets(),
    loadCV().catch(() => null),
    import("../data/materials.js").then((m) => m.materialsSummary()).catch(() => null),
  ]);
  const tickets = tk.tickets;
  const mttr = monthlyMTTR(tickets);
  const allowed = store.ROLES[store.role()].views;

  // ---- KPIs
  const last = mttrWindow(tickets, 180, 0), prev = mttrWindow(tickets, 360, 180);
  const itsmAudit = store.auditList().filter((e) => e.module === "itsm");
  const decided = itsmAudit.filter((e) => ["accepted", "rejected", "edited"].includes(e.decision));
  const accRate = decided.length ? decided.filter((e) => e.decision !== "rejected").length / decided.length : null;
  const ai = cv ? aiMetrics(cv.items, getThreshold()) : null;
  const rules = cv ? rulesMetrics(cv.items) : null;

  root.append(
    h("div.view-head",
      h("div.titles",
        h("h2", "Mission Overview"),
        h("p.lede", "Local AI on agency-owned hardware, over the agency's own data. Three use cases share one pattern: retrieve evidence, recommend, and let a human decide.")),
      h("div.actions", h("span.pill.info", icon("cpu"), "All inference on-prem · Dell Pro Max GB10"))),

    h("section.card.ov-mission",
      h("div.ov-mission-mark", icon("star")),
      h("div.stack", { style: { gap: "6px" } },
        h("h4", "Mission outcome · primary"),
        h("p.ov-quote", "Give IT support agents an AI-assisted “next best step” recommendation drawn from historical ticket resolutions, so resolution time does not regress after the move to ServiceNow — measured by MTTR before and after the migration."),
        h("p.small.muted", "Owner: IT service desk / operations manager overseeing the ITSM migration"))),

    h("div.grid.g-4",
      kpi({ label: "MTTR · last 180 days", icon: "clock", value: fmtDur(last.mttr), foot: [delta(prev.mttr ? (last.mttr - prev.mttr) / prev.mttr : null, { goodWhen: "down" }), "vs prior 180 days", h("span.muted", `· ${last.n} tickets`)] }),
      kpi({ label: "Recommendation acceptance", icon: "check", value: accRate == null ? "–" : fmtPct(accRate, 0), foot: decided.length ? `${decided.length} agent decisions logged` : "No agent decisions yet — try the ITSM module" }),
      kpi({ label: "False-reject rate · AI vs rules", icon: "eye", value: ai ? fmtPct(ai.frr) : "–", foot: ai ? [delta(ai.frr - rules.frr, { goodWhen: "down", fmt: (v) => (Math.abs(v) * 100).toFixed(1) + " pts" }), `vs rules ${fmtPct(rules.frr)}`] : "CV samples not built" }),
      kpi({ label: "Material variance explained", icon: "layers", value: mat ? fmtPct(mat.explainedPct / (mat.explainedPct > 1 ? 100 : 1), 0) : "–", foot: mat ? `Reconciled in ~${mat.reconMinutes} min vs days manually` : "Next-phase module" }),
    ),

    h("div.grid.g-main-side",
      h("section.card",
        h("div.card-head", h("h3", "Mean time to resolve, by month"),
          h("span.sub", `Synthetic ${tk.platform.split(" (")[0]} archive · ${fmtInt(tickets.length)} incidents`),
          h("div.end", h("span.pill", "Projection after cutover is illustrative"))),
        h("div#ov-mttr"),
        h("p.small.muted", { style: { marginTop: "8px" } },
          "Hours from Reported to Last Resolved, from the migrated Helix archive. Dashed lines project two post-cutover outcomes: the typical productivity dip after a platform migration, and the target with next-best-step assistance. Post-cutover actuals will come from ServiceNow. Cutover date: ", h("code", "CUTOVER"), " in js/ai/itsm.js.")),
      h("section.card",
        h("div.card-head", h("h3", "Where tickets land"), h("span.sub", "Assigned group, all incidents")),
        h("div#ov-groups"))),

    h("h4", { style: { marginTop: "4px" } }, "Use cases"),
    h("div.grid.g-3",
      useCase({
        key: "itsm", allowed, stage: ["info", "Pilot"], title: "ITSM next best step", icon: "ticket",
        owner: "IT service desk / operations manager",
        metric: "MTTR before (Helix) vs after (ServiceNow) cutover",
        stats: [["Tickets indexed", fmtInt(tickets.length)], ["Closed w/ resolution", fmtInt(tickets.filter((t) => t.resolution).length)], ["Agent decisions", fmtInt(decided.length)]],
      }),
      useCase({
        key: "vision", allowed, stage: ["info", "Proof of concept"], title: "CV defect detection", icon: "eye",
        owner: "Production QA / inspection manager",
        metric: "Recall ↑, escape rate ↓, false-reject rate ↓ vs camera baseline",
        stats: ai ? [["Recall AI / rules", `${fmtPct(ai.recall, 0)} / ${fmtPct(rules.recall, 0)}`], ["False-reject AI / rules", `${fmtPct(ai.frr, 0)} / ${fmtPct(rules.frr, 0)}`], ["Labeled test sheets", fmtInt(cv.items.length)]] : [],
      }),
      useCase({
        key: "materials", allowed, stage: ["", "WIP · later development"], title: "Raw material to output", icon: "layers",
        owner: "Manufacturing ops lead + material accountability lead",
        metric: "% of material variance auto-explained; reconciliation days → minutes",
        stats: mat ? [["Yield", fmtPct(mat.yieldPct / (mat.yieldPct > 1 ? 100 : 1))], ["Open findings", fmtInt(mat.findings)], ["Next shortfall", mat.nextShortfall ? `${mat.nextShortfall.material} · ${mat.nextShortfall.weeksUntil} wk` : "None"]] : [],
      })),

    h("div.grid.g-2",
      h("section.card",
        h("div.card-head", h("h3", "Recent reviewer activity"), h("span.sub", "From the audit trail"),
          allowed.includes("audit") ? h("div.end", h("a.btn.sm", { href: "#/audit" }, "Open audit log")) : null),
        activity()),
      h("section.card",
        h("div.card-head", h("h3", "Guardrails in effect")),
        h("ul.ov-guard",
          guard("good", "Models and data stay inside the agency boundary", "All inference runs locally; no external API calls."),
          guard("good", "Human decision required", "Ticket fixes, sheet disposition and variance actions are taken by people."),
          guard("good", "No production write-back during pilot", "ServiceNow, ERP and line systems are read-only stand-ins."),
          guard("good", "Synthetic / public data only", "No CUI, PII, production records or currency imagery."),
          guard("warning", "Models are placeholders", "Swap stand-ins in js/ai/*.js for the locally trained models.")))),
  );

  // ---- charts
  const n = mttr.months.length;
  const proj = [];
  const [cy, cm] = CUTOVER.split("-").map(Number);
  let y = +mttr.months[n - 1].slice(0, 4), m = +mttr.months[n - 1].slice(5);
  while (proj.length < 60) {
    m++; if (m > 12) { m = 1; y++; }
    const key = `${y}-${String(m).padStart(2, "0")}`;
    proj.push(key);
    if (y * 12 + m >= cy * 12 + cm + PROJECT_MONTHS) break;
  }
  const months = [...mttr.months, ...proj];
  const base = avg(mttr.values.slice(-6).filter((v) => v != null));
  const cutIdx = months.indexOf(CUTOVER);
  const dip = months.map((_, i) => i < n - 1 ? null : i < cutIdx ? base : base * (1 + 0.45 * Math.exp(-(i - cutIdx) / 4)));
  const assist = months.map((_, i) => i < n - 1 ? null : i < cutIdx ? base : base * (1 - 0.18 * (1 - Math.exp(-(i - cutIdx) / 2.5))));
  const actual = [...mttr.values.map((v) => v ?? null), ...proj.map(() => null)];
  lineChart(root.querySelector("#ov-mttr"), {
    x: months, height: 250,
    xFmt: (v) => monthLabel(v), tipTitle: (v) => monthLabel(v, true),
    yFmt: (v) => fmtDur(v),
    series: [
      { name: "Actual MTTR", values: actual, color: SERIES[0], points: true, area: true },
      { name: "Typical migration dip (projected)", values: dip, color: SERIES[1], dash: true },
      { name: "With next-best-step assist (target)", values: assist, color: SERIES[2], dash: true },
    ],
    refs: cutIdx >= 0 ? [{ x: cutIdx, label: "ServiceNow cutover (complete)" }] : [],
  });

  const byGroup = {};
  for (const t of tickets) byGroup[t.group || "Unassigned"] = (byGroup[t.group || "Unassigned"] || 0) + 1;
  const groups = Object.entries(byGroup).sort((a, b) => b[1] - a[1]);
  const top = groups.slice(0, 9);
  const other = groups.slice(9).reduce((a, g) => a + g[1], 0);
  if (other) top.push(["Other groups", other]);
  hbarChart(root.querySelector("#ov-groups"), {
    items: top.map(([label, value]) => ({ label, value })), valueName: "Tickets", fmt: fmtInt, rowH: 27,
  });

  const onAudit = () => { const host = root.querySelector(".ov-activity"); host?.replaceWith(activity()); };
  window.addEventListener("audit", onAudit);
  return () => window.removeEventListener("audit", onAudit);
}

// ---------------------------------------------------------------------------
function useCase({ key, allowed, stage, title, icon: ic, owner, metric, stats }) {
  const can = allowed.includes(key);
  return h("section.card.ov-uc",
    h("div.row", h("span.ov-uc-icon", icon(ic)), stage[0] ? statusPill(stage[0], stage[1]) : h("span.pill", stage[1])),
    h("h3", title),
    h("dl.kv.small", h("dt", "Owner"), h("dd", owner), h("dt", "Win metric"), h("dd", metric)),
    h("div.ov-uc-stats", stats.map(([k, v]) => h("div", h("span.muted.small", k), h("b.num", v)))),
    can ? h("a.btn.sm", { href: "#/" + key }, "Open module", icon("chevron"))
        : h("span.small.muted", icon("lock"), " Not available for the current role"));
}

function guard(level, title, text) {
  return h("li", statusPill(level, level === "good" ? "Enforced" : "Note"), h("div", h("b", title), h("p.small.muted", text)));
}

function activity() {
  const LABEL = { itsm: "ITSM", vision: "CV", materials: "Materials", system: "System" };
  const list = store.auditList().slice(-7).reverse();
  if (!list.length) return h("div.ov-activity.empty-state", "No reviewer decisions yet. Decisions made in each module appear here and in the audit log.");
  return h("ul.ov-activity",
    list.map((e) => h("li",
      h("span.chip", LABEL[e.module] || e.module),
      h("div", h("div", h("b", e.action.replace(/\./g, " ")), " · ", h("span.mono.small", e.subject || "")),
        h("div.small.muted", `${e.user} · ${timeAgo(e.ts)}${e.decision ? " · " + e.decision : ""}`)))));
}

function monthlyMTTR(tickets) {
  const by = {};
  for (const t of tickets) {
    if (!t.reported || !t.resolved || t.resolved < t.reported) continue;
    const k = new Date(t.reported * 1000).toISOString().slice(0, 7);
    (by[k] ||= []).push((t.resolved - t.reported) / 3600);
  }
  const keys = Object.keys(by).sort();
  const months = [];
  let [y, m] = keys[0].split("-").map(Number);
  const [ly, lm] = keys[keys.length - 1].split("-").map(Number);
  while (y * 12 + m <= ly * 12 + lm) {
    months.push(`${y}-${String(m).padStart(2, "0")}`);
    m++; if (m > 12) { m = 1; y++; }
  }
  return { months, values: months.map((k) => (by[k] ? avg(by[k]) : null)) };
}

function mttrWindow(tickets, fromDaysAgo, toDaysAgo) {
  const end = Math.max(...tickets.map((t) => t.reported || 0));
  const lo = end - fromDaysAgo * 86400, hi = end - toDaysAgo * 86400;
  const xs = tickets.filter((t) => t.reported > lo && t.reported <= hi && t.resolved >= t.reported).map((t) => (t.resolved - t.reported) / 3600);
  return { mttr: xs.length ? avg(xs) : null, n: xs.length };
}

const avg = (xs) => xs.reduce((a, b) => a + b, 0) / (xs.length || 1);
function monthLabel(k, long = false) {
  const [y, m] = k.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1, 1));
  return d.toLocaleString(undefined, { month: "short", year: long ? "numeric" : "2-digit", timeZone: "UTC" });
}

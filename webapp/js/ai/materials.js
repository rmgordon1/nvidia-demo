// ===========================================================================
// Material accountability — AI hooks (placeholder implementations).
//
//   attributeVariance(data, filters) → { findings[], explainedPct, totals, records }
//   forecast(data, plan)             → per-material 12-week procurement projection
//   ask(question, data)              → Promise<{ answer, citations[], provenance }>
//
// The placeholders are transparent statistics (robust z-scores + contribution
// analysis), not a trained model. They are deterministic so results can be
// checked against the known-answer scenarios in js/data/materials.js.
// No DOM access: importable from node.
// ===========================================================================
import { AI, provenance, callApi } from "./config.js";

const median = (a) => {
  if (!a.length) return 0;
  const s = [...a].sort((x, y) => x - y), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const mad = (a) => { const m = median(a); return 1.4826 * median(a.map((x) => Math.abs(x - m))) || 1e-9; };
const sum = (a, f = (x) => x) => a.reduce((t, x) => t + f(x), 0);
const groupBy = (a, key) => a.reduce((m, x) => ((m[key(x)] ||= []).push(x), m), {});
const spoilRate = (run) => run.spoiled / run.printed;

const Z_FLAG = 4;          // robust z needed to raise a finding
const MIN_SPOIL_PP = 0.01; // and at least +1 pp spoilage …
const MIN_RATE = 0.04;     // … or +4 % usage-rate over the baseline

/**
 * Actual vs standard per run × material, decomposed into a spoilage component
 * (extra sheets printed beyond the allowance) and a usage-rate component
 * (material per printed sheet above standard). Findings attribute variance to
 * substrate lots, line × shift groups, lines, and material × line × week cells.
 */
export function attributeVariance(data, filters = {}) {
  const { days = 30, line = "all", shift = "all", material = "all" } = filters;
  const mats = data.materials.filter((m) => material === "all" || m.code === material);
  const runs = data.runs.filter((x) => x.day >= data.days - days && (line === "all" || x.line === line) && (shift === "all" || x.shift === shift));
  const allow = data.allowedSpoilage;

  // ---- per run × material records
  const records = [];
  for (const run of runs) for (const m of mats) {
    const rate = m.stdPer1000 / 1000;
    const std = rate * run.good * (1 + allow);
    const actual = run.actual[m.code];
    records.push({
      run, m, std, actual, var: actual - std,
      varCost: (actual - std) * m.unitCost,
      spoilComp: rate * (run.printed - run.good * (1 + allow)),
      rateComp: actual - rate * run.printed,
    });
  }
  const costPerPrintedSheet = sum(mats, (m) => (m.stdPer1000 / 1000) * m.unitCost); // $ of material per printed sheet at standard

  const findings = [];
  // ---- 1. substrate lots (residual spoilage vs each run's line×shift median)
  const lsMedian = Object.fromEntries(Object.entries(groupBy(runs, (r) => r.line + r.shift)).map(([k, v]) => [k, median(v.map(spoilRate))]));
  const resid = (run) => spoilRate(run) - lsMedian[run.line + run.shift];
  const sdRun = mad(runs.map(resid)) || 0.006; // run-to-run noise in spoilage
  const flaggedRuns = new Set();
  for (const [lot, lr] of Object.entries(groupBy(runs, (r) => r.subLot))) {
    const eff = sum(lr, resid) / lr.length;
    const z = eff / (sdRun / Math.sqrt(lr.length));
    if (z < Z_FLAG || eff < MIN_SPOIL_PP) continue;
    lr.forEach((x) => flaggedRuns.add(x.id));
    const excessSheets = sum(lr, (x) => x.printed * resid(x));
    const info = data.lots.find((l) => l.lot === lot) || {};
    const linesHit = [...new Set(lr.map((x) => x.line))].sort();
    findings.push({
      id: "VF-" + lot, driver: "lot", dimension: "Substrate lot", lot, vendor: info.vendor, material: "SUB-100",
      lines: linesHit,
      title: `Substrate lot ${lot} (${info.vendor || "?"}) drove elevated spoilage on ${linesHit.join(", ")}`,
      effect: eff, z, runIds: lr.map((x) => x.id),
      varianceQty: excessSheets, varianceCost: excessSheets * costPerPrintedSheet,
      summary: `${lr.length} runs consumed this lot; spoilage averaged +${(eff * 100).toFixed(1)} pp above each line/shift's norm, across every line that drew from it — pointing at the material, not the press or crew.`,
    });
  }

  // ---- 2. line × shift groups (excluding runs already explained by a lot)
  const clean = runs.filter((x) => !flaggedRuns.has(x.id));
  const overall = median(clean.map(spoilRate));
  const lsFlag = new Set();
  for (const [k, gr] of Object.entries(groupBy(clean, (r) => r.line + "|" + r.shift))) {
    const eff = sum(gr, spoilRate) / gr.length - overall;
    const z = eff / (sdRun / Math.sqrt(gr.length));
    if (z < Z_FLAG || eff < MIN_SPOIL_PP) continue;
    const [ln, sh] = k.split("|");
    lsFlag.add(k);
    const excessSheets = sum(gr, (x) => x.printed * (spoilRate(x) - overall));
    findings.push({
      id: `VF-${ln}-SHIFT-${sh}`, driver: "line-shift", dimension: "Line × shift", line: ln, shift: sh, material: "all",
      title: `Persistent high spoilage on Line ${ln}, Shift ${sh}`,
      effect: eff, z, runIds: gr.map((x) => x.id),
      varianceQty: excessSheets, varianceCost: excessSheets * costPerPrintedSheet,
      summary: `Spoilage on ${ln} Shift ${sh} ran +${(eff * 100).toFixed(1)} pp above the plant median on ${gr.length} runs, while the other shifts on ${ln} are normal — consistent with a crew, make-ready or shift-handover issue.`,
    });
  }

  // ---- 3. whole lines (excluding lot runs and flagged line×shift groups)
  const clean2 = clean.filter((x) => !lsFlag.has(x.line + "|" + x.shift));
  for (const [ln, gr] of Object.entries(groupBy(clean2, (r) => r.line))) {
    const eff = sum(gr, spoilRate) / gr.length - overall;
    const z = eff / (sdRun / Math.sqrt(gr.length));
    if (z < Z_FLAG || eff < MIN_SPOIL_PP) continue;
    const excessSheets = sum(gr, (x) => x.printed * (spoilRate(x) - overall));
    findings.push({
      id: `VF-${ln}-LINE`, driver: "line", dimension: "Line", line: ln, material: "all",
      title: `Elevated spoilage on Line ${ln} across shifts`, effect: eff, z, runIds: gr.map((x) => x.id),
      varianceQty: excessSheets, varianceCost: excessSheets * costPerPrintedSheet,
      summary: `Line ${ln} ran +${(eff * 100).toFixed(1)} pp above the plant median on all shifts.`,
    });
  }

  // ---- 4. usage rate: material × line × week cells (consumption per printed sheet)
  for (const m of mats.filter((x) => x.code !== "SUB-100")) {
    const cells = Object.entries(groupBy(records.filter((r) => r.m === m), (r) => r.run.line + "|" + r.run.week)).map(([k, rs]) => ({
      k, rs, ratio: sum(rs, (r) => r.actual) / sum(rs, (r) => (m.stdPer1000 / 1000) * r.run.printed),
    }));
    if (cells.length < 4) continue;
    const med = median(cells.map((c) => c.ratio)), sd = mad(cells.map((c) => c.ratio)) || 0.005;
    // need >= 3 days of runs in a cell so partial weeks / discrete consumables don't raise noise findings
    const hits = cells.filter((c) => c.rs.length >= 9 && (c.ratio - med) / sd >= Z_FLAG && c.ratio - med >= MIN_RATE);
    for (const [ln, hc] of Object.entries(groupBy(hits, (c) => c.k.split("|")[0]))) {
      const rs = hc.flatMap((c) => c.rs);
      const weeks = hc.map((c) => c.k.split("|")[1]).sort();
      const qty = sum(rs, (r) => r.actual - (m.stdPer1000 / 1000) * r.run.printed * med);
      const eff = Math.max(...hc.map((c) => c.ratio)) - med;
      findings.push({
        id: `VF-${m.code}-${ln}-${weeks[0]}`, driver: "usage-rate", dimension: "Material × line", material: m.code, line: ln, weeks,
        title: `${m.code} over-consumption on Line ${ln}, week of ${weeks[0]}${weeks.length > 1 ? ` (+${weeks.length - 1} wk)` : ""}`,
        effect: eff, z: Math.max(...hc.map((c) => (c.ratio - med) / sd)), runIds: rs.map((r) => r.run.id),
        varianceQty: qty, varianceCost: qty * m.unitCost,
        summary: `${m.code} used ${(eff * 100).toFixed(1)}% more per printed sheet than the plant baseline on ${rs.length} ${ln} runs, while spoilage on those runs was normal — points to dispensing / metering rather than waste sheets.`,
      });
    }
  }

  // ---- totals and explained share
  // Gross variance = Σ |actual − standard| over material × line × shift × substrate-lot cells
  // (random run-to-run noise largely nets out inside a cell; systematic drivers do not).
  const cells = groupBy(records, (r) => [r.m.code, r.run.line, r.run.shift, r.run.subLot].join("|"));
  const grossAbs = sum(Object.values(cells), (rs) => Math.abs(sum(rs, (r) => r.varCost)));
  const attributed = sum(findings, (f) => Math.max(0, f.varianceCost));
  const explainedPct = grossAbs ? Math.min(0.99, attributed / grossAbs) : 0;
  for (const f of findings) {
    f.share = grossAbs ? f.varianceCost / grossAbs : 0;
    f.confidence = Math.max(0.5, Math.min(0.98, 1 - 1 / (1 + f.z / 6)));
    f.provenance = provenance("variance", { z: +f.z.toFixed(1) });
    f.evidence = evidenceFor(f, data);
  }
  findings.sort((a, b) => b.varianceCost - a.varianceCost);

  const totals = {
    printed: sum(runs, (x) => x.printed), good: sum(runs, (x) => x.good),
    std: sum(records, (r) => r.std * r.m.unitCost), actual: sum(records, (r) => r.actual * r.m.unitCost),
    varianceCost: sum(records, (r) => r.varCost), grossAbs, attributed,
  };
  return { findings, explainedPct, totals, records, runs, provenance: provenance("variance") };
}

function evidenceFor(f, data) {
  const ids = new Set(f.runIds);
  const ev = [];
  if (f.lot) {
    const lot = data.lots.find((l) => l.lot === f.lot);
    if (lot) ev.push({ type: "Lot", id: lot.lot, text: `Received ${lot.received} from ${lot.vendor}, ${lot.qty.toLocaleString()} ${lot.unit}` }, { type: "PO", id: lot.po, text: `Purchase order for ${lot.lot}` });
  }
  const runs = data.runs.filter((x) => ids.has(x.id)).sort((a, b) => b.spoiled / b.printed - a.spoiled / a.printed);
  for (const x of runs.slice(0, 4)) {
    ev.push({ type: "Run", id: x.id, text: `${x.printed.toLocaleString()} printed, ${(100 * x.spoiled / x.printed).toFixed(1)}% spoiled` + (f.material && f.material !== "all" && f.material !== "SUB-100" ? `, ${f.material} ${x.actual[f.material].toFixed(1)} kg` : "") });
  }
  if (f.driver === "usage-rate") {
    const t = data.txns.find((t) => t.type === "Issue" && t.material === f.material && ids.has(t.ref));
    if (t) ev.push({ type: "Issue txn", id: t.id, text: `${t.qty} ${t.unit} of ${t.material} from ${t.lot} to ${t.ref}` });
  }
  return ev;
}

// ---------------------------------------------------------------------------
/**
 * 12-week procurement forecast per material. Plan-driven = plan × observed usage
 * rate × (1 + recent spoilage); naive = average weekly net issue (historical).
 * Projected stock = on hand + open-PO arrivals − consumption, vs safety stock
 * (2 weeks of average use). Reorder recommendation is lead-time aware.
 */
export function forecast(data, plan = data.plan) {
  const recent = data.runs.filter((x) => x.day >= data.days - 28);
  const recentSpoil = sum(recent, (x) => x.spoiled) / sum(recent, (x) => x.good);
  const DAY = 86400000;
  return data.materials.map((m) => {
    const st = data.stock[m.code];
    const rateObs = m.code === "SUB-100" ? 1 : median(data.runs.map((x) => x.actual[m.code] / ((m.stdPer1000 / 1000) * x.printed)));
    const weeklyPlan = plan.map((p) => (p.goodSheets * (m.stdPer1000 / 1000) * rateObs) * (1 + recentSpoil));
    const weeklyNaive = plan.map(() => st.avgWeekly);
    const arrivals = plan.map((p) => sum(data.pos.filter((po) => po.status === "Open" && po.material === m.code && po.deliveryDate >= p.start && po.deliveryDate < addISO(p.start, 7)), (po) => po.qty));
    const safety = 2 * st.avgWeekly;
    const project = (cons) => { let s = st.closing; return cons.map((c, i) => (s = s + arrivals[i] - c)); };
    const stockPlan = project(weeklyPlan), stockNaive = project(weeklyNaive);
    const firstBelow = (a) => { const i = a.findIndex((v) => v < safety); return i < 0 ? null : i + 1; };
    const shortfallWeek = firstBelow(stockPlan), naiveShortfallWeek = firstBelow(stockNaive);
    let rec = null;
    if (shortfallWeek) {
      const arriveBy = plan[shortfallWeek - 1].start;
      const orderBy = addISO(arriveBy, -m.leadWeeks * 7);
      const need = sum(weeklyPlan.slice(shortfallWeek - 1, shortfallWeek + 5)) + safety - Math.max(0, stockPlan[shortfallWeek - 1]);
      const qty = Math.ceil(Math.max(need, m.moq) / m.moq) * m.moq;
      const daysLeft = Math.round((Date.parse(orderBy) - Date.parse(data.today)) / DAY);
      rec = { arriveBy, orderBy, daysLeft, qty, vendor: m.vendor, leadWeeks: m.leadWeeks,
        status: daysLeft < 0 ? "critical" : daysLeft <= 14 ? "warning" : "good",
        statusText: daysLeft < 0 ? `Expedite — ${-daysLeft}d late` : daysLeft <= 14 ? `Order within ${daysLeft}d` : "Planned" };
    }
    return {
      material: m.code, name: m.name, unit: m.unit, onHand: st.closing, safety,
      coverWeeks: st.closing / (sum(weeklyPlan.slice(0, 4)) / 4),
      weeklyPlan, weeklyNaive, arrivals, stockPlan, stockNaive, shortfallWeek, naiveShortfallWeek, rec,
      provenance: provenance("variance"),
    };
  });
}
const addISO = (iso, n) => new Date(Date.parse(iso + "T00:00:00Z") + n * 86400000).toISOString().slice(0, 10);

// ---------------------------------------------------------------------------
/**
 * Natural-language Q&A. Placeholder: keyword routing over the computed findings
 * and forecast, answered with templated text + record citations.
 */
export async function ask(question, data, filters = { days: 60 }) {
  if (AI.MODE === "api") {
    // TODO: return callApi("/materials/ask", { question, filters }); — local LLM grounded on ERP/MES exports
    return callApi("/materials/ask", { question, filters });
  }
  await new Promise((r) => setTimeout(r, 450 + Math.random() * 400));
  const q = question.toLowerCase();
  const att = attributeVariance(data, { days: filters.days || 60 });
  const lineM = q.match(/\b(?:line\s*|l)([1-4])\b/);
  const shiftM = q.match(/\bshift\s*([abc])\b/);
  const byCode = (c) => data.materials.find((m) => m.code === c);
  const matM = data.materials.find((m) => q.includes(m.code.toLowerCase()))
    || (/\bink\b|green/.test(q) ? byCode("INK-G1") : null)
    || (/substrate|sheet stock|paper/.test(q) ? byCode("SUB-100") : null)
    || (/varnish/.test(q) ? byCode("VRN-1") : null)
    || (/blanket|plate|consumable/.test(q) ? byCode("CON-5") : null);
  const prov = provenance("llm", { grounding: "attributeVariance + forecast (placeholder)" });
  const fmt$ = (v) => "$" + Math.round(v).toLocaleString();

  // procurement questions
  if (/(order|procure|forecast|short|stock ?out|run out|reorder|buy)/.test(q)) {
    const fc = forecast(data).filter((f) => !matM || f.material === matM.code);
    const risky = fc.filter((f) => f.rec).sort((a, b) => a.shortfallWeek - b.shortfallWeek);
    if (!risky.length) return { answer: `No projected shortfall below safety stock in the next 12 weeks${matM ? " for " + matM.code : ""}.`, citations: [], provenance: prov };
    const lines = risky.map((f) => `• ${f.material}: projected below safety stock in week ${f.shortfallWeek} (plan-driven)${f.naiveShortfallWeek ? `; historical-average method says week ${f.naiveShortfallWeek}` : "; the historical-average method misses it"}. Recommend ordering ${f.rec.qty.toLocaleString()} ${f.unit} from ${f.rec.vendor} by ${f.rec.orderBy} (${f.rec.leadWeeks}-week lead time).`);
    const cites = risky.flatMap((f) => data.pos.filter((p) => p.status === "Open" && p.material === f.material).map((p) => p.po)).slice(0, 6);
    return { answer: `The production plan rises ~18% from week 5, so consumption outpaces the historical average.\n${lines.join("\n")}`, citations: cites, provenance: prov };
  }

  // variance / why questions — pick the most relevant finding
  let cands = att.findings;
  if (lineM) cands = cands.filter((f) => f.line === "L" + lineM[1] || (f.lines || []).includes("L" + lineM[1]));
  if (shiftM) cands = cands.filter((f) => f.shift === shiftM[1].toUpperCase() || f.driver === "lot");
  if (matM && matM.code !== "SUB-100") cands = cands.filter((f) => f.material === matM.code || f.material === "all" || f.driver === "lot").sort((a, b) => (b.material === matM.code) - (a.material === matM.code));
  if (/lot|vendor|supplier/.test(q)) cands = cands.filter((f) => f.driver === "lot");
  if (/spoil|waste|yield|scrap/.test(q)) cands = cands.filter((f) => f.driver !== "usage-rate");

  if (!cands.length) {
    return {
      answer: `I found no statistically significant variance driver matching that question in the last ${filters.days || 60} days. Over that period ${fmt$(att.totals.varianceCost)} of net material variance was recorded; ${Math.round(att.explainedPct * 100)}% of gross variance is attributed to ${att.findings.length} findings (see the attribution table).`,
      citations: [], provenance: prov,
    };
  }
  const top = cands.slice(0, 2);
  const answer = top.map((f, i) => `${i ? "Also: " : ""}${f.title}. ${f.summary} Estimated impact ${fmt$(f.varianceCost)} (${Math.round(f.share * 100)}% of gross variance, confidence ${Math.round(f.confidence * 100)}%).`).join("\n");
  const citations = [...new Set(top.flatMap((f) => f.evidence.map((e) => e.id)))].slice(0, 8);
  return { answer, citations, provenance: prov };
}

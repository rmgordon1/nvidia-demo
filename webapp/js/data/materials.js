// Synthetic raw-material → finished-output data, shaped like ERP / MES exports.
// Deterministic (seeded) so evaluators can check results against known answers.
// No DOM access here: this file is importable from node for testing.
//
// KNOWN-ANSWER VARIANCE SCENARIOS (everything else is normal noise):
//   S1  Over-consumption of INK-G1 on Line L2 during the week of 2026-10-26 (+12% usage rate).
//   S2  Bad substrate lot LOT-SUB-261010-1 (vendor VND-B, stocked in Bay 1 → lines L1/L2):
//       +5.5 pp spoilage on every run that consumed it (2026-10-11 … 2026-10-16).
//   S3  Persistent high spoilage on Line L3, Shift C (+2.8 pp for the whole period).
import { rng } from "../ui.js";
import { attributeVariance, forecast } from "../ai/materials.js";

export const END_DATE = "2026-11-03";      // last production day in the extract
export const DAYS = 60;
export const LINES = ["L1", "L2", "L3", "L4"];
export const SHIFTS = ["A", "B", "C"];
export const BAD_LOT = "LOT-SUB-261010-1"; // S2 — received 2026-10-10 into Bay 1
const BAY = { L1: 1, L2: 1, L3: 2, L4: 2 }; // substrate staging bay feeding each line

/** Materials master + BOM / standard usage per 1,000 good sheets. */
export const MATERIALS = [
  { code: "SUB-100", name: "Security substrate (sheets)", unit: "sheets", stdPer1000: 1000, unitCost: 0.085, leadWeeks: 10, vendor: "VND-A", moq: 250000 },
  { code: "INK-G1",  name: "Intaglio ink — green",         unit: "kg",     stdPer1000: 2.4,  unitCost: 62,    leadWeeks: 6,  vendor: "VND-C", moq: 500 },
  { code: "INK-K2",  name: "Intaglio ink — black",         unit: "kg",     stdPer1000: 1.1,  unitCost: 48,    leadWeeks: 6,  vendor: "VND-C", moq: 250 },
  { code: "INK-C3",  name: "Offset ink — color",           unit: "kg",     stdPer1000: 0.6,  unitCost: 55,    leadWeeks: 6,  vendor: "VND-D", moq: 200 },
  { code: "VRN-1",   name: "Overprint varnish",            unit: "kg",     stdPer1000: 0.9,  unitCost: 31,    leadWeeks: 5,  vendor: "VND-D", moq: 250 },
  { code: "CON-5",   name: "Blanket / plate consumable",   unit: "each",   stdPer1000: 0.05, unitCost: 180,   leadWeeks: 4,  vendor: "VND-E", moq: 20 },
];
export const ALLOWED_SPOILAGE = 0.04;      // standard allowance (printed − good) / good
export const VENDORS = {
  "VND-A": "Substrate supplier A", "VND-B": "Substrate supplier B", "VND-C": "Ink supplier C",
  "VND-D": "Ink & coatings supplier D", "VND-E": "Consumables supplier E",
};

export const SCENARIOS = [
  { id: "S1", title: "INK-G1 over-consumption on Line L2", where: "Line L2 · INK-G1 · week of 2026-10-26",
    truth: "Usage rate +12% over standard for one week (e.g. a dispenser calibration drift).", expect: { driver: "usage-rate", material: "INK-G1", line: "L2" } },
  { id: "S2", title: "Bad substrate lot", where: "LOT-SUB-261010-1 (VND-B) · Bay 1 → L1, L2 · 2026-10-11 … 10-16",
    truth: "+5.5 pp spoilage on every run that consumed the lot.", expect: { driver: "lot", lot: "LOT-SUB-261010-1" } },
  { id: "S3", title: "High-spoilage shift", where: "Line L3 · Shift C · whole period",
    truth: "+2.8 pp spoilage on every L3 Shift C run.", expect: { driver: "line-shift", line: "L3", shift: "C" } },
];

// Forecast setup (tuned so plan-driven and historical-average forecasts disagree).
const PLAN_UPLIFT = { fromWeek: 5, factor: 1.18 };  // production plan increase from week 5
const STOCK_POLICY = {   // closing stock (weeks of average use) + open POs: [week due, weeks of cover]
  "SUB-100": { cover: 8.6, open: [[2, 3], [6, 3]] },
  "INK-G1":  { cover: 4.2, open: [[1, 2], [4, 2], [7, 2]] },
  "INK-K2":  { cover: 7.0, open: [[3, 3], [7, 4]] },
  "INK-C3":  { cover: 9.0, open: [[5, 5]] },
  "VRN-1":   { cover: 7.5, open: [[2, 4], [8, 4]] },
  "CON-5":   { cover: 10,  open: [[6, 6]] },
};

const DAY_MS = 86400000;
const endMs = Date.parse(END_DATE + "T00:00:00Z");
export const dayDate = (d) => new Date(endMs - (DAYS - 1 - d) * DAY_MS).toISOString().slice(0, 10);
/** Monday-based week key for a date string. */
export function weekOf(date) {
  const t = Date.parse(date + "T00:00:00Z");
  const dow = (new Date(t).getUTCDay() + 6) % 7;
  return new Date(t - dow * DAY_MS).toISOString().slice(0, 10);
}
export const todayISO = () => new Date(endMs + DAY_MS).toISOString().slice(0, 10); // 2026-11-04
export const addDays = (iso, n) => new Date(Date.parse(iso + "T00:00:00Z") + n * DAY_MS).toISOString().slice(0, 10);

let cached = null;

export function getMaterialsData() {
  if (cached) return cached;
  const r = rng(20261104);
  const norm = () => { let u = 0, v = 0; while (!u) u = r(); while (!v) v = r(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };
  const mat = Object.fromEntries(MATERIALS.map((m) => [m.code, m]));

  // ---- substrate lots per bay (received every 6 days; lot k active days 6k..6k+5)
  const subLotFor = (bay, d) => {
    const k = Math.floor(d / 6);
    return `LOT-SUB-${dayDate(6 * k - 1).slice(2).replace(/-/g, "")}-${bay}`;
  };

  // ---- production runs (MES-style)
  const runs = [];
  for (let d = 0; d < DAYS; d++) {
    const date = dayDate(d), week = weekOf(date);
    for (const line of LINES) for (const shift of SHIFTS) {
      const planned = Math.round(34000 + norm() * 900);
      const printed = Math.round(planned * (0.975 + r() * 0.035));
      const subLot = subLotFor(BAY[line], d);
      let spoil = 0.039 + norm() * 0.006;
      if (line === "L3" && shift === "C") spoil += 0.028;                         // S3
      if (subLot === BAD_LOT) spoil += 0.055;                           // S2
      spoil = Math.max(0.01, spoil);
      const spoiled = Math.round(printed * spoil);
      const good = printed - spoiled;
      const actual = {};
      for (const m of MATERIALS) {
        const base = (m.stdPer1000 * printed) / 1000;
        if (m.code === "SUB-100") { actual[m.code] = printed; continue; }
        let f = 1 + norm() * 0.005;
        if (m.code === "INK-G1" && line === "L2" && week === "2026-10-26") f *= 1.12; // S1
        // consumables are issued whole: unbiased stochastic rounding of the expected wear
        actual[m.code] = m.unit === "each" ? Math.floor(base * f) + (r() < (base * f) % 1 ? 1 : 0) : +(base * f).toFixed(2);
      }
      runs.push({
        id: `RUN-${date.replace(/-/g, "")}-${line}-${shift}`, day: d, date, week, line, shift,
        planned, printed, good, spoiled, subLot, actual,
      });
    }
  }

  // ---- receipts / lots / POs (ERP-style)
  const pos = [], lots = [], txns = [];
  let poSeq = 41000, txnSeq = 1;
  const usageByDay = (code) => {
    const a = new Array(DAYS).fill(0);
    for (const run of runs) a[run.day] += run.actual[code];
    return a;
  };
  const addReceipt = (m, day, qty, lotId, vendor) => {
    const recv = day < 0 ? addDays(dayDate(0), day) : dayDate(day);
    const po = `PO-26-${poSeq++}`;
    const cost = +(m.unitCost * (0.97 + r() * 0.06)).toFixed(m.unitCost < 1 ? 4 : 2);
    pos.push({ po, vendor, material: m.code, qty, unit: m.unit, unitCost: cost, orderDate: addDays(recv, -m.leadWeeks * 7 - Math.round(r() * 5)), deliveryDate: recv, status: "Received", lot: lotId });
    lots.push({ lot: lotId, material: m.code, vendor, po, received: recv, qty, unit: m.unit });
    if (day >= 0) txns.push({ id: `TXN-${String(txnSeq++).padStart(6, "0")}`, date: recv, type: "Receipt", material: m.code, qty, unit: m.unit, lot: lotId, ref: po, line: "", shift: "" });
  };

  // Substrate: one lot per bay every 6 days, sized to ~6 days of that bay's use.
  const subUse = usageByDay("SUB-100");
  const avgSubDay = subUse.reduce((a, b) => a + b, 0) / DAYS;
  for (let k = 0; k * 6 < DAYS; k++) for (const bay of [1, 2]) {
    const lotId = subLotFor(bay, 6 * k);
    const vendor = lotId === BAD_LOT ? "VND-B" : (k + bay) % 3 === 0 ? "VND-B" : "VND-A";
    const qty = Math.round((avgSubDay * 3 * (0.96 + r() * 0.08)) / 1000) * 1000;
    addReceipt(mat["SUB-100"], 6 * k - 1, qty, lotId, vendor);
  }
  // Inks / varnish every 10 days, consumables every 15 days; lots consumed FIFO by date.
  const otherLots = {};
  for (const m of MATERIALS.filter((x) => x.code !== "SUB-100")) {
    const use = usageByDay(m.code), avg = use.reduce((a, b) => a + b, 0) / DAYS;
    const every = m.unit === "each" ? 15 : 10, offset = MATERIALS.indexOf(m) % every;
    otherLots[m.code] = [];
    for (let d = -every + offset; d < DAYS; d += every) {
      const lotId = `LOT-${m.code}-${dayDate(Math.max(0, d)).slice(2).replace(/-/g, "")}`;
      const qty = Math.max(m.moq, Math.round((avg * every * (0.95 + r() * 0.1)) / m.moq) * m.moq);
      addReceipt(m, d, qty, d < 0 ? lotId.replace(/\d{6}$/, "OPEN") : lotId, m.vendor);
      otherLots[m.code].push({ from: d, lot: d < 0 ? lotId.replace(/\d{6}$/, "OPEN") : lotId });
    }
  }
  const lotFor = (code, d) => {
    const l = otherLots[code];
    let cur = l[0].lot;
    for (const x of l) if (x.from <= d - 1) cur = x.lot;
    return cur;
  };

  // Issues to production (one per run × material) and occasional ink returns.
  for (const run of runs) {
    for (const m of MATERIALS) {
      const lot = m.code === "SUB-100" ? run.subLot : lotFor(m.code, run.day);
      run.lots = run.lots || {};
      run.lots[m.code] = lot;
      let ret = 0;
      if (m.unit === "kg" && r() < 0.04) ret = +(run.actual[m.code] * (0.03 + r() * 0.05)).toFixed(2);
      txns.push({ id: `TXN-${String(txnSeq++).padStart(6, "0")}`, date: run.date, type: "Issue", material: m.code, qty: +(run.actual[m.code] + ret).toFixed(2), unit: m.unit, lot, ref: run.id, line: run.line, shift: run.shift });
      if (ret) txns.push({ id: `TXN-${String(txnSeq++).padStart(6, "0")}`, date: run.date, type: "Return", material: m.code, qty: ret, unit: m.unit, lot, ref: run.id, line: run.line, shift: run.shift });
    }
  }
  txns.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));

  // ---- opening / closing stock (closing set by the stock policy)
  const stock = {};
  const weeks = DAYS / 7;
  for (const m of MATERIALS) {
    const issued = txns.filter((t) => t.material === m.code && t.type === "Issue").reduce((a, t) => a + t.qty, 0);
    const returned = txns.filter((t) => t.material === m.code && t.type === "Return").reduce((a, t) => a + t.qty, 0);
    const received = txns.filter((t) => t.material === m.code && t.type === "Receipt").reduce((a, t) => a + t.qty, 0);
    const avgWeekly = (issued - returned) / weeks;
    const closing = Math.round(STOCK_POLICY[m.code].cover * avgWeekly);
    stock[m.code] = { opening: Math.round(closing - received + issued - returned), closing, avgWeekly };
  }

  // ---- open POs (future deliveries) + forward production plan
  const today = todayISO();
  for (const m of MATERIALS) {
    for (const [wk, cover] of STOCK_POLICY[m.code].open) {
      const qty = Math.round((cover * stock[m.code].avgWeekly) / m.moq) * m.moq;
      const due = addDays(today, (wk - 1) * 7 + 3);
      pos.push({ po: `PO-26-${poSeq++}`, vendor: m.vendor, material: m.code, qty, unit: m.unit, unitCost: m.unitCost, orderDate: addDays(due, -m.leadWeeks * 7), deliveryDate: due, status: "Open", lot: "" });
    }
  }
  pos.sort((a, b) => (a.deliveryDate < b.deliveryDate ? -1 : 1));
  const histWeeklyGood = runs.reduce((a, x) => a + x.good, 0) / weeks;
  const plan = Array.from({ length: 12 }, (_, i) => ({
    week: i + 1, start: addDays(today, i * 7),
    goodSheets: Math.round(histWeeklyGood * (i + 1 >= PLAN_UPLIFT.fromWeek ? PLAN_UPLIFT.factor : 1) / 1000) * 1000,
  }));

  cached = { materials: MATERIALS, lines: LINES, shifts: SHIFTS, allowedSpoilage: ALLOWED_SPOILAGE, vendors: VENDORS,
    scenarios: SCENARIOS, runs, pos, lots, txns, stock, plan, today, endDate: END_DATE, days: DAYS };
  return cached;
}

/** Headline numbers for the Mission Overview (last 30 days). Shape is a contract — keep it stable. */
export function materialsSummary(periodDays = 30) {
  const data = getMaterialsData();
  const runs = data.runs.filter((x) => x.day >= DAYS - periodDays);
  const printed = runs.reduce((a, x) => a + x.printed, 0), good = runs.reduce((a, x) => a + x.good, 0);
  const att = attributeVariance(data, { days: periodDays });
  const fc = forecast(data, data.plan);
  const next = fc.filter((f) => f.shortfallWeek).sort((a, b) => a.shortfallWeek - b.shortfallWeek)[0];
  return {
    periodDays,
    goodOutput: good,
    yieldPct: good / printed,
    spoilagePct: (printed - good) / printed,
    varianceCost: att.totals.varianceCost,
    explainedPct: att.explainedPct,
    findings: att.findings.length,
    reconMinutes: 3,
    nextShortfall: next ? { material: next.material, weeksUntil: next.shortfallWeek } : null,
  };
}

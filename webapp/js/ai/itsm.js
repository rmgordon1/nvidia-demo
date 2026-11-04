// ===========================================================================
// ITSM "next best step" — placeholder AI.
//
//   retrieveSimilar(query, tickets, { k, excludeId, sources })  -> [{ ticket, score, sim, matchedTerms }]
//   recommendNextSteps(query, hits, { sources })               -> recommendation (see bottom of file)
//
// `tickets` is the knowledge index: live ServiceNow incidents plus the migrated
// Helix archive; each record carries `origin: "servicenow" | "helix"`.
// `sources` lists which origins the agent chose to search.
//
// Today: a real client-side BM25 retriever + a deterministic "generator" that
// assembles steps from the retrieved tickets' resolutions and work notes.
// Later: the local LLM + RAG service on the GB10 (see TODOs, AI.MODE === "api").
// ===========================================================================
import { AI, provenance, callApi, streamApi, chatModel } from "./config.js";

// The ServiceNow cutover is complete: agents work incidents in ServiceNow, and
// the pre-cutover BMC Helix history was migrated into the knowledge index as a
// read-only archive. Update the date to match the real cutover.
export const CUTOVER = { date: "2026-11-02", live: "servicenow", archive: "helix" };

// Field mapping between the Helix archive and ServiceNow. The retrieval layer
// reads canonical fields; the UI labels everything with ServiceNow columns and
// shows the original Helix column for migrated records.
export const SOURCES = {
  helix: {
    label: "BMC Helix", note: "archive · migrated at cutover", table: "HPD:Help Desk",
    fields: {
      id: "Incident_Number", summary: "Description", details: "Detailed_Decription", group: "Assigned_Group",
      assignee: "Assignee", resolution: "Resolution", resCat: "Resolution_Category", cat: "Categorization_Tier_1-3",
      prodCat: "Product_Categorization_Tier_1-3", ci: "HPD_CI", serviceCI: "ServiceCI", priority: "Priority",
      impact: "Impact", urgency: "Urgency", status: "Status", site: "Site", reported: "Reported_Date",
      responded: "Responded_Date", resolved: "Last_Resolved_Date", closed: "Closed_Date", worklog: "HPD:WorkLog",
    },
  },
  servicenow: {
    label: "ServiceNow", note: "live platform", table: "incident",
    fields: {
      id: "number", summary: "short_description", details: "description", group: "assignment_group",
      assignee: "assigned_to", resolution: "close_notes", resCat: "close_code", cat: "category / subcategory",
      prodCat: "u_product_category", ci: "cmdb_ci", serviceCI: "business_service", priority: "priority",
      impact: "impact", urgency: "urgency", status: "state", site: "location", reported: "opened_at",
      responded: "u_responded_at", resolved: "resolved_at", closed: "closed_at", worklog: "sys_journal_field (work_notes)",
    },
  },
};

const STOP = new Set(("a an and are as at be been but by can cant could did do does dont for from had has have he her his " +
  "i im in into is it its ive just me my no not of on or our please said says she so that the their them then there they " +
  "this to too up us was we were what when which who will with would you your hi hello thanks thank regards called " +
  "user customer issue problem help need needs also still again today now get got any some like").split(" "));

export function tokenize(text) {
  return (text || "").toLowerCase()
    .replace(/[’']/g, "")
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length > 1 && !STOP.has(w))
    .map(stem);
}
function stem(w) {
  if (w.length > 5 && w.endsWith("ing")) return w.slice(0, -3);
  if (w.length > 4 && w.endsWith("ed")) return w.slice(0, -2);
  if (w.length > 3 && w.endsWith("s") && !w.endsWith("ss")) return w.slice(0, -1);
  return w;
}

let apiDown = null; // set when the model-server call fails, so the placeholder path is used for this request

/** Fields the server needs to index a client-side (ServiceNow) ticket. */
function compact(t) {
  return { id: t.id, summary: t.summary, details: t.details, resolution: t.resolution, status: t.status, group: t.group,
    cat: t.cat, product: t.product, ci: t.ci, site: t.site, priority: t.priority, reported: t.reported, resolved: t.resolved,
    origin: "servicenow", worklog: (t.worklog || []).slice(0, 6) };
}

/**
 * Agent copilot chat (API mode only). Streams the answer:
 *   onEvent({type:"sources", hits}) · ({type:"delta", text}) · ({type:"done", cited, uncited, model, ms}) · ({type:"error", message})
 */
export function copilotChat({ ticket, messages, sources, extra = [] }, onEvent, signal) {
  return streamApi("/itsm/chat", { ticket, messages, sources, extra: extra.map(compact), model: chatModel() }, onEvent, signal);
}

// ---- BM25 index (built once per ticket array) -----------------------------
const indexCache = new WeakMap();
function buildIndex(tickets) {
  const docs = tickets.map((t) => {
    // Problem text is weighted over resolution text: we match on symptoms.
    const text = [t.summary, t.summary, t.details, t.cat.join(" "), t.prodCat.join(" "), t.product, t.product, t.ci, t.resolution].join(" ");
    const toks = tokenize(text);
    const tf = new Map();
    for (const w of toks) tf.set(w, (tf.get(w) || 0) + 1);
    return { t, tf, len: toks.length };
  });
  const df = new Map();
  for (const d of docs) for (const w of d.tf.keys()) df.set(w, (df.get(w) || 0) + 1);
  const avg = docs.reduce((a, d) => a + d.len, 0) / Math.max(1, docs.length);
  return { docs, df, avg, N: docs.length };
}

/**
 * Rank historical tickets against `query` ({ summary, details, cat, product, ci } or a string).
 * Only closed/resolved tickets with a resolution note are eligible as evidence.
 */
export async function retrieveSimilar(query, tickets, { k = 6, excludeId = null, sources = ["servicenow", "helix"] } = {}) {
  if (AI.MODE === "api") {
    try {
      const extra = sources.includes("servicenow") ? tickets.filter((t) => t.origin === "servicenow").map(compact) : [];
      const r = await callApi("/itsm/retrieve", { query, k, sources, excludeId, extra });
      const byId = new Map(tickets.map((t) => [t.id, t]));
      return r.hits.map((hit) => ({ ...hit, ticket: byId.get(hit.ticket.id) || { ...hit.ticket, origin: hit.ticket.origin || "helix" } }));
    } catch (err) {
      console.warn("model-server retrieve failed, using browser placeholder", err);
      apiDown = String(err.message || err);
    }
  }
  const idx = indexCache.get(tickets) || (indexCache.set(tickets, buildIndex(tickets)), indexCache.get(tickets));
  const qText = typeof query === "string" ? query : [query.summary, query.summary, query.details, (query.cat || []).join(" "), query.product, query.ci].join(" ");
  const qTerms = [...new Set(tokenize(qText))];
  const K1 = 1.4, B = 0.75;
  const scored = [];
  for (const d of idx.docs) {
    const t = d.t;
    if (t.id === excludeId || !sources.includes(t.origin || "helix") || !t.resolution || !["Closed", "Resolved"].includes(t.status)) continue;
    let score = 0;
    const matched = [];
    for (const w of qTerms) {
      const f = d.tf.get(w);
      if (!f) continue;
      const n = idx.df.get(w);
      const idf = Math.log(1 + (idx.N - n + 0.5) / (n + 0.5));
      score += idf * (f * (K1 + 1)) / (f + K1 * (1 - B + B * d.len / idx.avg));
      matched.push(w);
    }
    if (score > 0) scored.push({ ticket: t, score, matchedTerms: matched });
  }
  scored.sort((a, b) => b.score - a.score);
  const top = scored.slice(0, k);
  // Normalised similarity 0-1 (relative to the best possible self-match of the query).
  const ceiling = qTerms.reduce((a, w) => a + Math.log(1 + (idx.N - (idx.df.get(w) || 0) + 0.5) / ((idx.df.get(w) || 0) + 0.5)) * 1.6, 0) || 1;
  for (const h of top) h.sim = Math.min(0.99, h.score / ceiling);
  return top;
}

// ---- deterministic "generator" --------------------------------------------
const ACTION = /\b(restart|reboot|reset|re-?enroll|reinstall|install|clear|replac|reissu|renew|rekey|unlock|re-?image|updat|patch|appl|enabl|disabl|reconnect|re-?seat|re-?map|mapped|resync|sync|escalat|rebuilt|rebuild|re-?run|reprocess|regenerat|grant|add|remov|revok|block|quarantin|power.?cycl|swap|configur|correct|recalibrat|repair|migrat|roll ?back|re-?download|traced|identified|purg|extend|provision|deploy|issued|scheduled)/i;
// Outcomes / contact chatter: true but not a step an agent can take.
const OUTCOME = /^(verified|confirmed|user confirmed|customer confirmed|process completed|spoke with|called|emailed|left (a )?voicemail|basic troubleshooting|closing|ticket)\b|did not resolve|still (no|not)|no change|didn.t (help|work)|unsuccessful|unable to attend|^tried\b|\b(is|are) (now )?back (online|up)\b|service restored$|contact info|en route|on the way|will follow up|awaiting/i;
const NOISE = /^(hi|hello|dear)\b|reply within|please (test|confirm|let us know)|regards|thank/i;
const MI_LINK = /resolved by (?:the )?(?:original|parent|master) incident (INC\d+)/i;

function sentences(text) {
  return (text || "").replace(/\s+/g, " ").split(/(?<=[.!?;])\s+(?=[A-Z])/).map((s) => s.trim()).filter((s) => s.length > 12);
}
function normalize(s) {
  s = s.replace(/^(update|resolution|fix)\s*:\s*/i, "");
  s = s.replace(/^(i |we |tech |agent )?(have |has )?/i, "").replace(/\s*[.;]+$/, "");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
function jaccard(a, b) {
  const A = new Set(tokenize(a)), B = new Set(tokenize(b));
  let i = 0;
  for (const w of A) if (B.has(w)) i++;
  return i / Math.max(1, A.size + B.size - i);
}
const median = (xs) => { const s = xs.filter((x) => x != null).sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : null; };
function vote(items) {
  const m = new Map();
  for (const [key, w] of items) if (key) m.set(key, (m.get(key) || 0) + w);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

/**
 * Build a ranked recommendation from retrieved hits. Returns:
 * { steps:[{ id, text, confidence, rationale, sources:[INC…] }], group, category, product,
 *   etaHours, confidence, hits, timings:{ retrievalMs, generationMs }, ai: provenance, warnings:[] }
 */
export async function recommendNextSteps(query, hits, { sources = ["servicenow", "helix"], retrievalMs = 0 } = {}) {
  const t0 = performance.now();
  if (AI.MODE === "api" && !apiDown) {
    try {
      const extra = hits.filter((x) => x.ticket.origin === "servicenow").map((x) => compact(x.ticket));
      const r = await callApi("/itsm/recommend", { query, sources, hits: hits.map((x) => x.ticket.id), excludeId: query.id || null, extra, model: chatModel() });
      const byId = new Map(hits.map((x) => [x.ticket.id, x.ticket]));
      r.hits = r.hits.map((x) => ({ ...x, ticket: byId.get(x.ticket.id) || { ...x.ticket, origin: x.ticket.origin || "helix" } }));
      r.timings.retrievalMs += Math.round(retrievalMs);
      r.ai = { ...provenance("llm"), ...r.ai, mode: "api" };
      return r;
    } catch (err) {
      console.warn("model-server recommend failed, using browser placeholder", err);
      apiDown = String(err.message || err);
    }
  }
  const fallbackNote = apiDown ? `Model server unavailable (${apiDown}). Showing the browser placeholder instead.` : null;
  apiDown = null;
  await new Promise((r) => setTimeout(r, 450 + Math.min(500, hits.length * 60))); // simulated generation latency

  const warnings = fallbackNote ? [fallbackNote] : [];
  const top = hits.slice(0, 6);
  const best = top[0]?.sim ?? 0;
  if (!top.length) warnings.push("No similar resolved tickets were found. Escalate per the standard triage runbook.");
  else if (best < 0.25) warnings.push("Low similarity to historical tickets. Treat these steps as weak evidence and confirm with Tier 2.");

  // Candidate steps: resolution sentences (strong evidence) + actionable work-log lines (weaker).
  const cands = [];
  top.forEach((h, rank) => {
    const w = h.sim * (1 - rank * 0.08);
    for (const s of sentences(h.ticket.resolution)) {
      const mi = s.match(MI_LINK);
      if (mi) {
        cands.push({ text: `Check for an active major incident (e.g. ${mi[1]}); if it matches, relate this ticket to the parent and follow its restoration plan`, w: w * 0.9, src: h.ticket.id, kind: "resolution", resCat: "Major incident" });
        continue;
      }
      // Resolution notes mix actions with outcomes ("press is back online"); keep the actions.
      if (!NOISE.test(s) && !OUTCOME.test(normalize(s)) && ACTION.test(s) && s.length > 20) cands.push({ text: normalize(s), w: w * 1.0, src: h.ticket.id, kind: "resolution", resCat: h.ticket.resCat.filter(Boolean).join(" › ") });
    }
    for (const wl of h.ticket.worklog) {
      if (wl.type !== "Working Log" && wl.type !== "Vendor Communication") continue;
      for (const s of sentences(wl.text)) {
        if (ACTION.test(s) && !NOISE.test(s) && !OUTCOME.test(normalize(s)) && s.length > 20 && s.length < 220) cands.push({ text: normalize(s), w: w * 0.55, src: h.ticket.id, kind: "worklog" });
      }
    }
  });

  // Cluster near-duplicates, accumulate support.
  const clusters = [];
  for (const c of cands) {
    const hit = clusters.find((k) => jaccard(k.text, c.text) > 0.36);
    if (hit) {
      hit.w += c.w * 0.6;
      if (!hit.sources.includes(c.src)) hit.sources.push(c.src);
      if (c.kind === "resolution" && hit.kind !== "resolution") Object.assign(hit, { text: c.text, kind: c.kind, resCat: c.resCat });
    } else clusters.push({ text: c.text, w: c.w, sources: [c.src], kind: c.kind, resCat: c.resCat });
  }
  clusters.sort((a, b) => b.w - a.w);
  const maxW = clusters[0]?.w || 1;

  const steps = clusters.slice(0, 5).map((c, i) => {
    const n = c.sources.length;
    const conf = Math.max(0.12, Math.min(0.96, (0.35 + 0.65 * (c.w / maxW)) * (0.55 + 0.45 * Math.min(1, best * 1.6)) + Math.min(0.12, (n - 1) * 0.05)));
    const rationale = c.kind === "resolution"
      ? `Resolved ${n} similar incident${n > 1 ? "s" : ""}${c.resCat ? ` (${c.resCat})` : ""}.`
      : `Diagnostic action recorded in the work notes of ${n} similar incident${n > 1 ? "s" : ""}.`;
    return { id: "S" + (i + 1), text: c.text, confidence: +conf.toFixed(2), rationale, sources: c.sources, kind: c.kind };
  });
  // Ranking: resolution-backed and more-confident first, but always lead with triage if nothing strong.
  steps.sort((a, b) => b.confidence - a.confidence);
  steps.forEach((s, i) => (s.id = "S" + (i + 1)));

  const W = (h) => h.sim;
  const group = vote(top.map((h) => [h.ticket.group, W(h)]))[0]?.[0] || "Service Desk";
  const category = vote(top.map((h) => [h.ticket.cat.filter(Boolean).join(" › "), W(h)]))[0]?.[0] || "";
  const product = vote(top.map((h) => [h.ticket.product, W(h)]))[0]?.[0] || "";
  const etaHours = median(top.map((h) => (h.ticket.resolved && h.ticket.reported ? (h.ticket.resolved - h.ticket.reported) / 3600 : null)));
  const confidence = steps.length ? +(steps.slice(0, 3).reduce((a, s) => a + s.confidence, 0) / Math.min(3, steps.length)).toFixed(2) : 0;
  const cited = [...new Set(steps.flatMap((s) => s.sources))];

  return {
    steps, group, category, product, etaHours, confidence, warnings,
    hits: top,
    timings: { retrievalMs: Math.round(retrievalMs), generationMs: Math.round(performance.now() - t0) },
    ai: provenance("llm", { retriever: AI.models.embed.name, platform: CUTOVER.live, knowledge: sources, sources: cited, confidence }),
  };
}

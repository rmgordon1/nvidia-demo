// ITSM "Next Best Step" — the agent works the open ServiceNow queue (or logs a new
// ticket); local AI retrieves similar resolved incidents and proposes ranked steps
// with citations; the agent accepts, edits or rejects. All decisions land in the audit trail.
import { h, icon, advisory, aiTag, statusPill, kpi, drawer, modal, toast, fmtDur, fmtDate, fmtPct, timeAgo } from "../ui.js";
import * as store from "../store.js";
import { loadTickets, loadServiceNow } from "../data/loaders.js";
import { retrieveSimilar, recommendNextSteps, copilotChat, SOURCES, CUTOVER, tokenize } from "../ai/itsm.js";
import { AI, callApi, chatModel } from "../ai/config.js";

// Dell-estate examples (scenario ids from it-tickets/out/dell): client, infrastructure, storage, security.
const EXAMPLE_SCENARIOS = ["dock_display", "laptop_thermal", "pe_drive_fail", "powerscale_share", "vxrail_lcm", "phishing_report"];
const EXAMPLE_TEXT = [
  ["Dock", "Latitude on the Dell dock stopped driving both external monitors after this morning's restart. No DisplayPort signal, and the dock isn't charging the laptop either."],
  ["Server disk", "iDRAC alert on HQ-PE-FS02: predictive failure on Physical Disk 0:1:3 and the RAID virtual disk is degraded. File services are still up."],
  ["File share", "S: drive maps but shows empty, and 'Access is denied' on HQ-PSCALE-01 since this morning. Several people in Finance are affected."],
  ["Phishing", "Got an email that looked like a vendor invoice. I clicked the link and typed my username and password before I noticed. What should I do?"],
];
const REJECT_REASONS = [
  "Not relevant to this ticket",
  "Steps are incorrect or unsafe",
  "Already tried these steps",
  "Cited tickets don't support the steps",
  "Needs a different support group",
  "Other",
];
const PRIORITY_ORDER = { Critical: 0, High: 1, Medium: 2, Low: 3 };
const PII = [
  { re: /\b\d{3}-\d{2}-\d{4}\b/g, label: "SSN-like number" },
  { re: /\b[\w.+-]+@[\w-]+\.[\w.]+\b/g, label: "email address" },
  { re: /\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b/g, label: "phone number" },
];

export async function render(root) {
  const data = await loadTickets();
  // Pre-cutover history: the migrated, read-only Helix archive.
  const tickets = data.tickets.map((t) => ({ ...t, origin: "helix" }));
  const cutoverTs = Date.parse(CUTOVER.date + "T12:00:00Z") / 1000; // midday UTC: same calendar date in US time zones
  // Open ServiceNow incidents: everything still open in Helix was migrated at cutover
  // (INC0010001…); the Helix originals are closed as transferred and are history only.
  const snowData = await loadServiceNow().catch(() => ({ incidents: [], nextNumber: 10001 }));
  const openIncidents = snowData.incidents.map((t) => ({ ...t, origin: "servicenow" }));
  const openIds = new Set(openIncidents.map((t) => t.id));
  // Post-cutover resolved ServiceNow incidents. Until a live ServiceNow feed is connected,
  // a new incident joins the index when the agent accepts its recommendation.
  const snowKb = () => store.get("itsm.snowKb", []);
  // New incident numbers continue after the migrated block; renumber demo incidents created before the migration.
  if (store.get("itsm.snowSeq", 0) < snowData.nextNumber - 1) store.set("itsm.snowSeq", snowData.nextNumber - 1);
  if (snowKb().some((t) => openIds.has(t.id))) store.set("itsm.snowKb", snowKb().map((t) => (openIds.has(t.id) ? { ...t, id: nextIncidentNumber() } : t)));
  const notesOf = (id) => store.get("itsm.snowNotes", {})[id] || [];   // work notes added in this demo
  const corpus = () => [...snowKb(), ...tickets];                        // evidence: resolved incidents only
  const byIdOf = () => new Map([...openIncidents, ...corpus()].map((t) => [t.id, t]));
  let byId = byIdOf();

  const state = {
    mode: store.get("itsm.mode", "text"),
    loaded: null,         // ticket picked from the knowledge source
    result: null,         // recommendation
    subject: null,
    shownAt: 0,
    decision: null,       // { decision, reason }
    dismissed: new Set(),
  };
  // The UI speaks ServiceNow (live platform); migrated Helix records also show their original column.
  const F = () => SOURCES.servicenow.fields;
  const H = SOURCES.helix.fields;
  // Every search uses all knowledge: live ServiceNow incidents and the full Helix history.
  const sourcesOn = () => ["servicenow", "helix"];

  // ------------------------------------------------------------------ header
  const head = h("div.view-head",
    h("div.titles",
      h("h2", "ITSM Next Best Step"),
      h("p.lede", "Work the open ServiceNow queue, or log a new incident. A local model searches resolved incidents, including the Helix history carried over at cutover, and proposes ranked next steps with citations. You review and decide; nothing is written back to ServiceNow.")),
    h("div.actions",
      h("button.btn.ghost.sm", { type: "button", onclick: showMapping, title: "How migrated Helix fields map to ServiceNow" }, "Field mapping"),
      h("span.pill.good", icon("check"), `ServiceNow live · cutover ${fmtDate(cutoverTs)}`)));

  // ------------------------------------------------------------------ KPIs
  const kpiRow = h("div.grid.g-4");

  // ------------------------------------------------------------------ intake
  const modeGroup = h("div.btn-group.itsm-modes", { role: "tablist" });
  const MODES = [["text", "Describe"], ["fields", "Structured"], ["existing", "Replay historical"]];
  for (const [k, label] of MODES) modeGroup.append(h("button", { type: "button", "data-k": k, onclick: () => setMode(k) }, label));

  // free text
  const ta = h("textarea", { rows: 7, placeholder: "e.g. OptiPlex XE4 HMI PC on Offset Press 1 froze and won't boot; the operator can't load the next job…", "aria-label": "Ticket description" });
  const textPane = h("div.stack",
    h("label.field", "Ticket description", ta),
    h("div.itsm-chips", h("span.small.muted", "Try:"),
      ...EXAMPLE_TEXT.map(([label, t]) => h("button.btn.sm.ghost.itsm-chip", { type: "button", title: t, onclick: () => { ta.value = t; validate(); } }, label))));

  // structured
  const uniq = (f) => [...new Set(tickets.map(f).filter(Boolean))].sort();
  const sel = (label, opts, attrs = {}) => {
    const s = h("select", attrs, h("option", { value: "" }, "—"), ...opts.map((o) => h("option", { value: o }, o)));
    return [h("label.field", label, s), s];
  };
  const fSummary = h("input", { type: "text", placeholder: "Short summary" });
  const fDetails = h("textarea", { rows: 4, placeholder: "Symptoms, error text, what was already tried" });
  const [lCat1, fCat1] = sel("Category tier 1", uniq((t) => t.cat[0]));
  const [lCat2, fCat2] = sel("Tier 2", uniq((t) => t.cat[1]));
  const [lCat3, fCat3] = sel("Tier 3", uniq((t) => t.cat[2]));
  const fProduct = h("input", { type: "text", list: "itsm-products", placeholder: "Product / application" });
  const fCI = h("input", { type: "text", list: "itsm-cis", placeholder: "Configuration item" });
  const [lSite, fSite] = sel("Site", uniq((t) => t.site));
  const [lPri, fPri] = sel("Priority", ["Critical", "High", "Medium", "Low"]);
  const [lImp, fImp] = sel("Impact", uniq((t) => t.impact));
  const [lUrg, fUrg] = sel("Urgency", uniq((t) => t.urgency));
  const fieldLabels = {};
  const lbl = (key, text, control) => { const l = h("label.field", h("span", text, h("span.itsm-api", "")), control); fieldLabels[key] = l; return l; };
  const fieldsPane = h("div.stack",
    lbl("summary", "Summary", fSummary),
    lbl("details", "Details", fDetails),
    h("div.field-row", lCat1, lCat2, lCat3),
    h("div.field-row", lbl("prodCat", "Product", fProduct), lbl("ci", "Configuration item", fCI)),
    h("div.field-row", lSite, lPri, lImp, lUrg),
    h("datalist#itsm-products", ...uniq((t) => t.product).map((v) => h("option", { value: v }))),
    h("datalist#itsm-cis", ...uniq((t) => t.ci).map((v) => h("option", { value: v }))));

  // existing
  const search = h("input", { type: "search", placeholder: "Helix incident number or keywords (e.g. INC000000873443, HMI frozen)", "aria-label": "Search archived tickets" });
  const results = h("div.itsm-picklist");
  const loadedBox = h("div");
  // a resolved example per scenario, so the replay can be compared with the real fix
  const examples = EXAMPLE_SCENARIOS.map((sc) => tickets.filter((t) => t.scenario === sc && t.resolution).at(-1)).filter(Boolean);
  const existingPane = h("div.stack",
    h("p.small.t2", "Replay a migrated Helix incident as if it had just arrived in ServiceNow, to compare the recommendation with how it was actually resolved."),
    h("label.field", "Find an archived Helix incident", search),
    h("div.itsm-chips", h("span.small.muted", "Examples:"),
      ...examples.slice(0, 6).map((t) => h("button.btn.sm.ghost.itsm-chip", { type: "button", title: t.summary, onclick: () => pickTicket(t) }, h("span.mono", t.id.slice(-6)), " ", shorten(t.summary, 20)))),
    results, loadedBox);

  const panes = { text: textPane, fields: fieldsPane, existing: existingPane };
  const msg = h("div.itsm-msg", { "aria-live": "polite" });
  const genBtn = h("button.btn.primary", { type: "submit" }, icon("spark"), "Get next best step");
  const clearBtn = h("button.btn.ghost", { type: "button", onclick: resetIntake }, "Clear");
  const intake = h("form.card.itsm-intake", { onsubmit: (e) => { e.preventDefault(); generate(); } },
    h("div.card-head", h("h3", "New ticket"), h("span.end", h("span.sub", "Input is never sent outside the GB10"))),
    modeGroup, h("div.itsm-pane", ...Object.values(panes)), msg,
    h("div.row", genBtn, clearBtn, h("span.small.muted", { style: { marginLeft: "auto" } }, "Ctrl + Enter")));
  intake.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); generate(); } });
  intake.addEventListener("input", () => validate(true));

  const recent = h("div.card",
    h("div.card-head", h("h3", "Recent decisions"), h("span.end", h("a.small", { href: "#/audit", hidden: store.role() !== "admin" }, "Full audit log"))),
    h("div.itsm-recent"));

  // ------------------------------------------------------------------ open-incident queue
  const queue = { group: store.get("itsm.qGroup", ""), state: "", text: "", preview: null, previewNote: "" };
  const qGroup = h("select", { "aria-label": "Assignment group", onchange: () => { queue.group = qGroup.value; store.set("itsm.qGroup", queue.group); renderQueue(); } },
    h("option", { value: "" }, "All groups"), ...[...new Set(openIncidents.map((t) => t.group))].sort().map((g) => h("option", { value: g }, g)));
  qGroup.value = queue.group;
  const qState = h("select", { "aria-label": "State", onchange: () => { queue.state = qState.value; renderQueue(); } },
    h("option", { value: "" }, "All states"), h("option", { value: "In Progress" }, "In Progress"), h("option", { value: "On Hold" }, "On Hold"));
  const qText = h("input", { type: "search", placeholder: "Filter by number or text", "aria-label": "Filter open incidents", oninput: () => { queue.text = qText.value.trim().toLowerCase(); renderQueue(); } });
  const qBody = h("tbody");
  const qCount = h("span.sub");
  const queueCard = h("div.card.itsm-queue",
    h("div.card-head", h("h3", "Open incidents"), qCount,
      h("span.end.row.itsm-qfilters", qGroup, qState, qText)),
    h("div.table-wrap.itsm-qtable", h("table.data",
      h("thead", h("tr", h("th", "Number"), h("th", "Short description"), h("th", "State"), h("th", "Priority"),
        h("th", "Assignment group"), h("th", "AI preview"), h("th", ""))), qBody)));

  // ------------------------------------------------------------------ output
  const recCard = h("div.card.itsm-rec");
  const simCard = h("div.card", h("div.card-head", h("h3", "Similar historical tickets"), h("span.sub", "retrieved evidence")), h("div.itsm-sim"));
  const placeholderNote = AI.MODE === "api"
    ? h("p.placeholder-note", "Local model server on the GB10: Nemotron embeddings + BM25 + Nemotron reranker for retrieval, a classifier with LLM fallback for routing, and the selected LLM on vLLM for steps and chat. If the server is down, the browser placeholder takes over automatically.")
    : h("p.placeholder-note",
      "Placeholder model: retrieval is a browser-side BM25 index and the steps are assembled deterministically from cited resolutions. Swap in the local LLM + RAG service in ", h("code", "js/ai/itsm.js"), " (", h("code", "AI.MODE = \"api\""), ").");

  // ------------------------------------------------------------------ copilot chat
  const chatLog = h("div.itsm-chatlog", { "aria-live": "polite" });
  const chatIn = h("textarea", { rows: 2, placeholder: "Ask about this incident, e.g. \"What if a power cycle didn't help?\" or \"Any similar cases at SPF?\"", "aria-label": "Ask the copilot" });
  const chatBtn = h("button.btn.primary", { type: "submit" }, icon("spark"), "Ask");
  const chat = { messages: [], busy: false, ctrl: null };
  const chatCard = h("form.card.itsm-chat", { onsubmit: (e) => { e.preventDefault(); askCopilot(); } },
    h("div.card-head", aiTag("Agent copilot"), h("span.sub", "grounded in resolved incidents"),
      h("span.end", h("button.btn.ghost.sm", { type: "button", onclick: () => { chat.ctrl?.abort(); chat.messages = []; chatLog.replaceChildren(chatHint()); } }, "New chat"))),
    chatLog, h("div.itsm-chatbar", chatIn, chatBtn));
  chatIn.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); askCopilot(); } });
  chatLog.append(chatHint());

  // model picker (Admin): which local LLM answers steps, routing fallback and chat
  const modelSel = h("select", { "aria-label": "Chat model", onchange: () => { localStorage.setItem("bcp.ai.model", JSON.stringify(modelSel.value || null)); toast(`Model: ${modelSel.value || "server default"}`); } },
    h("option", { value: "" }, "Server default"));
  if (AI.MODE === "api" && store.role() === "admin") {
    head.querySelector(".actions").prepend(h("label.role-pick", "Model", modelSel));
    callApi("/health", undefined).then((hl) => {
      for (const [m, st] of Object.entries(hl.llms)) modelSel.append(h("option", { value: m, disabled: st !== "up" }, `${m}${st !== "up" ? " (" + st + ")" : ""}`));
      modelSel.value = chatModel() || "";
    }).catch(() => modelSel.append(h("option", { disabled: true }, "server unreachable")));
  }

  root.append(head, kpiRow, queueCard,
    h("div.grid.g-side-main",
      h("div.stack.itsm-col", intake, recent),
      h("div.stack.itsm-col", recCard, AI.MODE === "api" ? chatCard : null, simCard, placeholderNote)));

  setMode(state.mode);
  labelFields();
  renderQueue();
  loadPreviews();
  renderEmpty();
  refreshStats();
  const onAudit = () => refreshStats();
  window.addEventListener("audit", onAudit);

  // ================================================================== logic
  function setMode(k) {
    state.mode = k;
    store.set("itsm.mode", k);
    for (const b of modeGroup.children) b.classList.toggle("on", b.dataset.k === k);
    for (const [pk, p] of Object.entries(panes)) p.hidden = pk !== k;
    msg.replaceChildren();
  }

  // ---- open-incident queue
  function renderQueue() {
    const rows = openIncidents
      .filter((t) => (!queue.group || t.group === queue.group) && (!queue.state || t.status === queue.state) &&
        (!queue.text || `${t.id} ${t.correlationId} ${t.summary} ${t.product} ${t.ci}`.toLowerCase().includes(queue.text)))
      .sort((a, b) => (PRIORITY_ORDER[a.priority] ?? 9) - (PRIORITY_ORDER[b.priority] ?? 9) || a.reported - b.reported);
    const onHold = openIncidents.filter((t) => t.status === "On Hold").length;
    qCount.textContent = `ServiceNow · ${openIncidents.length} open (${openIncidents.length - onHold} in progress, ${onHold} on hold) · ` +
      (rows.length === openIncidents.length ? "migrated from Helix at cutover" : `${rows.length} shown`);
    qBody.replaceChildren(...(rows.length ? rows.map((t) => {
      const p = queue.preview?.[t.id];
      const worked = notesOf(t.id).length;
      return h("tr.clickable", { class: state.subject === t.id ? "selected" : "", onclick: () => workIncident(t) },
        h("td", h("div.itsm-qnum", h("span.mono", t.id), h("span.small.muted", { title: `Opened ${fmtDate(t.reported, true)}` }, "opened " + timeAgo(t.reported * 1000)))),
        h("td.itsm-qsum", { title: t.summary }, h("span", t.summary)),
        h("td", h("div.itsm-qstate", statusPill(t.status === "On Hold" ? "warning" : "info", t.status), t.holdReason ? h("span.small.muted", t.holdReason) : null)),
        h("td", statusPill(t.priority === "Critical" ? "critical" : t.priority === "High" ? "warning" : "info", t.priority)),
        h("td.small.itsm-qgroup", { title: t.group }, t.group),
        h("td.small.itsm-qprevcell", previewCell(t, p)),
        h("td", worked ? h("span.pill.good", { title: "AI-assisted steps accepted and logged as a work note" }, icon("check"), "Logged")
          : h("button.btn.sm", { type: "button", title: "Get next best step", "aria-label": `Get next best step for ${t.id}`, onclick: (e) => { e.stopPropagation(); workIncident(t); } }, icon("spark"), "Steps")));
    }) : [h("tr", h("td.empty-state", { colSpan: 7 }, "No open incidents match these filters."))]));
  }

  function previewCell(t, p) {
    if (!p) return h("span.muted", queue.previewNote || "…");
    const g = p.routing.group;
    const top = p.similar[0];
    return h("div.itsm-qprev",
      g.value !== t.group
        ? h("span.pill.warning", { title: `Classifier suggests ${g.value} (${fmtPct(g.confidence, 0)}); currently ${t.group}` }, "Reroute? " + g.value)
        : h("span.muted", { title: `Classifier agrees (${fmtPct(g.confidence, 0)})` }, "Group ✓"),
      top ? h("button.itsm-cite", { type: "button", title: top.summary, onclick: (e) => { e.stopPropagation(); openTicket(top.id); } }, icon("link"), top.id.slice(-6), ` ${Math.round((top.sim || 0) * 100)}%`) : null);
  }

  /** Classifier route + closest resolved incidents per open incident (server-side, no LLM). */
  async function loadPreviews(tries = 0) {
    if (AI.MODE !== "api") { queue.previewNote = "server preview off"; renderQueue(); return; }
    try {
      const r = await callApi("/itsm/queue", undefined);
      if (!r.ready) {
        queue.previewNote = "scoring…";
        if (tries < 20 && root.isConnected) setTimeout(() => loadPreviews(tries + 1), 4000);
      } else queue.preview = r.items;
    } catch { queue.previewNote = "server unreachable"; }
    if (root.isConnected) renderQueue();
  }

  function workIncident(t) {
    const notes = t.worklog.filter((w) => w.by !== "Helix migration").slice(-6).map((w) => `- ${w.type}: ${w.text}`);
    const query = { summary: t.summary, details: t.details + (notes.length ? `\n\nWork notes so far:\n${notes.join("\n")}` : ""),
      cat: t.cat, product: t.product, ci: t.ci, site: t.site, priority: t.priority, raw: `${t.summary} ${t.details}`,
      id: t.id, open: true, excludeId: t.correlationId };
    generate(query);
    recCard.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function addWorkNote(id, steps) {
    const all = store.get("itsm.snowNotes", {});
    all[id] = [...(all[id] || []), { t: Math.floor(Date.now() / 1000), type: "Work notes", by: store.currentUser(),
      text: `AI-assisted next steps accepted: ${steps.map((s, i) => `${i + 1}. ${s.text}`).join(" ")} [cited ${[...new Set(steps.flatMap((s) => s.sources))].join(", ")}]` }];
    store.set("itsm.snowNotes", all);
    renderQueue();
  }

  /** Show the ServiceNow column name next to each structured-intake field. */
  function labelFields() {
    for (const [key, l] of Object.entries(fieldLabels)) l.querySelector(".itsm-api").textContent = F()[key];
  }

  function showMapping() {
    const { body } = drawer("Field mapping", { subtitle: "Helix archive → ServiceNow incident" });
    const rows = Object.keys(SOURCES.helix.fields).map((k) => h("tr",
      h("td", k), h("td.mono", SOURCES.servicenow.fields[k]), h("td.mono", SOURCES.helix.fields[k])));
    body.append(
      h("p.t2.small", `At cutover (${fmtDate(cutoverTs)}) the Helix incident history was migrated into the knowledge index as a read-only archive. The retriever indexes canonical fields, so archived and live records are searched together and shown with ServiceNow field names. Each migrated record keeps its original Helix incident number for traceability.`),
      h("div.table-wrap", h("table.data", h("thead", h("tr", h("th", "Canonical"), h("th", "ServiceNow (incident) · live"), h("th", "BMC Helix (HPD:Help Desk) · archive"))), h("tbody", rows))));
  }

  function resetIntake() {
    ta.value = "";
    [fSummary, fDetails, fProduct, fCI].forEach((i) => (i.value = ""));
    [fCat1, fCat2, fCat3, fSite, fPri, fImp, fUrg].forEach((s) => (s.value = ""));
    search.value = "";
    results.replaceChildren();
    state.loaded = null;
    loadedBox.replaceChildren();
    msg.replaceChildren();
  }

  // ---- existing-ticket search
  search.addEventListener("input", () => {
    const q = search.value.trim();
    results.replaceChildren();
    if (q.length < 2) return;
    const qt = tokenize(q);
    const idMatch = q.toUpperCase().replace(/\s/g, "");
    const list = tickets.filter((t) => t.id.includes(idMatch) ||
      (qt.length && qt.every((w) => tokenize(`${t.summary} ${t.product} ${t.ci}`).includes(w)))).slice(0, 8);
    if (!list.length) {
      results.append(h("div.itsm-msg.warn", icon("alert"), /^INC\d+/i.test(q) ? `No incident ${q.toUpperCase()} in the Helix archive.` : "No matching tickets."));
      return;
    }
    for (const t of list) results.append(h("button.itsm-pick", { type: "button", onclick: () => pickTicket(t) },
      h("span.mono.small", t.id), h("span", t.summary), h("span.small.muted", t.migratedTo ? `Transferred → ${t.migratedTo} · ${t.group}` : `${t.status} · ${t.group}`)));
  });

  function pickTicket(t) {
    setMode("existing");
    state.loaded = t;
    results.replaceChildren();
    search.value = t.id;
    loadedBox.replaceChildren(h("div.itsm-loaded",
      h("div.row", h("span.chip", t.id), statusPill(t.status === "Closed" ? "good" : "info", t.status), h("span.small.muted", fmtDate(t.reported, true)),
        h("button.btn.ghost.sm", { type: "button", style: { marginLeft: "auto" }, onclick: () => openTicket(t.id) }, "View")),
      h("b", t.summary),
      h("p.small.t2", redact(shorten(t.details, 260))),
      h("p.small.muted", "Replayed from the Helix archive. This ticket is excluded from its own evidence set.")));
    msg.replaceChildren();
  }

  // ---- build + validate the query
  function buildQuery() {
    if (state.mode === "text") {
      const v = ta.value.trim();
      const firstLine = v.split(/\n|(?<=[.!?])\s/)[0] || "";
      return { summary: firstLine.slice(0, 120), details: v, raw: v };
    }
    if (state.mode === "fields") {
      return {
        summary: fSummary.value.trim(), details: fDetails.value.trim(),
        cat: [fCat1.value, fCat2.value, fCat3.value].filter(Boolean), product: fProduct.value.trim(), ci: fCI.value.trim(),
        site: fSite.value, priority: fPri.value, impact: fImp.value, urgency: fUrg.value,
        raw: [fSummary.value, fDetails.value].join(" ").trim(),
      };
    }
    const t = state.loaded;
    return t ? { summary: t.summary, details: t.details, cat: t.cat, product: t.product, ci: t.ci, raw: `${t.summary} ${t.details}`, id: t.id } : null;
  }

  /** Returns { ok, query, notes[] }. quiet = only clear errors while typing. */
  function validate(quiet = false) {
    const q = buildQuery();
    const notes = [];
    if (quiet) { if (msg.querySelector(".err")) msg.replaceChildren(); return null; }
    msg.replaceChildren();
    if (!q) return fail(state.mode === "existing" ? "Pick an archived ticket to replay first." : "Enter a ticket description.");
    const meaningful = tokenize(`${q.raw} ${(q.cat || []).join(" ")} ${q.product || ""} ${q.ci || ""}`);
    if (!q.raw && !meaningful.length) return fail("Enter a ticket description, or at least a summary or product.");
    if (q.raw.length > 0 && q.raw.length < 12 && meaningful.length < 2) return fail("That's too short to match reliably. Add the symptom, the system or product, and where it happens.");
    if (!meaningful.length) return fail("No searchable terms found. Describe the symptom in words (error text, system, location).");
    if (/^[^a-z]*$/i.test(q.raw) && q.raw) return fail("The description doesn't contain any words. Paste the ticket text, not just codes or numbers.");
    if (meaningful.length < 3) notes.push(["warn", "Short or vague input; results may be ambiguous. Add details for better matches."]);
    if (q.raw.length > 6000) {
      q.details = q.details.slice(0, 6000);
      notes.push(["warn", "Long input truncated to the first 6,000 characters."]);
    }
    const found = PII.filter((p) => `${q.raw} ${q.details || ""}`.match(p.re));
    if (found.length) {
      for (const p of PII) { q.details = (q.details || "").replace(p.re, "[redacted]"); q.summary = (q.summary || "").replace(p.re, "[redacted]"); }
      notes.push(["warn", `Redacted ${found.map((p) => p.label).join(", ")} before retrieval. Don't paste personal information.`]);
    }
    for (const [cls, text] of notes) msg.append(h(`div.itsm-msg.${cls}`, icon("alert"), text));
    return { ok: true, query: q };
    function fail(text) { msg.append(h("div.itsm-msg.err", icon("alert"), text)); return { ok: false }; }
  }

  // ---- generate
  async function generate(override) {
    let q = override;
    if (!q) {
      const v = validate();
      if (!v?.ok) return;
      q = v.query;
      if (q.id) q.replay = true;   // a historical Helix ticket replayed
    }
    state.subject = q.id || nextIncidentNumber();
    state.decision = null;
    state.dismissed = new Set();
    genBtn.disabled = true;
    renderLoading();
    const t0 = performance.now();
    try {
      const sources = sourcesOn();
      const hits = await retrieveSimilar(q, corpus(), { k: 6, excludeId: q.excludeId || q.id, sources });
      const retrievalMs = performance.now() - t0;
      const res = await recommendNextSteps(q, hits, { sources, retrievalMs });
      res.timeToAnswerMs = Math.round(performance.now() - t0);
      res.query = q;
      state.result = res;
      state.shownAt = Date.now();
      store.audit({
        module: "itsm", action: "recommendation.generated", subject: state.subject,
        detail: { summary: shorten(q.summary || q.details, 120), platform: "servicenow", knowledge: res.ai.knowledge, replayOf: q.replay ? q.id : null, openIncident: q.open ? q.id : null, timeToAnswerMs: res.timeToAnswerMs, steps: res.steps.length, citations: res.ai.sources.length, confidence: res.confidence, inputMode: state.mode },
        ai: res.ai,
      });
      renderResult();
      renderQueue();
    } catch (err) {
      console.error(err);
      recCard.replaceChildren(h("div.card-head", h("h3", "Recommendation")), h("div.itsm-msg.err", icon("alert"), "The model call failed: " + err.message + ". Fall back to a manual search."));
    } finally {
      genBtn.disabled = false;
    }
  }

  // ---- rendering: recommendation
  function renderEmpty() {
    recCard.replaceChildren(
      h("div.card-head", aiTag("Next best step")),
      h("div.empty-state.itsm-empty", icon("spark"), h("b", "No recommendation yet"),
        h("span", "Pick an open incident above, or describe a new ticket on the left, and choose “Get next best step”. Ranked steps appear here with the historical tickets they came from.")));
    simCard.querySelector(".itsm-sim").replaceChildren(h("div.empty-state", "Evidence tickets appear here after retrieval."));
  }

  function renderLoading() {
    recCard.replaceChildren(
      h("div.card-head", aiTag("Next best step"), h("span.end.row.small.muted", h("span.spinner"), "Retrieving similar tickets · generating…")),
      ...[90, 75, 82, 60].map((w) => h("div.itsm-skel", h("div.skeleton", { style: { width: w + "%" } }), h("div.skeleton", { style: { width: w * 0.6 + "%", height: "8px" } }))));
    simCard.querySelector(".itsm-sim").replaceChildren(...[1, 2, 3].map(() => h("div.itsm-skel", h("div.skeleton"), h("div.skeleton", { style: { width: "50%", height: "8px" } }))));
  }

  function renderResult() {
    const r = state.result;
    const f = F();
    const decided = state.decision;
    const steps = r.steps.filter((s) => !state.dismissed.has(s.id));
    const head = h("div.card-head",
      aiTag("Next best step"),
      advisory(),
      h("span.end.row",
        h("span.chip", { title: "retrieval + generation" }, `${(r.timeToAnswerMs / 1000).toFixed(1)}s · ${r.timings.retrievalMs}ms + ${r.timings.generationMs}ms`)));

    const triage = h("div.itsm-triage",
      triageCell("Suggested group", r.group, f.group),
      triageCell("Category", r.category || "—", f.cat),
      triageCell("Product", r.product || "—", f.prodCat),
      triageCell("Est. time to resolve", r.etaHours != null ? fmtDur(r.etaHours) : "—", "median of similar"),
      triageCell("Overall confidence", fmtPct(r.confidence, 0), confLabel(r.confidence)));

    const warn = r.warnings.map((w) => h("div.itsm-msg.warn", icon("alert"), w));

    const list = h("ol.itsm-steps");
    steps.forEach((s, i) => {
      list.append(h("li.itsm-step", { class: s.edited ? "edited" : "" },
        h("div.itsm-rank", String(i + 1)),
        h("div.itsm-step-body",
          h("div.itsm-step-text", s.text, s.edited ? h("span.pill.info", { style: { marginLeft: "8px" } }, "edited") : null),
          h("div.itsm-step-meta",
            h("div.itsm-conf", { title: `Confidence ${fmtPct(s.confidence, 0)}` },
              h("div.bar-meter", h("i", { style: { width: s.confidence * 100 + "%", background: confColor(s.confidence) } })),
              h("span.small.num", fmtPct(s.confidence, 0))),
            h("span.small.t2", s.rationale)),
          h("div.itsm-cites", h("span.small.muted", "Sources"),
            ...s.sources.map((id) => h("button.itsm-cite", { type: "button", onclick: () => openTicket(id), title: byId.get(id)?.summary }, icon("link"), originTag(byId.get(id)), id)))),
        h("div.itsm-step-actions",
          h("button.btn.ghost.sm", { type: "button", title: "Edit step", disabled: !!decided, onclick: () => editStep(s) }, icon("edit")),
          h("button.btn.ghost.sm", { type: "button", title: "Dismiss step", disabled: !!decided, onclick: () => dismissStep(s) }, icon("x")))));
    });
    if (!steps.length) list.append(h("div.empty-state", r.steps.length ? "All steps dismissed." : "No steps could be grounded in historical tickets."));

    const decisionBar = decided
      ? h("div.itsm-decided", { class: decided.decision },
          decided.decision === "accepted" ? statusPill("good", "Accepted") : statusPill("critical", "Rejected"),
          h("span.small.t2", decided.decision === "accepted" ? state.result.query.open ? `Logged as a work note on ${state.subject}. Apply the steps in ServiceNow; the incident stays open until the fix is confirmed.` : state.result.query.id ? "Replay only: the archived ticket is unchanged." : "The agent applies the fix in ServiceNow and documents it there. Added to the knowledge index as a resolved ServiceNow incident." : `Reason: ${decided.reason}${decided.note ? " · " + decided.note : ""}`),
          h("button.btn.ghost.sm", { type: "button", style: { marginLeft: "auto" }, onclick: () => { state.decision = null; renderResult(); } }, "Change decision"))
      : h("div.itsm-actions",
          h("span.small.muted", "Your decision is logged with the cited sources."),
          h("div.row", { style: { marginLeft: "auto" } },
            h("button.btn.ghost", { type: "button", onclick: copyWorkNote, title: "Copy as a work note to paste into the ticket" }, "Copy as work note"),
            h("button.btn.bad", { type: "button", onclick: reject }, icon("x"), "Reject"),
            h("button.btn.good", { type: "button", onclick: accept, disabled: !steps.length }, icon("check"), "Accept")));

    recCard.replaceChildren(head,
      h("div.small.muted.itsm-subject", "For ", h("button.itsm-cite", { type: "button", onclick: () => openTicket(state.subject), disabled: !byId.has(state.subject) }, state.subject),
        r.query.open ? ` · open · ${byId.get(state.subject)?.status || ""} · ` : " · ", shorten(r.query.summary || r.query.details, 90)),
      ...warn, triage, list, decisionBar);
    renderSimilar();
  }

  function triageCell(label, value, sub) {
    return h("div.itsm-tri", h("span.small.muted", label), h("b", value), h("span.itsm-api", sub));
  }

  function renderSimilar() {
    const r = state.result;
    const box = simCard.querySelector(".itsm-sim");
    if (!r.hits.length) { box.replaceChildren(h("div.empty-state", "No resolved incidents matched this description.")); return; }
    const f = F();
    box.replaceChildren(...r.hits.map((hit) => {
      const t = hit.ticket;
      const ttr = t.resolved && t.reported ? (t.resolved - t.reported) / 3600 : null;
      return h("button.itsm-simrow", { type: "button", onclick: () => openTicket(t.id) },
        h("div.itsm-sim-score",
          h("b.num", Math.round(hit.sim * 100)), h("span.small.muted", "match"),
          h("div.bar-meter", h("i", { style: { width: hit.sim * 100 + "%" } }))),
        h("div.itsm-sim-body",
          h("div.row.wrap", originTag(t, true), h("span.chip", t.id), h("span.small.muted", `${f.group}: ${t.group}`), h("span.small.muted", `· resolved in ${fmtDur(ttr)}`)),
          h("div.itsm-sim-title", highlight(t.summary, hit.matchedTerms)),
          h("div.small.t2", h("span.muted", f.resolution + ": "), highlight(shorten(t.resolution, 180), hit.matchedTerms))),
        icon("chevron", "itsm-chev"));
    }));
  }

  // ---- copilot chat
  function chatHint() {
    return h("div.empty-state.small", "Ask follow-up questions about the incident on the left. Answers cite the historical incidents they rely on.");
  }
  function renderAnswer(el, text, valid) {
    // plain text with clickable [INC…] citations; **bold** kept as bold
    el.replaceChildren();
    for (const part of text.split(/(\[?INC\d{12}\]?|\*\*[^*]+\*\*)/g)) {
      if (!part) continue;
      const m = part.match(/INC\d{12}/);
      if (m) el.append(h("button.itsm-cite", { type: "button", class: valid && !valid.has(m[0]) ? "bad" : "", title: byId.get(m[0])?.summary || "not in retrieved evidence", onclick: () => openTicket(m[0]) }, icon("link"), m[0]));
      else if (part.startsWith("**")) el.append(h("b", part.slice(2, -2)));
      else el.append(part);
    }
  }
  async function askCopilot() {
    const text = chatIn.value.trim();
    if (!text || chat.busy) return;
    const q = state.result?.query || buildQuery();
    chatIn.value = "";
    if (chat.messages.length === 0) chatLog.replaceChildren();
    chat.messages.push({ role: "user", content: text });
    chatLog.append(h("div.itsm-bubble.user", text));
    const body = h("div.itsm-bubble-text");
    const meta = h("div.small.muted", h("span.spinner"), " searching incidents…");
    const bubble = h("div.itsm-bubble.bot", body, meta);
    chatLog.append(bubble);
    chatLog.scrollTop = chatLog.scrollHeight;
    chat.busy = true; chatBtn.disabled = true;
    chat.ctrl = new AbortController();
    let answer = "", hits = [];
    const t0 = performance.now();
    try {
      await copilotChat({ ticket: q && q.raw ? q : null, messages: chat.messages, sources: sourcesOn(), extra: snowKb() }, (ev) => {
        if (ev.type === "sources") { hits = ev.hits; meta.replaceChildren(h("span.spinner"), ` ${hits.length} incidents retrieved · writing…`); }
        else if (ev.type === "delta") { answer += ev.text; renderAnswer(body, answer); chatLog.scrollTop = chatLog.scrollHeight; }
        else if (ev.type === "error") throw new Error(ev.message);
        else if (ev.type === "done") {
          const valid = new Set(hits.map((x) => x.id));
          renderAnswer(body, answer, valid);
          meta.replaceChildren(`${ev.model} · ${((performance.now() - t0) / 1000).toFixed(1)}s · cites ${ev.cited.length} of ${hits.length} retrieved`,
            ev.uncited.length ? h("span.pill.warning", { style: { marginLeft: "8px" } }, `${ev.uncited.length} unverified citation(s)`) : null);
          store.audit({ module: "itsm", action: "copilot.answered", subject: state.subject || "chat",
            detail: { question: shorten(text, 160), chars: answer.length, ms: ev.ms, uncited: ev.uncited },
            ai: { ...provenanceLike(ev.model), sources: ev.cited } });
        }
      }, chat.ctrl.signal);
      chat.messages.push({ role: "assistant", content: answer });
    } catch (err) {
      if (err.name !== "AbortError") meta.replaceChildren(h("span.pill.critical", "error"), " ", String(err.message || err));
      chat.messages.pop();
    } finally {
      chat.busy = false; chatBtn.disabled = false;
    }
  }
  function provenanceLike(model) {
    return { model, version: "vllm-local", mode: AI.MODE, at: new Date().toISOString() };
  }

  // ---- step-level feedback
  async function editStep(s) {
    const text = await modal("Edit step", (box, done) => {
      const t = h("textarea", { rows: 4 }, s.text);
      t.value = s.text;
      box.append(h("p.small.t2", "Your edit is kept with this recommendation and logged for model feedback."), t,
        h("div.row", { style: { justifyContent: "flex-end" } },
          h("button.btn.ghost", { type: "button", onclick: () => done(null) }, "Cancel"),
          h("button.btn.primary", { type: "button", onclick: () => done(t.value.trim()) }, "Save edit")));
    });
    if (!text || text === s.text) return;
    store.audit({ module: "itsm", action: "recommendation.edited", subject: state.subject, decision: "edited",
      detail: { step: s.id, before: s.text, after: text }, ai: { ...state.result.ai, sources: s.sources } });
    s.text = text;
    s.edited = true;
    renderResult();
  }

  function dismissStep(s) {
    state.dismissed.add(s.id);
    store.audit({ module: "itsm", action: "recommendation.step_dismissed", subject: state.subject, decision: "dismissed",
      detail: { step: s.id, text: s.text }, ai: { ...state.result.ai, sources: s.sources } });
    renderResult();
  }

  // ---- set-level decisions
  function activeSteps() { return state.result.steps.filter((s) => !state.dismissed.has(s.id)); }

  function accept() {
    const steps = activeSteps();
    state.decision = { decision: "accepted" };
    store.audit({ module: "itsm", action: "recommendation.accepted", subject: state.subject, decision: "accepted",
      detail: { timeToDecisionMs: Date.now() - state.shownAt, steps: steps.map((s) => ({ id: s.id, text: s.text, edited: !!s.edited })), dismissed: [...state.dismissed], platform: "servicenow", replayOf: state.result.query.replay ? state.result.query.id : null, openIncident: state.result.query.open ? state.result.query.id : null },
      ai: { ...state.result.ai, sources: [...new Set(steps.flatMap((s) => s.sources))] } });
    if (state.result.query.open) {
      addWorkNote(state.subject, steps);
      toast(`Accepted. Work note added to ${state.subject}; it stays open until the fix is confirmed.`, "good");
    } else if (!state.result.query.id) {
      addSnowIncident(state.subject, state.result, steps);
      toast(`Accepted. ${state.subject} added to the ServiceNow knowledge index.`, "good");
    } else toast("Recommendation accepted and logged.", "good");
    renderResult();
  }

  async function reject() {
    const res = await modal("Reject recommendation", (box, done) => {
      const r = h("select", ...REJECT_REASONS.map((x) => h("option", { value: x }, x)));
      const n = h("textarea", { rows: 3, placeholder: "What would have been the right step? (optional, helps evaluation)" });
      const err = h("div.itsm-msg.err", { hidden: true }, icon("alert"), "Add a note when the reason is “Other”.");
      box.append(h("p.small.t2", "A reason is required. It is logged with the recommendation and the cited sources."),
        h("label.field", "Reason", r), h("label.field", "Note", n), err,
        h("div.row", { style: { justifyContent: "flex-end" } },
          h("button.btn.ghost", { type: "button", onclick: () => done(null) }, "Cancel"),
          h("button.btn.bad", { type: "button", onclick: () => {
            if (r.value === "Other" && !n.value.trim()) { err.hidden = false; return; }
            done({ reason: r.value, note: n.value.trim() });
          } }, "Reject")));
    });
    if (!res) return;
    state.decision = { decision: "rejected", ...res };
    store.audit({ module: "itsm", action: "recommendation.rejected", subject: state.subject, decision: "rejected", reason: res.reason,
      detail: { note: res.note, timeToDecisionMs: Date.now() - state.shownAt, platform: "servicenow", replayOf: state.result.query.replay ? state.result.query.id : null, openIncident: state.result.query.open ? state.result.query.id : null }, ai: state.result.ai });
    toast("Recommendation rejected. Reason logged.", "bad");
    renderResult();
  }

  async function copyWorkNote() {
    const r = state.result;
    const text = [`AI-assisted next steps (advisory, reviewed by ${store.currentUser()}):`,
      ...activeSteps().map((s, i) => `${i + 1}. ${s.text} [${s.sources.join(", ")}]`),
      `Suggested group: ${r.group}`].join("\n");
    try { await navigator.clipboard.writeText(text); toast("Work note copied."); }
    catch { toast("Clipboard unavailable in this browser context.", "bad"); }
  }

  // ---- source ticket drawer
  function openTicket(id) {
    const t = byId.get(id);
    if (!t) { toast(`${id} not found in the knowledge index.`, "bad"); return; }
    const f = F();
    const archived = t.origin === "helix";
    const open = openIds.has(t.id);
    const worklog = [...t.worklog, ...(open ? notesOf(t.id) : [])];
    // Migrated records show the ServiceNow field and the Helix column it came from.
    const api = (key) => (archived ? `${f[key]} ← ${H[key]}` : f[key]);
    const lab = (key, text) => h("dt", text, h("span.itsm-api", api(key)));
    const dur = (a, b) => (a && b ? fmtDur((b - a) / 3600) : "–");
    const { body } = drawer(t.id, {
      subtitle: archived ? (t.migratedTo ? `BMC Helix archive · history only · transferred open to ServiceNow ${t.migratedTo} at cutover (${fmtDate(cutoverTs)})` : `BMC Helix archive · migrated to ServiceNow at cutover (${fmtDate(cutoverTs)}) · read-only`)
        : open ? `ServiceNow · open incident · migrated from Helix ${t.correlationId} at cutover` : "ServiceNow · incident · resolved after cutover",
      actions: [originTag(t, true), statusPill(["Closed", "Resolved"].includes(t.status) ? "good" : "info", t.status),
        t.migratedTo ? h("button.btn.sm", { type: "button", onclick: () => openTicket(t.migratedTo) }, `Open ${t.migratedTo}`) : null,
        open ? h("button.btn.sm", { type: "button", onclick: () => openTicket(t.correlationId) }, `Helix ${t.correlationId.slice(-6)}`) : null] });
    body.append(
      section("Summary", lab("summary", "Summary"), t.summary),
      section("Details", lab("details", "Details"), redact(t.details || "")),
      h("div.stack", h("h4", "Classification"), h("dl.kv",
        lab("cat", "Category"), h("dd", t.cat.filter(Boolean).join(" › ") || "— (uncategorised)"),
        lab("prodCat", "Product category"), h("dd", t.prodCat.filter(Boolean).join(" › ") || "—"),
        h("dt", "Product"), h("dd", [t.product, t.manufacturer].filter(Boolean).join(" · ") || "—"),
        lab("ci", "CI"), h("dd", t.ci || "—"),
        lab("serviceCI", "Service"), h("dd", t.serviceCI || "—"),
        lab("group", "Group"), h("dd", t.group),
        lab("assignee", "Assignee"), h("dd", t.assignee || "—"),
        lab("priority", "Priority"), h("dd", `${t.priority} · impact ${t.impact} · urgency ${t.urgency}`),
        lab("site", "Site"), h("dd", `${t.site}${t.dept ? " · " + t.dept : ""}`),
        h("dt", "Transfers"), h("dd", `${t.transfers}${t.major ? " · major incident" : ""}`))),
      h("div.stack.itsm-resolution", h("h4", "Resolution ", h("span.itsm-api", api("resolution"))),
        h("p", t.resolution || "—"),
        h("span.small.muted", `${f.resCat}: ${t.resCat.filter(Boolean).join(" › ") || "—"}`)),
      t.acceptedFrom ? h("p.small.muted", `Resolution recorded from an accepted AI recommendation (cited ${t.acceptedFrom.join(", ")}).`) : null,
      h("div.stack", h("h4", "Timeline"), h("div.itsm-timeline",
        tl("Reported", t.reported, api("reported")), tl("Responded", t.responded, api("responded"), dur(t.reported, t.responded)),
        tl("Resolved", t.resolved, api("resolved"), dur(t.reported, t.resolved)), tl("Closed", t.closed, api("closed")))),
      t.holdReason ? h("p.small.muted", `On hold: ${t.holdReason}`) : null,
      t.statusReason ? h("p.small.muted", `Status reason: ${t.statusReason}${t.preMigrationStatus ? ` (was ${t.preMigrationStatus})` : ""}`) : null,
      h("div.stack", h("h4", `Work log `, h("span.itsm-api", api("worklog")), h("span.muted", ` · ${worklog.length}`)),
        worklog.length ? h("ol.itsm-worklog", ...worklog.map((w) => h("li",
          h("div.row.small", h("b", w.type), h("span.muted", w.by), h("span.muted", { style: { marginLeft: "auto" } }, fmtDate(w.t, true))),
          h("p.small.t2", w.text)))) : h("p.small.muted", "No work notes.")),
      t.related.length ? h("div.small.muted", "Related CIs: ", t.related.join(", ")) : null);
    function section(title, dt, text) {
      return h("div.stack", { style: { gap: "4px" } }, h("h4", title, " ", dt.querySelector(".itsm-api")), h("p", text || "—"));
    }
    function tl(label, ts, api, d) {
      return h("div.itsm-tl", { class: ts ? "" : "none" }, h("i"), h("b", label), h("span.small.t2", ts ? fmtDate(ts, true) : "—"), h("span.itsm-api", api), d ? h("span.small.muted", "+" + d) : null);
    }
  }

  // ---- ServiceNow incidents created in this demo
  function nextIncidentNumber() {
    const n = store.get("itsm.snowSeq", 10000) + 1;
    store.set("itsm.snowSeq", n);
    return "INC" + String(n).padStart(7, "0");   // ServiceNow default numbering
  }

  function addSnowIncident(id, r, steps) {
    const q = r.query;
    const now = Math.floor(Date.now() / 1000);
    const opened = Math.floor(state.shownAt / 1000) - Math.round(r.timeToAnswerMs / 1000);
    const cat = q.cat?.length ? q.cat : (r.category || "").split(" › ");
    const rec = {
      id, origin: "servicenow", summary: q.summary || shorten(q.details, 100), details: q.details || q.summary,
      status: "Resolved", priority: q.priority || "Medium", impact: q.impact || "", urgency: q.urgency || "",
      serviceType: "", source: "Self-service", site: q.site || "", dept: "", group: r.group, assignee: store.currentUser(),
      cat: [cat[0] || "", cat[1] || "", cat[2] || ""], prodCat: ["", "", ""], product: q.product || r.product || "",
      manufacturer: "", ci: q.ci || "", serviceCI: "", reported: opened, responded: opened, resolved: now, closed: null,
      reopened: null, resolution: steps.map((s) => s.text.replace(/[.;]+$/, "")).join(". ") + ".",
      resCat: ["Solved (Permanently)", "", ""], transfers: 0, major: false, related: [], domain: "", scenario: "",
      acceptedFrom: [...new Set(steps.flatMap((s) => s.sources))],
      worklog: [{ t: now, type: "Work notes", by: store.currentUser(), text: `AI-assisted next steps accepted: ${steps.map((s, i) => `${i + 1}. ${s.text}`).join(" ")}` }],
    };
    store.set("itsm.snowKb", [rec, ...snowKb().filter((t) => t.id !== id)].slice(0, 500));
    byId = byIdOf();
  }

  // ---- KPIs + recent decisions from the audit log
  function refreshStats() {
    const ev = store.auditList().filter((e) => e.module === "itsm");
    const gen = ev.filter((e) => e.action === "recommendation.generated");
    const acc = ev.filter((e) => e.action === "recommendation.accepted").length;
    const rej = ev.filter((e) => e.action === "recommendation.rejected").length;
    const avg = (xs) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
    const tta = avg(gen.map((e) => e.detail?.timeToAnswerMs).filter((x) => x != null));
    const cites = avg(gen.map((e) => e.detail?.citations).filter((x) => x != null));
    const decided = acc + rej;
    kpiRow.replaceChildren(
      kpi({ label: "Recommendations generated", icon: "spark", value: gen.length.toLocaleString(), foot: "this browser · all sessions" }),
      kpi({ label: "Agent acceptance rate", icon: "check", value: decided ? fmtPct(acc / decided, 0) : "–", foot: decided ? `${acc} accepted · ${rej} rejected` : "No decisions yet" }),
      kpi({ label: "Avg time-to-answer", icon: "clock", value: tta != null ? (tta / 1000).toFixed(1) : "–", unit: tta != null ? "s" : "", foot: "vs. manual search of the same tickets (measure on Day 3)" }),
      kpi({ label: "Citations per answer", icon: "link", value: cites != null ? cites.toFixed(1) : "–", foot: "source tickets backing each answer" }));

    const box = recent.querySelector(".itsm-recent");
    const dec = ev.filter((e) => e.decision && e.decision !== "dismissed").slice(-6).reverse();
    box.replaceChildren(...(dec.length ? dec.map((e) => h("div.itsm-recent-row",
      e.decision === "accepted" ? statusPill("good", "Accepted") : e.decision === "rejected" ? statusPill("critical", "Rejected") : statusPill("info", "Edited"),
      h("span.mono.small", e.subject),
      h("span.small.t2.itsm-ell", e.reason || e.detail?.after || (e.detail?.steps ? `${e.detail.steps.length} steps` : "")),
      h("span.small.muted", timeAgo(e.ts)))) : [h("div.empty-state", "Accepted, edited and rejected recommendations show up here.")]));
  }

  return () => window.removeEventListener("audit", onAudit);
}

// ---------------------------------------------------------------- helpers
/** Small badge marking where a record lives: live ServiceNow or the migrated Helix archive. */
function originTag(t, long = false) {
  if (!t) return null;
  return t.origin === "servicenow"
    ? h("span.itsm-origin.snow", { title: "ServiceNow incident (post-cutover)" }, long ? "ServiceNow" : "SN")
    : h("span.itsm-origin.helix", { title: "Migrated from the BMC Helix archive" }, long ? "Helix archive" : "Helix");
}

function shorten(s, n) {
  s = (s || "").replace(/\s+/g, " ").trim();
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
function redact(s) {
  for (const p of PII) s = s.replace(p.re, "[redacted]");
  return s;
}
function confColor(c) {
  return c >= 0.75 ? "var(--series-3)" : c >= 0.5 ? "var(--series-1)" : "var(--series-4)";
}
function confLabel(c) {
  return c >= 0.75 ? "strong evidence" : c >= 0.5 ? "moderate evidence" : "weak evidence";
}
function highlight(text, terms) {
  const set = new Set(terms);
  const frag = document.createDocumentFragment();
  for (const part of (text || "").split(/(\b[\w’'-]+\b)/)) {
    const tok = tokenize(part)[0];
    if (tok && set.has(tok)) frag.append(h("mark.itsm-hl", part));
    else frag.append(part);
  }
  return frag;
}

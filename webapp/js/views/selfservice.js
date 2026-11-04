// IT Self-Service — an employee describes a problem, the local assistant suggests safe fixes
// drawn from how similar incidents were resolved (other people's details are scrubbed
// server-side), and opens a pre-routed incident when the issue needs IT staff.
import { h, icon, aiTag, advisory, statusPill, toast, fmtDate } from "../ui.js";
import * as store from "../store.js";
import { callApi, chatModel } from "../ai/config.js";

const EXAMPLES = [
  "Outlook keeps crashing when I open it, webmail still works",
  "My PIV card isn't recognized when I log in this morning",
  "VPN won't connect from home, says gateway unreachable",
  "I clicked a link in an email about my paycheck and now I'm worried",
];

export async function render(root) {
  const state = { person: null, messages: [], busy: false, draft: null };
  const personSel = h("select", { "aria-label": "Demo employee" });
  const head = h("div.view-head",
    h("div.titles",
      h("h2", "IT Self-Service"),
      h("p.lede", "Describe what's wrong in your own words. The assistant suggests fixes you can safely try yourself and, if IT needs to step in, opens a ticket routed to the right team.")),
    h("div.actions", h("label.role-pick", "Signed in as", personSel)));

  const log = h("div.ss-log", { "aria-live": "polite" });
  const input = h("textarea", { rows: 3, placeholder: "e.g. My laptop is really slow since this morning and Teams keeps freezing", "aria-label": "Describe your problem" });
  const send = h("button.btn.primary", { type: "submit" }, icon("send"), "Send");
  const chatCard = h("form.card.ss-chat", { onsubmit: (e) => { e.preventDefault(); ask(); } },
    h("div.card-head", aiTag("IT assistant"), advisory("Suggestions only · never share passwords"),
      h("span.end", h("button.btn.ghost.sm", { type: "button", onclick: reset }, "Start over"))),
    log,
    h("div.itsm-chips", h("span.small.muted", "Try:"), ...EXAMPLES.map((t) => h("button.btn.sm.ghost.itsm-chip", { type: "button", onclick: () => { input.value = t; ask(); } }, t.split(",")[0].slice(0, 34)))),
    h("div.itsm-chatbar", input, send));
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } });

  const ticketBox = h("div");
  const mine = h("div.card", h("div.card-head", h("h3", "My tickets"), h("span.sub", "from the archive")), h("div.ss-mine"));

  root.append(head, h("div.grid.g-main-side", h("div.stack", chatCard, ticketBox), h("div.stack", mine)));
  reset();

  try {
    const { personas } = await callApi("/selfservice/personas", undefined);
    for (const p of personas) personSel.append(h("option", { value: p.person_id }, `${p.name} · ${p.title}, ${p.site}`));
    personSel.value = store.get("ss.person", personas[0]?.person_id) || personas[0]?.person_id;
    personSel.addEventListener("change", () => { store.set("ss.person", personSel.value); pickPerson(personas); });
    pickPerson(personas);
  } catch (err) {
    log.replaceChildren(h("div.itsm-msg.err", icon("alert"), `The local assistant is unavailable (${err.message}). Call the Service Desk.`));
    send.disabled = true;
  }

  function pickPerson(personas) {
    state.person = personas.find((p) => p.person_id === personSel.value) || personas[0];
    loadMine();
  }

  async function loadMine() {
    const box = mine.querySelector(".ss-mine");
    box.replaceChildren(h("div.row.muted.small", h("span.spinner"), "Loading…"));
    try {
      const { tickets } = await callApi(`/selfservice/my-tickets?person_id=${encodeURIComponent(state.person.person_id)}`, undefined);
      box.replaceChildren(...(tickets.length ? tickets.slice(0, 10).map((t) => h("div.ss-ticket",
        h("div.row.wrap", h("span.chip", t.id.slice(-6)), statusPill(["Closed", "Resolved"].includes(t.status) ? "good" : "warning", t.status), h("span.small.muted", fmtDate(t.reported))),
        h("div.small", t.summary))) : [h("div.empty-state", "No tickets yet.")]));
    } catch (err) {
      box.replaceChildren(h("div.empty-state", "Couldn't load your tickets."));
    }
  }

  function reset() {
    state.messages = [];
    state.draft = null;
    ticketBox.replaceChildren();
    log.replaceChildren(h("div.ss-bubble.bot", "Hi! What's going on? Tell me what you were doing, what you see (any error message), and where you are (building, plant, or teleworking)."));
  }

  function bubble(role, text) {
    const el = h(`div.ss-bubble.${role}`);
    for (const part of text.split(/(\*\*[^*]+\*\*)/g)) el.append(part.startsWith("**") ? h("b", part.slice(2, -2)) : part);
    log.append(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  async function ask() {
    const text = input.value.trim();
    if (!text || state.busy || !state.person) return;
    input.value = "";
    state.messages.push({ role: "user", content: text });
    bubble("user", text);
    const wait = h("div.ss-bubble.bot.muted", h("span.spinner"), " Looking at how similar issues were fixed…");
    log.append(wait);
    state.busy = true; send.disabled = true;
    try {
      const r = await callApi("/selfservice/chat", { messages: state.messages, person_id: state.person.person_id, model: chatModel() });
      wait.remove();
      bubble("bot", r.reply);
      state.messages.push({ role: "assistant", content: r.reply });
      store.audit({ module: "itsm", action: "selfservice.answered", subject: state.person.person_id,
        detail: { question: text.slice(0, 160), needs_ticket: r.needs_ticket }, ai: r.ai });
      if (r.needs_ticket) offerTicket(r.ticket_summary, r.ticket_details);
    } catch (err) {
      wait.replaceChildren(h("span.pill.critical", "error"), " ", err.message);
      state.messages.pop();
    } finally {
      state.busy = false; send.disabled = false;
    }
  }

  function offerTicket(summary, details) {
    const fS = h("input", { type: "text", value: summary || "" });
    fS.value = summary || "";
    const fD = h("textarea", { rows: 4 });
    fD.value = details || "";
    const submit = h("button.btn.primary", { type: "button", onclick: () => openTicket(fS.value, fD.value, submit) }, icon("ticket"), "Open ticket");
    ticketBox.replaceChildren(h("div.card.ss-offer",
      h("div.card-head", h("h3", "Open a ticket with IT?"), h("span.sub", "review and edit before sending")),
      h("label.field", "Summary", fS), h("label.field", "Details", fD),
      h("div.row", submit, h("button.btn.ghost", { type: "button", onclick: () => ticketBox.replaceChildren() }, "Not now"))));
  }

  async function openTicket(summary, details, btn) {
    if (!summary.trim()) { toast("Add a short summary first", "bad"); return; }
    btn.disabled = true;
    btn.replaceChildren(h("span.spinner"), "Routing…");
    try {
      const r = await callApi("/selfservice/ticket", { person_id: state.person.person_id, summary, details, model: chatModel() });
      const inc = r.incident;
      const m = r.routing;
      ticketBox.replaceChildren(h("div.card.ss-done",
        h("div.card-head", statusPill("good", "Ticket opened"), h("span.chip", inc.number), h("span.end.small.muted", "ServiceNow · " + inc.state)),
        h("b", inc.summary),
        h("div.itsm-triage",
          tri("Routed to", inc.assignment_group, m.group),
          tri("Category", inc.category || "—", m.category),
          tri("Priority", inc.priority, m.priority)),
        h("p.small.t2", "The team above will contact you. You can keep chatting here if anything changes.")));
      bubble("bot", `I've opened ${inc.number} and routed it to ${inc.assignment_group}.`);
      store.audit({ module: "itsm", action: "selfservice.ticket_opened", subject: inc.number,
        detail: { summary: inc.summary, group: inc.assignment_group, category: inc.category, priority: inc.priority,
          methods: r.ai.method }, ai: r.ai });
    } catch (err) {
      btn.disabled = false;
      btn.replaceChildren(icon("ticket"), "Open ticket");
      toast("Couldn't open the ticket: " + err.message, "bad");
    }
  }

  function tri(label, value, r) {
    const how = r ? `${r.method === "llm" ? "LLM" : "classifier"} · ${Math.round(r.confidence * 100)}%` : "";
    return h("div.itsm-tri", h("span.small.muted", label), h("b", value), h("span.itsm-api", how));
  }
}

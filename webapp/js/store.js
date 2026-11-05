// Persistence + audit trail. Everything is kept in browser localStorage for the
// demo; swap the four functions in `backend` for API calls to make it durable.

const NS = "bep.";
const AUDIT_KEY = NS + "audit";
const MAX_AUDIT = 5000;

const backend = {
  read: (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  write: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { console.warn("storage write failed", e); } },
  remove: (k) => { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

export function get(key, fallback = null) {
  const v = backend.read(NS + key);
  return v == null ? fallback : v;
}
export function set(key, value) {
  backend.write(NS + key, value);
}

// ---- roles ------------------------------------------------------------------
// Demo role switcher only — no authentication. Each role sees its own modules.
export const ROLES = {
  employee:{ label: "Employee (self-service)", user: "employee (demo)", views: ["help"] },
  agent:   { label: "IT Support Agent",   user: "it.agent (demo)",   views: ["overview", "itsm"] },
  manager: { label: "IT Manager",         user: "it.manager (demo)", views: ["overview", "insights", "itsm"] },
  qa:      { label: "QA Inspector",       user: "qa.inspector (demo)", views: ["overview", "vision"] },
  analyst: { label: "Material Analyst",   user: "material.analyst (demo)", views: ["overview", "materials"] },
  admin:   { label: "Administrator",      user: "admin (demo)",      views: ["overview", "itsm", "help", "insights", "vision", "audit", "materials"] },
};
export function role() {
  const r = get("role", "admin");
  return ROLES[r] ? r : "admin";
}
export function setRole(r) {
  set("role", r);
  window.dispatchEvent(new CustomEvent("rolechange", { detail: r }));
}
export const currentUser = () => ROLES[role()].user;

// ---- audit trail --------------------------------------------------------------
/**
 * Record an event. Shape:
 *   { module: "itsm"|"vision"|"materials"|"system",
 *     action: e.g. "recommendation.generated", "recommendation.accepted", "sheet.dispositioned",
 *     subject: the record acted on (ticket number, sheet id, finding id),
 *     decision?: "accepted"|"rejected"|"edited"|"pass"|"reject"|"reinspect"|..., reason?, detail?: object,
 *     ai?: { model, version, confidence, sources } }   // provenance of the AI output involved
 */
export function audit(event) {
  const list = backend.read(AUDIT_KEY) || [];
  const rec = {
    id: "AUD-" + Date.now().toString(36).toUpperCase() + "-" + Math.random().toString(36).slice(2, 6).toUpperCase(),
    ts: Date.now(),
    role: role(),
    user: currentUser(),
    ...event,
  };
  list.push(rec);
  if (list.length > MAX_AUDIT) list.splice(0, list.length - MAX_AUDIT);
  backend.write(AUDIT_KEY, list);
  window.dispatchEvent(new CustomEvent("audit", { detail: rec }));
  return rec;
}
export function auditList() {
  return backend.read(AUDIT_KEY) || [];
}
export function clearAudit() {
  backend.remove(AUDIT_KEY);
  window.dispatchEvent(new CustomEvent("audit", { detail: null }));
}

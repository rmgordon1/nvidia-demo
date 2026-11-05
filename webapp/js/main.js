// App shell: role-filtered navigation + hash router. Each view module exports
// `render(root)` and may return a cleanup function (timers, observers).
import { h, icon, closeOverlays } from "./ui.js";
import * as store from "./store.js";

const VIEWS = {
  overview:  { title: "Mission Overview",        icon: "home",   section: "Mission",  load: () => import("./views/overview.js") },
  itsm:      { title: "ITSM Next Best Step",     icon: "ticket", section: "Use cases", load: () => import("./views/itsm.js") },
  help:      { title: "IT Self-Service",         icon: "chat",   section: "Use cases", load: () => import("./views/selfservice.js") },
  insights:  { title: "IT Insights",             icon: "db",     section: "Use cases", load: () => import("./views/insights.js") },
  vision:    { title: "Defect Inspection",       icon: "eye",    section: "Use cases", load: () => import("./views/vision.js") },
  audit:     { title: "Governance & Audit",      icon: "shield", section: "Use cases", load: () => import("./views/audit.js") },
  materials: { title: "Material Accountability", icon: "layers", section: "Later development", tag: "WIP", load: () => import("./views/materials.js") },
};

const nav = document.getElementById("nav");
const view = document.getElementById("view");
const crumbs = document.getElementById("crumbs");
const roleSel = document.getElementById("role");
let cleanup = null;

for (const [k, r] of Object.entries(store.ROLES)) roleSel.append(h("option", { value: k }, r.label));
roleSel.value = store.role();
roleSel.addEventListener("change", () => store.setRole(roleSel.value));
window.addEventListener("rolechange", () => { buildNav(); route(); });

function allowed() {
  return store.ROLES[store.role()].views;
}

function buildNav() {
  nav.replaceChildren();
  let section = null;
  for (const k of allowed()) {
    const v = VIEWS[k];
    if (v.section !== section) nav.append(h("div.nav-section", (section = v.section)));
    nav.append(h("a", { href: "#/" + k, "data-view": k }, icon(v.icon), v.title, v.tag ? h("span.tag", v.tag) : null));
  }
}

async function route() {
  const want = location.hash.replace(/^#\/?/, "").split("?")[0] || "overview";
  const key = allowed().includes(want) ? want : allowed()[0];
  if (key !== want) { history.replaceState(null, "", "#/" + key); }
  nav.querySelectorAll("a").forEach((a) => a.classList.toggle("active", a.dataset.view === key));
  crumbs.replaceChildren("BEP Operations AI  /  ", h("b", VIEWS[key].title));
  document.title = `${VIEWS[key].title} · BEP Operations AI`;
  closeOverlays();
  if (typeof cleanup === "function") cleanup();
  cleanup = null;
  view.replaceChildren(h("div.row.muted", h("span.spinner"), "Loading…"));
  try {
    const mod = await VIEWS[key].load();
    view.replaceChildren();
    cleanup = await mod.render(view);
  } catch (err) {
    console.error(err);
    view.replaceChildren(h("div.card", h("h3", "Could not load this module"), h("p.muted", String(err.message || err))));
  }
  view.focus({ preventScroll: true });
  window.scrollTo(0, 0);
}

function foot() {
  const el = document.getElementById("sidebar-foot");
  el.replaceChildren(
    h("div.row-kv", h("span", "Inference"), h("b", "Local · on-prem")),
    h("div.row-kv", h("span", "External calls"), h("b", "None")),
    h("div.row-kv", h("span", "Data"), h("b", "Synthetic")),
    h("div.row-kv", h("span", "User"), h("b", store.currentUser())),
  );
}
window.addEventListener("rolechange", foot);

const clock = document.getElementById("clock");
const tick = () => (clock.textContent = new Date().toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }));
setInterval(tick, 15000);

tick();
foot();
buildNav();
window.addEventListener("hashchange", route);
route();

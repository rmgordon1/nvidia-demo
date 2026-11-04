// Static data bundled into the image (see tools/). Each loader caches its promise.
const cache = {};
const load = (url) => (cache[url] ||= fetch(url).then((r) => {
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}));

/** Synthetic BMC Helix incidents — built by tools/build_tickets.py. */
export const loadTickets = () => load("data/tickets.json");
/** Inspection images + placeholder scores — built by tools/build_cv_samples.py. */
export const loadCV = () => load("data/cv/manifest.json");
/** Open ServiceNow incidents migrated from Helix at cutover — built by tools/build_tickets.py. */
export const loadServiceNow = () => load("data/servicenow.json");

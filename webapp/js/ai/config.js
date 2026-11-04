// ===========================================================================
// AI backend configuration. Every model call in the UI goes through js/ai/*.js:
//
//   js/ai/itsm.js       retrieveSimilar(), recommendNextSteps()    — local LLM + RAG over tickets
//   js/ai/vision.js     scoreImage(), describeDefect()             — anomaly model + VLM
//   js/ai/materials.js  attributeVariance(), forecast(), ask()     — variance attribution + LLM Q&A
//
// MODE "placeholder" runs everything in the browser with deterministic stand-ins
// so the UI is fully demoable offline. Set MODE to "api" and implement the
// fetch calls (marked TODO in each file) once the local model server exists;
// nginx.conf already reserves /api/ for it.
// ===========================================================================
export const AI = {
  MODE: "api",                  // "placeholder" | "api"  (ITSM uses the model-server; others still placeholder)
  API_BASE: "/api",
  models: {
    llm:     { name: "gpt-oss-20b (vLLM)",                  version: "openai/gpt-oss-20b", runtime: "GB10 · on-prem" },
    embed:   { name: "Nemotron embed + BM25 + Nemotron rerank", version: "llama-nemotron-embed/rerank-1b-v2", runtime: "GB10 · on-prem" },
    anomaly: { name: "Registered golden template (PaDiM-style)", version: "0.2-demo", runtime: "browser" },
    vlm:     { name: "Local VLM (placeholder)",            version: "0.0-stub", runtime: "GB10 · on-prem" },
    variance:{ name: "Variance attribution (rules placeholder)", version: "0.0-stub", runtime: "browser" },
  },
};

/** Provenance stamp attached to every AI output and audit record. */
export const provenance = (key, extra = {}) => ({
  model: AI.models[key].name, version: AI.models[key].version, mode: AI.MODE, at: new Date().toISOString(), ...extra,
});

/** POST (or GET when body is undefined) to the model-server behind nginx /api/. */
export async function callApi(path, body) {
  const r = await fetch(AI.API_BASE + path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).detail || ""; } catch { /* not JSON */ }
    throw new Error(`${path}: HTTP ${r.status}${detail ? " – " + detail : ""}`);
  }
  return r.json();
}

/** POST and read a newline-delimited JSON event stream; calls onEvent(ev) for each event. */
export async function streamApi(path, body, onEvent, signal) {
  const r = await fetch(AI.API_BASE + path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal,
  });
  if (!r.ok || !r.body) throw new Error(`${path}: HTTP ${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

/** Currently selected chat model (Admin can switch it in the ITSM view); null = server default. */
export const chatModel = () => { try { return JSON.parse(localStorage.getItem("bcp.ai.model")) || null; } catch { return null; } };

// Defect Inspection — CV second-opinion beside the existing inspection cameras.
// Stage 1 anomaly score + heatmap, stage 2 (placeholder) VLM description, human
// disposition, threshold tuning against the rules baseline, and defect trends.
import { h, icon, advisory, aiTag, statusPill, kpi, delta, toast, fmtPct, fmtInt, fmtNum, rng } from "../ui.js";
import { lineChart, barChart, hbarChart, SERIES } from "../charts.js";
import * as store from "../store.js";
import { loadCV } from "../data/loaders.js";
import { getThreshold, setThreshold, aiMetrics, rulesMetrics, sweep, confusion, DEFAULT_THRESHOLD } from "../data/cv-metrics.js";
import { AI, provenance } from "../ai/config.js";
import * as vai from "../ai/vision.js";

const LINES = [1, 2, 3, 4];
const SHIFTS = ["A", "B", "C"];
const SHIFT_HOURS = { A: "06–14", B: "14–22", C: "22–06" };
const RATES = [1, 2, 5, 10];
const CLASS_COLOR = Object.fromEntries(vai.DEFECT_CLASSES.map((c, i) => [c.key, SERIES[i]]));
CLASS_COLOR.none = "var(--muted)";
const DECISIONS = {
  reject: { label: "Defect confirmed — reject", level: "critical" },
  pass: { label: "Override — pass", level: "good" },
  reinspect: { label: "Sent to re-inspection", level: "warning" },
};
const SEED_COUNT = 36;

export async function render(root) {
  const man = await loadCV();
  const model = man.model;
  vai.configure(man);
  let threshold = getThreshold();
  const plate = (key) => key ? key.replace(/^banknote_0?(\d+)_(front|back)$/, (_, n, side) => `Design ${+n + 1} · ${side}`) : "unknown";

  // ---- enrich items: deterministic line / shift / timestamp ---------------
  const r = rng(20261104);
  const hour0 = new Date(); hour0.setMinutes(0, 0, 0);
  const start = hour0.getTime() - 24 * 3600e3;
  const pilot = man.items.map((it) => {
    const bad = it.label !== "good";
    // Line 3 carries a little more defect load (worn blanket scenario).
    const line = bad && r() < 0.3 ? 3 : 1 + Math.floor(r() * 4);
    const ts = start + r() * 24 * 3600e3;
    const hr = new Date(ts).getHours();
    const shift = hr >= 6 && hr < 14 ? "A" : hr >= 14 && hr < 22 ? "B" : "C";
    return { ...it, src: "data/cv/sheets/" + it.file, thumb: "data/cv/thumbs/" + it.file, map: vai.decodeMap(it.map), line, ts, shift, upload: false };
  }).sort((a, b) => a.ts - b.ts);

  const dispositions = store.get("cv2.dispositions", {});
  const state = {
    inspected: pilot.slice(0, SEED_COUNT),
    cursor: SEED_COUNT,
    uploads: [],
    running: false,
    rate: 2,
    line: "all",
    shift: "all",
    tab: "ai",
    selected: null,
    overlay: true,
    opacity: 0.6,
    revealLabel: false,
    trendScope: "pilot",
    trendBy: "line",
    timer: null,
    recent: [],
    urls: [],
    describeToken: 0,
  };
  const flaggedAI = (it) => it.score >= threshold;
  const allInspected = () => [...state.inspected, ...state.uploads];

  // ---- layout -----------------------------------------------------------------
  const rateSel = h("select", { "aria-label": "Stream rate", onchange: (e) => { state.rate = +e.target.value; if (state.running) { stop(); play(); } } },
    RATES.map((v) => h("option", { value: v, selected: v === state.rate }, `${v} sheets/s`)));
  const lineSel = h("select", { "aria-label": "Line", onchange: (e) => { state.line = e.target.value; refreshAll(); } },
    h("option", { value: "all" }, "All lines"), LINES.map((l) => h("option", { value: l }, `Line ${l}`)));
  const shiftSel = h("select", { "aria-label": "Shift", onchange: (e) => { state.shift = e.target.value; refreshAll(); } },
    h("option", { value: "all" }, "All shifts"), SHIFTS.map((s) => h("option", { value: s }, `Shift ${s} (${SHIFT_HOURS[s]})`)));
  const playBtn = h("button.btn.primary", { onclick: () => (state.running ? stop() : play()) });
  const fileIn = h("input", { type: "file", accept: "image/*,video/*", multiple: true, hidden: true, onchange: onUpload });
  const uploadBtn = h("button.btn", { onclick: () => fileIn.click(), title: "Score your own sheet photos (whole sheet on a dark background) or a short video" }, icon("upload"), "Upload images / video");
  const resetBtn = h("button.btn.ghost", { onclick: resetStream, title: "Restart the replay" }, icon("refresh"), "Reset");

  const kpiRow = h("div.grid.g-4");
  const liveBar = h("div.card.cv-live");
  const viewer = h("div.card.cv-viewer");
  const queue = h("div.card.cv-queue");
  const thresholdCard = h("div.card.cv-threshold");
  const trendsHost = h("div.grid.g-2");

  root.append(
    h("div.view-head",
      h("div.titles",
        h("h2", "Defect Inspection"),
        h("p.lede", "Assistive second opinion beside the existing inspection cameras. A stage-1 anomaly model scores every sheet and localizes the defect; a stage-2 vision-language model explains flagged sheets. QA makes every disposition — nothing is rejected automatically.")),
      h("div.actions", lineSel, shiftSel, rateSel, playBtn, resetBtn, uploadBtn, fileIn)),
    h("div.placeholder-note",
      "Demo pipeline: stage 1 is a registered golden-template placeholder (sheet registration + per-pixel Gaussian of good sheets per plate design and side, PaDiM-style) and stage 2 is a stubbed VLM — both in ", h("code", "js/ai/vision.js"),
      `. Images are synthetic press sheets of fictional banknotes (${model.categories.length / 2} designs, fronts and backs) from `, h("code", "synthetic_print/"),
      " — no real currency; uploads replace the camera stream."),
    kpiRow, liveBar,
    h("div.grid.g-main-side", viewer, queue),
    thresholdCard,
    h("div.row.cv-section-head", h("h3", "Defect trends"),
      h("div.btn-group", { role: "group", "aria-label": "Trend scope" },
        scopeBtn("pilot", "Full pilot set (24 h)"), scopeBtn("stream", "Streamed so far"))),
    trendsHost,
  );

  function scopeBtn(k, label) {
    return h("button", { class: state.trendScope === k ? "on" : "", "data-scope": k, onclick: (e) => {
      state.trendScope = k;
      e.target.parentElement.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.scope === k));
      renderTrends();
    } }, label);
  }

  // ---- filtering helpers ------------------------------------------------------
  const matches = (it) => (state.line === "all" || it.line === +state.line) && (state.shift === "all" || it.shift === state.shift);
  const scoped = (list) => list.filter(matches);

  // ---- stream -------------------------------------------------------------------
  function play() {
    if (state.cursor >= pilot.length) resetStream();
    state.running = true;
    state.recent = [];
    state.timer = setInterval(stepStream, 1000 / state.rate);
    renderPlayBtn();
  }
  function stop() {
    state.running = false;
    clearInterval(state.timer);
    state.timer = null;
    renderPlayBtn();
    renderLive();
  }
  function stepStream() {
    // advance to the next sheet matching the line/shift filter
    while (state.cursor < pilot.length && !matches(pilot[state.cursor])) state.cursor++;
    if (state.cursor >= pilot.length) {
      stop();
      toast("Pilot set fully inspected. Reset to replay the stream.");
      return;
    }
    const it = pilot[state.cursor++];
    state.inspected.push(it);
    state.recent.push(performance.now());
    if (!state.selected && flaggedAI(it)) select(it);
    renderKPIs(); renderLive(); renderQueue();
    if (state.trendScope === "stream" && state.inspected.length % 5 === 0) renderTrends();
  }
  function resetStream() {
    stop();
    state.inspected = pilot.slice(0, SEED_COUNT);
    state.cursor = SEED_COUNT;
    refreshAll();
  }
  function renderPlayBtn() {
    playBtn.replaceChildren(icon(state.running ? "pause" : "play"), state.running ? "Pause stream" : "Start stream");
  }
  function throughput() {
    const now = performance.now();
    state.recent = state.recent.filter((t) => now - t < 5000);
    if (!state.running || state.recent.length < 2) return 0;
    return (state.recent.length - 1) / ((state.recent[state.recent.length - 1] - state.recent[0]) / 1000 || 1);
  }

  // ---- uploads -------------------------------------------------------------------
  async function onUpload() {
    const files = [...fileIn.files];
    fileIn.value = "";
    let added = 0;
    for (const f of files) {
      try {
        if (f.type.startsWith("video/")) added += await ingestVideo(f);
        else { await ingestImage(f); added++; }
      } catch (err) {
        console.error(err);
        toast(`Could not read ${f.name}: ${err.message}`, "bad");
      }
    }
    if (added) {
      toast(`${added} uploaded frame${added > 1 ? "s" : ""} scored locally.`, "good");
      store.audit({ module: "vision", action: "images.uploaded", subject: `${added} frame(s)`, detail: { files: files.map((f) => f.name) }, ai: provenance("anomaly") });
      const firstFlag = state.uploads.slice(-added).find(flaggedAI);
      if (firstFlag) { state.tab = "ai"; select(firstFlag); }
      refreshAll();
    }
  }
  function nextUploadId() {
    return `UPL-${Date.now().toString(36).toUpperCase().slice(-5)}-${String(state.uploads.length + 1).padStart(3, "0")}`;
  }
  function uploadItem(res, src, name) {
    const now = Date.now(), hr = new Date(now).getHours();
    return {
      id: nextUploadId(), file: name, src, label: null, labelName: "Unlabeled upload", score: res.score, map: res.map,
      stats: res.stats, ruleReject: res.ruleReject, latencyMs: res.latencyMs, upload: true,
      category: res.category, registered: res.registered,
      line: state.line === "all" ? 1 : +state.line, ts: now, shift: hr >= 6 && hr < 14 ? "A" : hr >= 14 && hr < 22 ? "B" : "C",
    };
  }
  async function ingestImage(file) {
    const url = URL.createObjectURL(file);
    state.urls.push(url);
    const img = await loadImg(url);
    const res = await vai.scoreImage(img, man);
    state.uploads.push(uploadItem(res, res.display, file.name));
  }
  async function ingestVideo(file, every = 500, maxFrames = 40) {
    const url = URL.createObjectURL(file);
    state.urls.push(url);
    const v = h("video", { muted: true, playsInline: true, src: url });
    await new Promise((ok, bad) => { v.onloadedmetadata = ok; v.onerror = () => bad(new Error("unsupported video")); });
    const c = document.createElement("canvas");
    const k = Math.min(1, 2048 / Math.max(v.videoWidth, v.videoHeight));
    c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
    const ctx = c.getContext("2d");
    let n = 0;
    for (let t = 0; t < v.duration && n < maxFrames; t += every / 1000, n++) {
      v.currentTime = t;
      await new Promise((ok) => (v.onseeked = ok));
      ctx.drawImage(v, 0, 0, c.width, c.height);
      const res = await vai.scoreImage(c, man);
      state.uploads.push(uploadItem(res, res.display, `${file.name} @ ${t.toFixed(1)}s`));
    }
    return n;
  }

  // ---- KPIs ------------------------------------------------------------------------
  function renderKPIs() {
    const ins = scoped(allInspected());
    const flagged = ins.filter(flaggedAI).length;
    const rules = ins.filter((i) => i.ruleReject).length;
    const labeled = scoped(pilot);
    const a = aiMetrics(labeled, threshold), b = rulesMetrics(labeled);
    kpiRow.replaceChildren(
      kpi({ label: "Sheets inspected", icon: "eye", value: fmtInt(ins.length),
        foot: [h("span", `of ${fmtInt(labeled.length)} in pilot set`), state.uploads.length ? h("span.chip", `+${state.uploads.length} uploads`) : null] }),
      kpi({ label: "Flagged for review", icon: "alert", value: fmtInt(flagged),
        foot: [h("span", `AI ${fmtPct(ins.length ? flagged / ins.length : 0)}`), h("span.muted", "·"), h("span", `rules ${fmtInt(rules)} (${fmtPct(ins.length ? rules / ins.length : 0)})`)] }),
      kpi({ label: "False-reject rate (good sheets)", icon: "x", value: fmtPct(a.frr),
        foot: [delta(a.frr - b.frr, { goodWhen: "down", fmt: (v) => fmtNum(Math.abs(v) * 100, 1) + " pts" }), h("span", `vs rules ${fmtPct(b.frr)}`)] }),
      kpi({ label: "Defect recall (known set)", icon: "check", value: fmtPct(a.recall),
        foot: [delta(a.recall - b.recall, { goodWhen: "up", fmt: (v) => fmtNum(Math.abs(v) * 100, 1) + " pts" }), h("span", `vs rules ${fmtPct(b.recall)}`)] }),
    );
  }

  // ---- live filmstrip ----------------------------------------------------------------
  function renderLive() {
    const ins = scoped(allInspected());
    const last = ins.slice(-14).reverse();
    const tp = throughput();
    liveBar.replaceChildren(
      h("div.cv-live-head",
        h("div.row", state.running ? h("span.live-dot") : h("span.dot", { style: { background: "var(--muted)" } }),
          h("b", state.running ? "Line stream live" : "Stream paused"),
          h("span.muted.small", state.line === "all" ? "All lines" : `Line ${state.line}`, " · ", state.shift === "all" ? "all shifts" : `Shift ${state.shift}`)),
        h("div.cv-live-stats",
          stat("Throughput", tp ? `${fmtNum(tp, 1)} img/s` : "–"),
          stat("Queue pending", fmtInt(pendingAI().length)),
          stat("Dispositioned", fmtInt(Object.keys(dispositions).length)),
          stat("Stage-1 scoring", "precomputed (pilot set)"))),
      h("div.cv-strip", last.length ? last.map((it) => {
        const f = flaggedAI(it);
        return h("button.cv-thumb", { class: (f ? "flag" : "") + (state.selected?.id === it.id ? " sel" : ""), title: `${it.id} · score ${it.score.toFixed(2)}`, onclick: () => select(it) },
          h("img", { src: it.thumb || it.src, alt: it.id }),
          h("span.cv-thumb-badge", f ? statusPill("warning", "Flag") : statusPill("good", "Pass")),
          h("span.cv-thumb-id", it.id.replace(/^SHT-0*/, "#")));
      }) : h("div.empty-state", "No sheets for this line/shift yet. Start the stream.")),
    );
  }
  const stat = (label, value) => h("div.cv-stat", h("span", label), h("b", value));

  // ---- review queue -----------------------------------------------------------------
  const pendingAI = () => scoped(allInspected()).filter((it) => flaggedAI(it) && !dispositions[it.id]);
  function queueItems() {
    const ins = scoped(allInspected());
    if (state.tab === "ai") return pendingAI();
    if (state.tab === "rules") return ins.filter((it) => it.ruleReject && !flaggedAI(it) && !dispositions[it.id]);
    return ins.filter((it) => dispositions[it.id]);
  }
  function renderQueue() {
    const ins = scoped(allInspected());
    const counts = {
      ai: pendingAI().length,
      rules: ins.filter((it) => it.ruleReject && !flaggedAI(it) && !dispositions[it.id]).length,
      done: ins.filter((it) => dispositions[it.id]).length,
    };
    const tabs = h("div.btn-group.cv-tabs", { role: "tablist" },
      [["ai", "AI flagged"], ["rules", "Rules only"], ["done", "Dispositioned"]].map(([k, label]) =>
        h("button", { class: state.tab === k ? "on" : "", role: "tab", "aria-selected": state.tab === k, onclick: () => { state.tab = k; renderQueue(); } },
          label, h("span.cv-count", counts[k]))));
    const items = queueItems().slice().reverse();
    const list = h("div.cv-qlist", items.length ? items.map(queueRow) : h("div.empty-state",
      state.tab === "ai" ? "No AI-flagged sheets waiting for review." : state.tab === "rules" ? "No sheets rejected by the rules baseline alone." : "No dispositions recorded yet."));
    queue.replaceChildren(
      h("div.card-head", h("h3", "Review queue"), h("span.sub", state.tab === "rules" ? "Rejected by the legacy rules but passed by AI — candidates for false rejects" : "Newest first"), h("div.end", tabs)),
      list);
  }
  function queueRow(it) {
    const c = vai.classifyDefect(it, threshold);
    const d = dispositions[it.id];
    return h("button.cv-qrow", { class: state.selected?.id === it.id ? "sel" : "", onclick: () => select(it) },
      h("img", { src: it.thumb || it.src, alt: "" }),
      h("div.cv-qmain",
        h("div.row", h("b.mono", it.id), it.upload ? h("span.chip", "upload") : h("span.chip", `L${it.line} · ${it.shift}`)),
        h("div.small.t2", h("i.dot", { style: { background: CLASS_COLOR[c.classKey] } }), " ", c.className, h("span.muted", ` · ${c.location}`))),
      h("div.cv-qside",
        h("span.mono.num", it.score.toFixed(2)),
        d ? statusPill(DECISIONS[d.decision].level, d.decision === "reject" ? "Rejected" : d.decision === "pass" ? "Passed" : "Re-inspect")
          : it.ruleReject ? h("span.small.muted", "rules: reject") : h("span.small.muted", "rules: pass")));
  }

  // ---- viewer --------------------------------------------------------------------------
  function select(it) {
    state.selected = it;
    state.revealLabel = false;
    renderViewer();
    renderQueue();
    renderLive();
  }

  function renderViewer() {
    const it = state.selected;
    if (!it) {
      viewer.replaceChildren(h("div.card-head", h("h3", "Sheet review")),
        h("div.empty-state.cv-empty", icon("eye"), h("p", "Select a flagged sheet from the queue or filmstrip to review its heatmap and AI explanation.")));
      return;
    }
    const flagged = flaggedAI(it);
    const region = vai.peakRegion(it.map);
    const d = dispositions[it.id];

    // image + heatmap stage
    const img = h("img.cv-img", { src: it.src, alt: `Sheet ${it.id}` });
    const heat = h("canvas.cv-heat", { width: model.gw * 8, height: model.gh * 8, "aria-hidden": "true" });
    const pct = (b) => ({ left: b.x * 100 + "%", top: b.y * 100 + "%", width: b.w * 100 + "%", height: b.h * 100 + "%" });
    const boxEl = h("div.cv-box", { style: pct(region.box) }, h("span", flagged ? "Peak anomaly" : "Peak (below threshold)"));
    drawHeat(heat, it.map);
    heat.style.opacity = state.overlay ? state.opacity : 0;
    boxEl.hidden = !state.overlay;
    // known defect boxes (evaluation only), revealed with the label
    const truthBoxes = state.revealLabel && it.defects
      ? it.defects.map((d) => h("div.cv-truth", { style: pct({ x: d.box[0], y: d.box[1], w: d.box[2], h: d.box[3] }), title: vai.classByKey(d.cls).name }))
      : [];
    // POC: fine-tuned RF-DETR-Seg detections (cv_model/scripts/poc_webapp.py) replace the peak-anomaly box
    const detBoxes = it.det
      ? it.det.filter((d) => d.conf >= threshold).map((d) => h("div.cv-box.cv-det", { style: { ...pct({ x: d.box[0], y: d.box[1], w: d.box[2], h: d.box[3] }), borderColor: CLASS_COLOR[d.cls] } },
        h("span", { style: { background: CLASS_COLOR[d.cls] } }, `${vai.classByKey(d.cls).name} ${d.conf.toFixed(2)}`)))
      : [];
    if (it.det) boxEl.style.display = "none";
    const stage = h("div.cv-stage", { style: { aspectRatio: `${model.W} / ${model.H}` } }, img, heat, boxEl, ...detBoxes, ...truthBoxes);

    // loupe: the peak region magnified from the full-resolution sheet image
    const loupe = h("canvas.cv-loupe", { width: 480, height: 300, "aria-label": "Magnified peak region" });
    loadImg(it.src).then((im) => {
      const W = im.naturalWidth, H = im.naturalHeight, b = region.box;
      const cx = (b.x + b.w / 2) * W, cy = (b.y + b.h / 2) * H;
      const sw = Math.min(W, Math.max(b.w * W * 1.8, 110)), sh = sw * loupe.height / loupe.width;
      const sx = Math.min(W - sw, Math.max(0, cx - sw / 2)), sy = Math.min(H - sh, Math.max(0, cy - sh / 2));
      const ctx = loupe.getContext("2d");
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(im, sx, sy, sw, sh, 0, 0, loupe.width, loupe.height);
      const k = loupe.width / sw;
      ctx.strokeStyle = "#ffe27a"; ctx.lineWidth = 2;
      ctx.strokeRect((b.x * W - sx) * k, (b.y * H - sy) * k, b.w * W * k, b.h * H * k);
    }).catch(() => {});

    const overlayToggle = h("label.check", h("input", { type: "checkbox", checked: state.overlay, onchange: (e) => {
      state.overlay = e.target.checked; heat.style.opacity = state.overlay ? state.opacity : 0; boxEl.hidden = !state.overlay;
    } }), "Heatmap overlay");
    const opac = h("input", { type: "range", min: 0.1, max: 1, step: 0.05, value: String(state.opacity), "aria-label": "Overlay opacity", oninput: (e) => {
      state.opacity = +e.target.value; if (state.overlay) heat.style.opacity = state.opacity;
    } });

    // score gauge
    const gauge = h("div.cv-gauge",
      h("div.cv-gauge-track", h("i", { style: { width: it.score * 100 + "%", background: flagged ? "var(--serious)" : "var(--accent)" } }),
        h("b.cv-gauge-th", { style: { left: threshold * 100 + "%" }, title: `Threshold ${threshold.toFixed(2)}` })),
      h("div.cv-gauge-legend.small.muted", h("span", "0"), h("span", `threshold ${threshold.toFixed(2)}`), h("span", "1")));

    const vlmBox = h("div.cv-vlm", h("div.row.muted.small", h("span.spinner"), it.det ? "Summarising detections…" : "Placeholder VLM describing flagged region…"));
    const token = ++state.describeToken;
    if (flagged || it.ruleReject) {
      vai.describeDefect(it, threshold).then((res) => {
        if (token !== state.describeToken) return;
        it._vlm = res;
        vlmBox.replaceChildren(
          h("div.row.wrap", aiTag(it.det ? "RF-DETR-Seg · templated text" : "Placeholder VLM"), h("span.pill.info", res.className), h("span.small.muted", `confidence ${fmtPct(res.confidence, 0)} · ${Math.round(res.latencyMs)} ms`)),
          h("p.cv-vlm-text", res.text),
          h("div.row.wrap.small.t2", "Suggested disposition: ",
            res.recommendation === "pass" ? statusPill("good", "Pass") : statusPill("critical", "Reject"), advisory()));
      });
    } else {
      vlmBox.replaceChildren(h("p.small.muted", "Stage-2 VLM runs only on flagged sheets. This sheet scored below the threshold and passed."));
    }

    const note = h("textarea", { rows: 2, placeholder: "Reviewer note (optional) — e.g. confirmed under loupe, ink void 3 mm" });
    const decide = (decision) => {
      const vlm = it._vlm;
      dispositions[it.id] = { decision, note: note.value.trim(), ts: Date.now(), user: store.currentUser() };
      store.set("cv2.dispositions", dispositions);
      store.audit({
        module: "vision", action: "sheet.dispositioned", subject: it.id, decision, reason: note.value.trim() || undefined,
        detail: { score: +it.score.toFixed(4), threshold, aiFlag: flagged, aiClass: vlm?.className, aiLocation: vlm?.location,
          rulesVerdict: it.ruleReject ? "reject" : "pass", line: it.line, shift: it.shift, knownLabel: it.label ?? "unlabeled" },
        ai: { ...provenance("anomaly"), vlm: AI.models.vlm.name, confidence: vlm ? +vlm.confidence.toFixed(2) : null },
      });
      toast(`${it.id}: ${DECISIONS[decision].label}. Recorded in audit log.`, decision === "pass" ? "good" : "");
      // advance to next pending sheet
      const next = pendingAI().slice(-1)[0];
      state.selected = next || it;
      refreshAll();
    };

    const truth = it.label == null ? h("span.muted", "unlabeled upload")
      : state.revealLabel ? h("span", it.label === "good" ? statusPill("good", "Good") : statusPill("critical", it.labelName))
      : h("button.btn.ghost.sm", { onclick: () => { state.revealLabel = true; renderViewer(); } }, "Reveal (evaluation only)");

    viewer.replaceChildren(
      h("div.card-head",
        h("h3", "Sheet review"), h("span.chip", it.id),
        h("span.sub", it.upload ? it.file : `Line ${it.line} · Shift ${it.shift} · ${new Date(it.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`),
        h("div.end", overlayToggle, h("span.small.muted", "Opacity"), opac)),
      h("div.cv-review",
        h("div.cv-stage-wrap", stage,
          h("div.cv-heat-legend.small.muted", h("span", "Low"), h("i"), h("span", "High anomaly")),
          h("div.cv-loupe-wrap", h("div.small.muted", "Peak region, magnified"), loupe)),
        h("div.cv-detail",
          h("div.cv-verdicts",
            h("div", h("h4", "Stage 1 · anomaly score"), h("div.row", h("span.cv-score.num", it.score.toFixed(3)),
              flagged ? statusPill("warning", "AI: flag for review") : statusPill("good", "AI: pass")), gauge),
            h("dl.kv",
              h("dt", "Plate"), h("dd", plate(it.category), it.registered === false ? h("span.muted", " · sheet edges not found") : null),
              h("dt", "Rules baseline"), h("dd", it.ruleReject ? statusPill("critical", "Reject") : statusPill("good", "Pass")),
              h("dt", "Peak region"), h("dd", vai.locationPhrase(region.cx, region.cy), h("span.muted", ` · ${region.cells} patch${region.cells > 1 ? "es" : ""}`)),
              h("dt", "Known label"), h("dd", truth))),
          h("div", h("h4", "Stage 2 · explanation"), vlmBox),
          h("div.cv-dispo",
            h("div.row", h("h4", "Human disposition"), advisory()),
            d ? h("div.cv-done", statusPill(DECISIONS[d.decision].level, DECISIONS[d.decision].label),
              h("span.small.muted", ` by ${d.user} · ${new Date(d.ts).toLocaleString()}`), d.note ? h("p.small.t2", `“${d.note}”`) : null)
              : null,
            note,
            h("div.row.wrap",
              h("button.btn.bad", { onclick: () => decide("reject") }, icon("x"), "Confirm defect — reject"),
              h("button.btn.good", { onclick: () => decide("pass") }, icon("check"), "Override — pass"),
              h("button.btn.warn", { onclick: () => decide("reinspect") }, icon("refresh"), "Send to re-inspection"))))),
    );
  }

  // ---- threshold tuning ------------------------------------------------------------------
  let tradeChart = null;
  function renderThreshold() {
    const labeled = scoped(pilot);
    const curve = sweep(labeled, 80);
    const lo = curve[0].t, hi = curve[curve.length - 1].t;
    const a = aiMetrics(labeled, threshold), b = rulesMetrics(labeled);
    const val = h("b.cv-th-val.num", threshold.toFixed(3));
    const slider = h("input.cv-slider", { type: "range", min: lo.toFixed(3), max: hi.toFixed(3), step: 0.002, value: String(threshold), "aria-label": "Anomaly threshold",
      oninput: (e) => { threshold = +e.target.value; val.textContent = threshold.toFixed(3); updateMetrics(); },
      onchange: () => commitThreshold(),
    });
    const metricsHost = h("div");
    const chartEl = h("div");
    const optimize = () => {
      // lowest FRR whose recall >= rules recall
      const ok = curve.filter((p) => p.recall >= b.recall).sort((x, y) => x.frr - y.frr || y.t - x.t)[0];
      if (ok) { threshold = +ok.t.toFixed(3); commitThreshold(true); }
    };
    function updateMetrics() {
      const m = aiMetrics(labeled, threshold);
      metricsHost.replaceChildren(metricTable(m, b), h("div.cv-cm-row", cm("AI (stage 1)", m), cm("Rules baseline", b)));
      tradeChart?.update({ refs: chartRefs(b), markers: chartMarkers(m) });
    }
    function commitThreshold(fromButton) {
      setThreshold(threshold);
      store.audit({ module: "vision", action: "threshold.changed", subject: "anomaly-threshold", detail: { threshold, ...pick(aiMetrics(labeled, threshold)) } });
      if (fromButton) renderThreshold(); else updateMetrics();
      renderKPIs(); renderLive(); renderQueue(); renderViewer(); renderTrends();
    }
    thresholdCard.replaceChildren(
      h("div.card-head", h("h3", "Threshold tuning"), h("span.sub", `Scored on the labeled pilot set (${labeled.length} sheets, ${labeled.filter((i) => i.label !== "good").length} known defects)`),
        h("div.end",
          h("button.btn.sm", { onclick: optimize, title: "Lowest false-reject rate that still catches at least as many defects as the rules" }, icon("sliders"), "Match rules recall, min FRR"),
          h("button.btn.ghost.sm", { onclick: () => { threshold = DEFAULT_THRESHOLD; commitThreshold(true); } }, "Reset"))),
      h("div.cv-th-grid",
        h("div.stack",
          h("label.field", h("span.row", "Anomaly threshold ", val), slider,
            h("span.row.small.muted", { style: { justifyContent: "space-between" } }, h("span", "More sensitive (more flags)"), h("span", "Fewer flags"))),
          metricsHost),
        h("div", h("h4", "Recall vs false-reject tradeoff"), chartEl)));
    tradeChart = lineChart(chartEl, {
      x: curve.map((p) => +p.t.toFixed(3)), numericX: true, height: 240, yMin: 0, yMax: 1,
      series: [{ name: "Defect recall", values: curve.map((p) => p.recall) }, { name: "False-reject rate", values: curve.map((p) => p.frr), color: SERIES[1] }],
      yFmt: (v) => Math.round(v * 100) + "%", xFmt: (v) => (+v).toFixed(2), tipTitle: (v) => `Threshold ${(+v).toFixed(3)}`,
      refs: chartRefs(b), markers: chartMarkers(a), label: "Recall and false-reject rate by threshold",
    });
    updateMetrics();
  }
  const pick = (m) => ({ recall: +m.recall.toFixed(3), frr: +m.frr.toFixed(3), escape: +m.escape.toFixed(3) });
  const chartRefs = (b) => [
    { x: threshold, label: `current ${threshold.toFixed(2)}` },
    { y: b.recall, label: `Rules recall ${fmtPct(b.recall, 0)}` },
    { y: b.frr, label: `Rules false-reject ${fmtPct(b.frr, 0)}` },
  ];
  const chartMarkers = (m) => [
    { x: threshold, y: m.recall, color: SERIES[0] },
    { x: threshold, y: m.frr, color: SERIES[1] },
  ];
  function metricTable(a, b) {
    const row = (name, k, goodWhen, hint) => h("tr",
      h("td", name, h("div.small.muted", hint)),
      h("td.r.num", fmtPct(a[k])), h("td.r.num", fmtPct(b[k])),
      h("td.r", delta(a[k] - b[k], { goodWhen, fmt: (v) => fmtNum(Math.abs(v) * 100, 1) + " pts" })));
    return h("div.table-wrap", h("table.data.cv-metrics",
      h("thead", h("tr", h("th", "Metric"), h("th.r", "AI"), h("th.r", "Rules"), h("th.r", "AI vs rules"))),
      h("tbody",
        row("Defect recall", "recall", "up", "known defects caught"),
        row("False-reject rate", "frr", "down", "good sheets rejected"),
        row("Escape rate", "escape", "down", "defects let through"),
        row("Precision", "precision", "up", "flags that are real defects"))));
  }
  function cm(title, m) {
    return h("div.cv-cm", h("div.small.t2", title),
      h("table.cv-cm-table",
        h("tr", h("th"), h("th", "Flag"), h("th", "Pass")),
        h("tr", h("th", "Defect"), h("td.tp", m.tp), h("td.fn", m.fn)),
        h("tr", h("th", "Good"), h("td.fp", m.fp), h("td.tn", m.tn))));
  }

  // ---- trends ---------------------------------------------------------------------------------
  function renderTrends() {
    trendsHost.querySelectorAll(".chart").forEach((c) => c._ro?.disconnect());
    const base = scoped(state.trendScope === "pilot" ? pilot : allInspected());
    const flagged = base.filter(flaggedAI).map((it) => ({ it, c: vai.classifyDefect(it, threshold) }));
    const keys = [...vai.DEFECT_CLASSES.map((c) => c.key), "none"];

    const distEl = h("div"), byEl = h("div"), timeEl = h("div");
    const byToggle = h("div.btn-group", ["line", "shift"].map((k) => h("button", { class: state.trendBy === k ? "on" : "", onclick: () => { state.trendBy = k; renderTrends(); } }, `By ${k}`)));
    const bench = h("div.cv-bench");
    trendsHost.replaceChildren(
      h("div.card", h("div.card-head", h("h3", "Flagged sheets by defect class"), h("span.sub", "Stage-2 classification (placeholder VLM)")), distEl),
      h("div.card", h("div.card-head", h("h3", "Flagged sheets by " + state.trendBy), h("div.end", byToggle)), byEl),
      h("div.card", h("div.card-head", h("h3", "Flag rate over time"), h("span.sub", "Hourly, AI vs rules baseline")), timeEl),
      h("div.card", h("div.card-head", h("h3", "Throughput on this device"), h("span.sub", "Stage-1 placeholder model, browser runtime")), bench),
    );
    if (!base.length) { distEl.append(h("div.empty-state", "No sheets in scope.")); return; }

    hbarChart(distEl, {
      items: keys.map((k) => ({ label: vai.classByKey(k).name, value: flagged.filter((f) => f.c.classKey === k).length, color: CLASS_COLOR[k],
        note: k === "none" ? "likely false alarms the VLM can clear" : undefined })),
      fmt: (v) => fmtInt(v), valueName: "Flagged sheets",
    });

    const groups = state.trendBy === "line" ? LINES.map((l) => ({ label: `Line ${l}`, f: (it) => it.line === l })) : SHIFTS.map((s) => ({ label: `Shift ${s}`, f: (it) => it.shift === s }));
    barChart(byEl, {
      labels: groups.map((g) => g.label), stacked: true, height: 230,
      series: keys.map((k) => ({ name: vai.classByKey(k).name, color: CLASS_COLOR[k], values: groups.map((g) => flagged.filter((f) => f.c.classKey === k && g.f(f.it)).length) })),
      yFmt: (v) => fmtInt(v),
    });

    const hours = Array.from({ length: 24 }, (_, i) => start + i * 3600e3);
    const bucket = (ts) => Math.min(23, Math.max(0, Math.floor((ts - start) / 3600e3)));
    const tot = new Array(24).fill(0), fa = new Array(24).fill(0), fr = new Array(24).fill(0);
    for (const it of base) { if (it.upload) continue; const b = bucket(it.ts); tot[b]++; if (flaggedAI(it)) fa[b]++; if (it.ruleReject) fr[b]++; }
    const rate = (arr) => arr.map((v, i) => (tot[i] ? v / tot[i] : null));
    lineChart(timeEl, {
      x: hours, height: 220, yMin: 0,
      series: [{ name: "AI flag rate", values: rate(fa), points: true }, { name: "Rules reject rate", values: rate(fr), color: SERIES[1], dash: true }],
      yFmt: (v) => Math.round(v * 100) + "%",
      xFmt: (v) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }),
      tipTitle: (v, i) => `${new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })} · ${tot[i]} sheets`,
    });

    state.benchEl = bench;
    renderBench(bench);
  }

  let benchResult = null;
  function renderBench(el) {
    const tp = throughput();
    const px = `${model.W}×${model.H}`;
    const show = () => el.replaceChildren(
      h("div.grid.g-2",
        h("div.kpi", h("div.k-label", "Line stream (current)"), h("div.k-value", tp ? fmtNum(tp, 1) : "–", h("small", "img/s")), h("div.k-foot", `target ${state.rate} sheets/s`)),
        h("div.kpi", h("div.k-label", "Stage-1 capacity (measured)"), h("div.k-value", benchResult ? fmtInt(benchResult.ips) : "–", h("small", "img/s")),
          h("div.k-foot", benchResult ? `${fmtNum(benchResult.ms, 0)} ms / sheet · ${px} · ${benchResult.n} runs` : "not measured yet"))),
      h("p.small.muted", { style: { marginTop: "10px" } }, "Single-threaded browser JavaScript, registration excluded. Production target is camera line speed (TBD in discovery); GB10 GPU throughput will be benchmarked once the real stage-1 model replaces the placeholder."),
      h("div.row", { style: { marginTop: "10px" } }, h("button.btn.sm", { onclick: runBench }, icon("cpu"), benchResult ? "Re-run benchmark" : "Run benchmark")));
    async function runBench() {
      const btn = el.querySelector("button");
      btn.disabled = true;
      btn.replaceChildren(h("span.spinner"), "Measuring…");
      const imgs = await Promise.all(pilot.slice(0, 3).map((it) => loadImg(it.src)));
      const n = 6, ms = await vai.benchmark(imgs, man, n);
      benchResult = { ms, ips: 1000 / ms, n };
      store.audit({ module: "vision", action: "benchmark.run", subject: "stage-1 placeholder", detail: { msPerImage: +ms.toFixed(3), imagesPerSecond: Math.round(1000 / ms) } });
      show();
    }
    show();
  }

  // ---- heatmap rendering --------------------------------------------------------------------------
  function drawHeat(canvas, map) {
    const GW = model.gw, GH = model.gh, W = canvas.width, Hc = canvas.height;
    const ctx = canvas.getContext("2d");
    const img = ctx.createImageData(W, Hc);
    const at = (x, y) => map[Math.min(GH - 1, Math.max(0, y)) * GW + Math.min(GW - 1, Math.max(0, x))];
    const lo = Math.max(0.15, threshold - 0.12); // transparent below this
    for (let py = 0; py < Hc; py++) {
      // pixel → cell-grid coordinate (cell k centred at k + 0.5)
      const gy = (py + 0.5) / Hc * (model.H / model.cell) - 0.5, y0 = Math.floor(gy), fy = gy - y0;
      for (let px = 0; px < W; px++) {
        const gx = (px + 0.5) / W * GW - 0.5, x0 = Math.floor(gx), fx = gx - x0;
        const v = (at(x0, y0) * (1 - fx) + at(x0 + 1, y0) * fx) * (1 - fy) + (at(x0, y0 + 1) * (1 - fx) + at(x0 + 1, y0 + 1) * fx) * fy;
        const t = Math.min(1, Math.max(0, (v - lo) / (1 - lo) * 1.6));
        const [r, g, b] = ramp(t);
        const i = (py * W + px) * 4;
        img.data[i] = r; img.data[i + 1] = g; img.data[i + 2] = b; img.data[i + 3] = Math.round(t ** 0.8 * 235);
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  // ---- refresh & cleanup --------------------------------------------------------------------------
  function refreshAll() {
    renderKPIs(); renderLive(); renderQueue(); renderViewer(); renderThreshold(); renderTrends();
  }

  renderPlayBtn();
  const firstFlag = pendingAI().slice(-1)[0];
  if (firstFlag) state.selected = firstFlag;
  refreshAll();
  const liveTimer = setInterval(() => {
    if (!state.running) return;
    renderLive();
    if (state.benchEl?.isConnected) renderBench(state.benchEl);
  }, 1000);

  return () => {
    clearInterval(state.timer);
    clearInterval(liveTimer);
    root.querySelectorAll(".chart").forEach((c) => c._ro?.disconnect());
    state.urls.forEach((u) => URL.revokeObjectURL(u));
  };
}

/** Warm sequential ramp for the anomaly heatmap: deep orange → amber → pale yellow. */
function ramp(t) {
  const stops = [[0, [124, 45, 18]], [0.45, [217, 89, 38]], [0.75, [250, 178, 25]], [1, [255, 236, 160]]];
  for (let k = 1; k < stops.length; k++) {
    if (t <= stops[k][0]) {
      const [t0, c0] = stops[k - 1], [t1, c1] = stops[k], f = (t - t0) / (t1 - t0);
      return c0.map((c, i) => Math.round(c + (c1[i] - c) * f));
    }
  }
  return stops[stops.length - 1][1];
}

function loadImg(src) {
  return new Promise((ok, bad) => {
    const i = new Image();
    i.onload = () => ok(i);
    i.onerror = () => bad(new Error("image failed to load"));
    i.src = src;
  });
}

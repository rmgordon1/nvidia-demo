// ===========================================================================
// CV defect detection hooks (two-stage pipeline) on banknote press sheets.
//
//   Stage 1 — scoreImage(source, man)   → { score, map[gw*gh], stats, ruleReject, category, display }
//     Placeholder: registered statistical golden template (per-pixel Gaussian of good sheets,
//     PaDiM-style per-position model). A step-for-step port of tools/build_cv_samples.py:
//     register the sheet on the dark conveyor → rectify to the canonical frame → 5-tap blur →
//     z = max_c |x - mean| / (std + floor) → 3x3 mean → max per cell ÷ per-cell normal level.
//     Replace with PatchCore/PaDiM served on the GB10 when ready.
//   Stage 2 — describeDefect(item)        → { classKey, className, location, text, confidence, ... }
//     Placeholder VLM: deterministic text from the heatmap peak and the known label of bundled
//     evaluation sheets (or simple heuristics on the peak region for uploads). Replace with a
//     local vision-language model.
//
// When AI.MODE === "api" both functions should call the model server instead (see TODOs).
// Every result carries provenance for the audit trail.
// ===========================================================================
import { AI, provenance, callApi } from "./config.js";

/** Fixed class order — also the chart/series order everywhere in the CV view. */
export const DEFECT_CLASSES = [
  { key: "ink_void", name: "Ink void / dropout", detail: "ink missing over a small area — the substrate shows through the design" },
  { key: "streak", name: "Doctor-blade streak", detail: "thin continuous ink line running in the direction of sheet travel" },
  { key: "smear", name: "Ink smear", detail: "ink dragged out of the printed detail with a soft deposit tail" },
  { key: "misregistration", name: "Misregistration", detail: "one ink layer offset from the others across the plate segment" },
  { key: "color_shift", name: "Color / density shift", detail: "ink density or hue drifting from the approved standard over a zone" },
  { key: "crease", name: "Crease / fold", detail: "linear fold with shading and cracked ink across the design" },
  { key: "tear", name: "Tear / hole", detail: "substrate missing — the conveyor shows through the sheet" },
  { key: "contamination", name: "Foreign-material contamination", detail: "small dark particle or fibre on the print surface" },
];
export const NO_DEFECT = { key: "none", name: "No clear defect", detail: "variation within the normal range for this plate; likely a false alarm" };
export const classByKey = (k) => DEFECT_CLASSES.find((c) => c.key === k) || NO_DEFECT;

const LUMA = [0.299, 0.587, 0.114];
let GEO = null; // { W, H, cell, gw, gh, layout } — set by configure()

/** Call once with the manifest before using the geometry helpers. */
export function configure(man) {
  const m = man.model;
  GEO = { W: m.W, H: m.H, cell: m.cell, gw: m.gw, gh: m.gh, layout: man.layout };
  return GEO;
}

/** base64 uint8 heatmap from the manifest → Float32Array in 0..1. */
export function decodeMap(b64) {
  const s = atob(b64), out = new Float32Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i) / 255;
  return out;
}

// ---------------------------------------------------------------------------
// Pixel helpers (RGB Float32Array, row-major, 3 channels)
// ---------------------------------------------------------------------------

function loadImage(src) {
  return new Promise((ok, bad) => {
    const i = new Image();
    i.onload = () => ok(i);
    i.onerror = () => bad(new Error(`failed to load ${src}`));
    i.src = src;
  });
}

/** Any image / canvas / video frame → { data: Float32Array RGB 0..1, w, h }. */
export function toRGB(source, maxSide = 2600) {
  const sw = source.naturalWidth || source.videoWidth || source.width;
  const sh = source.naturalHeight || source.videoHeight || source.height;
  const k = Math.min(1, maxSide / Math.max(sw, sh));
  const w = Math.round(sw * k), h = Math.round(sh * k);
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(source, 0, 0, w, h);
  const d = ctx.getImageData(0, 0, w, h).data, out = new Float32Array(w * h * 3);
  for (let i = 0, j = 0; i < d.length; i += 4, j += 3) { out[j] = d[i] / 255; out[j + 1] = d[i + 1] / 255; out[j + 2] = d[i + 2] / 255; }
  return { data: out, w, h };
}

function boxDown(img, w, h, f) {
  if (f <= 1) return { data: img, w, h };
  const w2 = Math.floor(w / f), h2 = Math.floor(h / f), out = new Float32Array(w2 * h2 * 3), n = f * f;
  for (let y = 0; y < h2; y++) {
    for (let x = 0; x < w2; x++) {
      let r = 0, g = 0, b = 0;
      for (let dy = 0; dy < f; dy++) {
        let i = ((y * f + dy) * w + x * f) * 3;
        for (let dx = 0; dx < f; dx++, i += 3) { r += img[i]; g += img[i + 1]; b += img[i + 2]; }
      }
      const o = (y * w2 + x) * 3;
      out[o] = r / n; out[o + 1] = g / n; out[o + 2] = b / n;
    }
  }
  return { data: out, w: w2, h: h2 };
}

/** Linear-interpolated percentile on a sorted array (numpy default). */
function pct(sorted, q) {
  const pos = (q / 100) * (sorted.length - 1), lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}
function median(arr) {
  const s = Float64Array.from(arr).sort(), n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

/** Robust least squares s = a*t + b: three rounds of residual trimming, then a final fit. */
function fitLine(t, s) {
  let keep = t.map(() => true);
  const ls = () => {
    let n = 0, st = 0, sv = 0, stt = 0, sts = 0;
    for (let i = 0; i < t.length; i++) if (keep[i]) { n++; st += t[i]; sv += s[i]; stt += t[i] * t[i]; sts += t[i] * s[i]; }
    const a = (n * sts - st * sv) / (n * stt - st * st);
    return [a, (sv - a * st) / n];
  };
  for (let it = 0; it < 3; it++) {
    const [a, b] = ls();
    const r = t.map((v, i) => Math.abs(s[i] - (a * v + b)));
    const tol = Math.max(1, 3 * median(r.filter((_, i) => keep[i])));
    keep = r.map((v) => v <= tol);
  }
  return ls();
}

/** Sheet corners [TL, TR, BR, BL] in full-res continuous px, or null when no sheet edge is found. */
export function register(img, w0, h0, regTarget) {
  const f = Math.max(1, Math.floor(Math.min(h0, w0) / regTarget));
  const { data, w, h } = boxDown(img, w0, h0, f);
  const g = new Float64Array(w * h);
  for (let i = 0; i < g.length; i++) g[i] = data[i * 3] * LUMA[0] + data[i * 3 + 1] * LUMA[1] + data[i * 3 + 2] * LUMA[2];
  const sorted = Float64Array.from(g).sort();
  const thr = (pct(sorted, 5) + pct(sorted, 95)) / 2;
  const rowN = new Int32Array(h), colN = new Int32Array(w);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) if (g[y * w + x] > thr) { rowN[y]++; colN[x]++; }
  const rows = [], cols = [];
  rowN.forEach((n, y) => { if (n / w > 0.2) rows.push(y); });
  colN.forEach((n, x) => { if (n / h > 0.2) cols.push(x); });
  if (rows.length < 20 || cols.length < 20) return null;

  const sides = {};
  for (const [axis, idx] of [[0, rows], [1, cols]]) {
    const lo = idx[0], hi = idx[idx.length - 1];
    const a = Math.trunc(lo + 0.1 * (hi - lo)), b = Math.trunc(hi - 0.1 * (hi - lo));
    const n = axis === 0 ? w : h;
    const at = axis === 0 ? (k, x) => g[k * w + x] : (k, y) => g[y * w + k];
    const pLo = { t: [], s: [] }, pHi = { t: [], s: [] };
    for (let k = a; k <= b; k++) {
      let first = -1, last = -1;
      for (let x = 0; x < n; x++) if (at(k, x) > thr) { first = x; break; }
      if (first < 0) continue;
      for (let x = n - 1; x >= 0; x--) if (at(k, x) > thr) { last = x; break; }
      let x = first;
      if (x > 0 && at(k, x) !== at(k, x - 1)) {
        const e = (x - 1) + Math.min(1, Math.max(0, (thr - at(k, x - 1)) / (at(k, x) - at(k, x - 1))));
        pLo.t.push(k + 0.5); pLo.s.push(e + 0.5);
      }
      x = last;
      if (x < n - 1 && at(k, x) !== at(k, x + 1)) {
        const e = x + Math.min(1, Math.max(0, (at(k, x) - thr) / (at(k, x) - at(k, x + 1))));
        pHi.t.push(k + 0.5); pHi.s.push(e + 0.5);
      }
    }
    sides[axis] = [pLo, pHi];
  }
  if (Math.min(...[...sides[0], ...sides[1]].map((p) => p.t.length)) < 20) return null;
  const [L, R] = sides[0].map((p) => fitLine(p.t, p.s)); // x = a*y + b
  const [T, B] = sides[1].map((p) => fitLine(p.t, p.s)); // y = a*x + b
  const meet = (v, hz) => {
    const x = (v[0] * hz[1] + v[1]) / (1 - v[0] * hz[0]);
    return [x * f, (hz[0] * x + hz[1]) * f];
  };
  return [meet(L, T), meet(R, T), meet(R, B), meet(L, B)];
}

/** 3x3 H (row-major) mapping canonical (0,0),(w,0),(w,h),(0,h) → dst corners. */
export function homography(dst, w, h) {
  const src = [[0, 0], [w, 0], [w, h], [0, h]], A = [];
  src.forEach(([x, y], i) => {
    const [u, v] = dst[i];
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y, u]);
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y, v]);
  });
  for (let c = 0; c < 8; c++) { // Gaussian elimination, partial pivoting
    let p = c;
    for (let r = c + 1; r < 8; r++) if (Math.abs(A[r][c]) > Math.abs(A[p][c])) p = r;
    [A[c], A[p]] = [A[p], A[c]];
    for (let r = 0; r < 8; r++) {
      if (r === c) continue;
      const k = A[r][c] / A[c][c];
      for (let j = c; j < 9; j++) A[r][j] -= k * A[c][j];
    }
  }
  return [...A.map((row, i) => row[8] / row[i]), 1];
}

function rectify(img, w0, h0, Hc, W, H, f2) {
  const { data: s, w: sw, h: sh } = boxDown(img, w0, h0, f2);
  const out = new Float32Array(W * H * 3);
  for (let j = 0; j < H; j++) {
    const v = j + 0.5;
    for (let i = 0; i < W; i++) {
      const u = i + 0.5, den = Hc[6] * u + Hc[7] * v + Hc[8];
      const x = (Hc[0] * u + Hc[1] * v + Hc[2]) / den / f2 - 0.5;
      const y = (Hc[3] * u + Hc[4] * v + Hc[5]) / den / f2 - 0.5;
      const x0 = Math.floor(x), y0 = Math.floor(y), fx = x - x0, fy = y - y0;
      const xa = Math.min(sw - 1, Math.max(0, x0)), xb = Math.min(sw - 1, Math.max(0, x0 + 1));
      const ya = Math.min(sh - 1, Math.max(0, y0)), yb = Math.min(sh - 1, Math.max(0, y0 + 1));
      const o = (j * W + i) * 3;
      for (let c = 0; c < 3; c++) {
        const top = s[(ya * sw + xa) * 3 + c] * (1 - fx) + s[(ya * sw + xb) * 3 + c] * fx;
        const bot = s[(yb * sw + xa) * 3 + c] * (1 - fx) + s[(yb * sw + xb) * 3 + c] * fx;
        out[o + c] = top * (1 - fy) + bot * fy;
      }
    }
  }
  return out;
}

/** Separable [1 4 6 4 1]/16 blur with edge replication (3 channels). */
function blur5(img, W, H) {
  const k = [1 / 16, 4 / 16, 6 / 16, 4 / 16, 1 / 16], tmp = new Float32Array(img.length), out = new Float32Array(img.length);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) for (let c = 0; c < 3; c++) {
    let acc = 0;
    for (let t = -2; t <= 2; t++) acc += k[t + 2] * img[(Math.min(H - 1, Math.max(0, y + t)) * W + x) * 3 + c];
    tmp[(y * W + x) * 3 + c] = acc;
  }
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) for (let c = 0; c < 3; c++) {
    let acc = 0;
    for (let t = -2; t <= 2; t++) acc += k[t + 2] * tmp[(y * W + Math.min(W - 1, Math.max(0, x + t))) * 3 + c];
    out[(y * W + x) * 3 + c] = acc;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Stage 1: model loading + anomaly scoring
// ---------------------------------------------------------------------------
const templates = new Map(); // url → Float32Array (RGB 0..1, or std after dequantisation)

async function pngRGB(url, scale) {
  if (templates.has(url)) return templates.get(url);
  const p = loadImage(url).then((img) => {
    const { data } = toRGB(img, Infinity);
    if (scale !== 1) for (let i = 0; i < data.length; i++) data[i] = Math.round(data[i] * 255) / scale;
    return data;
  });
  templates.set(url, p);
  return p;
}
const base = (path) => "data/cv/" + path;

/** Nearest plate design + side for a rectified, blurred sheet (low-res template match). */
async function matchCategory(xb, man) {
  const m = man.model, { data: low } = boxDown(xb, m.W, m.H, m.low);
  let best = null;
  for (const c of m.categories) {
    const t = await pngRGB(base(c.lowres), 255);
    let acc = 0;
    for (let i = 0; i < low.length; i++) acc += Math.abs(low[i] - t[i]);
    const dist = acc / low.length;
    if (!best || dist < best.dist) best = { key: c.key, dist, cat: c };
  }
  return best;
}

/**
 * Score one sheet image. Options: registered = true skips registration (the source is already a
 * rectified canonical sheet, e.g. a bundled sample).
 */
export async function scoreSheet(source, man, { registered = false } = {}) {
  const m = man.model, { W, H, cell, gw, gh } = m;
  const src = toRGB(source);
  let corners = registered ? null : register(src.data, src.w, src.h, m.regTarget);
  const regOk = registered || !!corners;
  if (!corners) corners = [[0, 0], [src.w, 0], [src.w, src.h], [0, src.h]];
  const Hc = homography(corners, W, H);
  const sheetW = (Math.hypot(corners[1][0] - corners[0][0], corners[1][1] - corners[0][1])
    + Math.hypot(corners[2][0] - corners[3][0], corners[2][1] - corners[3][1])) / 2;
  const rect = rectify(src.data, src.w, src.h, Hc, W, H, Math.max(1, Math.round(sheetW / W)));
  const xb = blur5(rect, W, H);
  const match = await matchCategory(xb, man);
  const [mean, std] = await Promise.all([pngRGB(base(match.cat.mean), 255), pngRGB(base(match.cat.std), m.sscale)]);

  // z-map, rules pixel count
  const N = W * H, z = new Float32Array(N), dl = new Float32Array(N);
  let count = 0;
  for (let p = 0; p < N; p++) {
    let zmax = 0, dmax = 0;
    for (let c = 0; c < 3; c++) {
      const d = Math.abs(xb[p * 3 + c] - mean[p * 3 + c]);
      if (d > dmax) dmax = d;
      const zz = d / (std[p * 3 + c] + m.floor);
      if (zz > zmax) zmax = zz;
    }
    z[p] = zmax;
    if (dmax > man.rules.tol) count++;
    dl[p] = (xb[p * 3] - mean[p * 3]) * LUMA[0] + (xb[p * 3 + 1] - mean[p * 3 + 1]) * LUMA[1] + (xb[p * 3 + 2] - mean[p * 3 + 2]) * LUMA[2];
  }
  const z3 = new Float32Array(N);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let acc = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++)
      acc += z[Math.min(H - 1, Math.max(0, y + dy)) * W + Math.min(W - 1, Math.max(0, x + dx))];
    z3[y * W + x] = acc / 9;
  }
  const cn = decodeMap(match.cat.cellnorm);
  const map = new Float32Array(gw * gh);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const k = ((y / cell) | 0) * gw + ((x / cell) | 0);
    if (z3[y * W + x] > map[k]) map[k] = z3[y * W + x];
  }
  for (let k = 0; k < map.length; k++) map[k] = Math.min(1, Math.max(0, map[k] / (cn[k] * 255 / m.cnq) / m.scale));
  let peak = 0;
  map.forEach((v, k) => { if (v > map[peak]) peak = k; });

  return { score: map[peak], map, rect, z, dl, ruleCount: count, ruleReject: count > man.rules.minPx,
    category: match.key, registered: regOk, peak };
}

/** Peak-region features for the upload heuristics (stage-2 stand-in). */
function regionFeatures(r, m) {
  const reg = peakRegion(r.map);
  const { W, H } = m;
  const x0 = Math.floor(reg.box.x * W), y0 = Math.floor(reg.box.y * H);
  const x1 = Math.ceil((reg.box.x + reg.box.w) * W), y1 = Math.ceil((reg.box.y + reg.box.h) * H);
  let zmax = 0;
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) zmax = Math.max(zmax, r.z[y * W + x]);
  let n = 0, sdl = 0, slum = 0, bx0 = W, by0 = H, bx1 = 0, by1 = 0;
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
    const p = y * W + x;
    if (r.z[p] < zmax * 0.5) continue;
    n++; sdl += r.dl[p];
    slum += r.rect[p * 3] * LUMA[0] + r.rect[p * 3 + 1] * LUMA[1] + r.rect[p * 3 + 2] * LUMA[2];
    bx0 = Math.min(bx0, x); by0 = Math.min(by0, y); bx1 = Math.max(bx1, x); by1 = Math.max(by1, y);
  }
  return { n, dl: n ? sdl / n : 0, lum: n ? slum / n : 1, bw: n ? bx1 - bx0 + 1 : 0, bh: n ? by1 - by0 + 1 : 0,
    edge: n ? bx0 < 4 || by0 < 4 || bx1 > W - 5 || by1 > H - 5 : false };
}

function displayURL(rect, W, H) {
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d"), img = ctx.createImageData(W, H);
  for (let p = 0; p < W * H; p++) {
    img.data[p * 4] = rect[p * 3] * 255; img.data[p * 4 + 1] = rect[p * 3 + 1] * 255;
    img.data[p * 4 + 2] = rect[p * 3 + 2] * 255; img.data[p * 4 + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
  return c.toDataURL("image/jpeg", 0.86);
}

/** Stage 1 entry point used by the UI for uploads / video frames. */
export async function scoreImage(source, man, opts) {
  const t0 = performance.now();
  if (AI.MODE === "api") {
    // TODO: POST the frame (canvas.toBlob → base64) to the anomaly model server:
    //   return callApi("/vision/score", { image: b64 })  → { score, map, category }
  }
  const r = await scoreSheet(source, man, opts);
  const latencyMs = performance.now() - t0;
  return {
    score: r.score, map: r.map, ruleReject: r.ruleReject, category: r.category, registered: r.registered,
    stats: { peak: r.peak, region: regionFeatures(r, man.model), ruleCount: r.ruleCount },
    display: displayURL(r.rect, man.model.W, man.model.H), latencyMs, provenance: provenance("anomaly"),
  };
}

/** Time the stage-1 pipeline (registration skipped) on already-rectified sheets. */
export async function benchmark(images, man, runs = 6) {
  await scoreSheet(images[0], man, { registered: true }); // warm the template cache
  const t0 = performance.now();
  for (let k = 0; k < runs; k++) await scoreSheet(images[k % images.length], man, { registered: true });
  return (performance.now() - t0) / runs;
}

// ---------------------------------------------------------------------------
// Heatmap geometry helpers (shared by the viewer and the VLM stand-in)
// ---------------------------------------------------------------------------

/** Connected region of cells around the peak above 80% of the peak value (normalised box). */
export function peakRegion(map) {
  const { gw, gh, cell, W, H } = GEO;
  let peak = 0;
  map.forEach((v, k) => { if (v > map[peak]) peak = k; });
  const cut = map[peak] * 0.8, seen = new Set([peak]), stack = [peak];
  while (stack.length) {
    const k = stack.pop(), x = k % gw, y = (k / gw) | 0;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy, nk = ny * gw + nx;
      if (nx >= 0 && ny >= 0 && nx < gw && ny < gh && !seen.has(nk) && map[nk] >= cut) { seen.add(nk); stack.push(nk); }
    }
  }
  const xs = [...seen].map((k) => k % gw), ys = [...seen].map((k) => (k / gw) | 0);
  const x0 = Math.min(...xs) * cell / W, y0 = Math.min(...ys) * cell / H;
  const x1 = Math.min(W, (Math.max(...xs) + 1) * cell) / W, y1 = Math.min(H, (Math.max(...ys) + 1) * cell) / H;
  return { peak, cells: seen.size, box: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 };
}

/** "note R3·C2 · lower-left" for a point on the sheet (normalised coords), or a margin description. */
export function locationPhrase(cx, cy) {
  const L = GEO?.layout;
  const third = (t, a, b, c) => (t < 1 / 3 ? a : t > 2 / 3 ? c : b);
  if (L) {
    const c = L.x.findIndex((x) => cx >= x && cx < x + L.nw), r = L.y.findIndex((y) => cy >= y && cy < y + L.nh);
    if (c >= 0 && r >= 0) {
      const u = (cx - L.x[c]) / L.nw, v = (cy - L.y[r]) / L.nh;
      const part = `${third(v, "upper", "mid", "lower")}-${third(u, "left", "center", "right")}`.replace("mid-center", "center");
      return `note R${r + 1}·C${c + 1} · ${part}`;
    }
  }
  return `sheet ${cy < 0.08 ? "top margin" : cy > 0.92 ? "bottom margin" : cx < 0.06 ? "left margin" : cx > 0.94 ? "right margin" : "gutter"}, ${third(cy, "upper", "mid", "lower")}-${third(cx, "left", "center", "right")}`;
}

// ---------------------------------------------------------------------------
// Stage 2: placeholder VLM
// ---------------------------------------------------------------------------
function hash(str) {
  let x = 2166136261;
  for (let i = 0; i < str.length; i++) x = Math.imul(x ^ str.charCodeAt(i), 16777619);
  return ((x >>> 0) % 1000) / 1000;
}

function heuristicClass(f) {
  // f = peak-region features { n, dl (signed luma diff), lum, bw, bh (px), edge }
  if (!f || !f.n) return "smear";
  const long = Math.max(f.bw, f.bh), short = Math.max(1, Math.min(f.bw, f.bh));
  if (f.lum < 0.25 && (f.edge || f.n > 40)) return "tear";
  if (f.bh / Math.max(1, f.bw) > 5 && f.bw <= 6) return f.dl > 0 ? "ink_void" : "streak";
  if (long > 60 && long / short > 3) return "crease";
  if (f.n > 600) return Math.abs(f.dl) < 0.03 ? "color_shift" : "misregistration";
  if (f.dl > 0.06) return "ink_void";
  if (f.n < 40 && f.dl < 0) return "contamination";
  return "smear";
}

/** Synchronous core — used for list/trend classification without latency. */
export function classifyDefect(item, threshold = 0.42) {
  if (item.det) return classifyFromDetector(item, threshold);
  const region = peakRegion(item.map);
  let key;
  if (item.label) key = item.label === "good" ? "none" : item.label;
  else key = item.score < threshold + 0.03 ? "none" : heuristicClass(item.stats?.region);
  const cls = classByKey(key);
  const loc = locationPhrase(region.cx, region.cy);
  const area = region.box.w * region.box.h * 100;
  const j = hash(item.id || "x");
  const confidence = key === "none"
    ? Math.min(0.93, 0.62 + j * 0.25)
    : Math.min(0.97, 0.58 + item.score * 0.45 + j * 0.08);
  const text = key === "none"
    ? `No clear defect in the flagged region (${loc}). Print matches the approved plate within normal variation; the stage-1 score is likely registration or texture noise. Suggest pass after visual confirmation.`
    : `${cls.name} at ${loc} — ${cls.detail}; affected area ${area < 0.1 ? "< 0.1" : "≈ " + (area < 1 ? area.toFixed(1) : Math.round(area))}% of the sheet.`;
  return {
    classKey: key, className: cls.name, location: loc, areaPct: area, box: region.box,
    text, confidence, recommendation: key === "none" ? "pass" : "reject",
  };
}

/**
 * POC: result built from the fine-tuned RF-DETR-Seg detections written into the manifest by
 * cv_model/scripts/poc_webapp.py (item.det). The description is templated from the detections — no VLM yet.
 */
function classifyFromDetector(item, threshold) {
  const dets = item.det.filter((d) => d.conf >= threshold);
  if (!dets.length) {
    return {
      classKey: "none", className: classByKey("none").name, location: "—", areaPct: 0, box: { x: 0, y: 0, w: 0, h: 0 },
      text: `No defect detected above confidence ${threshold.toFixed(2)}${item.det.length ? ` (weakest candidate: ${classByKey(item.det[0].cls).name} at ${item.det[0].conf.toFixed(2)})` : ""}.`,
      confidence: item.det.length ? 1 - item.det[0].conf : 0.95, recommendation: "pass", detections: [],
    };
  }
  const top = dets[0], cls = classByKey(top.cls);
  const box = { x: top.box[0], y: top.box[1], w: top.box[2], h: top.box[3] };
  const line = (d) => `${classByKey(d.cls).name} on note ${d.note} (${d.position}), ≈ ${d.size_mm[0]} × ${d.size_mm[1]} mm, confidence ${d.conf.toFixed(2)}`;
  const text = `${line(top)} — ${cls.detail}.` + (dets.length > 1 ? ` Also detected: ${dets.slice(1, 4).map(line).join("; ")}${dets.length > 4 ? `; +${dets.length - 4} more` : ""}.` : "");
  return {
    classKey: top.cls, className: cls.name, location: `note ${top.note}, ${top.position}`, areaPct: box.w * box.h * 100, box,
    text, confidence: top.conf, recommendation: "reject", detections: dets,
  };
}

/** Stage-2 entry point: describe + classify a flagged sheet. */
export async function describeDefect(item, threshold) {
  const t0 = performance.now();
  if (AI.MODE === "api") {
    // TODO: send the image + heatmap crop to the local VLM:
    //   return callApi("/vision/describe", { id: item.id, image: b64, box })
    void callApi;
  }
  const out = classifyDefect(item, threshold);
  if (item.det) return { ...out, latencyMs: performance.now() - t0, provenance: provenance("vlm") };
  // Simulate on-device VLM latency so the UI states (loading → result) are exercised.
  const simulated = 220 + hash(item.id + "lat") * 380;
  await new Promise((r) => setTimeout(r, simulated));
  return { ...out, latencyMs: performance.now() - t0, provenance: provenance("vlm") };
}

// Dependency-free SVG charts (works air-gapped). Each chart re-renders on resize
// and ships a hover layer: crosshair + tooltip for lines, per-mark tooltip for bars.
//
//   lineChart(el, { x, series:[{name, values, color?, dash?, area?}], yFmt, xFmt, refs, height })
//   barChart(el,  { labels, series:[{name, values, color?}], stacked, yFmt, height })
//   hbarChart(el, { items:[{label, value, color?, note?}], fmt, max })
//   sparkline(el, values, { color })
//
// Colors default to the categorical slots in fixed order (var(--series-N)).

const NS = "http://www.w3.org/2000/svg";
export const SERIES = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => `var(--series-${i})`);

function s(tag, attrs = {}, parent) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  if (parent) parent.append(el);
  return el;
}

function niceTicks(min, max, count = 5) {
  if (min === max) { max = min + 1; }
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((st) => span / st <= count) || 10 * mag;
  const lo = Math.floor(min / step) * step, hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

/** Attach chart to `el`, re-render on resize. Returns { update(opts) }. */
function mount(el, render, opts) {
  el.classList.add("chart");
  let current = opts;
  const draw = () => {
    el.replaceChildren();
    if (el.clientWidth > 0) render(el, current);
  };
  if (el._ro) el._ro.disconnect();
  let lastW = 0;
  el._ro = new ResizeObserver(() => {
    if (Math.abs(el.clientWidth - lastW) > 2) { lastW = el.clientWidth; draw(); }
  });
  el._ro.observe(el);
  draw();
  return { update(o) { current = { ...current, ...o }; draw(); } };
}

function tooltip(el) {
  const tip = document.createElement("div");
  tip.className = "chart-tip";
  tip.hidden = true;
  el.append(tip);
  return {
    show(x, y, title, rows) {
      tip.replaceChildren();
      const t = document.createElement("div");
      t.className = "tt-title";
      t.textContent = title;
      tip.append(t);
      for (const r of rows) {
        const row = document.createElement("div");
        row.className = "tt-row";
        const a = document.createElement("span");
        if (r.color) {
          const i = document.createElement("i");
          i.className = "dot";
          i.style.background = r.color;
          a.append(i);
        }
        a.append(r.name);
        const b = document.createElement("b");
        b.textContent = r.value;
        row.append(a, b);
        tip.append(row);
      }
      tip.hidden = false;
      const w = el.clientWidth, tw = tip.offsetWidth, th = tip.offsetHeight;
      tip.style.left = Math.min(Math.max(0, x + 14), w - tw) + "px";
      tip.style.top = Math.max(0, y - th - 10) + "px";
    },
    hide() { tip.hidden = true; },
  };
}

function legend(el, series) {
  if (series.length < 2) return;
  const lg = document.createElement("div");
  lg.className = "legend";
  for (const sr of series) {
    const sp = document.createElement("span");
    const i = document.createElement("i");
    i.className = sr.dash ? "dash" : sr.kind === "line" ? "line" : "";
    if (sr.dash) i.style.borderColor = sr.color; else i.style.background = sr.color;
    sp.append(i, sr.name);
    lg.append(sp);
  }
  el.prepend(lg);
}

// ---------------------------------------------------------------------------
export function lineChart(el, opts) {
  return mount(el, renderLine, opts);
}
function renderLine(el, o) {
  const series = o.series.map((sr, i) => ({ ...sr, color: sr.color || SERIES[i], kind: "line" }));
  legend(el, series);
  const W = el.clientWidth, H = o.height || 220;
  const m = { t: 10, r: o.rightPad ?? 16, b: 26, l: o.leftPad ?? 46 };
  const n = o.x.length;
  const all = series.flatMap((sr) => sr.values).filter((v) => v != null && isFinite(v));
  let yMin = o.yMin ?? Math.min(0, ...all), yMax = o.yMax ?? Math.max(...all, yMin + 1e-9);
  const ticks = niceTicks(yMin, yMax, o.yTicks || 4);
  yMin = o.yMin ?? ticks[0]; yMax = o.yMax ?? ticks[ticks.length - 1];
  const numericX = typeof o.x[0] === "number" && o.numericX;
  const xMin = numericX ? Math.min(...o.x) : 0, xMax = numericX ? Math.max(...o.x) : n - 1;
  const X = (i) => m.l + ((numericX ? o.x[i] - xMin : i) / Math.max(1e-9, xMax - xMin)) * (W - m.l - m.r);
  const Xv = (v) => m.l + ((v - xMin) / Math.max(1e-9, xMax - xMin)) * (W - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - yMin) / (yMax - yMin || 1)) * (H - m.t - m.b);
  const yFmt = o.yFmt || ((v) => v.toLocaleString());
  const xFmt = o.xFmt || ((v) => String(v));

  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.label || "line chart" });
  for (const t of (o.yMin != null || o.yMax != null) ? niceTicks(yMin, yMax, o.yTicks || 4).filter((t) => t >= yMin && t <= yMax) : ticks) {
    s("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: "grid-line" }, svg);
    s("text", { x: m.l - 8, y: Y(t) + 4, "text-anchor": "end", class: "axis-label" }, svg).textContent = yFmt(t);
  }
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((W - m.l - m.r) / 70))));
  if (numericX) {
    for (const t of niceTicks(xMin, xMax, 5).filter((t) => t >= xMin && t <= xMax))
      s("text", { x: Xv(t), y: H - 6, "text-anchor": "middle", class: "axis-label" }, svg).textContent = xFmt(t);
  } else {
    for (let i = 0; i < n; i += every)
      s("text", { x: X(i), y: H - 6, "text-anchor": "middle", class: "axis-label" }, svg).textContent = xFmt(o.x[i], i);
  }

  // reference lines / bands (vertical at x index or value; horizontal at y)
  for (const r of o.refs || []) {
    if (r.band) {
      const x1 = X(r.band[0]), x2 = X(r.band[1]);
      s("rect", { x: x1, y: m.t, width: Math.max(0, x2 - x1), height: H - m.t - m.b, fill: "var(--surface-3)", opacity: 0.55 }, svg);
      if (r.label) s("text", { x: x1 + 6, y: m.t + 12, class: "ref-label" }, svg).textContent = r.label;
    } else if (r.x != null) {
      const xx = numericX ? Xv(r.x) : X(r.x);
      s("line", { x1: xx, x2: xx, y1: m.t, y2: H - m.b, class: "ref-line" }, svg);
      if (r.label) s("text", { x: xx + 5, y: m.t + 12, class: "ref-label" }, svg).textContent = r.label;
    } else if (r.y != null) {
      s("line", { x1: m.l, x2: W - m.r, y1: Y(r.y), y2: Y(r.y), class: "ref-line" }, svg);
      if (r.label) s("text", { x: W - m.r, y: Y(r.y) - 5, "text-anchor": "end", class: "ref-label" }, svg).textContent = r.label;
    }
  }

  for (const sr of series) {
    let d = "", pen = false, first = null, last = null;
    sr.values.forEach((v, i) => {
      if (v == null || !isFinite(v)) { pen = false; return; }
      d += (pen ? "L" : "M") + X(i).toFixed(1) + "," + Y(v).toFixed(1);
      pen = true;
      if (first == null) first = i;
      last = i;
    });
    if (sr.area && first != null) {
      s("path", { d: d + `L${X(last)},${Y(Math.max(yMin, 0))}L${X(first)},${Y(Math.max(yMin, 0))}Z`, fill: sr.color, opacity: 0.12 }, svg);
    }
    s("path", { d, fill: "none", stroke: sr.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round", "stroke-dasharray": sr.dash ? "5 4" : null }, svg);
    if (sr.points) sr.values.forEach((v, i) => v != null && s("circle", { cx: X(i), cy: Y(v), r: 3.5, fill: sr.color, stroke: "var(--surface-1)", "stroke-width": 2 }, svg));
    if (o.directLabels && last != null && series.length <= 4) {
      s("text", { x: X(last) + 6, y: Y(sr.values[last]) + 4, class: "direct-label" }, svg).textContent = sr.label || sr.name;
    }
  }
  for (const mk of o.markers || []) {
    s("circle", { cx: numericX ? Xv(mk.x) : X(mk.x), cy: Y(mk.y), r: mk.r || 6, fill: mk.color || "var(--text)", stroke: "var(--surface-1)", "stroke-width": 2 }, svg);
    if (mk.label) s("text", { x: (numericX ? Xv(mk.x) : X(mk.x)) + 9, y: Y(mk.y) + 4, class: "direct-label" }, svg).textContent = mk.label;
  }

  // hover layer
  const cross = s("line", { y1: m.t, y2: H - m.b, class: "crosshair", visibility: "hidden" }, svg);
  const dots = series.map((sr) => s("circle", { r: 4.5, fill: sr.color, stroke: "var(--surface-1)", "stroke-width": 2, visibility: "hidden" }, svg));
  const hit = s("rect", { x: m.l, y: m.t, width: Math.max(0, W - m.l - m.r), height: H - m.t - m.b, class: "hit" }, svg);
  el.append(svg);
  const tip = tooltip(el);
  const locate = (px) => {
    let best = 0, bd = Infinity;
    for (let i = 0; i < n; i++) { const dd = Math.abs(X(i) - px); if (dd < bd) { bd = dd; best = i; } }
    return best;
  };
  hit.addEventListener("pointermove", (e) => {
    const rect = svg.getBoundingClientRect();
    const px = (e.clientX - rect.left) * (W / rect.width);
    const i = locate(px);
    cross.setAttribute("x1", X(i)); cross.setAttribute("x2", X(i)); cross.setAttribute("visibility", "visible");
    series.forEach((sr, k) => {
      const v = sr.values[i];
      if (v == null || !isFinite(v)) { dots[k].setAttribute("visibility", "hidden"); return; }
      dots[k].setAttribute("cx", X(i)); dots[k].setAttribute("cy", Y(v)); dots[k].setAttribute("visibility", "visible");
    });
    const rows = series.filter((sr) => sr.values[i] != null && isFinite(sr.values[i]))
      .map((sr) => ({ name: sr.name, color: sr.color, value: (sr.fmt || yFmt)(sr.values[i]) }));
    tip.show(X(i) * (rect.width / W), Y(Math.max(...series.map((sr) => sr.values[i] ?? -Infinity))) * (rect.height / H), (o.tipTitle || xFmt)(o.x[i], i), rows);
  });
  hit.addEventListener("pointerleave", () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    tip.hide();
  });
}

// ---------------------------------------------------------------------------
export function barChart(el, opts) {
  return mount(el, renderBar, opts);
}
function renderBar(el, o) {
  const series = o.series.map((sr, i) => ({ ...sr, color: sr.color || SERIES[i] }));
  legend(el, series);
  const W = el.clientWidth, H = o.height || 220;
  const m = { t: 10, r: 10, b: 26, l: o.leftPad ?? 46 };
  const n = o.labels.length;
  const totals = o.labels.map((_, i) => o.stacked
    ? series.reduce((a, sr) => a + Math.max(0, sr.values[i] || 0), 0)
    : Math.max(...series.map((sr) => sr.values[i] || 0)));
  const ticks = niceTicks(0, Math.max(...totals, o.yMax || 0, 1e-9), 4);
  const yMax = ticks[ticks.length - 1];
  const Y = (v) => m.t + (1 - v / yMax) * (H - m.t - m.b);
  const band = (W - m.l - m.r) / n;
  const groupW = Math.min(band * 0.72, o.stacked ? 42 : 18 * series.length + 2 * (series.length - 1));
  const barW = o.stacked ? groupW : Math.max(2, (groupW - 2 * (series.length - 1)) / series.length);
  const yFmt = o.yFmt || ((v) => v.toLocaleString());
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.label || "bar chart" });
  for (const t of ticks) {
    s("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: "grid-line" }, svg);
    s("text", { x: m.l - 8, y: Y(t) + 4, "text-anchor": "end", class: "axis-label" }, svg).textContent = yFmt(t);
  }
  const every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor((W - m.l - m.r) / 64))));
  o.labels.forEach((lb, i) => {
    if (i % every === 0) s("text", { x: m.l + band * (i + 0.5), y: H - 6, "text-anchor": "middle", class: "axis-label" }, svg).textContent = (o.xFmt || String)(lb, i);
  });
  el.append(svg);
  const tip = tooltip(el);
  const bars = [];
  o.labels.forEach((lb, i) => {
    const x0 = m.l + band * (i + 0.5) - groupW / 2;
    let acc = 0;
    series.forEach((sr, k) => {
      const v = Math.max(0, sr.values[i] || 0);
      if (!v) return;
      const x = o.stacked ? x0 : x0 + k * (barW + 2);
      const y1 = Y(acc + v), y0 = Y(acc);
      const isTop = !o.stacked || series.slice(k + 1).every((s2) => !(s2.values[i] > 0));
      const hgt = Math.max(1, y0 - y1 - (o.stacked && acc > 0 ? 2 : 0)); // 2px surface gap between stacked segments
      const r = Math.min(4, barW / 2, hgt);
      const d = isTop
        ? `M${x},${y1 + hgt} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + barW - r} Q${x + barW},${y1} ${x + barW},${y1 + r} V${y1 + hgt} Z`
        : `M${x},${y1 + hgt} V${y1} H${x + barW} V${y1 + hgt} Z`;
      bars.push(s("path", { d, fill: sr.color }, svg));
      if (o.stacked) acc += v;
    });
    // per-column hit target (bigger than the marks)
    const hit = s("rect", { x: m.l + band * i, y: m.t, width: band, height: H - m.t - m.b, class: "hit" }, svg);
    hit.addEventListener("pointermove", (e) => {
      const rect = svg.getBoundingClientRect();
      const rows = series.map((sr) => ({ name: sr.name, color: sr.color, value: (sr.fmt || yFmt)(sr.values[i] || 0) }));
      if (o.stacked && series.length > 1) rows.push({ name: "Total", value: yFmt(totals[i]) });
      hit.setAttribute("fill", "rgba(255,255,255,0.03)");
      tip.show((m.l + band * (i + 0.5)) * (rect.width / W), Y(totals[i]) * (rect.height / H), (o.tipTitle || o.xFmt || String)(lb, i), rows);
    });
    hit.addEventListener("pointerleave", () => { hit.setAttribute("fill", "transparent"); tip.hide(); });
    if (o.onClick) hit.addEventListener("click", () => o.onClick(i));
  });
}

// ---------------------------------------------------------------------------
export function hbarChart(el, opts) {
  return mount(el, renderHbar, opts);
}
function renderHbar(el, o) {
  const W = el.clientWidth;
  const rowH = o.rowH || 30, labelW = o.labelW ?? Math.min(200, W * 0.42), valueW = 56;
  const maxChars = Math.floor((labelW - 10) / 6.6);
  const H = o.items.length * rowH + 4;
  const max = o.max ?? Math.max(...o.items.map((it) => Math.abs(it.value)), 1e-9);
  const fmt = o.fmt || ((v) => v.toLocaleString());
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, role: "img", "aria-label": o.label || "bar chart" });
  el.append(svg);
  const tip = tooltip(el);
  const track = W - labelW - valueW;
  o.items.forEach((it, i) => {
    const y = i * rowH + 4, bh = Math.min(14, rowH - 12);
    const lb = s("text", { x: 0, y: y + rowH / 2 + 2, class: "axis-label", fill: "var(--text-2)" }, svg);
    lb.textContent = it.label.length > maxChars ? it.label.slice(0, maxChars - 1) + "…" : it.label;
    if (it.label.length > maxChars) s("title", {}, lb).textContent = it.label;
    lb.style.fill = "var(--text-2)";
    lb.style.fontSize = "12px";
    s("rect", { x: labelW, y: y + (rowH - bh) / 2 - 2, width: track, height: bh, rx: 4, fill: "var(--surface-3)", opacity: 0.6 }, svg);
    const w = Math.max(2, (Math.abs(it.value) / max) * track);
    s("rect", { x: labelW, y: y + (rowH - bh) / 2 - 2, width: w, height: bh, rx: 4, fill: it.color || o.color || SERIES[0] }, svg);
    const vt = s("text", { x: W, y: y + rowH / 2 + 2, "text-anchor": "end", class: "direct-label" }, svg);
    vt.textContent = fmt(it.value);
    const hit = s("rect", { x: 0, y, width: W, height: rowH, class: "hit" }, svg);
    hit.addEventListener("pointermove", (e) => {
      const rect = svg.getBoundingClientRect();
      tip.show((labelW + w) * (rect.width / W), y * (rect.height / H), it.label,
        [{ name: o.valueName || "Value", color: it.color || o.color || SERIES[0], value: fmt(it.value) }, ...(it.note ? [{ name: it.note, value: "" }] : [])]);
    });
    hit.addEventListener("pointerleave", () => tip.hide());
    if (o.onClick) { hit.style.cursor = "pointer"; hit.addEventListener("click", () => o.onClick(it, i)); }
  });
}

// ---------------------------------------------------------------------------
export function sparkline(el, values, { color = SERIES[0], height = 32 } = {}) {
  return mount(el, (host) => {
    const W = host.clientWidth, H = height;
    const v = values.filter((x) => x != null && isFinite(x));
    const lo = Math.min(...v), hi = Math.max(...v);
    const X = (i) => 2 + (i / Math.max(1, values.length - 1)) * (W - 4);
    const Y = (x) => 3 + (1 - (x - lo) / (hi - lo || 1)) * (H - 6);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, height: H, "aria-hidden": "true" });
    const d = values.map((x, i) => (i ? "L" : "M") + X(i).toFixed(1) + "," + Y(x).toFixed(1)).join("");
    s("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }, svg);
    s("circle", { cx: X(values.length - 1), cy: Y(values[values.length - 1]), r: 3, fill: color }, svg);
    host.append(svg);
  }, {});
}

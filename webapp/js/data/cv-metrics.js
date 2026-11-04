// Scoring helpers for the inspection set: AI (anomaly score >= threshold) vs the
// rules-based baseline (item.ruleReject). Ground truth: item.label !== "good".
import * as store from "../store.js";

export const DEFAULT_THRESHOLD = 0.42;
export const getThreshold = () => store.get("cv2.threshold", DEFAULT_THRESHOLD);
export const setThreshold = (t) => store.set("cv2.threshold", t);

export function confusion(items, flag) {
  let tp = 0, fp = 0, tn = 0, fn = 0;
  for (const it of items) {
    const bad = it.label !== "good", f = flag(it);
    if (bad && f) tp++; else if (bad) fn++; else if (f) fp++; else tn++;
  }
  const pos = tp + fn, neg = tn + fp;
  return {
    tp, fp, tn, fn,
    recall: pos ? tp / pos : 0,           // defect detection rate
    escape: pos ? fn / pos : 0,           // defects let through
    frr: neg ? fp / neg : 0,              // false-reject rate (good sheets rejected)
    precision: tp + fp ? tp / (tp + fp) : 0,
    flagged: tp + fp,
  };
}
export const aiMetrics = (items, t = getThreshold()) => confusion(items, (it) => it.score >= t);
export const rulesMetrics = (items) => confusion(items, (it) => it.ruleReject);

/** Sweep thresholds for the tradeoff curve. */
export function sweep(items, steps = 60) {
  const scores = items.map((i) => i.score);
  const lo = Math.min(...scores), hi = Math.max(...scores);
  return Array.from({ length: steps + 1 }, (_, k) => {
    const t = lo + ((hi - lo) * k) / steps;
    return { t, ...aiMetrics(items, t) };
  });
}

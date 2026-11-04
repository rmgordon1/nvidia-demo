#!/usr/bin/env python3
"""Export banknote press-sheet inspection samples + the stage-1 placeholder model.

Source: synthetic_print/dataset (run synthetic_print/generate.py first).
Writes webapp/data/cv/{sheets,thumbs,model}/ and webapp/data/cv/manifest.json.

Stage-1 stand-in: a registered statistical golden template -- per-pixel Gaussian statistics of
good sheets (PaDiM-style per-position model), one per plate design + side:

  1. register  find the sheet's four edges against the dark conveyor (sub-pixel threshold
               crossings, robust line fits) and intersect them -> homography
  2. rectify   resample the sheet to a canonical W x H frame (integer box pre-downsample +
               bilinear), then a 5-tap binomial blur
  3. score     z = max over RGB of |x - mean| / (std + FLOOR), 3x3 box mean, max per CELL px
               cell, divided by that cell's normal level (per-position 95th percentile on good
               sheets not used for the fit) -> heatmap; image score = max cell / `scale`

Template mean/std are written as PNGs and every step is mirrored in js/ai/vision.js, so the
browser scores uploads with the same method. A deliberately naive rules baseline compares the
same template with one fixed tolerance (no per-pixel variance) and a pixel-count limit.

    python3 tools/build_cv_samples.py [--good 96] [--per-class 8] [--fit 150]
"""
import argparse
import base64
import glob
import json
import os
import sys
from multiprocessing import Pool

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(HERE, "..", "..", "synthetic_print", "dataset")
OUT = os.path.join(HERE, "..", "data", "cv")
sys.path.insert(0, os.path.join(HERE, "..", "..", "synthetic_print"))
from generate import Geometry  # noqa: E402  (sheet layout, for note positions)

W = 1024             # canonical sheet width (px); height follows the sheet aspect
CELL = 16            # heatmap cell size in canonical px
FLOOR = 0.03         # std floor: sensor noise + JPEG
SSCALE = 1020.0      # std quantisation: std_png = round(std * SSCALE)
LOW = 8              # category matching runs on the canonical image box-downsampled by this
RULE_TOL = 0.12      # rules baseline: fixed |x - template| tolerance
REG_TARGET = 600     # registration runs at roughly this many px on the short side
CN_Q = 40.0         # cell-norm quantisation: cn_byte = round(cn * CN_Q)
LUMA = np.array([0.299, 0.587, 0.114], np.float32)

LABELS = {
    "good": "Good",
    "ink_void": "Ink void / dropout",
    "streak": "Doctor-blade streak",
    "smear": "Ink smear",
    "misregistration": "Misregistration",
    "color_shift": "Color / density shift",
    "crease": "Crease / fold",
    "tear": "Tear / hole",
    "contamination": "Foreign-material contamination",
}


# --------------------------------------------------------------------------- pipeline (mirrored in JS)


def box_down(img, f):
    if f <= 1:
        return img
    h, w = img.shape[0] // f, img.shape[1] // f
    return img[:h * f, :w * f].reshape(h, f, w, f, -1).mean((1, 3), dtype=np.float32)


def fit_line(t, s):
    """Robust least-squares s = a*t + b: three rounds of residual trimming, then a final fit."""
    keep = np.ones(len(t), bool)

    def ls(m):
        tt, ss = t[m], s[m]
        n, st, sv, stt, sts = len(tt), tt.sum(), ss.sum(), (tt * tt).sum(), (tt * ss).sum()
        a = (n * sts - st * sv) / (n * stt - st * st)
        return a, (sv - a * st) / n

    for _ in range(3):
        a, b = ls(keep)
        r = np.abs(s - (a * t + b))
        keep = r <= max(1.0, 3 * np.median(r[keep]))
    return ls(keep)


def register(rgb):
    """Sheet corners (TL, TR, BR, BL) in full-res continuous px, or None if no sheet edge is found."""
    h0, w0 = rgb.shape[:2]
    f = max(1, min(h0, w0) // REG_TARGET)
    g = (box_down(rgb, f) @ LUMA).astype(np.float64)
    h, w = g.shape
    thr = (np.percentile(g, 5) + np.percentile(g, 95)) / 2
    m = g > thr
    rows, cols = np.nonzero(m.mean(1) > 0.2)[0], np.nonzero(m.mean(0) > 0.2)[0]
    if len(rows) < 20 or len(cols) < 20:
        return None
    sides = {}
    for axis, idx in ((0, rows), (1, cols)):
        lo, hi = idx[0], idx[-1]
        a, b = int(lo + 0.1 * (hi - lo)), int(hi - 0.1 * (hi - lo))
        lines = np.arange(a, b + 1)
        G = g[lines] if axis == 0 else g[:, lines].T   # each row: a scan line across the sheet
        M = G > thr
        n = G.shape[1]
        has = M.any(1)
        first = np.argmax(M, 1)                      # outermost bright px from each end of the scan line
        last = n - 1 - np.argmax(M[:, ::-1], 1)
        pts_lo, pts_hi = [], []
        for k in np.nonzero(has)[0]:
            x = first[k]
            if x > 0 and G[k, x] != G[k, x - 1]:
                e = (x - 1) + np.clip((thr - G[k, x - 1]) / (G[k, x] - G[k, x - 1]), 0, 1)
                pts_lo.append((lines[k] + 0.5, e + 0.5))
            x = last[k]
            if x < n - 1 and G[k, x] != G[k, x + 1]:
                e = x + np.clip((G[k, x] - thr) / (G[k, x] - G[k, x + 1]), 0, 1)
                pts_hi.append((lines[k] + 0.5, e + 0.5))
        sides[axis] = (np.array(pts_lo), np.array(pts_hi))
    if min(len(p) for pair in sides.values() for p in pair) < 20:
        return None
    (left, right), (top, bottom) = sides[0], sides[1]
    L, R = fit_line(left[:, 0], left[:, 1]), fit_line(right[:, 0], right[:, 1])     # x = a*y + b
    T, B = fit_line(top[:, 0], top[:, 1]), fit_line(bottom[:, 0], bottom[:, 1])     # y = a*x + b

    def meet(v, hz):
        x = (v[0] * hz[1] + v[1]) / (1 - v[0] * hz[0])
        return x * f, (hz[0] * x + hz[1]) * f

    return np.array([meet(L, T), meet(R, T), meet(R, B), meet(L, B)])


def homography(dst, w, h):
    """3x3 H mapping canonical (0,0),(w,0),(w,h),(0,h) -> dst corners."""
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    A, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    return np.append(np.linalg.solve(np.array(A, float), np.array(rhs, float)), 1.0).reshape(3, 3)


def rectify(rgb, Hc, w, h, f2):
    s = box_down(rgb, f2)
    sh, sw = s.shape[:2]
    v, u = np.mgrid[0:h, 0:w].astype(np.float64) + 0.5
    den = Hc[2, 0] * u + Hc[2, 1] * v + Hc[2, 2]
    x = (Hc[0, 0] * u + Hc[0, 1] * v + Hc[0, 2]) / den / f2 - 0.5
    y = (Hc[1, 0] * u + Hc[1, 1] * v + Hc[1, 2]) / den / f2 - 0.5
    x0, y0 = np.floor(x), np.floor(y)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    xa, xb = np.clip(x0, 0, sw - 1).astype(int), np.clip(x0 + 1, 0, sw - 1).astype(int)
    ya, yb = np.clip(y0, 0, sh - 1).astype(int), np.clip(y0 + 1, 0, sh - 1).astype(int)
    top = s[ya, xa] * (1 - fx) + s[ya, xb] * fx
    bot = s[yb, xa] * (1 - fx) + s[yb, xb] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def blur5(img):
    k = np.array([1, 4, 6, 4, 1], np.float32) / 16
    p = np.pad(img, ((2, 2), (0, 0), (0, 0)), mode="edge")
    img = sum(k[i] * p[i:i + img.shape[0]] for i in range(5))
    p = np.pad(img, ((0, 0), (2, 2), (0, 0)), mode="edge")
    return sum(k[i] * p[:, i:i + img.shape[1]] for i in range(5)).astype(np.float32)


def box3(a):
    p = np.pad(a, 1, mode="edge")
    return sum(p[i:i + a.shape[0], j:j + a.shape[1]] for i in range(3) for j in range(3)) / 9.0


def prepare(path, h):
    """Load -> register -> rectify -> blur. Returns (rect, blurred, corners, Hc, registered)."""
    rgb = np.asarray(Image.open(path).convert("RGB"), np.float32) / 255.0
    corners = register(rgb)
    ok = corners is not None
    if not ok:
        H0, W0 = rgb.shape[:2]
        corners = np.array([(0, 0), (W0, 0), (W0, H0), (0, H0)], float)
    Hc = homography(corners, W, h)
    sheet_w = (np.hypot(*(corners[1] - corners[0])) + np.hypot(*(corners[2] - corners[3]))) / 2
    rect = rectify(rgb, Hc, W, h, max(1, int(round(sheet_w / W))))
    return rect, blur5(rect), corners, Hc, ok


def score_raw(xb, mean, std, gw, gh):
    d = np.abs(xb - mean)
    z = box3((d / (std + FLOOR)).max(-1))
    zp = np.zeros((gh * CELL, gw * CELL), np.float32)
    zp[:z.shape[0], :z.shape[1]] = z
    cells = zp.reshape(gh, CELL, gw, CELL).max((1, 3))
    return cells, int((d.max(-1) > RULE_TOL).sum())


# --------------------------------------------------------------------------- workers

CTX = {}


def fit_category(args):
    cat, paths, h = args
    s = np.zeros((h, W, 3)), np.zeros((h, W, 3))
    fails = 0
    for p in paths:
        _, xb, _, _, ok = prepare(p, h)
        fails += not ok
        s[0][:] += xb
        s[1][:] += xb.astype(np.float64) ** 2
    return cat, s[0], s[1], len(paths), fails


def _init(model_dir, cats, h):
    CTX["h"] = h
    CTX["cats"] = cats
    CTX["mean"], CTX["std"], CTX["low"] = {}, {}, {}
    for c in cats:
        CTX["mean"][c] = np.asarray(Image.open(os.path.join(model_dir, f"{c}_mean.png")), np.float32) / 255.0
        CTX["std"][c] = np.asarray(Image.open(os.path.join(model_dir, f"{c}_std.png")), np.float32) / SSCALE
        CTX["low"][c] = np.asarray(Image.open(os.path.join(model_dir, f"{c}_low.png")), np.float32) / 255.0


def score_job(job):
    path, export = job
    h, gw, gh = CTX["h"], W // CELL, -(-CTX["h"] // CELL)
    rect, xb, corners, Hc, ok = prepare(path, h)
    low = box_down(xb, LOW)
    dist = {c: float(np.abs(low - CTX["low"][c]).mean()) for c in CTX["cats"]}
    cat = min(dist, key=dist.get)
    cells, count = score_raw(xb, CTX["mean"][cat], CTX["std"][cat], gw, gh)
    if export:
        name = export
        im = Image.fromarray((np.clip(rect, 0, 1) * 255 + 0.5).astype(np.uint8))
        im.save(os.path.join(OUT, "sheets", name), quality=86)
        im.resize((256, round(256 * h / W)), Image.LANCZOS).save(os.path.join(OUT, "thumbs", name), quality=80)
    return dict(path=path, cat=cat, cells=cells, count=count, Hc=Hc, corners=corners, registered=ok)


# --------------------------------------------------------------------------- main


def to_canon_box(Hc, bbox):
    Hi = np.linalg.inv(Hc)
    x0, y0, x1, y1 = bbox
    pts = np.array([[x0, y0, 1], [x1, y0, 1], [x1, y1, 1], [x0, y1, 1]], float) @ Hi.T
    xs, ys = pts[:, 0] / pts[:, 2], pts[:, 1] / pts[:, 2]
    return xs.min(), ys.min(), xs.max(), ys.max()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=DATASET)
    ap.add_argument("--good", type=int, default=96, help="good sheets exported")
    ap.add_argument("--per-class", type=int, default=8, help="defective sheets exported per class")
    ap.add_argument("--fit", type=int, default=150, help="good train sheets per category used to fit")
    ap.add_argument("--norm", type=int, default=60, help="unused good train sheets per category for per-cell norms")
    ap.add_argument("--calib", type=int, default=40, help="held-out good val sheets per category for calibration")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    rng = np.random.default_rng(20261104)

    ann = json.load(open(os.path.join(a.dataset, "annotations.json")))
    imgs = ann["images"]
    sw, sh = ann["sheet_px"]
    h = int(round(W * sh / sw))
    gw, gh = W // CELL, -(-h // CELL)
    cats = sorted({r["design"] for r in imgs})
    path = lambda r: os.path.join(a.dataset, r["file"])  # noqa: E731

    for sub in ("sheets", "thumbs", "model"):
        d = os.path.join(OUT, sub)
        os.makedirs(d, exist_ok=True)
        for f in glob.glob(os.path.join(d, "*")):
            os.remove(f)
    for f in glob.glob(os.path.join(OUT, "*.jpg")):  # previous textile export
        os.remove(f)

    # ---- 1. fit a template per design+side on good train sheets
    jobs, norm_set = [], {}
    for c in cats:
        pool = [r for r in imgs if r["design"] == c and r["split"] == "train" and not r["defective"]]
        order = rng.permutation(len(pool))
        pick = order[:a.fit]
        norm_set[c] = [pool[i] for i in order[a.fit:a.fit + a.norm]]
        chunks = np.array_split(pick, 4)
        jobs += [(c, [path(pool[i]) for i in ch], h) for ch in chunks]
    acc = {}
    with Pool(min(a.workers, len(jobs))) as p:
        for c, sx, sxx, n, fails in p.imap_unordered(fit_category, jobs):
            s0, ss0, n0, f0 = acc.get(c, (0, 0, 0, 0))
            acc[c] = (s0 + sx, ss0 + sxx, n0 + n, f0 + fails)
    mdir = os.path.join(OUT, "model")
    fit_info = {}
    for c in cats:
        sx, sxx, n, fails = acc[c]
        mean = sx / n
        std = np.sqrt(np.maximum(sxx / n - mean ** 2, 0))
        mq = (np.clip(mean, 0, 1) * 255 + 0.5).astype(np.uint8)
        sq = np.clip(std * SSCALE + 0.5, 0, 255).astype(np.uint8)
        Image.fromarray(mq).save(os.path.join(mdir, f"{c}_mean.png"), optimize=True)
        Image.fromarray(sq).save(os.path.join(mdir, f"{c}_std.png"), optimize=True)
        low = box_down(mq.astype(np.float32) / 255.0, LOW)
        Image.fromarray((low * 255 + 0.5).astype(np.uint8)).save(os.path.join(mdir, f"{c}_low.png"))
        fit_info[c] = dict(n=int(n), registrationFailures=int(fails))
        print(f"fit {c}: {n} good sheets, registration failures {fails}")

    # ---- 2. calibration (held-out good val) + export selection (test split only)
    calib = []
    for c in cats:
        pool = [r for r in imgs if r["design"] == c and r["split"] == "val" and not r["defective"]]
        calib += [pool[i] for i in rng.choice(len(pool), size=min(a.calib, len(pool)), replace=False)]
    test = [r for r in imgs if r["split"] == "test"]
    good = [r for r in test if not r["defective"]]
    picks = [good[i] for i in rng.choice(len(good), size=a.good, replace=False)]
    for cls in [k for k in LABELS if k != "good"]:
        pool = [r for r in test if r["defective"] and {d["cls"] for d in r["defects"]} == {cls}]
        picks += [pool[i] for i in rng.choice(len(pool), size=min(a.per_class, len(pool)), replace=False)]
    order = rng.permutation(len(picks))
    picks = [picks[i] for i in order]
    names = {r["stem"]: f"s{n:04d}.jpg" for n, r in enumerate(picks)}

    norm_list = [r for c in cats for r in norm_set[c]]
    jobs = ([(path(r), None) for r in norm_list] + [(path(r), None) for r in calib]
            + [(path(r), names[r["stem"]]) for r in picks])
    with Pool(a.workers, initializer=_init, initargs=(mdir, cats, h)) as p:
        res = p.map(score_job, jobs, chunksize=4)
    nres, cal, out = res[:len(norm_list)], res[len(norm_list):len(norm_list) + len(calib)], res[len(norm_list) + len(calib):]

    # Per-cell normal level: 95th percentile of raw cell scores on good sheets, quantised as shipped.
    cellnorm = {}
    for c in cats:
        stack = np.stack([r["cells"] for r in nres if r["cat"] == c])
        cn = np.clip(np.round(np.percentile(stack, 95, axis=0) * CN_Q), 1, 255).astype(np.uint8)
        cellnorm[c] = cn
        fit_info[c]["normImages"] = len(stack)
    for r in cal + out:
        r["cells"] = r["cells"] / (cellnorm[r["cat"]].astype(np.float32) / CN_Q)

    # Normalise: the 98th percentile of held-out good-sheet maxima maps to 0.36 (default threshold 0.42).
    gmax = np.array([r["cells"].max() for r in cal])
    scale = float(np.percentile(gmax, 98) / 0.36)
    min_px = int(np.percentile([r["count"] for r in cal], 85))  # tight template limit -> ~15% false rejects
    wrong_cat = sum(r["cat"] != c["design"] for r, c in zip(cal + out, calib + picks))

    items = []
    for n, (r, src) in enumerate(zip(out, picks)):
        m = np.clip(r["cells"] / scale, 0, 1)
        q = (m * 255 + 0.5).astype(np.uint8)
        defects = []
        for d in src["defects"]:
            x0, y0, x1, y1 = to_canon_box(r["Hc"], d["bbox_xyxy"])
            x0, x1 = np.clip([x0 / W, x1 / W], 0, 1)
            y0, y1 = np.clip([y0 / h, y1 / h], 0, 1)
            defects.append({"cls": d["cls"], "box": [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]})
        label = src["defects"][0]["cls"] if src["defective"] else "good"
        items.append({
            "id": f"SHT-{n + 1:05d}", "file": names[src["stem"]], "source": src["file"],
            "category": r["cat"], "side": src["side"], "label": label, "labelName": LABELS[label],
            "defects": defects, "score": round(float(q.max()) / 255, 4),
            "map": base64.b64encode(q.tobytes()).decode(), "ruleReject": r["count"] > min_px,
            "registered": bool(r["registered"]),
        })

    gs = np.array([i["score"] for i in items if i["label"] == "good"])
    ds = np.array([i["score"] for i in items if i["label"] != "good"])
    auroc = float((ds[:, None] > gs[None, :]).mean() + 0.5 * (ds[:, None] == gs[None, :]).mean())
    th = 0.42
    print(f"exported {len(items)} sheets; category mismatches {wrong_cat}; AUROC={auroc:.3f}")
    print(f"AI @ {th}: recall={np.mean(ds >= th):.2f} FRR={np.mean(gs >= th):.2f}")
    print(f"rules: recall={np.mean([i['ruleReject'] for i in items if i['label'] != 'good']):.2f} "
          f"FRR={np.mean([i['ruleReject'] for i in items if i['label'] == 'good']):.2f}")
    for cls in [k for k in LABELS if k != "good"]:
        s = [i for i in items if i["label"] == cls]
        print(f"  {cls:16s} AI recall {np.mean([i['score'] >= th for i in s]):.2f}  "
              f"rules {np.mean([i['ruleReject'] for i in s]):.2f}  scores {[i['score'] for i in s]}")

    geo = Geometry(ann["config"]["width"], ann["config"]["cols"], ann["config"]["rows"])
    manifest = {
        "dataset": "synthetic_print banknote press sheets (fictional designs, rectified to the canonical sheet frame)",
        "labels": LABELS,
        "model": {
            "name": "registered golden template (per-pixel Gaussian, PaDiM-style placeholder for PatchCore)",
            "W": W, "H": h, "cell": CELL, "gw": gw, "gh": gh, "floor": FLOOR, "sscale": SSCALE, "low": LOW,
            "regTarget": REG_TARGET, "scale": scale, "cnq": CN_Q,
            "categories": [{"key": c, "mean": f"model/{c}_mean.png", "std": f"model/{c}_std.png",
                            "lowres": f"model/{c}_low.png", "fitImages": fit_info[c]["n"],
                            "normImages": fit_info[c]["normImages"],
                            "cellnorm": base64.b64encode(cellnorm[c].tobytes()).decode()} for c in cats],
        },
        "rules": {"tol": RULE_TOL, "minPx": min_px},
        "layout": {"cols": geo.cols, "rows": geo.rows,
                   "x": [round(x / geo.sw, 5) for x, _ in geo.origins[:geo.cols]],
                   "y": [round(y / geo.sh, 5) for _, y in geo.origins[::geo.cols]],
                   "nw": round(geo.nw / geo.sw, 5), "nh": round(geo.nh / geo.sh, 5)},
        "items": items,
    }
    with open(os.path.join(OUT, "manifest.json"), "w") as f:
        json.dump(manifest, f, separators=(",", ":"))
    print("wrote", os.path.relpath(os.path.join(OUT, "manifest.json")))


if __name__ == "__main__":
    main()

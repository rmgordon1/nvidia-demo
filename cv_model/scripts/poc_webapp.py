"""POC: run the fine-tuned RF-DETR-Seg over the webapp's Defect Inspection sheets and write real detections into
webapp/data/cv/manifest.json (placeholder original kept as manifest.placeholder.json).

Per sheet: 16 full-resolution 640 px tiles -> detector -> merge across tile seams -> map into the webapp's rectified
sheet space with its own registration (webapp/tools/build_cv_samples.py) -> per item:
  det      [{cls, conf, box:[x,y,w,h] (0-1, rectified sheet), note, position, size_mm}]
  score    max detection confidence (0 when none) — drives the existing threshold / queue logic
  map      heatmap grid rebuilt from the predicted masks (cell = max confidence of masks covering it)
  anomalyScore / anomalyMap   the placeholder's original values
Also prints a sheet-level per-class precision/recall table on these (test-split) sheets.

    source cv_model/env.sh && python cv_model/scripts/poc_webapp.py --weights models/detector/poc/checkpoint_best_ema.pth
"""
import argparse, base64, json, os, shutil, sys, time
from collections import defaultdict

import cv2
import numpy as np
import torch
from PIL import Image

ROOT = os.environ["CV_ROOT"]
DATASET = os.environ["DATASET_DIR"]
WEBAPP = os.path.join(ROOT, "..", "webapp")
sys.path.insert(0, os.path.join(WEBAPP, "tools"))
from build_cv_samples import homography, register  # noqa: E402

TILE, GRID = 640, 4
MM_PER_PX = 156.0 / 430.0


def tile_origins(w, h):
    xs = np.linspace(0, w - TILE, GRID).round().astype(int)
    ys = np.linspace(0, h - TILE, GRID).round().astype(int)
    return [(int(x), int(y)) for y in ys for x in xs]


def merge(boxes, confs, cls, masks_full_conf):
    """Class-aware merge across tile seams: same-class boxes that overlap or touch (<= 6 px) become one."""
    order = np.argsort(-confs)
    out = []
    for i in order:
        b = boxes[i].copy()
        for o in out:
            if o["cls"] != cls[i]:
                continue
            ob = o["box"]
            if b[0] <= ob[2] + 6 and ob[0] <= b[2] + 6 and b[1] <= ob[3] + 6 and ob[1] <= b[3] + 6:
                o["box"] = np.array([min(ob[0], b[0]), min(ob[1], b[1]), max(ob[2], b[2]), max(ob[3], b[3])])
                o["conf"] = max(o["conf"], float(confs[i]))
                break
        else:
            out.append(dict(cls=int(cls[i]), conf=float(confs[i]), box=b))
    return out


def where(u, v):
    h = "left" if u < 1 / 3 else "right" if u > 2 / 3 else "centre"
    vv = "top" if v < 1 / 3 else "bottom" if v > 2 / 3 else "middle"
    return "centre" if (h, vv) == ("centre", "middle") else f"{vv}-{h}" if h != "centre" else f"{vv}-centre"


def note_of(layout, cx, cy):
    xs, ys = np.array(layout["x"]), np.array(layout["y"])
    c = int(np.clip(np.searchsorted(xs, cx, side="right") - 1, 0, layout["cols"] - 1))
    r = int(np.clip(np.searchsorted(ys, cy, side="right") - 1, 0, layout["rows"] - 1))
    u = float(np.clip((cx - xs[c]) / layout["nw"], 0, 1))
    v = float(np.clip((cy - ys[r]) / layout["nh"], 0, 1))
    return f"R{r + 1}·C{c + 1}", where(u, v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--size", choices=["small", "medium"], default="small")
    ap.add_argument("--resolution", type=int, default=648)
    ap.add_argument("--threshold", type=float, default=0.25, help="keep detections above this (UI threshold applies on top)")
    ap.add_argument("--flag", type=float, default=0.42, help="confidence used for the printed precision/recall table")
    args = ap.parse_args()

    from rfdetr import RFDETRSegMedium, RFDETRSegSmall
    cls_ = RFDETRSegSmall if args.size == "small" else RFDETRSegMedium
    model = cls_(pretrain_weights=os.path.join(ROOT, args.weights) if not os.path.isabs(args.weights) else args.weights,
                 resolution=args.resolution)
    classes = json.load(open(os.path.join(DATASET, "annotations.json")))["classes"]

    mpath = os.path.join(WEBAPP, "data", "cv", "manifest.json")
    backup = os.path.join(WEBAPP, "data", "cv", "manifest.placeholder.json")
    if not os.path.exists(backup):
        shutil.copy2(mpath, backup)
    man = json.load(open(backup))                    # always start from the placeholder manifest
    m = man["model"]
    W, H, cell, gw, gh = m["W"], m["H"], m["cell"], m["gw"], m["gh"]

    t_all, n_tiles = 0.0, 0
    for k, it in enumerate(man["items"]):
        rgb8 = np.asarray(Image.open(os.path.join(DATASET, it["source"])).convert("RGB"))
        h0, w0 = rgb8.shape[:2]
        corners = register(rgb8.astype(np.float32) / 255.0)
        if corners is None:
            corners = np.array([(0, 0), (w0, 0), (w0, h0), (0, h0)], float)
        Hc = homography(corners, W, H)                # canonical -> raw
        Hinv = np.linalg.inv(Hc)

        origins = tile_origins(w0, h0)
        tiles = [Image.fromarray(rgb8[y:y + TILE, x:x + TILE]) for x, y in origins]
        torch.cuda.synchronize(); t0 = time.time()
        preds = model.predict(tiles, threshold=args.threshold)
        torch.cuda.synchronize(); t_all += time.time() - t0; n_tiles += len(tiles)

        boxes, confs, cls, conf_map = [], [], [], np.zeros((h0, w0), np.float32)
        for (x0, y0), d in zip(origins, preds):
            for j in range(len(d)):
                bx = d.xyxy[j] + [x0, y0, x0, y0]
                boxes.append(bx); confs.append(float(d.confidence[j])); cls.append(int(d.class_id[j]))
                if d.mask is not None:
                    sub = conf_map[y0:y0 + TILE, x0:x0 + TILE]
                    np.maximum(sub, d.mask[j].astype(np.float32) * float(d.confidence[j]), out=sub)
        dets = merge(np.array(boxes).reshape(-1, 4), np.array(confs), np.array(cls, int), conf_map) if boxes else []

        out = []
        for d in dets:
            x0, y0, x1, y1 = d["box"]
            pts = cv2.perspectiveTransform(np.array([[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]], np.float32), Hinv)[0]
            cx0, cy0 = pts.min(0) / [W, H]
            cx1, cy1 = pts.max(0) / [W, H]
            note, pos = note_of(man["layout"], (cx0 + cx1) / 2, (cy0 + cy1) / 2)
            out.append(dict(cls=classes[d["cls"]], conf=round(d["conf"], 3),
                            box=[round(float(v), 4) for v in (cx0, cy0, cx1 - cx0, cy1 - cy0)],
                            note=note, position=pos,
                            size_mm=[round(float(x1 - x0) * MM_PER_PX, 1), round(float(y1 - y0) * MM_PER_PX, 1)]))
        out.sort(key=lambda d: -d["conf"])

        # heatmap grid from masks, in the rectified sheet space
        canon = cv2.warpPerspective(conf_map, Hinv, (W, H), flags=cv2.INTER_LINEAR)
        canon = np.pad(canon, ((0, max(0, gh * cell - H)), (0, max(0, gw * cell - W))))
        grid = canon[:gh * cell, :gw * cell].reshape(gh, cell, gw, cell).max((1, 3))
        it.setdefault("anomalyScore", it["score"]); it.setdefault("anomalyMap", it["map"])
        it["map"] = base64.b64encode(np.clip(grid * 255 + 0.5, 0, 255).astype(np.uint8).tobytes()).decode()
        it["det"] = out
        it["score"] = round(out[0]["conf"], 4) if out else 0.0
        if (k + 1) % 20 == 0:
            print(f"  {k + 1}/{len(man['items'])} sheets", flush=True)

    man["detector"] = dict(name=f"RF-DETR-Seg {args.size.title()} (fine-tuned, POC)", weights=os.path.basename(args.weights),
                           tile=TILE, grid=GRID, ms_per_tile=round(t_all / n_tiles * 1000, 1),
                           ms_per_sheet=round(t_all / len(man["items"]) * 1000, 1))
    json.dump(man, open(mpath, "w"), separators=(",", ":"))
    print("wrote", mpath, json.dumps(man["detector"]))

    # ---- sheet-level scorecard on these sheets (they come from the generator's test split)
    tp, fn, fp = defaultdict(int), defaultdict(int), defaultdict(int)
    good_flag, good_n, bad_hit, bad_n = 0, 0, 0, 0
    for it in man["items"]:
        truth = {d["cls"] for d in it["defects"]}
        pred = {d["cls"] for d in it["det"] if d["conf"] >= args.flag}
        for c in classes:
            tp[c] += (c in truth and c in pred); fn[c] += (c in truth and c not in pred); fp[c] += (c not in truth and c in pred)
        if truth:
            bad_n += 1; bad_hit += bool(pred)
        else:
            good_n += 1; good_flag += bool(pred)
    print(f"\nsheet level @conf {args.flag}: defective caught {bad_hit}/{bad_n}, good wrongly flagged {good_flag}/{good_n}")
    print(f"{'class':16s} recall  precision  (n)")
    for c in classes:
        n = tp[c] + fn[c]
        r = tp[c] / n if n else float('nan'); p = tp[c] / (tp[c] + fp[c]) if tp[c] + fp[c] else float('nan')
        print(f"{c:16s} {r:6.2f}  {p:9.2f}  ({n})")


if __name__ == "__main__":
    main()

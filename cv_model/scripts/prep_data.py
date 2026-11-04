"""Phase 1 data prep: cut sheets into the runtime tile grid and write the three training exports.

  data/tiles_coco/{train,valid,test}/   RF-DETR-Seg  (Roboflow COCO layout, polygon instance masks)
  data/anomaly/<design>/                anomalib Folder layout per design+side (PatchCore)
      train/good/                         tiles of good train sheets (capped per category)
      val/good/ val/defect/ val/mask/     tiles of val sheets, masks share the defect tile's stem
  data/vlm/{train,val,test}.jsonl       VLM explainer: defect crops + labels, plus "no defect" negatives
  data/vlm/crops/
  data/tiles.json                       tile grid + export summary

Tiles are cut from the raw camera frame on a fixed 4 x 4 grid of 640 px tiles (the same grid DeepStream's
nvdspreprocess uses at runtime), at full resolution: defects are 5-30 px, so nothing is downscaled.

    source cv_model/env.sh && python cv_model/scripts/prep_data.py [--good-ratio 1.0] [--anomaly-good 120]
"""
import argparse, json, os, shutil, sys, time
from multiprocessing import Pool

import cv2
import numpy as np

ROOT = os.environ.get("CV_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET = os.environ.get("DATASET_DIR") or os.path.join(ROOT, "..", "synthetic_print", "dataset")
OUT = os.path.join(ROOT, "data")
TILE = 640
GRID = 4                      # 4 x 4 overlapping tiles per frame
MIN_PART_PX = 6               # clipped instance pieces smaller than this are dropped from a tile
MM_PER_PX = 156.0 / 430.0     # nominal: a 430 px note is 156 mm wide (sheet geometry has no physical scale)
CROP = 224                    # VLM crop size (px, full resolution)
JPEG_Q = 97
SPLIT_DIR = {"train": "train", "val": "valid", "test": "test"}

ANN, CFG = None, None


def tile_origins(w, h):
    xs = np.linspace(0, w - TILE, GRID).round().astype(int)
    ys = np.linspace(0, h - TILE, GRID).round().astype(int)
    return [(int(x), int(y)) for y in ys for x in xs]          # row-major: tile k = r * GRID + c


def note_at(Hinv, x, y):
    """(row, col, u, v): note containing image point (x, y); u, v are 0-1 within the note."""
    p = Hinv @ np.array([x, y, 1.0])
    sx, sy = p[0] / p[2], p[1] / p[2]
    g = ANN["geo"]
    c = int(np.clip((sx - g["margin"]) // (g["nw"] + g["gutter"]), 0, g["cols"] - 1))
    r = int(np.clip((sy - g["margin"]) // (g["nh"] + g["gutter"]), 0, g["rows"] - 1))
    ox, oy = g["margin"] + c * (g["nw"] + g["gutter"]), g["margin"] + r * (g["nh"] + g["gutter"])
    return r, c, float(np.clip((sx - ox) / g["nw"], 0, 1)), float(np.clip((sy - oy) / g["nh"], 0, 1))


def where(u, v):
    h = "left" if u < 1 / 3 else "right" if u > 2 / 3 else "centre"
    vv = "top" if v < 1 / 3 else "bottom" if v > 2 / 3 else "middle"
    return "centre" if (h, vv) == ("centre", "middle") else f"{vv}-{h}" if h != "centre" else f"{vv}-centre"


def polygons(m):
    """COCO polygons of a binary mask; thin (1 px) features are dilated by 1 px so every piece is a valid polygon."""
    cs, _ = cv2.findContours(cv2.dilate(m, np.ones((3, 3), np.uint8)), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [c.reshape(-1).astype(float).tolist() for c in cs if len(c) >= 3]


def process(rec):
    """One sheet -> list of tile records and VLM records (images written here, JSON assembled by the parent)."""
    stem, split = rec["stem"], rec["split"]
    rng = np.random.default_rng([CFG["seed"], int(stem.split("_")[1])])
    img = cv2.imread(os.path.join(DATASET, rec["file"]), cv2.IMREAD_COLOR)
    H, W = img.shape[:2]
    inst = None
    if rec["defective"]:
        inst = cv2.imread(os.path.join(DATASET, "instance_masks", stem + ".png"), cv2.IMREAD_UNCHANGED)
    Hinv = np.linalg.inv(np.array(rec["sheet_homography"]))
    out_tiles, out_vlm = [], []

    # ---- tiles
    anomaly_train = rec["anomaly_train"]
    for k, (x0, y0) in enumerate(tile_origins(W, H)):
        tile = img[y0:y0 + TILE, x0:x0 + TILE]
        name = f"{stem}_t{k:02d}"
        anns = []
        if inst is not None:
            sub = inst[y0:y0 + TILE, x0:x0 + TILE]
            for i, d in enumerate(rec["defects"]):
                m = (sub == i + 1).astype(np.uint8)
                if int(m.sum()) < MIN_PART_PX:
                    continue
                ys, xs = np.nonzero(m)
                polys = polygons(m)
                if not polys:
                    continue
                bx0, by0, bx1, by1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
                anns.append(dict(class_id=d["class_id"], bbox=[bx0, by0, bx1 - bx0, by1 - by0],
                                 area=int(m.sum()), segmentation=polys))
        defect_tile = bool(anns)
        # detector export: every defect tile + a random share of clean tiles
        keep_det = defect_tile or rng.random() < CFG["good_keep"]
        if keep_det:
            p = os.path.join(OUT, "tiles_coco", SPLIT_DIR[split], name + ".jpg")
            cv2.imwrite(p, tile, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        # anomaly export (per design + side)
        acat = os.path.join(OUT, "anomaly", rec["design"])
        if split == "train" and anomaly_train and not rec["defective"]:
            cv2.imwrite(os.path.join(acat, "train", "good", name + ".jpg"), tile, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        elif split == "val":
            if defect_tile:
                cv2.imwrite(os.path.join(acat, "val", "defect", name + ".jpg"), tile, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
                cv2.imwrite(os.path.join(acat, "val", "mask", name + ".png"),
                            ((inst[y0:y0 + TILE, x0:x0 + TILE] > 0) * 255).astype(np.uint8))
            else:
                cv2.imwrite(os.path.join(acat, "val", "good", name + ".jpg"), tile, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        out_tiles.append(dict(name=name, split=split, sheet=stem, tile=k, x0=x0, y0=y0, detector=keep_det,
                              anns=anns if keep_det else []))

    # ---- VLM crops: one per defect instance, plus negatives
    def crop_at(cx, cy):
        x0 = int(np.clip(cx - CROP // 2, 0, W - CROP))
        y0 = int(np.clip(cy - CROP // 2, 0, H - CROP))
        return x0, y0, img[y0:y0 + CROP, x0:x0 + CROP]

    for i, d in enumerate(rec["defects"]):
        bx0, by0, bx1, by1 = d["bbox_xyxy"]
        ys, xs = np.nonzero(inst == i + 1)
        if len(xs) == 0:
            continue
        # centre the crop on a defect pixel near the bbox centre (long streaks/bands: their middle)
        j = np.argmin((xs - (bx0 + bx1) / 2) ** 2 + (ys - (by0 + by1) / 2) ** 2)
        cx, cy = int(xs[j]), int(ys[j])
        x0, y0, crop = crop_at(cx, cy)
        name = f"{stem}_d{i}"
        cv2.imwrite(os.path.join(OUT, "vlm", "crops", name + ".jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
        r, c, u, v = note_at(Hinv, cx, cy)
        box_in_crop = [int(np.clip(bx0 - x0, 0, CROP)), int(np.clip(by0 - y0, 0, CROP)),
                       int(np.clip(bx1 - x0, 0, CROP)), int(np.clip(by1 - y0, 0, CROP))]
        out_vlm.append(dict(id=name, image=f"crops/{name}.jpg", split=split, sheet=stem, design=rec["design"],
                            side=rec["side"], defect=True, cls=d["cls"], class_id=d["class_id"],
                            note=f"R{r + 1}·C{c + 1}", position=where(u, v),
                            size_mm=[round((bx1 - bx0) * MM_PER_PX, 1), round((by1 - by0) * MM_PER_PX, 1)],
                            area_px=d["area_px"], box_in_crop=box_in_crop, params=d["params"]))
    # negatives: clean sheet -> 1 random crop; defective sheet -> 1 crop away from every defect
    for t in range(20):
        cx, cy = rng.integers(CROP // 2, W - CROP // 2), rng.integers(CROP // 2, H - CROP // 2)
        x0, y0, crop = crop_at(cx, cy)
        if inst is not None and inst[y0:y0 + CROP, x0:x0 + CROP].any():
            continue
        r, c, u, v = note_at(Hinv, cx, cy)
        p = Hinv @ np.array([cx, cy, 1.0])
        sx, sy = p[0] / p[2], p[1] / p[2]
        g = ANN["geo"]
        if not (0 <= sx < g["sw"] and 0 <= sy < g["sh"]):
            continue                                            # crop centre on the conveyor, not the sheet
        name = f"{stem}_n0"
        cv2.imwrite(os.path.join(OUT, "vlm", "crops", name + ".jpg"), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])
        out_vlm.append(dict(id=name, image=f"crops/{name}.jpg", split=split, sheet=stem, design=rec["design"],
                            side=rec["side"], defect=False, note=f"R{r + 1}·C{c + 1}", position=where(u, v)))
        break
    return out_tiles, out_vlm


def _init(ann, cfg):
    global ANN, CFG
    ANN, CFG = ann, cfg


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--good-ratio", type=float, default=1.0, help="clean : defect tile ratio in the detector set")
    ap.add_argument("--anomaly-good", type=int, default=120, help="good train sheets per anomaly category")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    ann = json.load(open(os.path.join(DATASET, "annotations.json")))
    recs = ann["images"]
    g = ann["config"]
    # rebuild the generator's sheet geometry (synthetic_print/generate.py Geometry)
    sys.path.insert(0, os.path.join(ROOT, "..", "synthetic_print"))
    from generate import Geometry
    geo = Geometry(g["width"], g["cols"], g["rows"])
    geo_d = dict(sw=geo.sw, sh=geo.sh, margin=geo.margin, gutter=geo.gutter, nw=geo.nw, nh=geo.nh,
                 cols=geo.cols, rows=geo.rows)
    assert [geo.sw, geo.sh] == ann["sheet_px"], "geometry mismatch with annotations.json"

    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    for s in SPLIT_DIR.values():
        os.makedirs(os.path.join(OUT, "tiles_coco", s))
    os.makedirs(os.path.join(OUT, "vlm", "crops"))
    designs = sorted({r["design"] for r in recs})
    for d in designs:
        for sub in ("train/good", "val/good", "val/defect", "val/mask"):
            os.makedirs(os.path.join(OUT, "anomaly", d, sub))

    # anomaly train subset: first N good train sheets per category (memory-bank size is the limit, not data)
    taken = {d: 0 for d in designs}
    for r in recs:
        r["anomaly_train"] = False
        if r["split"] == "train" and not r["defective"] and taken[r["design"]] < args.anomaly_good:
            r["anomaly_train"] = True
            taken[r["design"]] += 1

    # clean-tile keep probability so clean : defect tiles ~= good_ratio (defect tiles ~ 2.2 per defective sheet)
    n_def = sum(r["defective"] for r in recs)
    est_defect_tiles = n_def * 2.2
    est_clean_tiles = len(recs) * GRID * GRID - est_defect_tiles
    good_keep = min(1.0, args.good_ratio * est_defect_tiles / est_clean_tiles)
    cfg = dict(seed=args.seed, good_keep=good_keep)

    t0 = time.time()
    tiles, vlm = [], []
    with Pool(args.workers, initializer=_init, initargs=({"geo": geo_d}, cfg)) as pool:
        for k, (t, v) in enumerate(pool.imap(process, recs, chunksize=4), 1):
            tiles += t
            vlm += v
            if k % 500 == 0 or k == len(recs):
                print(f"  {k}/{len(recs)} sheets ({time.time() - t0:.0f}s)", flush=True)

    # ---- COCO files per split (category ids 0..7, supercategory "defect")
    cats = [dict(id=i, name=c, supercategory="defect") for i, c in enumerate(ann["classes"])]
    summary = dict(tile=TILE, grid=GRID, good_keep=round(good_keep, 4), detector={}, anomaly={}, vlm={})
    for split, sdir in SPLIT_DIR.items():
        images, anns = [], []
        for t in tiles:
            if t["split"] != split or not t["detector"]:
                continue
            iid = len(images)
            images.append(dict(id=iid, file_name=t["name"] + ".jpg", width=TILE, height=TILE,
                               sheet=t["sheet"], tile=t["tile"], x0=t["x0"], y0=t["y0"]))
            for a in t["anns"]:
                anns.append(dict(id=len(anns), image_id=iid, category_id=a["class_id"], bbox=a["bbox"],
                                 area=a["area"], segmentation=a["segmentation"], iscrowd=0))
        with open(os.path.join(OUT, "tiles_coco", sdir, "_annotations.coco.json"), "w") as f:
            json.dump(dict(images=images, annotations=anns, categories=cats), f)
        per_cls = {c: 0 for c in ann["classes"]}
        for a in anns:
            per_cls[ann["classes"][a["category_id"]]] += 1
        summary["detector"][sdir] = dict(tiles=len(images), defect_tiles=len({a["image_id"] for a in anns}),
                                         instances=len(anns), per_class=per_cls)
    for d in designs:
        summary["anomaly"][d] = {sub: len(os.listdir(os.path.join(OUT, "anomaly", d, sub)))
                                 for sub in ("train/good", "val/good", "val/defect")}
    for split in SPLIT_DIR:
        rows = [v for v in vlm if v["split"] == split]
        with open(os.path.join(OUT, "vlm", f"{split}.jsonl"), "w") as f:
            f.writelines(json.dumps(v, ensure_ascii=False) + "\n" for v in rows)
        summary["vlm"][split] = dict(defect=sum(v["defect"] for v in rows), negative=sum(not v["defect"] for v in rows))
    summary["mm_per_px"] = MM_PER_PX
    summary["tile_origins"] = tile_origins(ann["images"][0]["width"], ann["images"][0]["height"])
    with open(os.path.join(OUT, "tiles.json"), "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))
    print(f"done in {time.time() - t0:.0f}s -> {OUT}")


if __name__ == "__main__":
    main()

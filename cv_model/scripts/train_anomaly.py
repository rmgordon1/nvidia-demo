"""Phase 2: PatchCore per design + side on full-resolution 640 px tiles, evaluated on the val tiles.

Uses anomalib's PatchCore torch model directly (no Engine), so resolution, memory and metrics are explicit:
  * train: `--train-sheets` good train sheets x all 16 tile positions (memory bank = 1% coreset of their patches)
  * eval : every val tile of that design + side -> tile AUROC, pixel AUROC, AUPRO@0.3, threshold (max F1),
           FRR, per-class recall, sheet-level FRR / recall (a sheet fails when any tile is over threshold)
Outputs: models/anomaly/<design>/patchcore.pt (+ metrics.json), models/anomaly/summary.json, MLflow run.

    source cv_model/env.sh && python cv_model/scripts/train_anomaly.py [--designs banknote_00_front ...]
"""
import argparse, json, os, re, sys, time
from collections import defaultdict

import cv2
import mlflow
import numpy as np
import torch
from anomalib.metrics.aupro import _AUPRO
from anomalib.models.image.patchcore.torch_model import PatchcoreModel
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.join(os.environ["CV_ROOT"], "..", "webapp", "tools"))
from build_cv_samples import register  # noqa: E402  sub-pixel sheet corners, also used at runtime

ROOT = os.environ["CV_ROOT"]
DATASET = os.environ["DATASET_DIR"]
DATA = os.path.join(ROOT, "data", "anomaly")
OUT = os.path.join(ROOT, "models", "anomaly")
MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
CLASSES = json.load(open(os.path.join(DATASET, "annotations.json")))["classes"]
NAME_RE = re.compile(r"(sheet_\d+)_t(\d+)")


def load(paths, dev):
    x = np.stack([cv2.imread(p, cv2.IMREAD_COLOR)[..., ::-1] for p in paths]).astype(np.float32) / 255.0
    x = torch.from_numpy(x).permute(0, 3, 1, 2)
    return ((x - MEAN) / STD).to(dev)


SHEET_ERODE = int(os.environ.get("SHEET_ERODE", 12))   # px; sheet edge band excluded from scoring (map is 8 px cells, smoothed)


def sheet_mask(path):
    """Full-res bool mask of the paper (registration corners); conveyor background is never scored."""
    rgb = cv2.imread(path, cv2.IMREAD_COLOR)[..., ::-1].astype(np.float32) / 255.0
    c = register(rgb)
    m = np.zeros(rgb.shape[:2], np.uint8)
    if c is None:
        m[:] = 1
        return m.astype(bool)
    cv2.fillPoly(m, [np.round(c).astype(np.int32)], 1)
    return cv2.erode(m, np.ones((2 * SHEET_ERODE + 1,) * 2, np.uint8)).astype(bool)


def tile_classes(ann_by_stem, tiles_meta):
    """Classes whose instance mask touches each val defect tile."""
    origins = tiles_meta["tile_origins"]
    T = tiles_meta["tile"]
    cache, out = {}, {}

    def classes_of(name):
        stem, k = NAME_RE.match(name).groups()
        rec = ann_by_stem[stem]
        if stem not in cache:
            cache[stem] = cv2.imread(os.path.join(DATASET, "instance_masks", stem + ".png"), cv2.IMREAD_UNCHANGED)
        x0, y0 = origins[int(k)]
        ids = np.unique(cache[stem][y0:y0 + T, x0:x0 + T])
        return sorted({rec["defects"][i - 1]["cls"] for i in ids if i > 0})

    return classes_of


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--designs", nargs="*", help="default: all design+side categories")
    ap.add_argument("--backbone", default="wide_resnet50_2")
    ap.add_argument("--layers", nargs="+", default=["layer2", "layer3"])
    ap.add_argument("--train-sheets", type=int, default=15, help="good train sheets per category (x16 tiles)")
    ap.add_argument("--coreset", type=float, default=0.01, help="fraction of the (capped) patches kept")
    ap.add_argument("--max-patches", type=int, default=1_536_000,
                    help="random patch sample fed to coreset selection (bounds memory and time; 0 = all)")
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--max-val-good", type=int, default=0, help="cap good val tiles (0 = all)")
    ap.add_argument("--eval-only", action="store_true", help="reuse models/anomaly/<design>/patchcore.pt")
    ap.add_argument("--no-sheet-mask", action="store_true", help="score the whole tile, background included")
    args = ap.parse_args()

    dev = torch.device("cuda")
    ann = json.load(open(os.path.join(DATASET, "annotations.json")))
    ann_by_stem = {r["stem"]: r for r in ann["images"]}
    tiles_meta = json.load(open(os.path.join(ROOT, "data", "tiles.json")))
    classes_of = tile_classes(ann_by_stem, tiles_meta)
    designs = args.designs or sorted(os.listdir(DATA))
    os.makedirs(OUT, exist_ok=True)

    mlflow.set_experiment("anomaly-patchcore")
    summary = {}
    with mlflow.start_run(run_name=f"patchcore-{args.backbone}-{args.train_sheets}sh"):
        mlflow.log_params({k: str(v) for k, v in vars(args).items()})
        for design in designs:
            t0 = time.time()
            d = os.path.join(DATA, design)
            # ---- train: whole sheets (all 16 positions), evenly spread over the available good sheets
            names = sorted(os.listdir(os.path.join(d, "train", "good")))
            sheets = sorted({NAME_RE.match(n).group(1) for n in names})
            pick = set(sheets[i] for i in np.linspace(0, len(sheets) - 1, args.train_sheets).round().astype(int))
            train = [os.path.join(d, "train", "good", n) for n in names if NAME_RE.match(n).group(1) in pick]

            model = PatchcoreModel(layers=args.layers, backbone=args.backbone, pre_trained=True, num_neighbors=9).to(dev)
            ckpt = os.path.join(OUT, design, "patchcore.pt")
            if args.eval_only:
                sd = torch.load(ckpt, map_location=dev)["state_dict"]
                model.memory_bank = sd["memory_bank"]
                model.load_state_dict(sd)
                n_patches = -1
            else:
                model.train()
                total = len(train) * (640 // 8) ** 2                       # layer2 stride 8
                keep = 1.0 if not args.max_patches else min(1.0, args.max_patches / total)
                g = torch.Generator(device=dev).manual_seed(0)
                for i in range(0, len(train), args.batch):
                    model(load(train[i:i + args.batch], dev))
                    if keep < 1.0:                                         # random sample of this batch's patches
                        e = model.embedding_store[-1]
                        model.embedding_store[-1] = e[torch.rand(e.shape[0], device=dev, generator=g) < keep]
                n_patches = sum(e.shape[0] for e in model.embedding_store)
                print(f"[{design}] {len(train)} tiles -> {n_patches} patches; coreset {args.coreset} "
                      f"({time.time() - t0:.0f}s)", flush=True)
                model.subsample_embedding(args.coreset)
                torch.cuda.empty_cache()
            t_train = time.time() - t0
            print(f"[{design}] memory bank {model.memory_bank.shape[0]} ({t_train:.0f}s); evaluating", flush=True)

            # ---- eval on val tiles
            model.eval()
            good = sorted(os.listdir(os.path.join(d, "val", "good")))
            if args.max_val_good:
                good = good[:: max(1, len(good) // args.max_val_good)]
            bad = sorted(os.listdir(os.path.join(d, "val", "defect")))
            items = [(os.path.join(d, "val", "good", n), 0) for n in good] + \
                    [(os.path.join(d, "val", "defect", n), 1) for n in bad]
            scores, labels, names_eval = [], [], []
            pix_s, pix_t = [], []
            aupro = _AUPRO(fpr_limit=0.3)
            masks = {}
            # sheet-major order so each sheet's registration mask is computed once
            items.sort(key=lambda it: os.path.basename(it[0]))
            rng = np.random.default_rng(0)
            t1 = time.time()
            for i in range(0, len(items), args.batch):
                chunk = items[i:i + args.batch]
                with torch.no_grad():
                    out = model(load([p for p, _ in chunk], dev))
                amap = out.anomaly_map[:, 0].float().cpu()                 # (B, 640, 640)
                if args.no_sheet_mask:
                    scores += out.pred_score.float().cpu().tolist()
                else:
                    for (p, _), a in zip(chunk, amap):
                        stem, k = NAME_RE.match(os.path.basename(p)).groups()
                        if stem not in masks:
                            masks.clear()
                            masks[stem] = sheet_mask(os.path.join(DATASET, ann_by_stem[stem]["file"]))
                        x0, y0 = tiles_meta["tile_origins"][int(k)]
                        sm = torch.from_numpy(masks[stem][y0:y0 + 640, x0:x0 + 640])
                        a *= sm
                        scores.append(float(a.max()))
                labels += [y for _, y in chunk]
                names_eval += [os.path.basename(p)[:-4] for p, _ in chunk]
                for (p, y), a in zip(chunk, amap):
                    if y:
                        m = cv2.imread(os.path.join(d, "val", "mask", os.path.basename(p)[:-4] + ".png"), 0) > 0
                    else:
                        m = np.zeros(a.shape, bool)
                    flat_a, flat_m = a.numpy().ravel(), m.ravel()
                    pos = np.nonzero(flat_m)[0]
                    neg = rng.choice(len(flat_m), 2000, replace=False)
                    idx = np.concatenate([pos[rng.permutation(len(pos))[:2000]], neg[~flat_m[neg]]])
                    pix_s.append(flat_a[idx]); pix_t.append(flat_m[idx])
                    # AUPRO on 4x-downsampled maps (connected components are the cost)
                    aupro.update(torch.nn.functional.avg_pool2d(a[None, None], 4)[0],
                                 torch.from_numpy(cv2.resize(m.astype(np.uint8), (160, 160),
                                                             interpolation=cv2.INTER_NEAREST))[None].bool())
            t_eval = (time.time() - t1) / len(items)
            s, y = np.array(scores), np.array(labels)

            # threshold: max F1 on tiles
            order = np.sort(np.unique(s))
            cand = order[np.linspace(0, len(order) - 1, min(len(order), 2000)).astype(int)]
            f1s = [(2 * ((s >= t) & (y == 1)).sum()) / max(1, (s >= t).sum() + (y == 1).sum()) for t in cand]
            thr = float(cand[int(np.argmax(f1s))])
            pred = s >= thr
            frr = float(pred[y == 0].mean())
            recall = float(pred[y == 1].mean())
            thr_1 = float(np.quantile(s[y == 0], 0.99))                   # operating point at 1% tile FRR
            recall_at_1 = float((s[y == 1] >= thr_1).mean())

            per_cls = defaultdict(lambda: [0, 0])
            for n, yy, pp in zip(names_eval, y, pred):
                if yy:
                    for c in classes_of(n):
                        per_cls[c][0] += int(pp); per_cls[c][1] += 1
            pos_frr = defaultdict(list)
            for n, yy, pp in zip(names_eval, y, pred):
                if not yy:
                    pos_frr[int(NAME_RE.match(n).group(2))].append(pp)
            # sheet level: fails when any tile is over threshold
            sheet = defaultdict(lambda: [0, 0.0])
            for n, sc in zip(names_eval, s):
                st = NAME_RE.match(n).group(1)
                sheet[st][1] = max(sheet[st][1], sc)
            sh_def = np.array([ann_by_stem[st]["defective"] for st in sheet])
            sh_pred = np.array([v[1] >= thr for v in sheet.values()])

            m = dict(design=design, train_tiles=len(train), patches=n_patches,
                     memory_bank=int(model.memory_bank.shape[0]), train_s=round(t_train, 1),
                     ms_per_tile=round(t_eval * 1000, 1), val_good_tiles=int((y == 0).sum()),
                     val_defect_tiles=int((y == 1).sum()),
                     tile_auroc=round(float(roc_auc_score(y, s)), 4),
                     pixel_auroc=round(float(roc_auc_score(np.concatenate(pix_t), np.concatenate(pix_s))), 4),
                     aupro=round(float(aupro.compute()), 4), threshold=round(thr, 4),
                     tile_recall=round(recall, 4), tile_frr=round(frr, 4),
                     tile_recall_at_frr1=round(recall_at_1, 4),
                     sheet_recall=round(float(sh_pred[sh_def].mean()), 4),
                     sheet_frr=round(float(sh_pred[~sh_def].mean()), 4),
                     per_class_recall={c: round(a / b, 3) for c, (a, b) in sorted(per_cls.items())},
                     frr_by_tile={k: round(float(np.mean(v)), 3) for k, v in sorted(pos_frr.items())},
                     per_class_n={c: b for c, (a, b) in sorted(per_cls.items())})
            summary[design] = m
            od = os.path.join(OUT, design)
            os.makedirs(od, exist_ok=True)

            if args.eval_only:
                ck = torch.load(ckpt, map_location="cpu")
                ck.update(threshold=thr, threshold_frr1=thr_1, sheet_mask=not args.no_sheet_mask)
                torch.save(ck, ckpt)
            else:
                torch.save(dict(sheet_mask=not args.no_sheet_mask, state_dict=model.state_dict(), backbone=args.backbone, layers=args.layers,
                                threshold=thr, threshold_frr1=thr_1, tile=640), ckpt)
            json.dump(m, open(os.path.join(od, "metrics.json"), "w"), indent=1)
            mlflow.log_metrics({f"{design}.{k}": v for k, v in m.items() if isinstance(v, (int, float))})
            print(json.dumps({k: v for k, v in m.items() if k != "per_class_n"}), flush=True)
            del model
            torch.cuda.empty_cache()

        # ---- across categories
        keys = ["tile_auroc", "pixel_auroc", "aupro", "tile_recall", "tile_frr", "tile_recall_at_frr1",
                "sheet_recall", "sheet_frr", "ms_per_tile"]
        mean = {k: round(float(np.mean([m[k] for m in summary.values()])), 4) for k in keys}
        tot = defaultdict(lambda: [0.0, 0])
        for m in summary.values():
            for c, r in m["per_class_recall"].items():
                tot[c][0] += r * m["per_class_n"][c]; tot[c][1] += m["per_class_n"][c]
        mean["per_class_recall"] = {c: round(a / b, 3) for c, (a, b) in sorted(tot.items())}
        mlflow.log_metrics({f"mean.{k}": v for k, v in mean.items() if isinstance(v, float)})
        all_sum = dict(mean=mean, categories=summary, args=vars(args))
        json.dump(all_sum, open(os.path.join(OUT, "summary.json"), "w"), indent=1)
        mlflow.log_artifact(os.path.join(OUT, "summary.json"))
        print("MEAN", json.dumps(mean))


if __name__ == "__main__":
    main()

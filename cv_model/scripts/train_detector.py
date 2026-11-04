"""Phase 3: fine-tune RF-DETR-Seg (COCO weights) on the 640 px defect tiles (data/tiles_coco).

Tiles are trained at native resolution (648 = nearest multiple of patch 12 x 2 windows; no multi-scale or scale
jitter, which would shrink 5 px defects). Per-class COCO box/mask AP on valid, then on test (run_test).
Outputs: models/detector/<size>/ (checkpoint_best_*.pth, logs), MLflow experiment "detector-rfdetr-seg".

    source cv_model/env.sh && python cv_model/scripts/train_detector.py [--size small|medium] [--epochs 20]
"""
import argparse, json, os

ROOT = os.environ["CV_ROOT"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--size", choices=["small", "medium"], default="small")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--accum", type=int, default=2)
    ap.add_argument("--resolution", type=int, default=648)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    from rfdetr import RFDETRSegMedium, RFDETRSegSmall
    cls, weights = {"small": (RFDETRSegSmall, "rf-detr-seg-s-ft.pth"),
                    "medium": (RFDETRSegMedium, "rf-detr-seg-m-ft.pth")}[args.size]
    out = os.environ.get("DET_OUT", os.path.join(ROOT, "models", "detector", args.size))
    os.makedirs(out, exist_ok=True)
    classes = json.load(open(os.path.join(os.environ["DATASET_DIR"], "annotations.json")))["classes"]

    model = cls(pretrain_weights=os.path.join(ROOT, "weights", weights))
    model.train(
        dataset_dir=os.environ.get("DET_DATA", os.path.join(ROOT, "data", "tiles_coco")),
        output_dir=out,
        resolution=args.resolution,
        epochs=args.epochs,
        batch_size=args.batch,
        grad_accum_steps=args.accum,
        lr=args.lr,
        num_workers=args.workers,
        multi_scale="off",
        scale_jitter=False,
        class_names=classes,
        log_per_class_metrics=True,
        early_stopping=True,
        early_stopping_patience=5,
        checkpoint_interval=1,
        run_test=True,
        tensorboard=False,
        mlflow=True,
        project="detector-rfdetr-seg",
        run=f"rfdetr-seg-{args.size}-{args.resolution}",
        progress_bar="tqdm",
        seed=0,
    )


if __name__ == "__main__":
    main()

"""Load the synthetic sheets into FiftyOne (dataset "bcp-sheets") for review: boxes + instance masks per defect.

    source cv_model/env.sh && python cv_model/scripts/load_fiftyone.py           # build / rebuild
    source cv_model/env.sh && python cv_model/scripts/load_fiftyone.py --app     # then open http://localhost:5151
Predictions from later phases are added to the same dataset as extra fields (e.g. "rfdetr", "patchcore").
"""
import argparse, json, os

import cv2
import fiftyone as fo
import numpy as np

DATASET = os.environ["DATASET_DIR"]
NAME = "bcp-sheets"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", action="store_true", help="launch the FiftyOne app after loading")
    ap.add_argument("--port", type=int, default=5151)
    args = ap.parse_args()

    if NAME in fo.list_datasets():
        fo.delete_dataset(NAME)
    ds = fo.Dataset(NAME, persistent=True)
    ann = json.load(open(os.path.join(DATASET, "annotations.json")))
    samples = []
    for r in ann["images"]:
        W, H = r["width"], r["height"]
        dets = []
        inst = None
        if r["defects"]:
            inst = cv2.imread(os.path.join(DATASET, "instance_masks", r["stem"] + ".png"), cv2.IMREAD_UNCHANGED)
        for i, d in enumerate(r["defects"]):
            x0, y0, x1, y1 = d["bbox_xyxy"]
            mask = (inst[y0:y1, x0:x1] == i + 1) if inst is not None else None
            dets.append(fo.Detection(label=d["cls"], bounding_box=[x0 / W, y0 / H, (x1 - x0) / W, (y1 - y0) / H],
                                     mask=mask, area_px=d["area_px"],
                                     variant=str(d["params"].get("variant", ""))))
        samples.append(fo.Sample(filepath=os.path.abspath(os.path.join(DATASET, r["file"])), tags=[r["split"]],
                                 design=r["design"], side=r["side"], defective=r["defective"],
                                 ground_truth=fo.Detections(detections=dets)))
    ds.add_samples(samples)
    ds.compute_metadata()
    print(ds)
    if args.app:
        session = fo.launch_app(ds, port=args.port, address="0.0.0.0", remote=True)
        session.wait()


if __name__ == "__main__":
    main()

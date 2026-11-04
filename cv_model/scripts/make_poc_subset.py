"""Class-balanced subset of data/tiles_coco for a quick POC detector run -> data/tiles_coco_poc."""
import json, os, random, shutil
from collections import defaultdict

random.seed(0)
ROOT = os.environ["CV_ROOT"]
src, dst = os.path.join(ROOT, "data", "tiles_coco"), os.path.join(ROOT, "data", "tiles_coco_poc")
shutil.rmtree(dst, ignore_errors=True)
plan = {"train": (2400, 600), "valid": (320, 80), "test": (320, 80)}   # (defect tiles, clean tiles)
for sp, (nd, ng) in plan.items():
    d = json.load(open(f"{src}/{sp}/_annotations.coco.json"))
    by = defaultdict(list)
    for a in d["annotations"]:
        by[a["image_id"]].append(a)
    per = defaultdict(list)                     # round-robin over classes so rare classes are not crowded out
    for iid, anns in by.items():
        per[min(a["category_id"] for a in anns)].append(iid)
    for v in per.values():
        random.shuffle(v)
    pick, k = [], 0
    while len(pick) < nd and any(per.values()):
        c = sorted(per)[k % len(per)]; k += 1
        if per[c]:
            pick.append(per[c].pop())
    clean = [i["id"] for i in d["images"] if i["id"] not in by]
    random.shuffle(clean)
    ids = set(pick + clean[:ng])
    os.makedirs(f"{dst}/{sp}")
    imgs = [i for i in d["images"] if i["id"] in ids]
    for i in imgs:
        os.link(f"{src}/{sp}/{i['file_name']}", f"{dst}/{sp}/{i['file_name']}")
    json.dump(dict(images=imgs, annotations=[a for a in d["annotations"] if a["image_id"] in ids],
                   categories=d["categories"]), open(f"{dst}/{sp}/_annotations.coco.json", "w"))
    print(sp, len(imgs), "tiles")

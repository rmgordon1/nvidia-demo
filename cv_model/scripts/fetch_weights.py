"""Phase 0: download pretrained weights once (run with CV_ONLINE=1 source env.sh)."""
import hashlib, json, os, pathlib, urllib.request

ROOT = pathlib.Path(os.environ["CV_ROOT"])
W = ROOT / "weights"
W.mkdir(exist_ok=True)
lock = {}

# RF-DETR-Seg COCO checkpoints (Small / Medium; plan starts here)
for name in ["rf-detr-seg-s-ft.pth", "rf-detr-seg-m-ft.pth"]:
    dst = W / name
    if not dst.exists():
        print("downloading", name)
        urllib.request.urlretrieve(f"https://storage.googleapis.com/rfdetr/{name}", dst)
    lock[name] = hashlib.sha256(dst.read_bytes()).hexdigest()

# PatchCore / EfficientAD backbones via timm (land in HF_HOME)
import timm
for arch in ["wide_resnet50_2", "resnet18"]:
    m = timm.create_model(arch, pretrained=True, features_only=True)
    cfg = m.pretrained_cfg
    lock[f"timm/{arch}"] = cfg.get("hf_hub_id") or cfg.get("url")
    print("timm ok", arch, lock[f"timm/{arch}"])

(ROOT / "weights" / "weights.lock.json").write_text(json.dumps(lock, indent=2))
print(json.dumps(lock, indent=2))

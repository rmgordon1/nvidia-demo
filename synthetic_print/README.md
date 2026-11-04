# synthetic_print

Procedural generator for **synthetic press-sheet inspection images** with labeled print defects, for the
CV defect-detection use case. Fully offline (numpy / OpenCV / Pillow), deterministic per `--seed`.

Sheets are **fictional engraved banknotes** by default, half showing the **front** and half the **back** of the
notes. Generic press QA targets (CMYK colour bars, halftone ramps, Siemens star) are available with
`--style testsheet` or mixed in with `--style both`.

Banknotes follow classic engraved-note conventions for invented issuers ("Bank of Estovia", "Republic of
Valdoria", ...): split-fountain lathe underprint, line-engraved procedural portrait (not a real person) in an
oval frame, guilloche border, corner counters, issuer and denomination legends, two signatures, coloured seal and
serial numbers, colour-shifting numeral, watermark window, windowed security thread, microtext, and a small
"SPECIMEN" mark. Backs share the issuer, denomination and palette, with a line-engraved building vignette, large
numeral, four corner counters, no serials, and the watermark window, thread and colour fountain mirrored as on a
flipped sheet. They are deliberately **not** modelled on any real currency (in particular not US notes: no US
layout, portraits, seals, wording or green-on-green palette) — similitudes of real currency are restricted by law
and excluded by the use-case submission. Real sheet imagery belongs on-prem, for fine-tuning in production.

```bash
python3 generate.py                                  # 300 sheets, 30% defective -> ./dataset
python3 generate.py --count 3000 --overwrite         # bigger set
python3 generate.py --cols 5 --rows 10 --width 4096 --capture strong --seed 7 --out dataset_hr
python3 generate.py -h                               # all options
```

300 sheets take ~40 s on the GB10 (20 workers), ~390 MB.

## Output

```
dataset/
  images/{train,val,test}/sheet_NNNNNN.jpg   camera-style captures (default 2048 x 1946)
  labels/{train,val,test}/sheet_NNNNNN.txt   YOLO boxes (empty file = good sheet)
  data.yaml                                  YOLO dataset config
  masks/sheet_NNNNNN.png                     pixel mask (union of defects), defective sheets only
  instance_masks/sheet_NNNNNN.png            per-instance mask: pixel = 1 + index into the sheet's `defects` list
  mvtec/<design>/train/good/                 good train-split sheets   (hard links to images/)
                 test/good/                  good val+test sheets
                 test/<defect>/              defective sheets; "combined" when >1 defect class
                 ground_truth/<defect>/*_mask.png
  annotations.json                           per sheet: design, split, boxes, area, defect parameters, homography
  preview/                                   annotated defective sheets for eyeballing
```

Each `--designs` entry (default 4 banknotes; front and back alternate per design) is one plate design (issuer, denomination, palette, portrait side) repeated across the sheet;
in the MVTec layout each design *and side* is its own category (`banknote_00_front`, `banknote_00_back`, ...),
as anomaly models are usually trained per product appearance. `annotations.json` records `side` per sheet.

## Defect classes

| id | class | what is simulated |
|---|---|---|
| 0 | `ink_void` | missing ink: round dropouts or thin ink-starvation streaks, one or all inks |
| 1 | `streak` | doctor-blade line in the direction of travel, 1–3.5 px, partial to full sheet length |
| 2 | `smear` | ink dragged from printed detail with a faint deposit tail |
| 3 | `misregistration` | one ink layer shifted 2.5–9 px over a note, block, row or column |
| 4 | `color_shift` | density (light/heavy) or hue drift over notes or an ink-key zone band |
| 5 | `crease` | fold line with shading and cracked ink |
| 6 | `tear` | edge tear or hole (background shows through) with fibrous fringe |
| 7 | `contamination` | dark speck, fibre/hair, or translucent oil spot |

Defective sheets get 1–3 defects. Masks come from the rendering itself (pixels that changed by more than
0.04 RGB), so they are exact; boxes are the mask extents after the camera warp.

"Good" sheets still vary naturally: sub-pixel register jitter, ±2% ink density, substrate tint, paper texture,
and the camera effects (`--capture mild`: ±1.2° rotation, slight perspective, lighting gradient, blur, sensor
noise, JPEG 90–96). Use `--capture none` for flat renders or `strong` for harder data.

## Training

**YOLO (Ultralytics)**: many defects are only 5–30 px, so train at full resolution or tile:

```bash
pip install ultralytics
yolo detect train data=dataset/data.yaml model=yolo11s.pt imgsz=2048 batch=4 epochs=100
```

**Anomaly detection (anomalib PatchCore / PaDiM)**: one category per design:

```python
# anomalib 1.x Folder datamodule (sketch, not tested against this dataset). Images are JPEG.
from pathlib import Path
from anomalib.data import Folder
from anomalib.models import Patchcore
from anomalib.engine import Engine

root = Path("dataset/mvtec/banknote_00_front")
defects = sorted(p.name for p in (root / "test").iterdir() if p.name != "good")
dm = Folder(name="banknote_00_front", root=root, normal_dir="train/good", normal_test_dir="test/good",
            abnormal_dir=[f"test/{d}" for d in defects], mask_dir=[f"ground_truth/{d}" for d in defects])
Engine().fit(model=Patchcore(), datamodule=dm)
```

Full sheets are large for PatchCore's default 256 px input, so small defects get downsampled away. A higher
input size or a per-note/tile crop works better; each sheet's sheet→image homography is in `annotations.json`
for mapping note positions into the captured image.

# CV defect model — plan (for approval)

Train a local defect-inspection model on the synthetic banknote sheets (`synthetic_print/dataset`), run it on
a simulated camera stream on the GB10, and feed live results into the webapp's Defect Inspection view.
Everything trains and runs on-prem; downloads happen once during setup, then the system runs air-gapped.

## Decisions so far

| Topic | Decision |
|---|---|
| Model shape | Vision models detect + localize; a fine-tuned VLM explains flagged defects |
| Outputs per sheet | pass/fail + anomaly heatmap · boxes + class · pixel masks · plain-language description |
| Licensing | Commercial allowed; core stack chosen is open source (Apache-2.0) on NVIDIA runtime |
| Runtime | Camera-stream pipeline (NVIDIA DeepStream), fed by a simulated stream from files |
| Target data | More synthetic sheets (new seeds / designs / capture settings) |
| Throughput | Demo speed, 1–5 sheets/s |
| Success | Per-class scorecard (no fixed pass bar), compared with the rules baseline and today's placeholder |
| UI | Live results into the existing Defect Inspection view |
| Tracking | MLflow (runs, registry) + Voxel51 FiftyOne (data and failure browsing) |
| Operators | Mixed-skill team → containers, one-command scripts, runbook |
| Disk | ≤ ~60 GB for this project; jobs refuse to start below 40 GB free |

## Architecture

```
 sheet JPEGs ─► DeepStream 9.2 pipeline (container ghcr.io/nvidia/deepstream:9.2-triton-sbsa-dgx-spark)
   (replayed      multifilesrc → nvjpegdec → nvstreammux → nvdspreprocess (16 overlapping 640 px tiles)
    1–5 fps)        ├─► nvinfer #1  RF-DETR-Seg  (TensorRT)  → boxes + class + masks  ─┐
                    └─► nvinfer #2  PatchCore    (TensorRT)  → anomaly map + score    ─┤ probe merges tiles,
                                                                                         │ applies thresholds
                    nvdsosd → annotated MP4 / RTSP          nvmsgbroker (MQTT) ◄─────────┘
                                                                  │
                         results API (FastAPI) ◄──────────────────┘──► VLM server (vLLM, Qwen3-VL-8B + LoRA)
                                │  SSE                                   called only for flagged defects
                                ▼
                webapp Defect Inspection (js/ai/vision.js MODE "api", nginx /api/ proxy already stubbed)
```

Why two vision models: PatchCore learns from good sheets only, so it flags defect types it has never seen
and produces heatmaps. RF-DETR-Seg names the 8 known classes with boxes and masks. A sheet is flagged when
either fires; the VLM then writes the explanation from the crop plus the detector's output.

## Components (versions checked 2026-11-04)

| Piece | Project | Version | License | Role |
|---|---|---|---|---|
| Anomaly detection | [anomalib](https://github.com/openvinotoolkit/anomalib) (PatchCore, EfficientAD for comparison) | 2.7.0 | Apache-2.0 | heatmap, pass/fail, unseen defects |
| Detector + masks | [RF-DETR](https://github.com/roboflow/rf-detr) (RF-DETR-Seg, Nano–Large) | 1.11.2 | Apache-2.0 (Seg and Nano–Large det) | boxes, classes, masks; built-in TensorRT export |
| Small-object slicing | [SAHI](https://github.com/obss/sahi) (training-time tiling reference) | latest | MIT | tile/merge logic reused for 2048 px sheets |
| Stream runtime | [NVIDIA DeepStream](https://github.com/NVIDIA/DeepStream) 9.2 SBSA/DGX Spark container | 9.2 | NVIDIA DeepStream license (free; support via NVIDIA AI Enterprise) | decode, tiling, TensorRT inference, OSD, message broker |
| DeepStream parsers | [DeepStream-Yolo](https://github.com/marcoslucianops/DeepStream-Yolo) (has RF-DETR / RT-DETR / D-FINE parsers) | latest | MIT | nvinfer custom bbox parser for RF-DETR |
| Python bindings | [deepstream_python_apps](https://github.com/NVIDIA-AI-IOT/deepstream_python_apps) | matching 9.2 | Apache-2.0 | pipeline + metadata probes in Python |
| VLM | [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) (4B / 2B as faster fallbacks) | — | Apache-2.0 | defect explanation |
| VLM fine-tune | Hugging Face TRL `SFTTrainer` + PEFT LoRA (installed: trl 1.14.2, peft 0.21.2) | installed | Apache-2.0 | LoRA on defect crops |
| VLM serving | [vLLM](https://github.com/vllm-project/vllm) via NVIDIA's DGX Spark container (`nvcr.io/nvidia/vllm`, [Spark playbook](https://build.nvidia.com/spark/vllm)) | 26.x | Apache-2.0 | OpenAI-compatible endpoint with LoRA |
| Tracking | [MLflow](https://github.com/mlflow/mlflow) | 3.18.0 | Apache-2.0 | runs, metrics, model registry |
| Data / error review | [FiftyOne](https://github.com/voxel51/fiftyone) | 1.22.1 | Apache-2.0 (Teams edition commercial) | browse sheets, predictions, false negatives |
| Broker | Eclipse Mosquitto (MQTT) | — | EPL-2.0 | DeepStream → results API |

Enterprise options if the agency wants vendor support later: NVIDIA AI Enterprise (DeepStream, Triton, TAO,
NIM), Voxel51 FiftyOne Teams, Databricks-managed MLflow. Commercial alternatives to benchmark against:
Cognex VisionPro Deep Learning, MVTec HALCON (anomaly + deep OCR), Landing AI LandingLens.

## Code reused from this repo

- `synthetic_print/generate.py` — more data on demand; one addition: write **per-instance masks** (today only a
  per-sheet union mask is saved) so RF-DETR-Seg gets clean instance polygons. Re-generating 5,000 sheets ≈ 6 min.
- `webapp/tools/build_cv_samples.py` — sheet registration (sub-pixel edges, < 1 px corner error) reused to
  rectify sheets before tiling and to map results to note positions (`note R3·C2`).
- `webapp/js/ai/vision.js` — the `MODE === "api"` hooks become real calls; heatmap/box rendering stays.
- The golden-template placeholder stays as the comparison baseline in every scorecard.

## Phases

Each phase ends with something runnable and a checkpoint with you.

**0 · Setup (online, once)**
- Admin prerequisite: add the user to the `docker` group (`sudo usermod -aG docker dellnvidia04`; today Docker
  returns *permission denied*). NVIDIA Container Toolkit 1.20.1 is already installed.
- Python venv `cv_model/.venv` created with `--system-site-packages`, reusing the installed CUDA 13 torch 2.14
  (arm64 CUDA wheels are the fragile part on GB10); add anomalib, rfdetr, fiftyone, mlflow.
- Pull containers (DeepStream 9.2 DGX Spark, NVIDIA vLLM, Mosquitto) and weights (RF-DETR-Seg COCO
  checkpoints, DINOv2/WideResNet backbones, Qwen3-VL-8B). Record everything in `artifacts.lock`
  (pip hashes, HF revisions, container digests).
- Offline switches: `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `FIFTYONE_DO_NOT_TRACK=true`, MLflow on
  localhost; then a disconnected smoke test of every component.

**1 · Data prep** (`make data`)
- Regenerate with instance masks; cut each sheet into 16 overlapping 640 px tiles at full resolution (defects
  are 5–30 px, so no downscaling); keep all defect tiles + a sample of good tiles.
- Exports: COCO-seg (RF-DETR), anomalib Folder layout per design + side (PatchCore), VLM crop/caption JSONL.
- Load into FiftyOne for review; split stays sheet-level (train/val/test from the generator).

**2 · Anomaly model** (`make train-anomaly`)
- PatchCore per design + side (8 models) on good tiles; EfficientAD as a speed comparison.
- Metrics: image AUROC, pixel AUROC, PRO, false-reject rate at the chosen threshold, per class.

**3 · Detector** (`make train-detector`)
- RF-DETR-Seg (start with Small/Medium), fine-tuned from COCO weights on the tiles; tile results merged back
  to the sheet with SAHI-style NMS.
- Metrics: per-class AP50 / AP50-95, mask IoU, recall at the operating confidence.

**4 · Export + stream** (`make export`, `make stream`)
- ONNX → TensorRT FP16 engines (RF-DETR built-in export; anomalib ONNX + `trtexec`).
- DeepStream app: replay sheets at 1–5 fps, tile via `nvdspreprocess`, two `nvinfer` stages, OSD video,
  MQTT messages. Measured: sheets/s and per-stage latency on the GB10.
- Risk to verify early: mask output in `nvinfer` (DeepStream-Yolo covers RF-DETR boxes); fallback is masks
  from the anomaly map inside each box, or running segmentation in Triton within the same container.

**5 · VLM explainer** (`make train-vlm`)
- ~6–10k examples from labels: defect crop + note context + detector box → JSON
  `{class, note, position, size_mm, description, severity}`, plus "no defect" negatives.
- LoRA on Qwen3-VL-8B (4B if latency is too high) with TRL; serve with vLLM `--enable-lora`.
- Metrics: class accuracy vs labels, location correctness, valid-JSON rate, latency per flagged sheet.

**6 · Webapp integration**
- Results API (FastAPI) subscribes to MQTT, calls the VLM for flagged sheets, stores results, streams them to
  the browser over SSE; `docker-compose.yml` gains `deepstream`, `vlm`, `results-api`, `mqtt` services and
  the nginx `/api/` proxy is enabled. Defect Inspection shows real model output; human disposition and
  audit logging unchanged.

**7 · Scorecard + hand-off**
- One HTML report per run (also logged to MLflow): per-class recall / precision / AP, anomaly AUROC, FRR,
  VLM accuracy, latency, sheets/s, vs rules baseline and the template placeholder.
- Runbook (setup, retrain, add a design, offline checks), license inventory, model cards.

## Disk budget (≈ 50–60 GB of the 140 GB free)

| Item | Est. |
|---|---|
| DeepStream 9.2 container | 12–18 GB |
| vLLM container | 10–15 GB |
| Qwen3-VL-8B weights (bf16) + LoRA | ~17 GB |
| Tiles, exports, FiftyOne DB | 8–12 GB |
| Checkpoints, TensorRT engines, MLflow artifacts | 5–8 GB |

## Known risks / open items

- **Creases** are near noise at 1024 px in today's placeholder; full-resolution tiles should fix this —
  confirmed or not in phase 2/3.
- **Synthetic-only training**: results describe this generator, not real presses; real imagery would need
  on-prem fine-tuning later.
- **GB10 / arm64 maturity**: some NVIDIA components lag on DGX Spark; each container is smoke-tested in
  phase 0 before anything depends on it.
- **DeepStream mask parsing** for RF-DETR-Seg (see phase 4).

## Progress log

**2026-11-04 — phases 0 + 1 done**
- Env: `cv_model/.venv` (anomalib 2.7.0, rfdetr 1.11.2, fiftyone 1.22.1, mlflow 3.18.0, sahi, pycocotools)
  reuses the shared CUDA torch 2.14 via a `.pth` link; venv-local `pip.conf` overrides the workshop's
  offline-only global pip config. `setuptools<75` pinned (FiftyOne's `eta` still imports `distutils`).
  `source env.sh` sets project caches + offline switches (`CV_ONLINE=1` for downloads).
- Deviation: **DeepStream 9.0** (`nvcr.io/nvidia/deepstream:9.0-triton-sbsa-dgx-spark`, already on disk) instead
  of 9.2 — saves ~30 GB; image `cv-defect/deepstream:9.0` adds pyds 1.2.3 built from source (verified on GPU:
  pyds, nvinfer, nvdspreprocess). vLLM: existing `ws/vllm-u2404` (v0.29) instead of a new pull.
- Weights: RF-DETR-Seg S/M, timm wide_resnet50_2 + resnet18 (offline load verified; RF-DETR-Seg-S ≈ 14 ms/tile).
- Generator writes `instance_masks/`; 5,000-sheet set regenerated (images byte-identical to before).
- `make data` (17 s) → `data/` 8.4 GB: detector 11.3k/2.5k/2.7k tiles (train/valid/test), anomaly 1,920 good
  train tiles + val per design×side, VLM 3,740 defect crops + 5,000 negatives. `make fiftyone` → dataset `bcp-sheets`.
- Open: free RAM ≈ 23 GB while the ITSM vLLM servers run; VLM base choice (Qwen3-VL-8B download vs cached
  NVIDIA Cosmos-Reason2-8B, NVIDIA Open Model License).

**2026-11-04 (afternoon) — quick POC instead of phases 2-7 (user request: "quick and dirty", ~1 h)**
- PatchCore pilot (banknote_00_front, 40 sheets): tile AUROC 0.87, sheet FRR 7 %, sheet recall 79 %; strong on
  tear / contamination / streak / crease, weak on color_shift / misregistration (detector's job). The full 8-design
  run was stopped; `make train-anomaly` resumes it (random 1.5M-patch cap → ~7 min / ~15-30 GB per design).
- POC detector: RF-DETR-Seg Small, 648 px, 2 epochs on a class-balanced 3k-tile subset (`make poc-train`, ~28 min
  at ~0.5 it/s with the GPU shared with the ITSM vLLM + Ollama).
- `make poc-webapp`: runs the detector over the 160 Defect Inspection sheets and writes `det`, `score` (max
  confidence) and a mask-derived heatmap into `webapp/data/cv/manifest.json` (placeholder kept as
  `manifest.placeholder.json`; `make poc-revert` restores it). `js/ai/vision.js` / `js/views/vision.js` use the
  detections when present (boxes drawn per class; description templated — no VLM yet).
- Not in the POC: DeepStream/TensorRT, VLM fine-tune, MQTT/results API, live scoring of uploads, scorecard.
- Overnight: `make overnight` (full detector ~3-5 h, then all PatchCore models, then refreshes the webapp).

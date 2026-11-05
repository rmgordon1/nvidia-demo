# GB10 lab: BEP Operations AI demo

| Folder | What it is |
|---|---|
| `webapp/` | The UI (static, nginx). The CV and materials views read precomputed data in `webapp/data/` |
| `itsm-rag-server/` | ITSM backend: retrieval + ranking over the ticket archive, answers from local LLMs |
| `it-tickets/` | Synthetic BMC Helix ticket generator; `out/dell/` is the archive the server loads |
| `cv_model/` | CV defect detection (RF-DETR detector + PatchCore anomaly models), DeepStream image |
| `synthetic_print/` | Synthetic banknote sheet generator (the CV training data) |
| `model-server/` | Earlier NIM + Milvus gateway, superseded by `itsm-rag-server` |

## Run on another machine

Needs an NVIDIA GPU host (arm64 GB10 / DGX Spark, or x86_64) with Docker, the NVIDIA Container
Toolkit, ~60 GB free disk and internet on the first start.

```bash
docker compose up -d --build     # UI on http://localhost:8080
docker compose logs -f rag       # ready when "Application startup complete" appears
```

The first start downloads gpt-oss-20b, the Nemotron embed/rerank models and gemma4 (~27 GB) into
Docker volumes, then builds the search index. Later starts are offline. Settings (port, an existing
Hugging Face cache, vLLM GPU share) are listed at the top of `docker-compose.yml`.

## CV models

The webapp doesn't need them. To re-run CV inference or DeepStream, set up the environment and fetch
the trained models from the release:

```bash
cd cv_model
python3.12 -m venv .venv
.venv/bin/pip install --extra-index-url https://download.pytorch.org/whl/cu130 -r requirements.txt
scripts/fetch_models.sh https://github.com/rmgordon1/nvidia-demo/releases/download/<tag>
```

`scripts/bundle_models.sh` makes the release files on the GB10. To rebuild the models from scratch,
see `cv_model/PLAN.md`.

## Data on the GB10

Training data, checkpoints and caches live in `~/gb10-lab-data`. The gitignored paths in this repo
(`cv_model/data`, `cv_model/models`, `synthetic_print/dataset`, ...) are symlinks into it.

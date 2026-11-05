# Install and run BEP Operations AI

These steps take a fresh machine to a running demo at `http://localhost:8080`. Everything runs in Docker,
so you don't need Python or a virtual environment for the app itself.

## 1. Check the machine

| Need | Why |
|---|---|
| NVIDIA GPU with ~45 GB free GPU memory (a GB10 / DGX Spark, or an x86_64 box with a large GPU) | gpt-oss-20b (~31 GB), the embed/rerank models (~8 GB) and gemma4 (~5 GB) all load on the GPU |
| Linux with the NVIDIA driver installed | `nvidia-smi` must work |
| Docker Engine 24+ with the Compose plugin | `docker compose version` must work |
| NVIDIA Container Toolkit | lets containers use the GPU |
| ~60 GB free disk | model downloads, Docker images and the search index |
| Internet on the first start | models download once; later starts work offline |

Check the GPU, Docker and the toolkit in one go:

```bash
nvidia-smi
docker compose version
docker run --rm --gpus all ubuntu nvidia-smi    # must print the same GPU table
```

If the last command fails, install the NVIDIA Container Toolkit
(https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), then run:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

If `docker` needs `sudo`, either prefix the commands below with `sudo` or add yourself to the group with
`sudo usermod -aG docker $USER` and log out and back in.

## 2. Get the code

```bash
git clone https://github.com/rmgordon1/nvidia-demo.git
cd nvidia-demo
```

The ticket archive the ITSM server searches (`it-tickets/out/dell`) and the web UI's sample data
(`webapp/data`) are already in the repo. Nothing else needs to be downloaded by hand.

## 3. (Optional) Change settings

Defaults work for most machines. To change them, create a `.env` file next to `docker-compose.yml`:

```bash
# .env
WEB_PORT=8080            # host port for the UI
VLLM_GPU_MEM=0.25        # share of GPU memory vLLM reserves. 0.25 suits the GB10's 128 GB.
                         # On a smaller GPU raise it so vLLM gets ~32 GB, e.g. 0.40 on an 80 GB card.
# HF_CACHE=/path/to/hf   # reuse an existing Hugging Face cache (the folder that contains hub/)
# HF_TOKEN=hf_...        # only needed if you swap in a gated model
```

## 4. Start the stack

```bash
docker compose up -d --build
```

This builds two images (the web UI and the ITSM RAG server) and starts four containers:

| Container | What it does |
|---|---|
| `webapp` | the UI (nginx) on port 8080; forwards `/api/` to `rag` |
| `rag` | the ITSM backend: retrieval, ranking, routing and answers |
| `vllm` | serves gpt-oss-20b, the "big" model |
| `ollama` | serves gemma4, the "small" model |

**The first start takes 15–40 minutes,** depending on your connection. It downloads about 27 GB of models
and then builds the search index over 10,000 tickets. Follow progress with:

```bash
docker compose logs -f rag vllm ollama
```

The stack is ready when this returns JSON (not an error):

```bash
curl -s http://localhost:8080/api/health
```

Later starts reuse the downloaded models and index and take a few minutes.

## 5. Use the app

Open **http://localhost:8080** (or `http://<server-ip>:8080` from another machine).

The **Viewing as** dropdown in the header picks a demo role. There's no login; the role only changes
which screens show in the sidebar. Pick **Administrator** to see everything.

| Screen | Role | What to try |
|---|---|---|
| Mission Overview | everyone | Headline numbers for all three use cases |
| ITSM Next Best Step | IT Support Agent | Open an incident from the queue, or paste a ticket description, and get ranked next steps with cited past tickets. Accept, reject or edit them |
| IT Self-Service | Employee | Describe a problem in the chat; it suggests safe self-help and can open a pre-routed ticket |
| IT Insights | IT Manager | Ask plain-English questions about the ticket archive, e.g. "Which group had the longest resolution times last quarter?" |
| Defect Inspection | QA Inspector | Step through press-sheet images, view heatmaps, mark sheets good or bad, tune the threshold |
| Material Accountability | Material Analyst | Yield and spoilage, variance attribution, procurement forecast |
| Governance & Audit | Administrator | Every AI answer and reviewer decision, with export to CSV/JSON |

The Defect Inspection and Material Accountability screens use precomputed data, so they work even before
the models finish loading. The three IT screens need the `rag` container to be ready.

Reviewer decisions and audit records are stored in your browser, so each browser has its own history.

## 6. Stop, restart, update

```bash
docker compose stop                  # stop, keep everything
docker compose start                 # start again (fast)
git pull && docker compose up -d --build    # update to the latest code
docker compose down                  # remove containers, keep downloaded models
docker compose down -v               # remove containers AND models/index (next start re-downloads)
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `could not select device driver "nvidia"` | The NVIDIA Container Toolkit isn't set up. Redo step 1 |
| `vllm` restarts with "out of memory" or "No available memory for the cache blocks" | Free GPU memory or raise `VLLM_GPU_MEM` in `.env`, then `docker compose up -d` |
| `rag` keeps restarting with CUDA out of memory | Lower `VLLM_GPU_MEM` so the other models have room |
| `/api/health` returns 502 | `rag` is still building its index. Wait and watch `docker compose logs -f rag` |
| ITSM answers are slow the first time | gemma4 loads on the first request; later requests are faster |
| Port 8080 is already in use | Set `WEB_PORT=8090` (or another free port) in `.env` |
| Downloads fail behind a proxy | Set `HTTP_PROXY` / `HTTPS_PROXY` for the Docker daemon, or pre-fill a Hugging Face cache and point `HF_CACHE` at it |

## Optional: CV models

The web app doesn't need these. They're only needed to re-run defect-detection training or inference
outside the UI. See the "CV models" section of `README.md` and `cv_model/PLAN.md`.

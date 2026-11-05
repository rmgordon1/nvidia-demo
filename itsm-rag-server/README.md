# ITSM RAG server

The local AI backend behind the **BEP Operations AI** webapp (`../webapp`). It answers IT tickets
using the synthetic BMC Helix archive in `../it-tickets/out/full` (10,000 incidents). Everything
runs on the GB10, and nothing calls out at runtime.

| Audience | Webapp view | Endpoint |
|---|---|---|
| IT support agent | ITSM Next Best Step: ranked steps with citations, routing, copilot chat | `/itsm/recommend`, `/itsm/retrieve`, `/itsm/classify`, `/itsm/chat` (streamed) |
| Employee | IT Self-Service: safe self-help that opens a pre-routed ticket | `/selfservice/chat`, `/selfservice/ticket`, `/selfservice/personas`, `/selfservice/my-tickets` |
| IT manager | IT Insights: plain-English questions over the archive | `/analytics/ask` (text-to-SQL) |

## Architecture

```
webapp (nginx :8080) ──/api/──▶ itsm-rag-server (uvicorn, 172.17.0.1:8030)
                                  ├─ loaders.py   pluggable ticket loaders (helix_csv today)
                                  ├─ index.py     Nemotron embed-1b-v2 + BM25 → RRF → Nemotron rerank-1b-v2
                                  ├─ router.py    logistic regression on embeddings; LLM decides low-confidence fields
                                  ├─ rag.py       recommend · copilot · self-service (PII-scrubbed) · text-to-SQL
                                  └─ llm.py       OpenAI-compatible client ──▶ vLLM gpt-oss-20b (:8021) / Gemma-4 (:8022)
```

"Training on the tickets" works through retrieval (RAG), not fine-tuning. The classifier is the
only trained component; it fits in seconds on the ticket embeddings.

## Two model tiers

| Tier | Model | Serving | Used for |
|---|---|---|---|
| Small | `gemma4-e4b` (Ollama `gemma4:latest`) | Ollama native API, loaded on demand, 8k context, ~5 GB | Recommendations and self-service when the evidence is strong |
| Big | `gpt-oss-20b` | vLLM :8021, always on, ~31 GB | Everything else: weak or conflicting evidence, routing confidence < 0.7, security/OT conversations, longer chats, copilot chat, LLM routing fallback, text-to-SQL |

`app/tiers.py` decides the tier from measurable signals:
- the best match is at least 0.9;
- the top 3 matches agree on the group;
- classifier confidence is at least 0.7;
- in self-service, no security or production-equipment keywords.

If the small model fails, the request is retried on the big one. Every response carries `ai.tier` and
`ai.tier_reason`, so the audit log shows which model answered and why. The Admin model picker pins
one model and bypasses tiering. Settings: `ITSM_SMALL_LLM` (empty disables tiering), `ITSM_BIG_LLM`,
`ITSM_TIER_MIN_SIM`, `ITSM_TIER_MIN_ROUTE_CONF`.

**Measured on 8 easy tickets** (the same evidence for both models):

| Model | Usable | p50 / max latency | GPU memory |
|---|---|---|---|
| Small | 8/8, fully cited | 4.1 s / 7.0 s | ~5 GB |
| Big | 8/8, fully cited | 11.8 s / 15.4 s | ~31 GB |

**Ruled out:**
- granite4.2: empty output on every test.
- nemotron-3-nano-4b: correct, but p50 31 s.

**Not yet tested:**
- Gemma-4-26B: about the same size as gpt-oss-20b, so it doesn't make a meaningful second tier.
- **gpt-oss-120b as the big tier: tried on 2026-11-04 and reverted.** It needed about 85–90 GB in practice (61 GB of weights, plus CUDA graphs, plus KV cache). At `--gpu-memory-utilization 0.62` it held about 89 GB and the RAG server ran out of memory. At 0.50 and 0.56 it had no room left for KV cache. That doesn't fit on a box shared with the CV jobs. Revisit only when the 120B can have most of the GPU to itself.

## Run

```bash
# LLMs (vLLM images already on the box; offline). gpt-oss needs the tiktoken vocab in assets/.
W=/opt/workshop; T=$PWD/assets/tiktoken
sg docker -c "docker run -d --name itsm-llm-gptoss --restart unless-stopped --gpus all --ipc host \
  --ulimit memlock=-1 --ulimit stack=67108864 -p 127.0.0.1:8021:8000 \
  -v $W/models/hf:/root/.cache/huggingface -v $W/cache/flashinfer:/root/.cache/flashinfer \
  -v $W/cache/vllm:/root/.cache/vllm -v $T:/tiktoken:ro \
  -e HF_HUB_OFFLINE=1 -e HF_HOME=/root/.cache/huggingface -e TIKTOKEN_ENCODINGS_BASE=/tiktoken -e TIKTOKEN_RS_CACHE_DIR=/tiktoken \
  --entrypoint vllm ws/vllm-u2404 serve openai/gpt-oss-20b --served-model-name gpt-oss-20b \
  --host 0.0.0.0 --port 8000 --max-model-len 32768 --gpu-memory-utilization 0.25 --max-num-seqs 8 \
  --enable-prefix-caching --reasoning-parser openai_gptoss"
# optional second model (same mounts, no tiktoken): nvidia/Gemma-4-26B-A4B-NVFP4 on 127.0.0.1:8022,
#   --served-model-name gemma-4-26b --trust-remote-code --reasoning-parser gemma4 --moe-backend marlin
#   --max-model-len 32768 --gpu-memory-utilization 0.30

./run.sh                       # server on 172.17.0.1:8030 (~30 s with warm caches, ~90 s cold)
cd ../webapp && sg docker -c "docker compose up -d --build"   # UI on :8080, /api/ proxied here
```

Configuration is set through environment variables (`app/config.py`):
- `ITSM_DATA` and `ITSM_LOADER`: where the tickets come from and which loader reads them.
- `ITSM_LLMS`: `name=url` pairs. Ollama works too, e.g. `gpt-oss-20b=http://127.0.0.1:11434/v1|gpt-oss:20b`.
- `ITSM_DEFAULT_LLM`: the model used when a request doesn't pick one.
- `ITSM_ROUTE_CONFIDENCE`: the classifier confidence below which the LLM decides (default 0.55).

The Admin role in the ITSM view has a model picker.

**Memory (121 GB shared with other workshop jobs):**

| Component | Memory |
|---|---|
| gpt-oss | ~31 GB, fixed |
| Gemma | ~36 GB, fixed |
| This server | ~5.5 GB |

If memory is needed for training, point `ITSM_LLMS` at Ollama instead of stopping containers
without warning.

## Real data later

Write a loader in `app/loaders.py` that returns `Corpus(tickets=[...])` in the canonical ticket
shape (same as `webapp/data/tickets.json`), and register it in `LOADERS`. Possible sources: the
ServiceNow Table API, Helix REST, or a database export. Only `ground_truth` is synthetic-only;
evaluation needs it, the server doesn't.

## Evaluation on the Dell archive (`out/dell`, `eval/results.dell.json`, 2026-11-04)

The same time-based holdout as below: 8,500 training tickets and 1,029 resolved test tickets, 78 of them with
unseen phrasing.

| Metric | Dell data | Original mixed-vendor data |
|---|---|---|
| Group routing vs. true owner | **87.5%** (95.5% on the 65% of tickets with confidence ≥ 0.7) | 84.3% |
| Category vs. true category | 97.6% | 97.5% |
| Priority | 53% | 52% |
| Retrieval recall@1 | 98.8% | 100% |
| Retrieval latency p50 / p95 | 753 / 1265 ms (shared GPU) | 382 / 592 ms |

The data source is set by `ITSM_DATA`: the default is `out/dell`, and `out/full` is the rollback. The original
results below are kept for reference; their raw numbers are in `eval/results.full.json`.

## Evaluation (`eval/evaluate.py`, results in `eval/results.json`)

Time-based holdout: train on the oldest 85% (8,500), test on the newest 15% (1,332 resolved).

| Metric | Result | Notes |
|---|---|---|
| Group routing, classifier only, vs. true owner | **84.3%** | 92.3% on the 75% of tickets with confidence ≥ 0.7 |
| Category vs. true category | **97.5%** | 82.8% vs. the deliberately noisy recorded labels: it learns *through* label noise |
| Priority | 52% | Priority comes from random impact and urgency in the generator, so text barely predicts it |
| Retrieval recall@1 / @5 (same scenario) | 100% / 100% | **Saturated, so not informative.** The 55 synthetic scenarios are too well separated; real data will be harder |
| Retrieval latency | p50 382 ms, p95 592 ms | Including rerank of 40 candidates |

**Caveat:** only 44 test tickets use text phrasing not seen in training, because the synthetic
text pool reuses phrasings. Treat these numbers as optimistic, and group splits by
`ground_truth.text_variant` when doing ML work on this data.

**Spot-checked behaviour** (tested through the UI with gpt-oss-20b):
- Recommendation steps cite only retrieved incidents; uncited steps are dropped.
- Copilot citations are checked against the retrieved set.
- Self-service turned "clicked a phishing link, typed password" into a ticket routed to
  Cybersecurity Operations, with category Security › Email › Phishing.
- Text-to-SQL answers MTTR, top-issue and reassignment questions correctly. It retries on empty
  results and only runs read-only SELECTs, enforced by a SQLite authorizer.

**Known gaps:**
- Recommendations take 12–30 s, longer while training shares the GPU.
- Self-service priority can be too low for security incidents.
- The gpt-oss vs. Gemma benchmark hasn't run yet; it needs both models loaded at the same time.
- No guardrail models in v1, by request; ticket text is only marked "data, not instructions" in
  the prompts.

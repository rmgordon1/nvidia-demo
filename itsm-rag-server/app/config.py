"""Runtime configuration (environment variables with local defaults)."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# out/dell = all-Dell estate (current); out/full = original mixed-vendor archive (rollback: ITSM_DATA=.../out/full)
DATA_SOURCE = os.environ.get("ITSM_DATA", os.path.join(ROOT, "..", "it-tickets", "out", "dell"))
DATA_LOADER = os.environ.get("ITSM_LOADER", "helix_csv")
CACHE_DIR = os.environ.get("ITSM_CACHE", os.path.join(ROOT, ".cache", "index"))
# ServiceNow incidents migrated open from Helix at cutover (webapp/tools/build_tickets.py writes it).
SNOW_PATH = os.environ.get("ITSM_SNOW", os.path.join(ROOT, "..", "webapp", "data", "servicenow.json"))

EMBED_MODEL = os.environ.get("ITSM_EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")
RERANK_MODEL = os.environ.get("ITSM_RERANK_MODEL", "nvidia/llama-nemotron-rerank-1b-v2")
DEVICE = os.environ.get("ITSM_DEVICE", "cuda")

# Chat endpoints as name=url pairs. http(s)://…/v1 = OpenAI-compatible (vLLM); ollama://host:port|tag = Ollama
# native API (load on demand, context capped at 8k). Add Gemma-4-26B back with
# ",gemma-4-26b=http://127.0.0.1:8022/v1" when its vLLM container is running.
LLMS = os.environ.get(
    "ITSM_LLMS",
    "gpt-oss-20b=http://127.0.0.1:8021/v1,gemma4-e4b=ollama://127.0.0.1:11434|gemma4:latest",
)
# gpt-oss-120b was tried as the big tier (2026-11-04) and reverted: on this shared box it needs ~85-90 GB
# (61 GB weights + CUDA graphs + KV cache), which left no room for the RAG server or other workshop jobs.
DEFAULT_LLM = os.environ.get("ITSM_DEFAULT_LLM", "gpt-oss-20b")

# Two tiers (app/tiers.py): the small model handles easy cases, the big one everything else.
BIG_LLM = os.environ.get("ITSM_BIG_LLM", DEFAULT_LLM)
SMALL_LLM = os.environ.get("ITSM_SMALL_LLM", "gemma4-e4b")   # "" disables tiering
TIER_MIN_SIM = float(os.environ.get("ITSM_TIER_MIN_SIM", "0.9"))
TIER_MIN_ROUTE_CONF = float(os.environ.get("ITSM_TIER_MIN_ROUTE_CONF", "0.7"))
LLM_TIMEOUT = float(os.environ.get("ITSM_LLM_TIMEOUT", "180"))

ROUTE_CONFIDENCE = float(os.environ.get("ITSM_ROUTE_CONFIDENCE", "0.55"))


def llm_registry():
    """name -> (base_url, served_model_name)"""
    out = {}
    for part in LLMS.split(","):
        if not part.strip():
            continue
        name, _, url = part.strip().partition("=")
        url, _, served = url.partition("|")
        out[name] = (url.rstrip("/"), served or name)
    return out

"""Runtime configuration, all from environment variables (see ../.env.example).

The gateway speaks the OpenAI-compatible API that NIM exposes, so the same code
runs against NIM containers (production / demo) or Ollama (quick local dev).
"""
import os


def _env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


class Settings:
    # ---- LLM NIM (chat completions) -----------------------------------------
    llm_base_url = _env("LLM_BASE_URL", "http://nim-llm:8000/v1")
    llm_model = _env("LLM_MODEL", "meta/llama-3.1-8b-instruct")
    llm_api_key = _env("LLM_API_KEY", "not-needed")          # local NIMs don't check it
    # nvext   = NIM structured generation (nvext.guided_json) — use with NIM
    # json_schema = OpenAI response_format json_schema      — use with Ollama / vLLM
    # none    = prompt-only JSON, parsed and repaired
    llm_guided = _env("LLM_GUIDED", "nvext")
    llm_max_tokens = int(_env("LLM_MAX_TOKENS", "1500"))
    llm_temperature = float(_env("LLM_TEMPERATURE", "0.1"))
    llm_timeout = float(_env("LLM_TIMEOUT", "120"))

    # ---- Embedding NIM (NeMo Retriever) --------------------------------------
    embed_base_url = _env("EMBED_BASE_URL", "http://nim-embed:8000/v1")
    embed_model = _env("EMBED_MODEL", "nvidia/llama-nemotron-embed-1b-v2")
    embed_api_key = _env("EMBED_API_KEY", "not-needed")
    # NeMo Retriever models are asymmetric: tickets are embedded as "passage",
    # the new ticket as "query". Set false for servers that reject the field.
    embed_input_type = _env("EMBED_INPUT_TYPE", "true").lower() == "true"
    embed_dimensions = int(_env("EMBED_DIMENSIONS", "0")) or None   # Matryoshka: 384/512/768/1024/2048
    embed_batch = int(_env("EMBED_BATCH", "32"))

    # ---- Milvus ---------------------------------------------------------------
    milvus_uri = _env("MILVUS_URI", "http://milvus:19530")     # or a local file path for Milvus Lite
    milvus_token = _env("MILVUS_TOKEN", "")
    collection = _env("MILVUS_COLLECTION", "itsm_tickets")
    index_type = _env("MILVUS_INDEX", "HNSW")                   # GPU_CAGRA with the milvus GPU image

    # ---- Data -----------------------------------------------------------------
    tickets_path = _env("TICKETS_PATH", "/data/tickets.json")
    auto_ingest = _env("AUTO_INGEST", "true").lower() == "true"


settings = Settings()

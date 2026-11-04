"""Thin clients for the two NIM microservices (OpenAI-compatible HTTP APIs)."""
import json
import re
import time

import httpx

from .config import settings


class NimError(RuntimeError):
    pass


def _client(base, key, timeout):
    return httpx.AsyncClient(base_url=base.rstrip("/"), timeout=timeout,
                             headers={"Authorization": f"Bearer {key}"})


# ---------------------------------------------------------------- embeddings
async def embed(texts, input_type):
    """POST /v1/embeddings. input_type is "query" (new ticket) or "passage" (indexed ticket)."""
    out = []
    async with _client(settings.embed_base_url, settings.embed_api_key, 120) as c:
        for i in range(0, len(texts), settings.embed_batch):
            body = {"model": settings.embed_model, "input": texts[i:i + settings.embed_batch],
                    "encoding_format": "float"}
            if settings.embed_input_type:
                body["input_type"] = input_type
                body["truncate"] = "END"          # NIM: truncate over-long input instead of erroring
            if settings.embed_dimensions:
                body["dimensions"] = settings.embed_dimensions
            r = await c.post("/embeddings", json=body)
            if r.status_code >= 400:
                raise NimError(f"embedding NIM HTTP {r.status_code}: {r.text[:300]}")
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out += [d["embedding"] for d in data]
    return out


# ---------------------------------------------------------------- chat / LLM
async def chat_json(messages, schema):
    """Chat completion constrained to `schema`. Returns (parsed_json, meta)."""
    body = {"model": settings.llm_model, "messages": messages, "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens, "stream": False}
    if settings.llm_guided == "nvext":
        body["nvext"] = {"guided_json": schema}                    # NIM structured generation
    elif settings.llm_guided == "json_schema":
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": "result", "schema": schema, "strict": True}}
    t0 = time.perf_counter()
    async with _client(settings.llm_base_url, settings.llm_api_key, settings.llm_timeout) as c:
        r = await c.post("/chat/completions", json=body)
    if r.status_code >= 400:
        raise NimError(f"LLM NIM HTTP {r.status_code}: {r.text[:300]}")
    j = r.json()
    choice = j["choices"][0]
    text = choice["message"].get("content") or ""
    meta = {"llmMs": round((time.perf_counter() - t0) * 1000), "usage": j.get("usage"),
            "model": j.get("model", settings.llm_model), "finish": choice.get("finish_reason")}
    if choice.get("finish_reason") == "length":
        raise NimError(f"LLM output hit max_tokens ({settings.llm_max_tokens}); raise LLM_MAX_TOKENS")
    try:
        return parse_json(text), meta
    except (NimError, json.JSONDecodeError) as e:
        raise NimError(f"LLM returned invalid JSON ({e}); finish={choice.get('finish_reason')}; output starts: {text[:160]!r}") from e


def parse_json(text):
    """Parse model output as JSON; tolerate code fences or leading chatter."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise NimError("LLM did not return JSON: " + text[:200])


# ---------------------------------------------------------------- health
async def ready(base):
    """NIM exposes /v1/health/ready; fall back to /v1/models for other OpenAI-compatible servers."""
    async with httpx.AsyncClient(timeout=3) as c:
        for path in ("/health/ready", "/models"):
            try:
                r = await c.get(base.rstrip("/") + path)
                if r.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
    return False

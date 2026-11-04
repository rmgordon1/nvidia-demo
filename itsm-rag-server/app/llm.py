"""OpenAI-compatible chat client for the local model servers (vLLM or Ollama)."""
import json
import time

import requests

from . import config


class LLMError(RuntimeError):
    pass


def _target(model):
    reg = config.llm_registry()
    name = model or config.DEFAULT_LLM
    if name not in reg:
        raise LLMError(f"unknown model {name!r}; configured: {', '.join(reg)}")
    url, served = reg[name]
    return name, url, served


def _body(served, messages, max_tokens, temperature, schema=None, stream=False):
    body = {"model": served, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
            "stream": stream}
    if schema is not None:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}
    if served.startswith("gpt-oss"):
        body["reasoning_effort"] = "low"
    return body


OLLAMA_CTX = 8192  # the system Ollama defaults to a huge context (27 GB for a 3B model); cap it per request


def _is_ollama(url):
    return url.startswith("ollama://")


def _ollama_chat(name, url, served, messages, schema, max_tokens, temperature, t0):
    """Ollama native /api/chat: allows num_ctx and think=false, which the OpenAI-compatible endpoint does not."""
    base = "http://" + url[len("ollama://"):].rstrip("/")
    body = {"model": served, "messages": messages, "stream": False, "think": False, "keep_alive": "15m",
            "options": {"num_ctx": OLLAMA_CTX, "num_predict": max_tokens, "temperature": temperature}}
    if schema is not None:
        body["format"] = schema
    try:
        r = requests.post(f"{base}/api/chat", json=body, timeout=config.LLM_TIMEOUT)
        if r.status_code == 400 and "think" in r.text:  # model without a thinking switch
            body.pop("think")
            r = requests.post(f"{base}/api/chat", json=body, timeout=config.LLM_TIMEOUT)
    except requests.RequestException as e:
        raise LLMError(f"{name} unreachable at {base}: {e}") from e
    if r.status_code != 200:
        raise LLMError(f"{name} HTTP {r.status_code}: {r.text[:300]}")
    js = r.json()
    content = js.get("message", {}).get("content") or ""
    meta = dict(model=name, latency_ms=int((time.time() - t0) * 1000), finish=js.get("done_reason"),
                usage=dict(prompt_tokens=js.get("prompt_eval_count"), completion_tokens=js.get("eval_count")))
    if schema is not None:
        try:
            content = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"{name} returned invalid JSON (finish={meta['finish']}): {content[:200]}") from e
    return content, meta


def chat(messages, model=None, schema=None, max_tokens=1500, temperature=0.2):
    """Returns (content, meta). With `schema`, content is the parsed JSON object."""
    name, url, served = _target(model)
    t0 = time.time()
    if _is_ollama(url):
        return _ollama_chat(name, url, served, messages, schema, max_tokens, temperature, t0)
    try:
        r = requests.post(f"{url}/chat/completions", json=_body(served, messages, max_tokens, temperature, schema),
                          timeout=config.LLM_TIMEOUT)
    except requests.RequestException as e:
        raise LLMError(f"{name} unreachable at {url}: {e}") from e
    if r.status_code != 200:
        raise LLMError(f"{name} HTTP {r.status_code}: {r.text[:300]}")
    js = r.json()
    msg = js["choices"][0]["message"]
    content = msg.get("content") or ""
    meta = dict(model=name, latency_ms=int((time.time() - t0) * 1000), usage=js.get("usage", {}),
                finish=js["choices"][0].get("finish_reason"))
    if schema is not None:
        try:
            content = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"{name} returned invalid JSON (finish={meta['finish']}): {content[:200]}") from e
    return content, meta


def stream(messages, model=None, max_tokens=1500, temperature=0.3):
    """Yields text deltas from a streamed completion."""
    name, url, served = _target(model)
    if _is_ollama(url):
        base = "http://" + url[len("ollama://"):].rstrip("/")
        body = {"model": served, "messages": messages, "stream": True, "think": False, "keep_alive": "15m",
                "options": {"num_ctx": OLLAMA_CTX, "num_predict": max_tokens, "temperature": temperature}}
        try:
            r = requests.post(f"{base}/api/chat", json=body, stream=True, timeout=config.LLM_TIMEOUT)
        except requests.RequestException as e:
            raise LLMError(f"{name} unreachable at {base}: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"{name} HTTP {r.status_code}: {r.text[:300]}")
        for line in r.iter_lines(decode_unicode=True):
            if line:
                d = json.loads(line).get("message", {}).get("content")
                if d:
                    yield d
        return
    try:
        r = requests.post(f"{url}/chat/completions", json=_body(served, messages, max_tokens, temperature, stream=True),
                          stream=True, timeout=config.LLM_TIMEOUT)
    except requests.RequestException as e:
        raise LLMError(f"{name} unreachable at {url}: {e}") from e
    if r.status_code != 200:
        raise LLMError(f"{name} HTTP {r.status_code}: {r.text[:300]}")
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            delta = json.loads(data)["choices"][0]["delta"].get("content")
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        if delta:
            yield delta


def health():
    out = {}
    for name, (url, served) in config.llm_registry().items():
        try:
            if _is_ollama(url):  # Ollama loads on demand: "up" means the server is up and the model is pulled
                tags = requests.get("http://" + url[len("ollama://"):].rstrip("/") + "/api/tags", timeout=3).json()
                out[name] = "up" if any(m["name"] == served for m in tags.get("models", [])) else "not pulled"
                continue
            r = requests.get(f"{url}/models", timeout=3)
            out[name] = "up" if r.ok else f"HTTP {r.status_code}"
        except requests.RequestException:
            out[name] = "down"
    return out

"""Run the gateway without Milvus, for local testing only.

Replaces app.store with an in-memory cosine index exposing the same functions,
then starts uvicorn. Everything else (NIM/OpenAI-compatible calls, prompts,
validation) is the real code. Example (against Ollama):

    EMBED_BASE_URL=http://localhost:11434/v1 EMBED_MODEL=nomic-embed-text-v2-moe EMBED_INPUT_TYPE=false \
    LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=nemotron-3-nano:4b LLM_GUIDED=json_schema \
    TICKETS_PATH=../webapp/data/tickets.json /opt/workshop/venv/bin/python3 tests/dev_server.py 8790
"""
import atexit
import math
import os
import pickle
import sys

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])
from app import store  # noqa: E402

CACHE = os.path.join(os.path.dirname(__file__), ".devstore.pkl")   # dev only: skip re-embedding on restart
_rows = pickle.load(open(CACHE, "rb")) if os.path.exists(CACHE) else {}
atexit.register(lambda: pickle.dump(_rows, open(CACHE, "wb")))


def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


store.exists = lambda: bool(_rows)
store.count = lambda: len(_rows)
store.create = lambda dim, drop=False: _rows.clear() if drop else None
def _upsert(rows):
    _rows.update({r["id"]: {**r, "embedding": _norm(r["embedding"])} for r in rows})
    pickle.dump(_rows, open(CACHE, "wb"))


store.upsert = _upsert
store.get = lambda ids: [{"id": i, "origin": _rows[i]["origin"], "doc": _rows[i]["doc"], "text": _rows[i]["text"]} for i in ids if i in _rows]


def _search(vector, k, sources, exclude_id=None):
    q = _norm(vector)
    cands = [r for r in _rows.values() if r["has_resolution"] and r["origin"] in sources and r["id"] != exclude_id]
    scored = sorted(((sum(a * b for a, b in zip(q, r["embedding"])), r) for r in cands), key=lambda x: -x[0])[:k]
    return [{"id": r["id"], "score": s, "origin": r["origin"], "doc": r["doc"], "text": r["text"]} for s, r in scored]


store.search = _search

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(sys.argv[1] if len(sys.argv) > 1 else 8790))

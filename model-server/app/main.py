"""BEP Operations AI — model gateway.

The web UI calls /api/* (nginx strips the prefix and proxies here). The gateway
owns every model call: it talks to the NIM microservices and Milvus, validates
outputs, and never writes back to ServiceNow / ERP / line systems.
"""
import asyncio
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import itsm, nim, store
from .config import settings

log = logging.getLogger("gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="BEP Operations AI gateway", version="0.1.0")
app.include_router(itsm.router)
state = {"ingest": "idle", "ingestError": None}


@app.exception_handler(nim.NimError)
async def nim_error(_req: Request, exc: nim.NimError):
    # Upstream model problem: 502 with a readable message; the UI falls back to its placeholder.
    log.warning("model call failed: %s", exc)
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/health")
async def health():
    llm, emb = await asyncio.gather(nim.ready(settings.llm_base_url), nim.ready(settings.embed_base_url))
    try:
        n, mil, mil_err = store.count(), True, None
    except Exception as e:  # noqa: BLE001 — report, don't crash health
        n, mil, mil_err = 0, False, str(e)[:200]
    itsm_ready = llm and emb and mil and n > 0
    return {
        "status": "ok" if itsm_ready else "degraded",
        "capabilities": {"itsm": itsm_ready, "vision": False, "materials": False},
        "components": {
            "llm": {"ready": llm, "url": settings.llm_base_url, "model": settings.llm_model, "guided": settings.llm_guided},
            "embed": {"ready": emb, "url": settings.embed_base_url, "model": settings.embed_model},
            "milvus": {"ready": mil, "uri": settings.milvus_uri, "collection": settings.collection, "entities": n,
                       "index": f"{settings.index_type}/COSINE", "error": mil_err},
        },
        "ingest": state,
    }


# Not built yet — the UI keeps using its in-browser placeholders for these.
@app.post("/vision/{_path:path}")
@app.post("/materials/{_path:path}")
async def not_yet(_path: str):
    raise HTTPException(501, "Not implemented in the gateway yet; the UI uses its placeholder.")


@app.on_event("startup")
async def startup():
    if settings.auto_ingest:
        asyncio.create_task(auto_ingest())


async def auto_ingest():
    """Index the bundled ticket archive once the embedding NIM and Milvus are up (first boot only)."""
    if not os.path.exists(settings.tickets_path):
        state.update(ingest="skipped", ingestError=f"{settings.tickets_path} not found")
        return
    for attempt in range(180):                      # NIMs can take several minutes to load on first start
        try:
            if store.exists() and store.count() > 0:
                state["ingest"] = "already indexed"
                return
            if await nim.ready(settings.embed_base_url):
                state["ingest"] = "running"
                res = await itsm.index_tickets(itsm.load_tickets(settings.tickets_path), log=log.info)
                state.update(ingest=f"done: {res['indexed']} incidents, dim {res['dim']}, {res['seconds']}s", ingestError=None)
                return
        except Exception as e:  # noqa: BLE001
            state["ingestError"] = str(e)[:300]
            log.warning("auto-ingest waiting: %s", e)
        await asyncio.sleep(10)
    state["ingest"] = "gave up waiting for the embedding NIM / Milvus"

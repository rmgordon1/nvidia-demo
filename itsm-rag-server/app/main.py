"""BEP Operations AI ITSM RAG server (FastAPI). nginx in the webapp proxies /api/* here.

Endpoints (all JSON):
  GET  /health                       models, index size
  POST /itsm/retrieve                hybrid search over resolved incidents
  POST /itsm/recommend               next best steps grounded in retrieved incidents + routing
  POST /itsm/classify                hybrid routing (group / category / priority)
  POST /itsm/chat                    agent copilot chat (streamed NDJSON events)
  GET  /itsm/queue                   open ServiceNow incidents with a suggested route + closest past fix
  GET  /selfservice/personas         demo employees
  GET  /selfservice/my-tickets       one employee's own tickets
  POST /selfservice/chat             employee self-service assistant
  POST /selfservice/ticket           open a pre-routed incident from the conversation
  POST /analytics/ask                manager questions over the archive (text-to-SQL)
"""
import collections
import hashlib
import json
import logging
import os
import threading
import time

import joblib
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import config, llm, loaders, rag, tiers
from .index import Encoder, Index, query_text
from .router import Router, route

log = logging.getLogger("itsm-rag-server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = FastAPI(title="BEP Operations AI ITSM RAG server", version="1.0")
S = {}  # runtime state
_new_lock = threading.Lock()


@app.on_event("startup")
def startup():
    t0 = time.time()
    corpus = loaders.load(config.DATA_LOADER, config.DATA_SOURCE)
    snow = apply_migration(corpus)
    enc = Encoder()
    idx = Index(corpus.tickets, enc)
    key = hashlib.sha1(idx.emb[:64].tobytes() + str(len(idx.tickets)).encode()).hexdigest()[:12]
    path = os.path.join(config.CACHE_DIR, f"router-{key}.joblib")
    if os.path.exists(path):
        router = joblib.load(path)
    else:
        router = Router(idx.emb, corpus.tickets)
        joblib.dump(router, path)
    counts = collections.Counter(t["_customer"]["person_id"] for t in corpus.tickets)
    S.update(corpus=corpus, idx=idx, router=router, db=rag.build_db(corpus.tickets),
             names=sorted({t["_customer"]["name"] for t in corpus.tickets} |
                          {t.get("assignee") for t in corpus.tickets if t.get("assignee")}, key=len, reverse=True),
             ticket_counts=counts, snow=snow, queue=None,
             seq=max(0, (snow.get("nextNumber") or 10001) - 10001))
    log.info("ready: %d tickets from %s in %.0fs", len(corpus.tickets), corpus.source, time.time() - t0)
    threading.Thread(target=_build_queue, daemon=True).start()


def apply_migration(corpus):
    """Helix is history after cutover: close the records that were migrated open to ServiceNow."""
    try:
        with open(config.SNOW_PATH, encoding="utf-8") as f:
            snow = json.load(f)
    except FileNotFoundError:
        log.warning("no ServiceNow migration file at %s; Helix open tickets stay open", config.SNOW_PATH)
        return {"incidents": []}
    by_id = {t["id"]: t for t in corpus.tickets}
    for inc in snow["incidents"]:
        t = by_id.get(inc["correlationId"])
        if t:
            t.update(migratedTo=inc["id"], status="Closed", closed=inc["migratedAt"], resolution="")
    log.info("cutover: %d open Helix incidents now live in ServiceNow", len(snow["incidents"]))
    return snow


def _build_queue():
    """Precompute, per open incident, the classifier's route (no LLM) and the closest resolved incidents."""
    t0, out = time.time(), {}
    for inc in S["snow"]["incidents"]:
        q = {k: inc.get(k) for k in ("summary", "details", "cat", "product", "ci")}
        qt = query_text(q)
        vec = S["idx"].enc.query(qt)
        hits = _search(q, 3, exclude=inc["correlationId"], qvec=vec)
        routing, _ = route(S["router"], qt, vec, hits, threshold=0.0)
        out[inc["id"]] = dict(
            routing={f: dict(value=v["value"], confidence=v["confidence"]) for f, v in routing.items()},
            similar=[dict(id=h["ticket"]["id"], summary=h["ticket"].get("summary"), sim=h.get("sim")) for h in hits])
    S["queue"] = out
    log.info("queue: %d open incidents scored in %.0fs", len(out), time.time() - t0)


# ------------------------------------------------------------------ models
class Query(BaseModel):
    summary: str | None = None
    details: str | None = None
    cat: list[str] | None = None
    product: str | None = None
    ci: str | None = None
    site: str | None = None
    raw: str | None = None
    id: str | None = None


class RetrieveReq(BaseModel):
    query: Query | str
    k: int = 6
    sources: list[str] = ["servicenow", "helix"]
    excludeId: str | None = None
    extra: list[dict] = Field(default_factory=list, description="client-side tickets to search too")


class RecommendReq(RetrieveReq):
    hits: list[str] | None = Field(None, description="ticket ids from a prior /itsm/retrieve; re-retrieves if absent")
    model: str | None = None


class ClassifyReq(BaseModel):
    query: Query | str
    model: str | None = None
    force_llm: bool = False


class ChatReq(BaseModel):
    ticket: Query | None = None
    messages: list[dict]
    sources: list[str] = ["servicenow", "helix"]
    extra: list[dict] = Field(default_factory=list)
    model: str | None = None
    k: int = 6


class SelfChatReq(BaseModel):
    messages: list[dict]
    person_id: str | None = None
    model: str | None = None


class SelfTicketReq(BaseModel):
    person_id: str | None = None
    summary: str
    details: str = ""
    model: str | None = None


class AskReq(BaseModel):
    question: str
    model: str | None = None


def _q(q):
    return q if isinstance(q, str) else q.model_dump(exclude_none=True)


def _search(q, k=6, sources=("servicenow", "helix"), exclude=None, extra=None, qvec=None):
    return S["idx"].search(q, k=k, sources=sources, exclude_id=exclude, extra=extra, qvec=qvec)


def _provenance(meta, **kw):
    return dict(model=(meta or {}).get("model") or config.DEFAULT_LLM, version="vllm-local", mode="api",
                runtime="GB10 · on-prem", at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **kw)


# ------------------------------------------------------------------ routes
@app.get("/health")
def health():
    ready = "idx" in S
    return dict(status="ok" if ready else "starting", tickets=len(S["idx"].tickets) if ready else 0,
                llms=llm.health(), default_llm=config.DEFAULT_LLM, tiers=dict(big=config.BIG_LLM, small=config.SMALL_LLM),
                embed=config.EMBED_MODEL,
                rerank=config.RERANK_MODEL, source=S["corpus"].source if ready else None)


@app.post("/itsm/retrieve")
def retrieve(r: RetrieveReq):
    t0 = time.time()
    hits = _search(_q(r.query), r.k, r.sources, r.excludeId, r.extra)
    return dict(hits=rag.strip_hits(hits), ms=int((time.time() - t0) * 1000))


@app.post("/itsm/classify")
def classify(r: ClassifyReq):
    q = _q(r.query)
    qt = query_text(q)
    vec = S["idx"].enc.query(qt)
    hits = _search(q, 6, qvec=vec)
    res, meta = route(S["router"], qt, vec, hits, model=r.model or config.BIG_LLM, force_llm=r.force_llm)
    return dict(routing=res, llm=meta)


@app.post("/itsm/recommend")
def recommend(r: RecommendReq):
    t0 = time.time()
    q = _q(r.query)
    qt = query_text(q)
    vec = S["idx"].enc.query(qt)
    if r.hits:
        pool = {t["id"]: t for t in r.extra} | S["idx"].by_id
        fresh = {h["ticket"]["id"]: h for h in _search(q, max(r.k, 10), r.sources, r.excludeId, r.extra, qvec=vec)}
        hits = [fresh[i] for i in r.hits if i in fresh] or list(fresh.values())[:r.k]
        hits += [dict(ticket=pool[i], score=0, sim=0.3, matchedTerms=[]) for i in r.hits if i not in fresh and i in pool]
    else:
        hits = _search(q, r.k, r.sources, r.excludeId, r.extra, qvec=vec)
    retrieval_ms = int((time.time() - t0) * 1000)
    routing, rmeta = route(S["router"], qt, vec, hits, model=r.model or config.BIG_LLM)
    try:
        rec, tier = tiers.call(lambda m: rag.recommend(q, hits, routing, model=m), "recommend", r.model,
                               hits=hits, routing=routing)
    except llm.LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}") from e
    steps = rec["steps"]
    confidence = round(sum(s["confidence"] for s in steps[:3]) / min(3, len(steps)), 2) if steps else 0
    cited = list(dict.fromkeys(x for s in steps for x in s["sources"]))
    products = collections.Counter(h["ticket"].get("product") for h in hits if h["ticket"].get("product"))
    return dict(
        steps=steps, group=routing["group"]["value"], category=routing["category"]["value"],
        product=products.most_common(1)[0][0] if products else "",
        priority=routing["priority"]["value"], routing=routing, etaHours=rag.eta_hours(hits), confidence=confidence,
        warnings=rec["warnings"], hits=rag.strip_hits(hits),
        timings=dict(retrievalMs=retrieval_ms, generationMs=rec["generation_ms"]),
        ai=_provenance(rec["meta"], retriever=f"{config.EMBED_MODEL} + BM25 + {config.RERANK_MODEL}",
                       knowledge=r.sources, sources=cited, confidence=confidence,
                       routing_llm=(rmeta or {}).get("model"), tier=tier["tier"], tier_reason=tier["reason"]))


@app.post("/itsm/chat")
def chat(r: ChatReq):
    user_msgs = [m["content"] for m in r.messages if m.get("role") == "user"]
    if not user_msgs:
        raise HTTPException(400, "messages must include a user message")
    t = _q(r.ticket) if r.ticket else None
    probe = (query_text(t) + "\n" if t else "") + user_msgs[-1]
    hits = _search(probe, r.k, r.sources, (t or {}).get("id") if isinstance(t, dict) else None, r.extra)
    msgs = rag.copilot_messages(t, r.messages, hits)

    def events():
        t0 = time.time()
        yield json.dumps(dict(type="sources", hits=[dict(id=h["ticket"]["id"], summary=h["ticket"].get("summary"),
                                                         group=h["ticket"].get("group"), sim=h["sim"]) for h in hits])) + "\n"
        text = ""
        try:
            for d in llm.stream(msgs, model=r.model or config.BIG_LLM, max_tokens=1500):
                text += d
                yield json.dumps(dict(type="delta", text=d)) + "\n"
        except llm.LLMError as e:
            yield json.dumps(dict(type="error", message=str(e))) + "\n"
            return
        valid = {h["ticket"]["id"] for h in hits}
        cited = list(dict.fromkeys(rag.CITE.findall(text)))
        yield json.dumps(dict(type="done", cited=[c for c in cited if c in valid],
                              uncited=[c for c in cited if c not in valid], model=r.model or config.BIG_LLM, tier="big",
                              ms=int((time.time() - t0) * 1000))) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.get("/itsm/queue")
def queue():
    """Open ServiceNow incidents are served to the browser as static data; this adds the AI preview per incident."""
    q = S.get("queue")
    return dict(ready=q is not None, count=len(S.get("snow", {}).get("incidents", [])), items=q or {})


@app.get("/selfservice/personas")
def personas(n: int = 8):
    people = S["corpus"].people
    out = []
    for pid, c in S["ticket_counts"].most_common(400):
        p = people.get(pid)
        if p and not p["support"] and 3 <= c <= 40:
            out.append(dict(person_id=pid, name=p["name"], dept=p["dept"], site=p["site"], title=p["title"], tickets=c))
        if len(out) >= n:
            break
    return dict(personas=out)


@app.get("/selfservice/my-tickets")
def my_tickets(person_id: str):
    mine = [t for t in S["corpus"].tickets if t["_customer"]["person_id"] == person_id]
    snow = {i["correlationId"]: i for i in S["snow"]["incidents"]}
    rows = []
    # open (migrated) tickets first so the 50-row cap never hides them, then newest first
    for t in sorted(mine, key=lambda t: (t["id"] not in snow, -(t["reported"] or 0)))[:50]:
        s = snow.get(t["id"])  # migrated at cutover: the employee sees the live ServiceNow incident
        rows.append(dict(id=s["id"], summary=s["summary"], status=s["status"], reported=s["reported"], resolved=None,
                         group=s["group"], resolution="", platform="servicenow", helixId=t["id"]) if s else
                    dict(id=t["id"], summary=t["summary"], status=t["status"], reported=t["reported"],
                         resolved=t["resolved"], group=t["group"], resolution=t["resolution"], platform="helix"))
    return dict(tickets=rows)


@app.post("/selfservice/chat")
def self_chat(r: SelfChatReq):
    user_msgs = [m["content"] for m in r.messages if m.get("role") == "user"]
    if not user_msgs:
        raise HTTPException(400, "messages must include a user message")
    hits = _search(" ".join(user_msgs[-3:]), 5, ("helix",))
    try:
        (out, meta), tier = tiers.call(lambda m: rag.selfservice(r.messages, hits, S["names"], model=m), "selfservice",
                                       r.model, hits=hits, messages=r.messages)
    except llm.LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}") from e
    return dict(out, ai=_provenance(meta, sources=len(hits), tier=tier["tier"], tier_reason=tier["reason"]))


@app.post("/selfservice/ticket")
def self_ticket(r: SelfTicketReq):
    q = {"summary": r.summary, "details": r.details}
    qt = query_text(q)
    vec = S["idx"].enc.query(qt)
    hits = _search(q, 6, qvec=vec)
    routing, meta = route(S["router"], qt, vec, hits, model=r.model or config.BIG_LLM)
    with _new_lock:
        S["seq"] += 1
        num = f"INC{10000 + S['seq']:07d}"
    person = S["corpus"].people.get(r.person_id or "", {})
    return dict(incident=dict(number=num, summary=r.summary[:100], details=r.details, caller=person.get("name"),
                              site=person.get("site"), assignment_group=routing["group"]["value"],
                              category=routing["category"]["value"], priority=routing["priority"]["value"],
                              state="New", opened_at=int(time.time())),
                routing=routing, ai=_provenance(meta, method={k: v["method"] for k, v in routing.items()}))


@app.post("/analytics/ask")
def ask(r: AskReq):
    if len(r.question.strip()) < 5:
        raise HTTPException(400, "question too short")
    try:
        return rag.analytics(S["db"], r.question.strip()[:500], model=r.model or config.BIG_LLM)
    except llm.LLMError as e:
        raise HTTPException(503, f"LLM unavailable: {e}") from e


@app.get("/models")
def models():
    return dict(default=config.DEFAULT_LLM, big=config.BIG_LLM, small=config.SMALL_LLM, llms=llm.health())

"""ITSM next-best-step: retrieval (embedding NIM + Milvus) and grounded generation (LLM NIM).

    POST /itsm/retrieve   new ticket -> top-k similar resolved incidents
    POST /itsm/recommend  new ticket + retrieved ids -> ranked, cited next steps
    POST /itsm/index      add/refresh one incident (e.g. a ServiceNow incident just resolved)
"""
import json
import re
import statistics
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import nim, store
from .config import settings

router = APIRouter(prefix="/itsm")

RESOLVED = {"Closed", "Resolved"}
MAX_WORKLOG = 12


# ------------------------------------------------------------------ text building
def passage_text(t):
    """The text embedded for an indexed incident: symptom first, then classification and fix."""
    cat = " > ".join(x for x in t.get("cat") or [] if x)
    parts = [
        f"Summary: {t.get('summary') or ''}",
        f"Details: {(t.get('details') or '')[:1500]}",
        f"Category: {cat}" if cat else "",
        f"Product: {t.get('product')}" if t.get("product") else "",
        f"Configuration item: {t.get('ci')}" if t.get("ci") else "",
        f"Resolution: {(t.get('resolution') or '')[:1500]}" if t.get("resolution") else "",
    ]
    return "\n".join(p for p in parts if p)[:8000]


def query_text(q):
    cat = " > ".join(x for x in q.get("cat") or [] if x)
    parts = [q.get("summary") or "", q.get("details") or "",
             f"Category: {cat}" if cat else "", f"Product: {q.get('product')}" if q.get("product") else "",
             f"Configuration item: {q.get('ci')}" if q.get("ci") else ""]
    return "\n".join(p for p in parts if p)[:4000]


def to_row(t, vector):
    doc = dict(t)
    doc["worklog"] = [{**w, "text": (w.get("text") or "")[:600]} for w in (t.get("worklog") or [])[:MAX_WORKLOG]]
    doc["details"] = (doc.get("details") or "")[:4000]
    return {
        "id": t["id"], "origin": t.get("origin") or "helix", "status": (t.get("status") or "")[:24],
        "has_resolution": bool(t.get("resolution")) and t.get("status") in RESOLVED,
        "assigned_group": (t.get("group") or "")[:128],
        "reported": int(t.get("reported") or 0), "resolved": int(t.get("resolved") or 0),
        "text": passage_text(t), "doc": doc, "embedding": vector,
    }


async def index_tickets(tickets, recreate=False, log=print):
    """Embed incidents as passages and upsert them into Milvus."""
    t0 = time.perf_counter()
    texts = [passage_text(t) for t in tickets]
    probe = await nim.embed(texts[:1], "passage")
    store.create(len(probe[0]), drop=recreate)
    done = 0
    for i in range(0, len(tickets), 64):
        batch = tickets[i:i + 64]
        vecs = await nim.embed(texts[i:i + 64], "passage")
        store.upsert([to_row(t, v) for t, v in zip(batch, vecs)])
        done += len(batch)
        log(f"  indexed {done}/{len(tickets)}")
    return {"indexed": done, "dim": len(probe[0]), "seconds": round(time.perf_counter() - t0, 1)}


# ------------------------------------------------------------------ API models
class Query(BaseModel):
    summary: str = ""
    details: str = ""
    cat: list[str] = Field(default_factory=list)
    product: str = ""
    ci: str = ""
    id: str | None = None                       # set when replaying an archived ticket


class RetrieveReq(BaseModel):
    query: Query
    k: int = Field(6, ge=1, le=20)
    sources: list[str] = Field(default_factory=lambda: ["servicenow", "helix"])
    excludeId: str | None = None


class RecommendReq(BaseModel):
    query: Query
    hitIds: list[str]
    hitScores: dict[str, float] = Field(default_factory=dict)   # id -> retrieval cosine similarity
    sources: list[str] = Field(default_factory=lambda: ["servicenow", "helix"])


class IndexReq(BaseModel):
    ticket: dict


# ------------------------------------------------------------------ retrieve
WORD = re.compile(r"[a-z0-9]+")
STOP = set("a an and are as at be by for from has have in is it its of on or the to was were with this that user".split())


def _terms(s):
    return {w for w in WORD.findall((s or "").lower()) if len(w) > 2 and w not in STOP}


@router.post("/retrieve")
async def retrieve(req: RetrieveReq):
    qt = query_text(req.query.model_dump())
    if len(qt.strip()) < 3:
        raise HTTPException(422, "Query is empty.")
    t0 = time.perf_counter()
    vec = (await nim.embed([qt], "query"))[0]
    t1 = time.perf_counter()
    hits = store.search(vec, req.k, req.sources, req.excludeId or req.query.id)
    t2 = time.perf_counter()
    q_terms = _terms(qt)
    out = []
    for h in hits:
        # Dense retrieval has no "matched terms"; overlap is returned only for UI highlighting.
        matched = sorted(q_terms & _terms(h["text"]))
        out.append({"id": h["id"], "origin": h["origin"], "score": round(h["score"], 4),
                    "sim": round(max(0.0, min(1.0, h["score"])), 4), "matchedTerms": matched, "ticket": h["doc"]})
    return {"hits": out, "timings": {"embedMs": round((t1 - t0) * 1000), "searchMs": round((t2 - t1) * 1000)},
            "model": {"embed": settings.embed_model, "store": f"Milvus · {settings.collection} · {settings.index_type}/COSINE"}}


# ------------------------------------------------------------------ recommend
SYSTEM = """You are an IT service desk assistant for a federal production agency. You recommend the next best \
troubleshooting/resolution steps for a NEW ServiceNow incident, using ONLY the resolved historical incidents provided \
as context. Rules:
- Every step must be supported by at least one context incident; cite their ids exactly as given (e.g. INC000000873417).
- Prefer steps that actually resolved similar incidents; order steps by what the agent should do first.
- Write each step as a short imperative instruction an agent can perform (max 30 words). No customer names or contact details.
- Never claim you performed an action. The human agent decides and acts; you only advise.
- If the context does not fit the new incident, return fewer steps with low confidence and say so in "notes".
- confidence is 0-1: how strongly the cited incidents support the step for THIS incident.
Respond with JSON only, matching the schema."""

SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "maxItems": 5, "items": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "rationale": {"type": "string"},
                "confidence": {"type": "number"},
                "sources": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text", "rationale", "confidence", "sources"],
        }},
        "assignment_group": {"type": "string"},
        "category": {"type": "string"},
        "product": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["steps", "assignment_group", "category", "product", "notes"],
}


def _context_block(doc):
    actions = [w["text"] for w in doc.get("worklog") or [] if w.get("type") in ("Working Log", "Work notes", "Vendor Communication")][:4]
    lines = [
        f"[{doc['id']}] ({'ServiceNow' if doc.get('origin') == 'servicenow' else 'Helix archive'})",
        f"Summary: {doc.get('summary', '')}",
        f"Details: {(doc.get('details') or '')[:700]}",
        f"Category: {' > '.join(x for x in doc.get('cat') or [] if x)} | Product: {doc.get('product') or '-'} | CI: {doc.get('ci') or '-'}",
        f"Assignment group: {doc.get('group', '')}",
        f"Resolution: {(doc.get('resolution') or '')[:700]}",
    ]
    if actions:
        lines.append("Work notes: " + " | ".join(a[:220] for a in actions))
    return "\n".join(lines)


@router.post("/recommend")
async def recommend(req: RecommendReq):
    docs = [r["doc"] | {"origin": r["origin"]} for r in store.get(req.hitIds)]
    warnings = []
    if not docs:
        return {"steps": [], "group": "", "category": "", "product": "", "etaHours": None, "confidence": 0,
                "warnings": ["No similar resolved incidents were retrieved. Escalate per the standard triage runbook."],
                "timings": {"llmMs": 0}, "model": {"llm": settings.llm_model}}
    q = req.query
    user = ("NEW INCIDENT\n" + query_text(q.model_dump()) +
            "\n\nCONTEXT: resolved historical incidents (most similar first)\n\n" +
            "\n\n".join(_context_block(d) for d in docs))
    parsed, meta = await nim.chat_json([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], SCHEMA)

    # ---- validate: citations must point at retrieved incidents (citation correctness)
    allowed = {d["id"] for d in docs}
    steps, dropped = [], 0
    for s in parsed.get("steps") or []:
        srcs = [x for x in dict.fromkeys(s.get("sources") or []) if x in allowed]
        dropped += len(s.get("sources") or []) - len(srcs)
        text = (s.get("text") or "").strip()
        if not text or not srcs:
            continue                                     # ungrounded step: discard
        steps.append({"id": f"S{len(steps) + 1}", "text": text[:400], "rationale": (s.get("rationale") or "").strip()[:300],
                      "confidence": round(max(0.0, min(1.0, float(s.get("confidence") or 0))), 2),
                      "sources": srcs, "kind": "resolution"})
    if dropped:
        warnings.append(f"{dropped} citation(s) to incidents outside the retrieved set were removed.")
    # Calibrate: the LLM's self-reported confidence alone is not reliable, so blend it with
    # retrieval evidence (similarity of the cited incidents) and support (how many cite it).
    if req.hitScores:
        for s in steps:
            sim = max(req.hitScores.get(x, 0.0) for x in s["sources"])
            support = min(1.0, len(s["sources"]) / 3)
            s["modelConfidence"] = s["confidence"]
            s["confidence"] = round(0.4 * s["confidence"] + 0.45 * sim + 0.15 * support, 2)
        steps.sort(key=lambda s: -s["confidence"])
        for i, s in enumerate(steps):
            s["id"] = f"S{i + 1}"
    if not steps:
        warnings.append("The model could not ground any step in the retrieved incidents.")

    ttr = [(d["resolved"] - d["reported"]) / 3600 for d in docs if d.get("resolved") and d.get("reported") and d["resolved"] >= d["reported"]]
    conf = round(sum(s["confidence"] for s in steps[:3]) / min(3, len(steps)), 2) if steps else 0
    return {
        "steps": steps,
        "group": parsed.get("assignment_group") or docs[0].get("group", ""),
        "category": parsed.get("category") or "",
        "product": parsed.get("product") or "",
        "etaHours": round(statistics.median(ttr), 2) if ttr else None,
        "confidence": conf,
        "warnings": warnings,
        "notes": (parsed.get("notes") or "").strip()[:300],
        "timings": {"llmMs": meta["llmMs"]},
        "usage": meta.get("usage"),
        "model": {"llm": meta.get("model"), "guided": settings.llm_guided},
    }


# ------------------------------------------------------------------ index one incident
@router.post("/index")
async def index_one(req: IndexReq):
    t = req.ticket
    if not t.get("id") or not (t.get("summary") or t.get("details")):
        raise HTTPException(422, "ticket needs id and summary/details")
    t.setdefault("origin", "servicenow")
    vec = (await nim.embed([passage_text(t)], "passage"))[0]
    if not store.exists():
        store.create(len(vec))
    store.upsert([to_row(t, vec)])
    return {"indexed": t["id"], "count": store.count()}


def load_tickets(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [{**t, "origin": t.get("origin") or "helix"} for t in data["tickets"]]

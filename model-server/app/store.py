"""Milvus vector store for the ITSM knowledge index.

One entity per incident (Helix archive + ServiceNow). The embedded text is the
incident's symptom + classification + resolution; scalar fields allow filtering
(which sources to search, only resolved tickets, exclude the ticket being replayed);
the full record is kept in a JSON field so citations can be shown without a second
system of record.
"""
import json
import threading

from pymilvus import DataType, MilvusClient

from .config import settings

_lock = threading.Lock()
_client = None


def client():
    global _client
    with _lock:
        if _client is None:
            kw = {"uri": settings.milvus_uri}
            if settings.milvus_token:
                kw["token"] = settings.milvus_token
            _client = MilvusClient(**kw)
    return _client


def exists():
    return client().has_collection(settings.collection)


def count():
    if not exists():
        return 0
    res = client().query(settings.collection, filter="", output_fields=["count(*)"])
    return int(res[0]["count(*)"]) if res else 0


def create(dim, drop=False):
    c = client()
    if drop and c.has_collection(settings.collection):
        c.drop_collection(settings.collection)
    if c.has_collection(settings.collection):
        return
    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)       # INC number
    schema.add_field("origin", DataType.VARCHAR, max_length=16)                    # "helix" | "servicenow"
    schema.add_field("status", DataType.VARCHAR, max_length=24)
    schema.add_field("has_resolution", DataType.BOOL)
    schema.add_field("assigned_group", DataType.VARCHAR, max_length=128)
    schema.add_field("reported", DataType.INT64)
    schema.add_field("resolved", DataType.INT64)
    schema.add_field("text", DataType.VARCHAR, max_length=8192)                    # what was embedded
    schema.add_field("doc", DataType.JSON)                                         # full ticket record
    schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=dim)
    idx = c.prepare_index_params()
    params = {"M": 16, "efConstruction": 200} if settings.index_type == "HNSW" else {}
    idx.add_index("embedding", index_type=settings.index_type, metric_type="COSINE", params=params)
    c.create_collection(settings.collection, schema=schema, index_params=idx)


def upsert(rows):
    client().upsert(settings.collection, rows)


def search(vector, k, sources, exclude_id=None):
    """Cosine top-k over resolved incidents from the selected sources."""
    allowed = [s for s in sources if s in ("helix", "servicenow")] or ["helix", "servicenow"]
    expr = f"has_resolution == true and origin in {json.dumps(allowed)}"
    if exclude_id:
        expr += f" and id != {json.dumps(exclude_id)}"
    params = {"metric_type": "COSINE", "params": {"ef": max(64, k * 8)}} if settings.index_type == "HNSW" else {"metric_type": "COSINE"}
    res = client().search(settings.collection, data=[vector], limit=k, filter=expr,
                          search_params=params, output_fields=["id", "origin", "doc", "text"])
    return [{"id": h["entity"]["id"], "score": float(h["distance"]), "origin": h["entity"]["origin"],
             "doc": h["entity"]["doc"], "text": h["entity"]["text"]} for h in res[0]]


def get(ids):
    if not ids:
        return []
    rows = client().get(settings.collection, ids=list(ids), output_fields=["id", "origin", "doc", "text"])
    by = {r["id"]: r for r in rows}
    return [by[i] for i in ids if i in by]

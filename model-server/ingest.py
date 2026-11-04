#!/usr/bin/env python3
"""(Re)build the Milvus ticket index from webapp/data/tickets.json.

    # inside the stack
    docker compose --profile ai run --rm gateway python ingest.py --recreate
    # or locally
    MILVUS_URI=... EMBED_BASE_URL=... python ingest.py --tickets ../webapp/data/tickets.json
"""
import argparse
import asyncio

from app import itsm, store
from app.config import settings


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickets", default=settings.tickets_path)
    ap.add_argument("--recreate", action="store_true", help="drop and rebuild the collection")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    tickets = itsm.load_tickets(a.tickets)
    if a.limit:
        tickets = tickets[: a.limit]
    print(f"embedding {len(tickets)} incidents with {settings.embed_model} -> Milvus {settings.milvus_uri} [{settings.collection}]")
    res = await itsm.index_tickets(tickets, recreate=a.recreate)
    print(f"done: {res} · collection now holds {store.count()} entities")


if __name__ == "__main__":
    asyncio.run(main())

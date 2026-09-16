"""Preview or reset only MediPet demo storage; restart the API to restore defaults.

Stop the API before execution so active requests cannot recreate data mid-reset.
The normal application startup remains responsible for default initialization.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import chromadb
from dotenv import load_dotenv
from redis.asyncio import Redis


REDIS_PREFIX = "medipet:"
CHROMA_COLLECTIONS = ("medipet_knowledge", "medipet_episodic", "medipet_profiles")


async def reset_demo(redis_client: Any, chroma_client: Any, *, execute: bool = False) -> dict:
    """Delete only the fixed demo namespace, with both connections checked first."""
    await redis_client.ping()
    await asyncio.to_thread(chroma_client.heartbeat)
    keys = [key async for key in redis_client.scan_iter(match=f"{REDIS_PREFIX}*")]
    # Chroma 0.5 returns Collection objects; newer clients may return names.
    existing = await asyncio.to_thread(chroma_client.list_collections)
    names = {item if isinstance(item, str) else item.name for item in existing}
    collections = [name for name in CHROMA_COLLECTIONS if name in names]
    deleted_keys = 0
    if execute:
        for offset in range(0, len(keys), 500):
            deleted_keys += await redis_client.delete(*keys[offset:offset + 500])
        for name in collections:
            await asyncio.to_thread(chroma_client.delete_collection, name=name)
    return {
        "mode": "execute" if execute else "preview",
        "redis_prefix": REDIS_PREFIX,
        "matched_redis_keys": len(keys),
        "deleted_redis_keys": deleted_keys,
        "matched_chroma_collections": collections,
        "deleted_chroma_collections": collections if execute else [],
        "restart_api_to_initialize": execute,
    }


async def _run(execute: bool) -> dict:
    # Use the same connection variables as the API. Never print connection URLs.
    load_dotenv()
    redis_client = Redis.from_url(
        os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True,
        socket_connect_timeout=5, socket_timeout=5,
    )
    try:
        chroma_client = chromadb.HttpClient(
            host=os.getenv("CHROMA_HOST", "chromadb"),
            port=int(os.getenv("CHROMA_PORT", "8000")),
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
        return await reset_demo(redis_client, chroma_client, execute=execute)
    finally:
        await redis_client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="delete only the fixed MediPet demo keys and collections")
    args = parser.parse_args()
    try:
        result = asyncio.run(_run(args.execute))
    except Exception as exc:
        # Exception messages can include connection credentials; keep CLI output safe.
        print(f"MediPet reset failed ({type(exc).__name__}); check storage connections and retry.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The standalone reset targets only the established demo namespace."""
from types import SimpleNamespace

import pytest

from scripts.reset_demo import CHROMA_COLLECTIONS, reset_demo


class ResetRedis:
    def __init__(self):
        self.keys = {"medipet:visit:one", "medipet:slot:one", "other:marker", "medipet_other:marker"}
        self.delete_calls = []

    async def ping(self):
        return True

    async def scan_iter(self, *, match):
        assert match == "medipet:*"
        for key in sorted(self.keys):
            if key.startswith("medipet:"):
                yield key

    async def delete(self, *keys):
        self.delete_calls.append(keys)
        before = len(self.keys)
        self.keys.difference_update(keys)
        return before - len(self.keys)


class ResetChroma:
    def __init__(self, *, names_only=False, unavailable=False):
        self.names = {*CHROMA_COLLECTIONS, "other_collection", "medipet_external", "medipet_eval_fixture_profiles"}
        self.names_only = names_only
        self.unavailable = unavailable
        self.deleted = []

    def heartbeat(self):
        if self.unavailable:
            raise RuntimeError("not available")
        return 1

    def list_collections(self):
        return sorted(self.names) if self.names_only else [SimpleNamespace(name=name) for name in sorted(self.names)]

    def delete_collection(self, *, name):
        self.names.remove(name)
        self.deleted.append(name)


@pytest.mark.asyncio
async def test_preview_never_mutates_demo_or_foreign_storage():
    redis, chroma = ResetRedis(), ResetChroma()
    result = await reset_demo(redis, chroma)
    assert result["matched_redis_keys"] == 2 and result["deleted_redis_keys"] == 0
    assert set(result["matched_chroma_collections"]) == set(CHROMA_COLLECTIONS)
    assert not result["deleted_chroma_collections"] and not result["restart_api_to_initialize"]
    assert not redis.delete_calls and not chroma.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("names_only", [False, True])
async def test_execution_preserves_foreign_data_and_repeated_reset_is_empty(names_only):
    redis, chroma = ResetRedis(), ResetChroma(names_only=names_only)
    result = await reset_demo(redis, chroma, execute=True)
    assert result["deleted_redis_keys"] == 2 and result["restart_api_to_initialize"]
    assert redis.keys == {"other:marker", "medipet_other:marker"}
    assert chroma.names == {"other_collection", "medipet_external", "medipet_eval_fixture_profiles"}
    repeated = await reset_demo(redis, chroma, execute=True)
    assert repeated["matched_redis_keys"] == 0 and repeated["deleted_chroma_collections"] == []


@pytest.mark.asyncio
async def test_chroma_connection_failure_prevents_redis_deletion():
    redis, chroma = ResetRedis(), ResetChroma(unavailable=True)
    with pytest.raises(RuntimeError, match="not available"):
        await reset_demo(redis, chroma, execute=True)
    assert "medipet:visit:one" in redis.keys and not redis.delete_calls

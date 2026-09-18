from types import SimpleNamespace

import pytest

from mcp.tool_manager import MCPToolManager


@pytest.mark.asyncio
async def test_reranker_receives_body_even_when_source_metadata_is_long():
    calls = []
    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="[1, 0]")])
    manager = MCPToolManager.__new__(MCPToolManager)
    manager._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    manager._model = "test"
    items = [{"chunk_id": "x" * 250, "doc_id": "doc", "source": "y" * 250,
              "title": "说明", "content": "关键信息在正文末尾：取消方案须明确确认。"},
             {"chunk_id": "other", "content": "第二份资料"}]
    ranked, applied, error = await manager._rerank("怎么取消", items, 1)
    assert "取消方案须明确确认" in calls[0]["messages"][0]["content"]
    assert applied and error is None and ranked == [items[1]]

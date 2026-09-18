"""Paired direct-vs-rewrite retrieval evaluation on an isolated real Chroma collection."""
import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from mcp.knowledge_base import KnowledgeBase
from mcp.tool_manager import MCPToolManager, Tool


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def metrics(items, required):
    ids = {item.get("doc_id") for item in items}
    hits = len(ids & set(required))
    return {"recall_at_3": hits / len(required), "hit_at_3": int(hits > 0),
            "all_required_at_3": int(hits == len(required)), "doc_ids": sorted(ids)}


async def run(inputs_path, output):
    output.mkdir(parents=True, exist_ok=False)
    inputs = json.loads(inputs_path.read_text(encoding="utf-8"))["retrieval"]
    save(output / "inputs.json", inputs)
    collection = "medipet_retrieval_metrics_" + uuid4().hex
    KnowledgeBase.COLLECTION_NAME = collection
    knowledge = KnowledgeBase(chroma_host=os.environ["CHROMA_HOST"], chroma_port=int(os.environ["CHROMA_PORT"]))
    if not knowledge._use_server:
        raise RuntimeError("Real Chroma required")
    manager = MCPToolManager(api_key=os.environ["ANTHROPIC_API_KEY"], base_url=os.getenv("ANTHROPIC_BASE_URL"), model=os.environ["ANTHROPIC_MODEL"])
    manager.register(Tool(name="knowledge_search", description="检索就诊资料", handler=knowledge.search_handler,
                          schema={"type":"object", "properties":{"query":{"type":"string"},"top_k":{"type":"integer"}},"required":["query"]},
                          cache_ttl=300, supports_rerank=True))
    calls = [0]
    original = manager._client.messages.create

    async def counted(**kwargs):
        calls[0] += 1
        return await original(**kwargs)

    manager._client.messages.create = counted
    configuration = {"started_at":datetime.now(timezone.utc).isoformat(), "collection":collection,"top_k":3,
                     "model":os.environ["ANTHROPIC_MODEL"],"thinking":os.getenv("MEDIPET_THINKING"),
                     "embedding":"Chroma default all-MiniLM-L6-v2; unchanged in both arms", "chunks":knowledge.doc_count,
                     "script_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                     "sources":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path("mcp/knowledge_base.py"),Path("mcp/tool_manager.py"),*sorted(Path("knowledge").glob("*.md"))]}}
    save(output / "configuration.json",configuration)
    rows=[]
    try:
        available = {m["doc_id"] for m in knowledge._collection.get(include=["metadatas"])["metadatas"]}
        assert len(inputs)==40 and all(set(c["required_doc_ids"])<=available for c in inputs)
        for index, case in enumerate(inputs,1):
            row={"id":case["id"],"query":case["query"],"required_doc_ids":case["required_doc_ids"]}
            start=time.monotonic()
            try:
                direct=await knowledge.search_async(case["query"],top_k=3)
                row["direct"]={"success":True,"items":direct,"metrics":metrics(direct,case["required_doc_ids"]),"elapsed_seconds":time.monotonic()-start}
            except Exception as exc:
                row["direct"]={"success":False,"error_type":type(exc).__name__,"metrics":metrics([],case["required_doc_ids"])}
            start=time.monotonic()
            before_calls=calls[0]
            # Empty manager cache at each pair: identical corpus, explicit no cross-question cache benefit.
            manager._cache.clear()
            try:
                enhanced=await manager.search_with_rewrite("knowledge_search",case["query"],top_k=3)
                row["enhanced"]={"result":asdict(enhanced),"success":enhanced.success,
                                  "metrics":metrics(enhanced.data if enhanced.success else [],case["required_doc_ids"]),
                                  "elapsed_seconds":time.monotonic()-start,"model_calls":calls[0]-before_calls}
            except Exception as exc:
                row["enhanced"]={"success":False,"error_type":type(exc).__name__,"metrics":metrics([],case["required_doc_ids"]),"model_calls":calls[0]-before_calls}
            rows.append(row)
            save(output/(case["id"]+".json"),row)
            print(json.dumps({"index":index,"id":case["id"],"direct":row["direct"]["metrics"]["recall_at_3"],"enhanced":row["enhanced"]["metrics"]["recall_at_3"]}),flush=True)
    finally:
        await manager._client.close()
        knowledge._client.delete_collection(collection)
        configuration.update(finished_at=datetime.now(timezone.utc).isoformat(), model_calls=calls[0],owned_collection_deleted=collection not in {c.name for c in knowledge._client.list_collections()})
        save(output/"configuration.json",configuration)
        summary={}
        for arm in ["direct","enhanced"]:
            summary[arm]={key:sum(r[arm]["metrics"][key] for r in rows)/len(inputs) for key in ["recall_at_3","hit_at_3","all_required_at_3"]}
            summary[arm].update(total=len(inputs),observed=len(rows),failed=[r["id"] for r in rows if not r[arm]["success"]],
                                misses=[r["id"] for r in rows if r[arm]["metrics"]["all_required_at_3"]!=1])
        summary["recall_gain_percentage_points"]=(summary["enhanced"]["recall_at_3"]-summary["direct"]["recall_at_3"])*100
        summary["enhanced_degradations"]=[{"id":r["id"],"rewrite_error":r["enhanced"].get("result",{}).get("rewrite_error"),"rerank_error":r["enhanced"].get("result",{}).get("rerank_error"),"partial":r["enhanced"].get("result",{}).get("partial")} for r in rows if any(r["enhanced"].get("result",{}).get(k) for k in ["rewrite_error","rerank_error","partial"])]
        save(output/"summary.json",summary)
        print(json.dumps(summary),flush=True)


if __name__=="__main__":
    asyncio.run(run(Path(sys.argv[1]),Path(sys.argv[2])))

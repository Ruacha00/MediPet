"""Paired old/new retrieval on isolated collections, with frozen answer snapshots."""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from mcp.knowledge_base import KnowledgeBase
from mcp.tool_manager import MCPToolManager, Tool


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


async def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    old_kb = module(args.baseline / "knowledge_base.py", "baseline_knowledge_base")
    old_tm = module(args.baseline / "tool_manager.py", "baseline_tool_manager")
    for name in ("knowledge_base", "tool_manager"):
        shutil.copyfile(args.baseline / (name + ".py"), args.output / ("baseline_" + name + ".py.txt"))
    known = json.loads(args.retrieval_cases.read_text(encoding="utf-8"))["retrieval"]
    answer = json.loads(args.answer_cases.read_text(encoding="utf-8"))["cases"]
    cases = [dict(c, group="retrieval") for c in known] + [
        {"id": c["id"], "query": c["question"], "required_doc_ids": c["source_doc_ids"], "group": "answer"}
        for c in answer
    ]
    save(args.output / "inputs.json", cases)
    snapshots = {"schema_version": 1, "arms": {"current": {}, "improved": {}}}
    configurations = {"model": os.environ["ANTHROPIC_MODEL"], "top_k": 3, "retries": 0,
                      "arms": {}, "sources": {}}
    for path in [Path("mcp/knowledge_base.py"), Path("mcp/tool_manager.py"),
                 *Path("mcp").glob("*retrieval*.py"), *sorted(Path("knowledge").glob("*.md"))]:
        configurations["sources"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    resources = {}
    rows = []
    try:
        for arm, kb_cls, manager_cls, tool_cls in (
            ("current", old_kb.KnowledgeBase, old_tm.MCPToolManager, old_tm.Tool),
            ("improved", KnowledgeBase, MCPToolManager, Tool),
        ):
            kb_cls.COLLECTION_NAME = "medipet_recall_pair_" + uuid4().hex
            kb = kb_cls(chroma_host=os.environ["CHROMA_HOST"], chroma_port=int(os.environ["CHROMA_PORT"]),
                        knowledge_dir=str(Path("knowledge").resolve()))
            if not kb._use_server:
                raise RuntimeError("Real Chroma required")
            manager = manager_cls(api_key=os.environ["ANTHROPIC_API_KEY"],
                                  base_url=os.getenv("ANTHROPIC_BASE_URL"), model=os.environ["ANTHROPIC_MODEL"])
            manager._client.max_retries = 0
            manager.register(tool_cls(name="knowledge_search", description="检索就诊资料", handler=kb.search_handler,
                schema={"type":"object", "properties":{"query":{"type":"string"},"top_k":{"type":"integer"}},"required":["query"]},
                cache_ttl=0, supports_rerank=True))
            resources[arm] = (kb, manager)
            configurations["arms"][arm] = {"collection": kb_cls.COLLECTION_NAME, "chunks": kb.doc_count}
        save(args.output / "configuration.json", configurations)
        for index, case in enumerate(cases):
            row = {**case, "arms": {}}
            order = ("current", "improved") if index % 2 == 0 else ("improved", "current")
            for arm in order:
                kb, manager = resources[arm]
                save(args.output / "in-flight.json", {"id":case["id"], "arm":arm})
                try:
                    result = await manager.search_with_rewrite("knowledge_search", case["query"], top_k=3)
                    value = {"success": result.success, "results": result.data if result.success else [],
                             "metadata": {k:v for k,v in asdict(result).items() if k != "data"}}
                except Exception as exc:
                    value = {"success": False, "results": [], "error": type(exc).__name__}
                row["arms"][arm] = value
                if case["group"] == "answer":
                    snapshots["arms"][arm][case["id"]] = value
                save(args.output / (case["id"] + ".json"), row)
                save(args.output / "answer-retrievals.json", snapshots)
            rows.append(row)
            print(json.dumps({"index": index + 1, "id":case["id"], "success": [row["arms"][a]["success"] for a in order]}), flush=True)
    finally:
        for arm, (kb, manager) in resources.items():
            await manager._client.close()
            kb._client.delete_collection(kb.COLLECTION_NAME)
            configurations["arms"][arm]["owned_collection_deleted"] = kb.COLLECTION_NAME not in {c.name for c in kb._client.list_collections()}
        save(args.output / "configuration.json", configurations)
        summary = {}
        for arm in ("current", "improved"):
            retrieval_rows = [r for r in rows if r["group"] == "retrieval"]
            recalls = []
            for row in retrieval_rows:
                actual = {r.get("doc_id") for r in row["arms"][arm]["results"]}
                recalls.append(len(actual & set(row["required_doc_ids"])) / len(row["required_doc_ids"]))
            summary[arm] = {"total":len(known), "observed":len(retrieval_rows),
                            "recall_at_3":sum(recalls)/len(known),
                            "hit_at_3":sum(r > 0 for r in recalls)/len(known),
                            "all_required_at_3":sum(r == 1 for r in recalls)/len(known),
                            "failures": [r["id"] for r in rows if not r["arms"].get(arm, {}).get("success")],
                            "degraded": [r["id"] for r in rows if any(r["arms"][arm].get("metadata",{}).get(k) for k in ("rewrite_error","rerank_error","partial"))]}
        save(args.output / "summary.json", summary)
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--retrieval-cases", type=Path, required=True)
    parser.add_argument("--answer-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))

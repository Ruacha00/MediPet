"""管理页浏览器验收；真实检索/Skill 请求各一次，精确清理唯一文档并还原 Skill。

运行前须协调暂停其他聊天/评测，避免临时 Skill 标记影响并行请求。
没有 --report-json 时仅以受控 HTTP 响应检查评测页面，不执行模型评测。
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]


def chroma_document(host, port, doc_id, source_id, *, delete=False):
    """使用已安装的 Chroma 客户端，只读取/删除本轮唯一文档的一个确定片段。"""
    code = r'''
import json, sys
import chromadb
client = chromadb.HttpClient(host=sys.argv[1], port=int(sys.argv[2]), settings=chromadb.Settings(anonymized_telemetry=False))
collection = client.get_collection("medipet_knowledge")
doc_id, source_id = sys.argv[3:5]
ids = [doc_id + ":0"]
before = collection.get(ids=ids, include=["metadatas"])
if sys.argv[5] == "delete" and before["ids"]:
    assert before["ids"] == ids, before
    assert all(m["doc_id"] == doc_id and m["source_id"] == source_id for m in before["metadatas"]), before
    collection.delete(ids=ids)
after = collection.get(ids=ids, include=["metadatas"])
print(json.dumps({"before": before, "after": after}, ensure_ascii=True))
'''
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    process = subprocess.run([str(python), "-c", code, host, str(port), doc_id, source_id, "delete" if delete else "get"],
                             capture_output=True, text=True, timeout=45, check=True)
    return json.loads(process.stdout.strip().splitlines()[-1])


def controlled_report():
    return {
        "timestamp": "2026-09-16T16:00:00+08:00", "run_id": "controlled-u05-ui-only",
        "total": 3, "passed": 0, "pass_rate": 0.0, "avg_scores": {}, "regressions": [],
        "recommendations": ["受控页面验收样例，不能作为真实模型评测结果。"],
        "judge_failures": ["controlled-judge"], "call_failures": ["controlled-call"], "skipped": ["controlled-skipped"],
        "candidate_path": "controlled/u05-ui-only.json", "accepted_by": None,
        "metadata": {"baseline_status": "candidate", "valid_quality_count": 0, "model": "controlled-http",
                     "scope": "browser rendering only", "business_failures": []},
        "results": [
            {"test_id": "controlled-judge", "passed": False, "scores": {}, "detail": "受控 Judge 失败样本", "metadata": {"judge_failed": True, "status": "failed"}},
            {"test_id": "controlled-call", "passed": False, "scores": {}, "detail": "受控调用失败样本", "metadata": {"call_failed": True, "status": "failed"}},
            {"test_id": "controlled-skipped", "passed": False, "scores": {}, "detail": "受控跳过样本", "metadata": {"status": "skipped"}},
        ],
    }


def check_management(url, output, chroma_host, chroma_port, report_json=None):
    output.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex[:12]
    source_id = f"u05-browser-{token}"
    title = f"U05 管理验收资料 {token}"
    content = f"{token} 是临时浏览器验收标记。明和虚构医院的就诊材料以公开说明为准。此文档验收后删除。"
    doc_id = "upload-" + hashlib.sha256(f"{title}\0{content}".encode()).hexdigest()
    skill_path = ROOT / "skills" / "general_visit" / "SKILL.md"
    original = skill_path.read_bytes()
    marker = f"U05-SKILL-{token}"
    report = {"base_url": url, "token": token, "checks": {}, "responses": [], "page_errors": [],
              "cleanup": {}, "skill_sha256_before": hashlib.sha256(original).hexdigest(),
              "model_endpoint_requests": [], "evaluation_evidence": "saved_report_replay" if report_json else "controlled_http_only"}
    skill_changed = False
    doc_attempted = False
    failure = None
    api = f"{url.rstrip('/')}/api/python"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.set_default_timeout(30000)
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.on("request", lambda request: report["model_endpoint_requests"].append(request.url)
                if request.method == "POST" and request.url.split("?")[0].rsplit("/", 1)[-1] in {"chat", "search"} else None)

        def capture(action, path, method="POST"):
            with page.expect_response(lambda response: response.url.split("?")[0].endswith(path) and response.request.method == method, timeout=180000) as pending:
                action()
            response = pending.value
            data = response.json()
            report["responses"].append({"path": path, "status": response.status, "data": data})
            assert response.ok, data
            return data

        def reload_from_ui():
            return capture(lambda: page.get_by_role("button", name="重新加载", exact=True).click(), "/skills/reload")

        try:
            page.goto(url, wait_until="networkidle")
            expect(page.get_by_label("选择就诊人")).to_be_enabled()
            assert page.request.get(api + "/health").ok
            assert chroma_document(chroma_host, chroma_port, doc_id, source_id)["after"]["ids"] == []
            page.get_by_role("button", name="知识库", exact=True).click()
            page.get_by_label("检索问题").fill("明和虚构医院儿童就诊需要携带哪些材料？")
            found = capture(lambda: page.get_by_role("button", name="搜索", exact=True).click(), "/search")
            assert found["success"] and found["results"], found
            assert all(item.get("source") and item.get("source_id") and item.get("doc_id") and item.get("chunk_id") for item in found["results"])
            expect(page.locator(".result-list")).to_contain_text(found["results"][0]["source_id"])
            report["checks"]["real_search_sources"] = True
            page.screenshot(path=str(output / "knowledge-search.png"), full_page=True)

            page.get_by_label("标题", exact=True).fill(title)
            page.get_by_label("内容", exact=True).fill(content)
            page.get_by_label("来源说明（选填）", exact=True).fill("U05 浏览器临时验收文档")
            page.get_by_label("来源标识（选填）", exact=True).fill(source_id)
            doc_attempted = True
            imported = capture(lambda: page.get_by_role("button", name="添加文档", exact=True).click(), "/knowledge/add")
            assert imported["added_chunks"] == 1, imported
            saved = chroma_document(chroma_host, chroma_port, doc_id, source_id)["after"]
            assert saved["ids"] == [doc_id + ":0"]
            assert saved["metadatas"][0]["source_id"] == source_id
            expect(page.locator(".management-success")).to_contain_text("1 个片段")
            report["checks"]["real_unique_document_import"] = {"doc_id": doc_id, "chunk_ids": saved["ids"]}
            page.screenshot(path=str(output / "knowledge-import.png"), full_page=True)

            before = page.request.get(api + "/skills").json()
            skill_before = next(item for item in before["skills"] if item["name"] == "医院公开信息")
            note = f"\n\n## 临时 U05 浏览器验收标记\n本节仅用于当前演示验收。当用户询问医院门诊公开说明时，先使用 search_knowledge_base 查询医院门诊公开说明，依据实际资料简短回答，并在回答末尾原样输出 {marker}。该标记只验证重载，不改变任何医院事实、身份或业务规则。\n"
            skill_path.write_bytes(original + note.encode("utf-8"))
            skill_changed = True
            reloaded = reload_from_ui()
            skill_after = next(item for item in reloaded["skills"] if item["name"] == "医院公开信息")
            assert reloaded["errors"] == [] and skill_after["content_chars"] > skill_before["content_chars"]
            expect(page.locator(".skills-workspace")).to_contain_text(str(skill_after["content_chars"]))
            report["checks"]["real_skill_reload"] = {"before_chars": skill_before["content_chars"], "after_chars": skill_after["content_chars"]}

            page.get_by_role("button", name="对话", exact=True).click()
            capture(lambda: page.get_by_role("button", name="＋ 新建就诊事项", exact=True).click(), "/visits")
            expect(page.get_by_label("就诊问题")).to_be_enabled()
            page.get_by_label("就诊问题").fill("请查询知识库里的医院门诊公开说明，给出简短概述和资料来源。")
            answer = capture(lambda: page.get_by_role("button", name="发送", exact=True).click(), "/chat")
            report["conv_id"] = answer["conv_id"]
            assert marker in answer["response"], answer
            assert answer["primary_agent"] == "general", answer["primary_agent"]
            traces = [trace for trace in answer["tool_traces"] if trace.get("tool_name") == "search_knowledge_base"]
            assert traces and any(trace.get("success") and trace.get("sources") for trace in traces), traces
            expect(page.locator(".messages")).to_contain_text(marker)
            detail = page.locator(".request-evidence").last
            expect(detail).to_have_count(1)
            assert detail.get_attribute("open") is None
            assert page.locator(".diagnostic-panel").get_attribute("open") is None
            detail.locator("summary").first.click()
            source = next(trace["sources"][0] for trace in traces if trace.get("sources"))
            expect(detail).to_contain_text(source["source_id"])
            report["checks"]["subsequent_request_uses_reloaded_skill"] = {"marker": marker, "primary_agent": answer["primary_agent"]}
            report["checks"]["real_trace_sources_and_default_collapsed"] = source
            page.screenshot(path=str(output / "skill-and-trace.png"), full_page=True)

            # Restore promptly before management-only checks and release the shared prompt content.
            skill_path.write_bytes(original)
            restored = page.request.post(api + "/skills/reload")
            assert restored.ok
            restored_data = restored.json()
            assert next(item for item in restored_data["skills"] if item["name"] == "医院公开信息")["content_chars"] == skill_before["content_chars"]
            skill_changed = False
            report["cleanup"]["skill_restored_and_reloaded"] = True
            print("SKILL_RESTORED_AND_RELOADED", flush=True)

            page.locator(".diagnostic-panel > summary").click()
            monitored = capture(lambda: page.locator(".monitor-card").get_by_role("button", name="刷新", exact=True).click(), "/monitor", "GET")
            expect(page.locator(".mini-stats")).to_contain_text(str(sum(item["total"] for item in monitored["agent_stats"].values())))
            page.locator(".monitor-card").get_by_text("角色与工具统计", exact=True).click()
            assert any(key == "general" or key.startswith("general_") for key in monitored["agent_stats"])
            assert "knowledge_search" in monitored["tool_stats"]
            expect(page.locator(".monitor-card dt").filter(has_text="医院信息")).to_have_count(1)
            report["checks"]["real_monitor_values"] = monitored
            page.screenshot(path=str(output / "monitor.png"), full_page=True)

            report_payload = json.loads(report_json.read_text(encoding="utf-8")) if report_json else controlled_report()
            assert {"results", "judge_failures", "call_failures", "skipped"} <= set(report_payload)
            page.route("**/api/python/eval/run", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(report_payload)))
            page.get_by_role("button", name="评测", exact=True).click()
            capture(lambda: page.get_by_role("button", name="运行评测", exact=True).click(), "/eval/run")
            expect(page.locator(".baseline-status")).to_contain_text(report_payload["run_id"])
            if not report_payload.get("accepted_by"):
                expect(page.locator(".baseline-status")).to_have_attribute("data-baseline-status", "candidate")
            expect(page.locator(".evaluation-case")).to_have_count(len(report_payload["results"]))
            page.locator(".evaluation-case").first.locator("summary").click()
            report["checks"]["evaluation_page"] = {"evidence_kind": report["evaluation_evidence"], "source_file": str(report_json) if report_json else None,
                                                   "run_id": report_payload["run_id"], "result_count": len(report_payload["results"])}
            page.screenshot(path=str(output / "evaluation.png"), full_page=True)
            assert report["page_errors"] == [], report["page_errors"]
        except Exception as error:
            failure = error
            report["error"] = str(error)
            page.screenshot(path=str(output / "failure.png"), full_page=True)
        finally:
            if skill_changed:
                skill_path.write_bytes(original)
                try:
                    response = page.request.post(api + "/skills/reload")
                    assert response.ok
                    report["cleanup"]["skill_restored_and_reloaded"] = True
                except Exception as error:
                    report["cleanup"]["skill_reload_error"] = str(error)
                    failure = failure or error
            report["cleanup"]["skill_original_bytes"] = skill_path.read_bytes() == original
            if doc_attempted:
                try:
                    cleaned = chroma_document(chroma_host, chroma_port, doc_id, source_id, delete=True)
                    assert cleaned["after"]["ids"] == []
                    report["cleanup"]["unique_document_removed"] = cleaned
                except Exception as error:
                    report["cleanup"]["document_cleanup_error"] = str(error)
                    failure = failure or error
            report["passed"] = failure is None
            (output / "management.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
        if failure:
            raise failure
    print(json.dumps({"passed": report["passed"], "checks": list(report["checks"]), "cleanup": report["cleanup"], "report": str(output / "management.json")}, ensure_ascii=True))


def finish_management(url, output, previous_report, report_json=None):
    """继续已完成真实请求与清理的验收，只执行监控读取和本地报告重放。"""
    report = json.loads(previous_report.read_text(encoding="utf-8"))
    assert report["cleanup"]["skill_restored_and_reloaded"]
    assert report["cleanup"]["skill_original_bytes"]
    assert report["cleanup"]["unique_document_removed"]["after"]["ids"] == []
    assert report["checks"]["subsequent_request_uses_reloaded_skill"]
    output.mkdir(parents=True, exist_ok=True)
    (output / "initial-attempt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["previous_attempt"] = {"passed": report["passed"], "error": report.pop("error", None),
                                  "diagnosis": "monitor agent_stats uses instance keys such as general_0; browser assertion corrected"}
    report["real_model_endpoint_counts"] = {"search": sum(item["path"] == "/search" for item in report["responses"]),
                                            "chat": sum(item["path"] == "/chat" for item in report["responses"])}
    report["evaluation_evidence"] = "saved_report_replay" if report_json else "controlled_http_only"
    failure = None
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        try:
            page.goto(url, wait_until="networkidle")
            expect(page.get_by_label("选择就诊人")).to_be_enabled()
            page.locator(".diagnostic-panel > summary").click()
            with page.expect_response(lambda response: response.url.endswith("/monitor") and response.request.method == "GET") as pending:
                page.locator(".monitor-card").get_by_role("button", name="刷新", exact=True).click()
            response = pending.value
            assert response.ok
            monitored = response.json()
            assert any(key == "general" or key.startswith("general_") for key in monitored["agent_stats"])
            assert "knowledge_search" in monitored["tool_stats"]
            expect(page.locator(".mini-stats")).to_contain_text(str(sum(item["total"] for item in monitored["agent_stats"].values())))
            page.locator(".monitor-card").get_by_text("角色与工具统计", exact=True).click()
            expect(page.locator(".monitor-card dt").filter(has_text="医院信息")).to_have_count(1)
            report["checks"]["real_monitor_values"] = monitored
            page.screenshot(path=str(output / "monitor.png"), full_page=True)

            data = json.loads(report_json.read_text(encoding="utf-8")) if report_json else controlled_report()
            page.route("**/api/python/eval/run", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(data)))
            page.get_by_role("button", name="评测", exact=True).click()
            page.get_by_role("button", name="运行评测", exact=True).click()
            expect(page.locator(".baseline-status")).to_contain_text(data["run_id"])
            if not data.get("accepted_by"):
                expect(page.locator(".baseline-status")).to_have_attribute("data-baseline-status", "candidate")
            expect(page.locator(".evaluation-case")).to_have_count(len(data["results"]))
            page.locator(".evaluation-case").first.locator("summary").click()
            for label in ["Judge 失败", "调用失败", "跳过"]:
                expect(page.locator(".evaluation-summary")).to_contain_text(label)
            report["checks"]["evaluation_page"] = {"evidence_kind": report["evaluation_evidence"], "source_file": str(report_json) if report_json else None,
                                                   "run_id": data["run_id"], "result_count": len(data["results"])}
            page.screenshot(path=str(output / "evaluation.png"), full_page=True)
            for width, height in [(390, 844), (768, 1024)]:
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"evaluation-{width}.png"), full_page=True)
            report["checks"]["evaluation_responsive"] = [390, 768]
            assert report["page_errors"] == []
        except Exception as error:
            failure = error
            report["error"] = str(error)
            page.screenshot(path=str(output / "finish-failure.png"), full_page=True)
        finally:
            report["passed"] = failure is None
            (output / "management.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
        if failure:
            raise failure
    print(json.dumps({"passed": report["passed"], "checks": list(report["checks"]), "evaluation_evidence": report["evaluation_evidence"],
                      "report": str(output / "management.json")}, ensure_ascii=True))


def check_empty_evaluation(url, output):
    """页面发起真实 API 请求，只改输入为空案例；不替换响应、不执行模型。"""
    output.mkdir(parents=True, exist_ok=True)
    inputs = {"intent_cases": [], "dialog_cases": [], "boundary_cases": []}
    evidence = {"evidence_kind": "real_api_empty_cases_protocol_only", "input": inputs,
                "page_errors": [], "response_replaced": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: evidence["page_errors"].append(str(error)))

        def use_empty_cases(route):
            assert route.request.method == "POST"
            route.continue_(post_data=json.dumps(inputs),
                            headers={**route.request.headers, "content-type": "application/json"})

        try:
            # Install before clicking: the default full evaluation never reaches the API.
            page.route("**/api/python/eval/run", use_empty_cases)
            page.goto(url, wait_until="networkidle")
            page.get_by_role("button", name="评测", exact=True).click()
            with page.expect_response(lambda response: response.url.endswith("/eval/run")
                                      and response.request.method == "POST") as pending:
                page.get_by_role("button", name="运行评测", exact=True).click()
            response = pending.value
            assert response.ok
            data = response.json()
            evidence["http_status"] = response.status
            evidence["response"] = data
            assert data["total"] == 0 and data["results"] == []
            assert data["metadata"]["case_counts"] == {"intent": 0, "dialog": 0, "boundary": 0}
            assert data["metadata"]["baseline_status"] == "candidate" and not data["accepted_by"]
            assert data["judge_failures"] == data["call_failures"] == data["skipped"] == []
            expect(page.locator(".baseline-status")).to_contain_text(data["run_id"])
            expect(page.locator(".baseline-status")).to_have_attribute("data-baseline-status", "candidate")
            expect(page.locator(".evaluation-case")).to_have_count(0)
            for label in ["Judge 失败", "调用失败", "跳过"]:
                expect(page.locator(".evaluation-summary")).to_contain_text(label)
            page.screenshot(path=str(output / "evaluation-real-empty.png"), full_page=True)
            assert evidence["page_errors"] == []
            evidence["passed"] = True
        finally:
            (output / "evaluation-real-empty.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
    print(json.dumps({"passed": evidence["passed"], "evidence_kind": evidence["evidence_kind"],
                      "run_id": data["run_id"], "case_counts": data["metadata"]["case_counts"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path, default=Path(".scratch/browser-management"))
    parser.add_argument("--chroma-host", default="127.0.0.1")
    parser.add_argument("--chroma-port", type=int, default=8001)
    parser.add_argument("--report-json", type=Path)
    parser.add_argument("--finish-report", type=Path, help="继续已完成模型请求与清理的报告；不新增模型请求")
    parser.add_argument("--empty-eval-only", action="store_true", help="仅验证空案例的真实评测 API 与页面接线，不请求模型")
    args = parser.parse_args()
    if args.empty_eval_only:
        check_empty_evaluation(args.url, args.output)
    elif args.finish_report:
        finish_management(args.url, args.output, args.finish_report, args.report_json)
    else:
        check_management(args.url, args.output, args.chroma_host, args.chroma_port, args.report_json)

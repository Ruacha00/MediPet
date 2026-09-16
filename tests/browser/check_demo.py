"""真实浏览器业务验收。使用服务已配置的模型，产生并取消一条演示预约。"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def check_demo(url, output):
    output.mkdir(parents=True, exist_ok=True)
    report = {"base_url": url, "responses": [], "checks": {}, "page_errors": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))

        def capture(action, predicate):
            with page.expect_response(predicate, timeout=180000) as pending:
                action()
            response = pending.value
            data = response.json()
            report["responses"].append({"path": response.url, "status": response.status, "data": data})
            assert response.ok, data
            page.wait_for_load_state("networkidle")
            return data

        def chat(text):
            page.get_by_label("就诊问题").fill(text)
            result = capture(lambda: page.get_by_role("button", name="发送", exact=True).click(),
                             lambda r: r.url.endswith("/chat") and r.request.method == "POST")
            expect(page.get_by_role("button", name="发送", exact=True)).to_be_visible(timeout=30000)
            return result

        def history(conv):
            response = page.request.get(f"{url.rstrip('/')}/api/python/visits/{conv}/messages")
            assert response.ok
            return response.json()["items"]

        try:
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.get_by_label("选择就诊人").select_option("patient_child")
            capture(lambda: page.get_by_role("button", name="＋ 新建就诊事项", exact=True).click(),
                    lambda r: r.url.endswith("/visits") and r.request.method == "POST")
            expect(page.get_by_label("就诊问题")).to_be_enabled()
            query = chat("查一下明天儿科的号，顺便告诉我要带什么。")
            conv = query["conv_id"]
            report["conv_id"] = conv
            assert {"slot_list", "visit_checklist"} <= {a["type"] for a in query["artifacts"]}
            assert {"appointment", "guidance"} <= set(query["agent_types"])
            prepared = capture(lambda: page.get_by_role("button", name="选择此号源", exact=True).first.click(),
                               lambda r: r.url.endswith("/chat") and r.request.method == "POST")
            proposal = next(a["data"] for a in prepared["artifacts"] if a["type"] == "appointment_proposal")
            assert proposal["status"] == "pending" and proposal["operation"] == "create"
            assert all(m["kind"] == "chat" for m in history(conv))
            report["checks"]["pending_has_no_execution"] = True
            confirmed = capture(lambda: page.get_by_role("button", name="确认预约", exact=True).dblclick(),
                                lambda r: "/appointment-proposals/" in r.url and r.url.endswith("/confirm"))
            record = confirmed["receipt"]["appointment"]
            assert record["status"] == "active"
            assert "tool_traces" not in confirmed and "request_id" not in confirmed
            messages = history(conv)
            assert sum(m["kind"] == "operation_result" for m in messages) == 1
            report["checks"]["double_click_single_receipt"] = True
            page.reload()
            page.wait_for_load_state("networkidle")
            queried = chat("查看当前就诊人的预约记录。")
            assert any(a["type"] == "appointment_record" and a["data"]["appointment_id"] == record["appointment_id"]
                       and a["data"]["status"] == "active" for a in queried["artifacts"])
            directions = chat("从门诊大厅到药房怎么走？需要无障碍通道。")
            route = next(a["data"] for a in directions["artifacts"] if a["type"] == "wayfinding")
            assert route["origin_id"] == "hall" and route["destination_id"] == "pharmacy"
            assert route["mode"] == "accessible" and route["steps"]
            report["checks"]["accessible_wayfinding"] = True
            cancelled_proposal = capture(lambda: page.get_by_role("button", name="准备取消此预约", exact=True).first.click(),
                                         lambda r: r.url.endswith("/chat") and r.request.method == "POST")
            assert any(a["type"] == "appointment_proposal" and a["data"]["operation"] == "cancel"
                       and a["data"]["status"] == "pending" for a in cancelled_proposal["artifacts"])
            cancelled = capture(lambda: page.get_by_role("button", name="确认取消预约", exact=True).click(),
                                lambda r: "/appointment-proposals/" in r.url and r.url.endswith("/confirm"))
            assert cancelled["receipt"]["appointment"]["status"] == "cancelled"
            assert cancelled["receipt"]["appointment"]["appointment_id"] == record["appointment_id"]
            report["checks"]["create_query_cancel"] = True
            messages = history(conv)
            assert sum(m["kind"] == "operation_result" for m in messages) == 2
            title = f"儿童预约验收 {conv[-6:]}"
            page.locator(".visit-list li.selected").get_by_role("button", name="重命名", exact=True).click()
            page.get_by_label("新的事项名称").fill(title)
            capture(lambda: page.get_by_role("button", name="保存", exact=True).click(), lambda r: r.request.method == "PATCH")
            expect(page.get_by_label("新的事项名称")).not_to_be_visible()
            capture(lambda: page.locator(".visit-list li.selected").get_by_role("button", name="归档", exact=True).click(),
                    lambda r: r.request.method == "PATCH")
            expect(page.get_by_label("就诊问题")).to_be_disabled()
            page.get_by_role("button", name="查看归档", exact=True).click()
            capture(lambda: page.get_by_role("button", name="恢复事项", exact=True).first.click(), lambda r: r.request.method == "PATCH")
            expect(page.get_by_label("就诊问题")).to_be_enabled()
            assert history(conv) == messages
            page.get_by_label("选择就诊人").select_option("patient_self")
            expect(page.get_by_label("选择就诊人")).to_be_enabled()
            expect(page.get_by_label("事项完整记录")).not_to_contain_text(record["appointment_id"])
            page.get_by_label("选择就诊人").select_option("patient_child")
            expect(page.locator(".visit-list li.selected")).to_contain_text(title)
            page.reload()
            page.wait_for_load_state("networkidle")
            expect(page.locator(".visit-list li.selected")).to_contain_text(title)
            report["checks"]["rename_archive_restore_switch_refresh"] = True
            for width, height in ((390, 844), (768, 1024), (1440, 1000)):
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"business-{width}.png"), full_page=True)
            report["checks"]["responsive"] = True
            assert report["page_errors"] == []
            report["passed"] = True
        except Exception as error:
            report["passed"] = False
            report["error"] = str(error)
            page.screenshot(path=str(output / "failure.png"), full_page=True)
            raise
        finally:
            (output / "business.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "report": str(output / "business.json")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path, default=Path(".scratch/browser"))
    args = parser.parse_args()
    check_demo(args.url, args.output)

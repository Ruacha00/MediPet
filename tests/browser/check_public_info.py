"""通过实际页面验收医院公开信息、导诊联系和预设急症中断。"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def check_public_info(url, output):
    output.mkdir(parents=True, exist_ok=True)
    report = {"base_url": url, "responses": [], "checks": {}, "page_errors": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))

        def chat(text):
            page.get_by_label("就诊问题").fill(text)
            with page.expect_response(lambda r: r.url.endswith("/chat") and r.request.method == "POST", timeout=180000) as pending:
                page.get_by_role("button", name="发送", exact=True).click()
            response = pending.value
            assert response.ok
            result = response.json()
            report["responses"].append(result)
            page.wait_for_load_state("networkidle")
            expect(page.get_by_role("button", name="发送", exact=True)).to_be_visible()
            return result

        try:
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.get_by_label("选择就诊人").select_option("patient_self")
            page.get_by_role("button", name="＋ 新建就诊事项", exact=True).click()
            expect(page.get_by_label("就诊问题")).to_be_enabled()
            hospital = chat("请查询医院地址和门诊开放时间。")
            catalog = next(a["data"] for a in hospital["artifacts"] if a["type"] == "catalog")
            assert catalog["items"][0]["hospital_id"] == "minghe"
            assert "08:00" in hospital["response"] and "明和路1号" in hospital["response"]
            report["checks"]["hospital_public_information"] = True
            contact = chat("我想联系人工导诊，请给我联系电话和地点。")
            info = next(a["data"] for a in contact["artifacts"] if a["type"] == "contact_info")
            assert info["delivery"] == "contact_only" and info["phone"] == "010-00000000（演示号码）"
            report["checks"]["contact_only"] = True
            emergency = chat("现在呼吸困难")
            assert emergency["intent"] == "emergency" and emergency["agent_types"] == ["escalation"]
            assert emergency["tool_traces"] == [] and emergency["tools_used"] == []
            assert "不要等待在线聊天回复" in emergency["response"]
            assert {a["type"] for a in emergency["artifacts"]} == {"contact_info"}
            report["checks"]["fixed_emergency_interrupt"] = True
            report["conv_id"] = hospital["conv_id"]
            assert not report["page_errors"]
            report["passed"] = True
            page.screenshot(path=str(output / "public-information.png"), full_page=True)
        except Exception as error:
            report["passed"] = False
            report["error"] = str(error)
            page.screenshot(path=str(output / "failure.png"), full_page=True)
            raise
        finally:
            (output / "public-information.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
    print(json.dumps({"passed": report["passed"], "checks": report["checks"]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5173")
    parser.add_argument("--output", type=Path, default=Path(".scratch/browser-public"))
    args = parser.parse_args()
    check_public_info(args.url, args.output)

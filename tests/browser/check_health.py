"""Single-pass real UI acceptance for U001; uses the configured model and synthetic reports.

Run only after the updated validation containers are deployed. No retries or reset.
"""
import argparse
import io
import json
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright
from reportlab.pdfgen import canvas


def create_fixtures(output):
    output.mkdir(parents=True, exist_ok=True)
    lines = ["WBC 6.2 mg/L 4.0-10.0", "CRP 8 mg/L 0-5"]
    text_path = output / "synthetic-text.pdf"
    document = canvas.Canvas(str(text_path), pagesize=(600, 300))
    document.setFont("Helvetica", 16)
    for index, line in enumerate(lines):
        document.drawString(40, 240 - 35 * index, line)
    document.save()
    font_path = next((p for p in ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"] if Path(p).exists()), None)
    font = ImageFont.truetype(font_path, 42) if font_path else ImageFont.load_default(size=42)
    image = Image.new("RGB", (1800, 420), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(lines):
        draw.text((60, 50 + 90 * index), line, font=font, fill="black")
    image_path, scan_path = output / "synthetic-image.png", output / "synthetic-scan.pdf"
    image.save(image_path)
    image.save(scan_path, "PDF", resolution=200)
    return {"image": image_path, "scan_pdf": scan_path, "text_pdf": text_path}


def check_health(url, output):
    fixtures = create_fixtures(output / "fixtures")
    started = time.monotonic()
    report = {"base_url": url, "synthetic_data_only": True, "responses": [], "checks": {}, "page_errors": [], "passed": False}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        upload_requests = []
        page.on("request", lambda request: upload_requests.append(request.url) if request.url.endswith("/reports/preprocess") else None)

        def record(response, label):
            data = response.json()
            report["responses"].append({"label": label, "url": response.url, "status": response.status, "data": data})
            return data

        def capture(action, endpoint, label):
            with page.expect_response(lambda r: r.url.endswith(endpoint) and r.request.method == "POST", timeout=180000) as pending:
                action()
            response = pending.value
            data = record(response, label)
            assert response.ok, data
            page.wait_for_load_state("networkidle")
            return data

        def chat(text, label):
            page.get_by_label("就诊问题").fill(text)
            result = capture(lambda: page.get_by_role("button", name="发送", exact=True).click(), "/chat", label)
            expect(page.get_by_role("button", name="发送", exact=True)).to_be_visible(timeout=30000)
            print(json.dumps({"stage": label, "artifacts": [a["type"] for a in result.get("artifacts", [])]}, ensure_ascii=False), flush=True)
            return result

        def card(result, kind, matches=lambda data: True):
            return next(a["data"] for a in result["artifacts"] if a["type"] == kind and matches(a["data"]))

        def new_visit(patient):
            page.get_by_label("选择就诊人").select_option(patient)
            page.wait_for_load_state("networkidle")
            data = capture(lambda: page.get_by_role("button", name="＋ 新建就诊事项", exact=True).click(), "/visits", f"new_{patient}")
            expect(page.get_by_label("就诊问题")).to_be_enabled()
            return data

        try:
            page.goto(url)
            page.wait_for_load_state("networkidle")
            new_visit("patient_self")
            triage = chat("我30岁，咳嗽两天，症状较轻，想了解建议就诊科室和要补充哪些信息。", "adult_triage")
            conv = triage["conv_id"]
            report["self_conv_id"] = conv
            data = card(triage, "triage_guidance", lambda data: data["recommended_departments"] and data["sources"])
            assert data["recommended_departments"] and data["sources"]
            assert "triage" in triage["agent_types"]
            triage_card = page.locator('[data-artifact="triage_guidance"]').filter(
                has_text=data["recommended_departments"][0]["name"]).filter(
                has=page.get_by_role("link", name=data["sources"][0]["title"], exact=True)).first
            expect(triage_card).to_contain_text(data["recommended_departments"][0]["name"])
            report["checks"]["adult_triage_card_and_sources"] = True
            medication = chat("我正在服用华法林，能一起用布洛芬片吗？请查询禁忌、相互作用和成人说明书用法。", "medication_interaction")
            data = card(medication, "medication_info", lambda data: "布洛芬" in data["drug_name"] and data["sources"])
            assert data["sources"] and data["sections"] and "布洛芬" in data["drug_name"]
            assert "华法林" in json.dumps(data, ensure_ascii=False)
            assert "medication" in medication["agent_types"]
            medication_card = page.locator('[data-artifact="medication_info"]').filter(
                has=page.locator(".drug-name", has_text=data["drug_name"])).filter(
                has=page.get_by_role("link", name=data["sources"][0]["title"], exact=True)).first
            expect(medication_card).to_contain_text("华法林")
            report["checks"]["medication_interaction_and_sources"] = True
            pasted = chat("请整理以下报告：\n白细胞 6.2 x10^9/L 4.0-10.0\nC反应蛋白 8 mg/L 0-5", "pasted_report")
            data = card(pasted, "report_summary", lambda data: data["input_kind"] == "text" and
                        {o["flag"] for o in data["observations"]} >= {"within", "above"})
            assert data["input_kind"] == "text"
            assert {o["flag"] for o in data["observations"]} >= {"within", "above"}
            report["checks"]["pasted_report_preserves_values_and_original_ranges"] = True

            latest = None
            for kind, path in fixtures.items():
                result = capture(lambda p=path: page.get_by_label("上传报告 PDF 或图片").set_input_files(str(p)), "/reports/preprocess", f"upload_{kind}")
                data = card(result, "report_summary")
                assert result["conv_id"] == conv and result["patient_id"] == "patient_self"
                assert data["input_kind"] == ("image" if kind == "image" else "pdf")
                assert "WBC" in data["extracted_text"] and "6.2" in data["extracted_text"] and data["observations"]
                expect(page.locator('[data-artifact="report_summary"]').last).to_contain_text("6.2")
                latest = data
                report["checks"][f"real_{kind}_upload"] = True
                print(json.dumps({"stage": f"upload_{kind}", "observations": data["observations"]}, ensure_ascii=False), flush=True)
            assert [o["flag"] for o in latest["observations"]] == ["within", "above"]
            count_before = page.locator('[data-artifact="report_summary"]').count()
            page.reload()
            page.wait_for_load_state("networkidle")
            expect(page.locator('[data-artifact="report_summary"]')).to_have_count(count_before)
            expect(page.locator('[data-artifact="report_summary"]').last).to_contain_text("CRP")
            report["checks"]["upload_history_survives_refresh"] = True
            followup = chat("请核对这份报告里哪些项目超出原参考区间。", "same_visit_report_followup")
            assert any(t.get("tool_name") == "read_current_report" and t.get("success") is True for t in followup["tool_traces"])
            assert card(followup, "report_summary", lambda data: data["observations"] == latest["observations"])
            count_after_followup = page.locator('[data-artifact="report_summary"]').count()
            report["checks"]["followup_reads_exact_same_visit_report"] = True
            page.screenshot(path=str(output / "health-self.png"), full_page=True)

            request_count = len(upload_requests)
            large = b"%PDF-" + b"0" * (10 * 1024 * 1024)
            page.get_by_label("上传报告 PDF 或图片").set_input_files({"name": "oversize.pdf", "mimeType": "application/pdf", "buffer": large})
            expect(page.locator(".report-upload [role=alert]")).to_contain_text("10 MB")
            assert len(upload_requests) == request_count
            rejected = page.request.post(f"{url.rstrip('/')}/api/python/reports/preprocess", multipart={"user_id": "anonymous", "patient_id": "patient_self", "conv_id": conv,
                "file": {"name": "oversize.pdf", "mimeType": "application/pdf", "buffer": large}})
            rejected_data = record(rejected, "server_oversize_rejection")
            assert rejected.status == 413 and rejected_data["detail"]["code"] == "file_too_large"
            report["checks"]["client_and_server_file_limits"] = True

            new_visit("patient_child")
            expect(page.locator('[data-artifact="report_summary"]')).to_have_count(0)
            isolated = chat("请核对这份报告里哪些项目超出原参考区间。", "other_patient_has_no_report")
            report["child_conv_id"] = isolated["conv_id"]
            assert not any(a["type"] == "report_summary" for a in isolated["artifacts"])
            assert not any(t.get("tool_name") == "read_current_report" and t.get("success") is True for t in isolated["tool_traces"])
            # Either an explicit failed lookup or a safe request for this visit's
            # missing report is valid. Do not require an unnecessary tool call.
            assert any(word in isolated["response"] for word in ("上传", "粘贴", "提供", "没有", "未收到"))
            assert not any(re.search(rf"(?<![\d.]){re.escape(o['value'])}(?![\d.])", isolated["response"])
                           for o in latest["observations"])
            assert not any(o["item"] in isolated["response"] for o in latest["observations"])
            expect(page.locator('[data-artifact="report_summary"]')).to_have_count(0)
            rejected = page.request.post(f"{url.rstrip('/')}/api/python/reports/preprocess", multipart={"user_id": "anonymous", "patient_id": "patient_child", "conv_id": conv,
                "file": {"name": "report.pdf", "mimeType": "application/pdf", "buffer": fixtures["text_pdf"].read_bytes()}})
            record(rejected, "cross_patient_upload_rejection")
            assert rejected.status == 403
            report["checks"]["patient_isolation_in_ui_tool_and_upload"] = True
            page.get_by_label("选择就诊人").select_option("patient_self")
            page.wait_for_load_state("networkidle")
            expect(page.locator('[data-artifact="report_summary"]')).to_have_count(count_after_followup)
            for width, height in ((390, 844), (768, 1024), (1440, 1100)):
                page.set_viewport_size({"width": width, "height": height})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"health-{width}.png"), full_page=True)
            report["checks"]["responsive_health_cards"] = True
            assert not report["page_errors"]
            report["passed"] = True
        except Exception as error:
            report["error"] = repr(error)
            page.screenshot(path=str(output / "failure.png"), full_page=True)
            raise
        finally:
            report["elapsed_seconds"] = round(time.monotonic() - started, 3)
            (output / "health.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            browser.close()
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "report": str(output / "health.json")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18088")
    parser.add_argument("--output", type=Path, default=Path(".scratch/u001-browser-health"))
    args = parser.parse_args()
    check_health(args.url, args.output)

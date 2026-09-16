"""Report parsing, bounded local extraction, and real PDF/image OCR fixtures."""
import io
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject

from health.reports import preprocess_report
from health import report_upload as upload


def text_pdf(text="WBC 6.2 x10^9/L 4.0-10.0", *, pages=1, encrypted=False):
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    for _ in range(pages):
        page = writer.add_blank_page(width=600, height=300)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 16 Tf 40 240 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if encrypted:
        writer.encrypt("test-password")
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


def report_image(*, chinese=False):
    font_paths = (["C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"] if chinese else
                  ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    available = next((p for p in font_paths if Path(p).exists()), None)
    if chinese and available is None:
        pytest.skip("Chinese fixture font is not installed")
    font = ImageFont.truetype(available, 42) if available else ImageFont.load_default(size=42)
    image = Image.new("RGB", (1800, 420), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 50), "白细胞 6.2 x10^9/L 4.0-10.0" if chinese else "WBC 6.2 mg/L 4.0-10.0", font=font, fill="black")
    return image


def image_bytes(image, format="PNG"):
    output = io.BytesIO(); image.save(output, format=format)
    return output.getvalue()


def mixed_report_pdf(header_text="Scanned report"):
    scanned = PdfReader(io.BytesIO(image_bytes(report_image(), "PDF")))
    header = PdfReader(io.BytesIO(text_pdf(header_text)))
    writer = PdfWriter(); writer.add_page(scanned.pages[0])
    writer.pages[0].merge_page(header.pages[0])
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


def test_only_explicit_numeric_unit_and_own_reference_range_are_compared():
    raw = "白细胞 6.2 x10^9/L 4.0-10.0\nTestA 1 mg/L 2-5\nTestB 8 mmol/L 2~5\nTestC 2 % 2-5"
    result = preprocess_report(raw)
    assert [o.flag for o in result.observations] == ["within", "below", "above", "within"]
    assert result.extracted_text == raw and result.sources == []
    assert result.observations[0].raw_line == raw.splitlines()[0]
    assert "不推断疾病" in result.summary


@pytest.mark.parametrize("line", ["WBC 6.2", "WBC 6.2 4.0-10.0", "WBC 6.2 mg/L", "WBC 6.2 mg/L 10-4", "WBC 1O.0 mg/L 4-10", "WBC 6.2 mg/L <10", "患者编号 12345", "WBC 6.2 mg/L 4-10 �", "WBC 6.2 参考范围 4-10"])
def test_missing_or_suspicious_fields_are_kept_without_inferred_reference(line):
    result = preprocess_report(line)
    assert result.extracted_text == line and result.observations[0].raw_line == line
    assert all(o.flag == "unassessed" for o in result.observations)


def test_negative_values_and_mixed_rows_do_not_invent_units_or_diagnoses():
    summary = preprocess_report("Test -2 mg/L -3--1\nUnknown 1O.0 mg/L 4-10\n原报告说明保留全文")
    assert summary.observations[0].flag == "within"
    assert summary.observations[1].flag == "unassessed"
    assert "原报告说明保留全文" in summary.extracted_text


@pytest.mark.parametrize("text", ["", " ", "x" * 100_001], ids=["empty", "blank", "over-limit"])
def test_invalid_report_text_is_bounded(text):
    with pytest.raises(ValueError): preprocess_report(text)


def test_real_text_pdf_does_not_use_ocr(monkeypatch):
    monkeypatch.setattr(upload, "_ocr", lambda _: pytest.fail("text PDF must not call OCR"))
    result = upload.extract_report(text_pdf(), "report.pdf", "application/pdf")
    assert result.input_kind == "pdf" and "WBC 6.2" in result.extracted_text
    assert result.observations[0].flag == "within"


@pytest.mark.asyncio
async def test_real_worker_process_returns_text_pdf_result():
    result = await upload.process_report_file(text_pdf(), "report.pdf", "application/pdf")
    assert "WBC 6.2" in result.extracted_text and result.input_kind == "pdf"


@pytest.mark.parametrize("content,name,mime,code", [
    (b"", "a.pdf", "application/pdf", "empty_file"),
    (b"x" * (upload.MAX_FILE_BYTES + 1), "a.pdf", "application/pdf", "file_too_large"),
    (b"not-pdf", "a.pdf", "application/pdf", "format_mismatch"),
    (b"%PDF-broken", "a.pdf", "application/pdf", "damaged_file"),
    (b"x", "a.exe", "application/octet-stream", "unsupported_format"),
    (b"%PDF-x", "a.pdf", "image/png", "format_mismatch"),
], ids=["empty", "large", "fake-pdf", "damaged", "unsupported", "mime-mismatch"])
def test_upload_format_size_and_corruption_errors(content, name, mime, code):
    with pytest.raises(upload.ReportUploadError) as error: upload.extract_report(content, name, mime)
    assert error.value.code == code


def test_pdf_encryption_and_page_limit():
    for content, code in [(text_pdf(encrypted=True), "encrypted_pdf"), (text_pdf(pages=11), "page_limit")]:
        with pytest.raises(upload.ReportUploadError) as error: upload.extract_report(content, "a.pdf")
        assert error.value.code == code


def test_image_extension_pixels_and_no_text(monkeypatch):
    content = image_bytes(Image.new("RGB", (20, 20), "white"))
    with pytest.raises(upload.ReportUploadError, match="扩展名"): upload.extract_report(content, "a.jpg")
    monkeypatch.setattr(upload, "MAX_PAGE_PIXELS", 100)
    with pytest.raises(upload.ReportUploadError) as error: upload.extract_report(content, "a.png")
    assert error.value.code == "image_limit"
    monkeypatch.setattr(upload, "MAX_PAGE_PIXELS", 16_000_000)
    monkeypatch.setattr(upload, "_ocr", lambda image: ("", False))
    with pytest.raises(upload.ReportUploadError) as error: upload.extract_report(content, "a.png")
    assert error.value.code == "no_text"


def test_ocr_failure_timeout_and_uncertainty_are_explicit(monkeypatch):
    def timeout(*args, **kwargs): raise subprocess.TimeoutExpired("tesseract", 10)
    monkeypatch.setattr(upload.subprocess, "run", timeout)
    with pytest.raises(upload.ReportUploadError) as error: upload._ocr(Image.new("RGB", (10, 10)))
    assert error.value.code == "ocr_timeout"
    monkeypatch.setattr(upload.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(upload.ReportUploadError) as error: upload._ocr(Image.new("RGB", (10, 10)))
    assert error.value.code == "ocr_failed"
    monkeypatch.setattr(upload, "_ocr", lambda image: ("WBC 8 mg/L 4-6", True))
    summary = upload.extract_report(image_bytes(Image.new("RGB", (20, 20))), "a.png")
    assert summary.observations[0].flag == "unassessed" and any("低置信度" in w for w in summary.warnings)


@pytest.mark.skipif(not shutil.which("tesseract"), reason="real local Tesseract required")
@pytest.mark.parametrize("format,filename", [("PNG", "scan.png"), ("PDF", "scan.pdf")])
def test_real_scanned_pdf_and_image_ocr(format, filename):
    result = upload.extract_report(image_bytes(report_image(), format), filename)
    assert "WBC" in result.extracted_text and "6.2" in result.extracted_text
    assert result.input_kind == ("pdf" if format == "PDF" else "image")
    assert result.observations and result.warnings


@pytest.mark.skipif(not shutil.which("tesseract"), reason="real local Tesseract required")
def test_real_chinese_image_ocr():
    result = upload.extract_report(image_bytes(report_image(chinese=True)), "scan.png")
    assert "白细胞" in result.extracted_text.replace(" ", "") and "6.2" in result.extracted_text


@pytest.mark.skipif(not shutil.which("tesseract"), reason="real local Tesseract required")
def test_real_mixed_pdf_does_not_mistake_text_header_for_scanned_report_body():
    content = mixed_report_pdf()
    assert "Scanned report" in PdfReader(io.BytesIO(content)).pages[0].extract_text()
    assert "WBC" not in PdfReader(io.BytesIO(content)).pages[0].extract_text()
    result = upload.extract_report(content, "mixed.pdf")
    assert "WBC" in result.extracted_text and "6.2" in result.extracted_text
    assert "Scanned report" in result.extracted_text and "页面 OCR 补充，未校对" in result.extracted_text
    assert any("WBC" in observation.raw_line for observation in result.observations)


def test_mixed_pdf_low_confidence_ocr_does_not_produce_trusted_range_flags(monkeypatch):
    monkeypatch.setattr(upload, "_ocr", lambda _: ("WBC 8 mg/L 4-6", True))
    result = upload.extract_report(mixed_report_pdf(), "mixed.pdf")
    assert result.observations[0].value == "8"
    assert result.observations[0].flag == "unassessed"
    assert any("低置信度" in warning for warning in result.warnings)


def test_mixed_pdf_preserves_text_layer_and_deduplicates_only_exact_ocr_lines(monkeypatch):
    original = "WBC 6.2 mg/L 4-10"
    monkeypatch.setattr(upload, "_ocr", lambda _: (original + "\nCRP 1O.0 mg/L 0-5", True))
    result = upload.extract_report(mixed_report_pdf(original), "mixed.pdf")
    assert result.extracted_text.count(original) == 1
    assert "CRP 1O.0 mg/L 0-5" in result.extracted_text
    assert "PDF 文字层原提取" in result.extracted_text
    assert result.observations[0].value == "6.2"
    assert all(observation.flag == "unassessed" for observation in result.observations)

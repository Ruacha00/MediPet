"""Bounded, local-only PDF/image extraction. Original bytes live only during this call."""
import asyncio
import base64
import csv
import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys

from health.reports import MAX_REPORT_TEXT, preprocess_report
from health.models import ReportSummary

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_PAGE_PIXELS = 16_000_000
OCR_TIMEOUT_SECONDS = 10
PROCESS_TIMEOUT_SECONDS = 120
_WORKER_SLOTS = asyncio.Semaphore(2)


class ReportUploadError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _check_file(content: bytes, filename: str, content_type: str | None) -> str:
    if not content:
        raise ReportUploadError("empty_file", "文件为空，请重新选择报告。")
    if len(content) > MAX_FILE_BYTES:
        raise ReportUploadError("file_too_large", "报告文件不能超过 10 MB。", 413)
    extension = Path(filename or "").suffix.lower()
    allowed = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    if extension not in allowed:
        raise ReportUploadError("unsupported_format", "仅支持 PDF、PNG、JPEG 或 WebP 报告。", 415)
    if content_type not in {None, "", "application/octet-stream", allowed[extension]}:
        raise ReportUploadError("format_mismatch", "文件类型与扩展名不一致，请上传原始 PDF 或图片。", 415)
    if extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise ReportUploadError("format_mismatch", "文件内容不是有效 PDF。", 415)
    return extension


def _ocr(image) -> tuple[str, bool]:
    output = io.BytesIO()
    image.save(output, format="PNG")
    try:
        result = subprocess.run(["tesseract", "stdin", "stdout", "-l", "chi_sim+eng", "--psm", "6", "tsv"],
            input=output.getvalue(), capture_output=True, timeout=OCR_TIMEOUT_SECONDS,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except FileNotFoundError as exc:
        raise ReportUploadError("ocr_unavailable", "本地 OCR 服务未安装，请使用带 OCR 的服务镜像。", 503) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReportUploadError("ocr_timeout", "单页文字识别超时，请裁剪或降低图片分辨率后重试。", 408) from exc
    if result.returncode:
        raise ReportUploadError("ocr_failed", "本地中英文 OCR 无法处理该图片，请确认图片清晰且服务语言包完整。")
    lines = {}
    uncertain = False
    for word in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
        text = (word.get("text") or "").strip()
        if not text:
            continue
        key = tuple(word.get(field) for field in ("page_num", "block_num", "par_num", "line_num"))
        lines.setdefault(key, []).append(text)
        try:
            uncertain |= float(word.get("conf", 0)) < 60
        except ValueError:
            uncertain = True
    return "\n".join(" ".join(words) for words in lines.values()), uncertain


def _image_text(content: bytes, extension: str) -> tuple[str, bool]:
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(content)) as image:
            expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}[extension]
            if image.format != expected:
                raise ReportUploadError("format_mismatch", "图片内容与扩展名不一致。", 415)
            if image.width * image.height > MAX_PAGE_PIXELS or getattr(image, "n_frames", 1) != 1:
                raise ReportUploadError("image_limit", "请上传单帧且不超过 1600 万像素的图片。", 413)
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            return _ocr(ImageOps.exif_transpose(image).convert("RGB"))
    except ReportUploadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ReportUploadError("damaged_file", "图片损坏或尺寸异常，无法读取。") from exc


def _pdf_text(content: bytes) -> tuple[str, bool]:
    from pypdf import PdfReader
    import pypdfium2 as pdfium
    texts, uncertain = [], False
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise ReportUploadError("encrypted_pdf", "不支持加密 PDF，请先导出未加密副本。")
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            raise ReportUploadError("page_limit", "PDF 必须为 1–10 页，请拆分后上传。", 413)
        # Worker process owns all PDFium calls: no concurrent threads enter PDFium.
        with pdfium.PdfDocument(content) as document:
            for index, page in enumerate(reader.pages):
                text = (page.extract_text(extraction_mode="layout") or "").strip()
                # A text header or watermark does not prove that a scanned body
                # is represented by the text layer. OCR mixed image/text pages
                # as a whole; keep the direct path for genuinely text-only pages.
                if not text or page.images:
                    raster_page = document[index]
                    try:
                        width, height = raster_page.get_size()
                        scale = 200 / 72
                        if not all(math.isfinite(x) and x > 0 for x in (width, height)) or math.ceil(width * scale) * math.ceil(height * scale) > MAX_PAGE_PIXELS:
                            raise ReportUploadError("image_limit", "扫描页渲染超过 1600 万像素，请裁剪或拆分报告。", 413)
                        bitmap = raster_page.render(scale=scale)
                        try:
                            ocr_text, low_confidence = _ocr(bitmap.to_pil())
                            uncertain |= low_confidence
                            if text:
                                # Keep the accurate text layer even on a page
                                # whose only image is a logo. De-duplicate exact
                                # lines only; never silently repair OCR values.
                                original_lines = {line.strip() for line in text.splitlines()}
                                extra_lines = [line for line in ocr_text.splitlines()
                                               if line.strip() and line.strip() not in original_lines]
                                if extra_lines:
                                    text = f"【PDF 文字层原提取】\n{text}\n\n【页面 OCR 补充，未校对】\n" + "\n".join(extra_lines)
                            else:
                                text = ocr_text
                        finally:
                            bitmap.close()
                    finally:
                        raster_page.close()
                texts.append(text)
                if sum(map(len, texts)) > MAX_REPORT_TEXT:
                    raise ReportUploadError("text_limit", "报告提取文字过多，请拆分文件。", 413)
    except ReportUploadError:
        raise
    except Exception as exc:
        raise ReportUploadError("damaged_file", "PDF 损坏或无法可靠读取，请重新导出。") from exc
    return "\n\n".join(texts), uncertain


def extract_report(content: bytes, filename: str, content_type: str | None = None) -> ReportSummary:
    """Synchronous worker entry; tests may call it with real local extraction tools."""
    extension = _check_file(content, filename, content_type)
    text, uncertain = _pdf_text(content) if extension == ".pdf" else _image_text(content, extension)
    if not text.strip():
        raise ReportUploadError("no_text", "没有提取到可核对文字，请上传更清晰的报告。")
    if len(text) > MAX_REPORT_TEXT:
        raise ReportUploadError("text_limit", "报告提取文字过多，请拆分文件。", 413)
    summary = preprocess_report(text, input_kind="pdf" if extension == ".pdf" else "image")
    if uncertain:
        summary.warnings.append("OCR 存在低置信度文字，本次所有数值均标为待核对，不进行区间判断。")
        for observation in summary.observations:
            observation.flag = "unassessed"
    return summary


async def process_report_file(content: bytes, filename: str, content_type: str | None = None) -> ReportSummary:
    _check_file(content, filename, content_type)
    if _WORKER_SLOTS.locked():
        raise ReportUploadError("processing_busy", "已有报告正在识别，请稍后重试。", 503)
    async with _WORKER_SLOTS:
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "health.report_upload", "--worker",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=os.name != "nt", **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        payload = json.dumps({"content": base64.b64encode(content).decode("ascii"), "filename": filename, "content_type": content_type}).encode()
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(payload), PROCESS_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                await killer.wait()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ReportUploadError("processing_timeout", "报告处理超过 120 秒，请减少页数后重试。", 408) from exc
        try:
            result = json.loads(stdout)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReportUploadError("processing_failed", "报告处理未完成，请检查文件或稍后重试。", 503) from exc
        if "error" in result:
            raise ReportUploadError(**result["error"])
        return ReportSummary.model_validate(result["report"])


def _worker():
    try:
        if os.name == "posix":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        payload = json.loads(sys.stdin.buffer.read(MAX_FILE_BYTES * 2))
        report = extract_report(base64.b64decode(payload["content"], validate=True), payload["filename"], payload.get("content_type"))
        result = {"report": report.model_dump(mode="json")}
    except ReportUploadError as exc:
        result = {"error": {"code": exc.code, "message": exc.message, "status": exc.status}}
    except Exception:
        result = {"error": {"code": "processing_failed", "message": "文件处理失败，请核对报告格式。", "status": 422}}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__" and "--worker" in sys.argv:
    _worker()

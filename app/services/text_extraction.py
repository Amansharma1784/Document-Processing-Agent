import logging
import re
import tempfile
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageEnhance, ImageFilter

logger = logging.getLogger(__name__)

MIN_USABLE_TEXT_CHARS = 20
MAX_PDF_PAGES = 200
RENDER_DPI = 220

# Storage layout in your existing project:
# storage/documents/<server-generated-filename>.pdf
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STORAGE_DIR = (PROJECT_ROOT / "storage" / "documents").resolve()

_ocr_engine = None


def get_ocr_engine():
    """Load PaddleOCR once, only when OCR is needed."""
    global _ocr_engine

    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        try:
            # PaddleOCR 3.x
            _ocr_engine = PaddleOCR(
                lang="en",
                use_textline_orientation=True,
            )
        except TypeError:
            # PaddleOCR 2.x
            _ocr_engine = PaddleOCR(
                lang="en",
                use_angle_cls=True,
                show_log=False,
            )

    return _ocr_engine


def normalize_text(text: str) -> str:
    """Normalize whitespace without merging separate lines."""
    lines = []

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()

        if line:
            lines.append(line)

    return "\n".join(lines)


def extract_native_text(page: fitz.Page) -> str:
    """
    Extract words in reading order.

    Coordinates help preserve rows, columns, amounts, and line items
    better than blindly joining all words into one paragraph.
    """
    words = page.get_text("words", sort=True)

    if not words:
        return normalize_text(page.get_text("text", sort=True))

    rows: list[list[tuple[float, float, str]]] = []

    for x0, y0, x1, y1, word, *_ in words:
        # Group words with approximately aligned vertical positions.
        matched_row = None

        for row in rows:
            row_y = sum(item[0] for item in row) / len(row)

            if abs(y0 - row_y) <= max(3.0, (y1 - y0) * 0.45):
                matched_row = row
                break

        if matched_row is None:
            rows.append([(y0, x0, word)])
        else:
            matched_row.append((y0, x0, word))

    rows.sort(key=lambda row: sum(item[0] for item in row) / len(row))

    result = []

    for row in rows:
        row.sort(key=lambda item: item[1])
        result.append(" ".join(item[2] for item in row))

    return normalize_text("\n".join(result))


def _flatten_ocr_v3(result: Any) -> list[dict]:
    """Read PaddleOCR 3.x prediction results."""
    items = []

    if result is None:
        return items

    for page_result in result:
        data = (
            page_result
            if isinstance(page_result, dict)
            else getattr(page_result, "json", None)
        )

        if callable(data):
            data = data()

        if isinstance(data, dict) and "res" in data:
            data = data["res"]

        if not isinstance(data, dict):
            continue

        texts = data.get("rec_texts", [])
        scores = data.get("rec_scores", [])
        boxes = data.get("rec_polys", data.get("dt_polys", []))

        for index, text in enumerate(texts):
            score = float(scores[index]) if index < len(scores) else None
            box = boxes[index].tolist() if index < len(boxes) else None

            items.append(
                {
                    "text": str(text),
                    "confidence": score,
                    "box": box,
                }
            )

    return items


def _flatten_ocr_v2(result: Any) -> list[dict]:
    """Read PaddleOCR 2.x prediction results."""
    items = []

    if not result:
        return items

    # Standard PaddleOCR 2.x structure:
    # [[ [box, (text, confidence)], ... ]]
    for page_result in result:
        if not page_result:
            continue

        for line in page_result:
            try:
                box, recognition = line
                text, confidence = recognition

                items.append(
                    {
                        "text": str(text),
                        "confidence": float(confidence),
                        "box": box,
                    }
                )
            except (TypeError, ValueError):
                logger.warning("Skipping malformed OCR result")

    return items


def run_ocr(image_path: Path) -> dict:
    """Run OCR and return text plus word-level metadata."""
    engine = get_ocr_engine()

    if hasattr(engine, "predict"):
        # PaddleOCR 3.x
        raw_result = engine.predict(input=str(image_path))
        items = _flatten_ocr_v3(raw_result)
    else:
        # PaddleOCR 2.x
        raw_result = engine.ocr(str(image_path), cls=True)
        items = _flatten_ocr_v2(raw_result)

    # Sort OCR lines top-to-bottom, then left-to-right.
    def position(item):
        box = item.get("box")

        if box is None:
            return (0, 0)

        try:
            return (
                float(min(point[1] for point in box)),
                float(min(point[0] for point in box)),
            )
        except (TypeError, ValueError, IndexError):
            return (0, 0)

    items.sort(key=position)

    text = normalize_text(
        "\n".join(item["text"] for item in items if item["text"].strip())
    )

    scores = [item["confidence"] for item in items if item["confidence"] is not None]

    return {
        "text": text,
        "words": items,
        "average_confidence": (round(sum(scores) / len(scores), 4) if scores else None),
    }


def extract_pdf(file_path: Path) -> dict:
    """Extract page-wise text with OCR fallback for scanned pages."""
    if not file_path.is_file():
        raise FileNotFoundError("Stored PDF was not found")

    pages = []

    with fitz.open(file_path) as pdf:
        if pdf.needs_pass:
            raise ValueError("Password-protected PDFs are not supported")

        if pdf.page_count == 0:
            raise ValueError("PDF contains no pages")

        if pdf.page_count > MAX_PDF_PAGES:
            raise ValueError(f"PDF exceeds the {MAX_PDF_PAGES}-page processing limit")

        for index, page in enumerate(pdf):
            native_text = extract_native_text(page)

            # A page with only a few characters is likely scanned
            # or has unusably little embedded text.
            needs_ocr = len(native_text.strip()) < MIN_USABLE_TEXT_CHARS

            page_result = {
                "page_number": index + 1,
                "text": native_text,
                "extraction_method": "pymupdf",
                "ocr_used": False,
                "average_ocr_confidence": None,
                "words": [],
                "warnings": [],
            }

            if needs_ocr:
                try:
                    # PyMuPDF applies the page's rotation when rendering.
                    pixmap = page.get_pixmap(
                        dpi=RENDER_DPI,
                        colorspace=fitz.csRGB,
                        alpha=False,
                    )

                    with tempfile.TemporaryDirectory() as temp_dir:
                        image_path = Path(temp_dir) / "page.png"
                        pixmap.save(str(image_path))

                        # Mild enhancement can help some low-contrast scans.
                        with Image.open(image_path) as image:
                            image = image.convert("RGB")
                            image = ImageEnhance.Contrast(image).enhance(1.2)
                            image = ImageEnhance.Sharpness(image).enhance(1.2)
                            image = image.filter(ImageFilter.SHARPEN)
                            image.save(image_path)

                        ocr_result = run_ocr(image_path)

                    if ocr_result["text"]:
                        page_result["text"] = ocr_result["text"]
                        page_result["extraction_method"] = "paddleocr"
                        page_result["ocr_used"] = True
                        page_result["average_ocr_confidence"] = ocr_result[
                            "average_confidence"
                        ]
                        page_result["words"] = ocr_result["words"]
                    else:
                        page_result["warnings"].append("OCR did not detect usable text")

                except Exception:
                    logger.exception(
                        "OCR failed for PDF page %s",
                        index + 1,
                    )
                    page_result["warnings"].append("OCR failed for this page")

            if len(page_result["text"].strip()) < MIN_USABLE_TEXT_CHARS:
                page_result["warnings"].append("Page contains little or no usable text")

            pages.append(page_result)

    return {
        "filename": file_path.name,
        "page_count": len(pages),
        "pages": pages,
        "full_text": "\n\n".join(
            f"--- Page {page['page_number']} ---\n{page['text']}" for page in pages
        ),
    }


def extract_document(storage_key: str) -> dict:
    """Resolve a stored document key without allowing path traversal."""
    relative_path = Path(storage_key)
    print(relative_path)

    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError("Invalid document storage key")

    # The database stores keys such as documents/<uuid>.pdf.
    if relative_path.parts[:1] != ("documents",):
        raise ValueError("Invalid document storage key")

    filename = relative_path.name
    print(filename)

    if not filename.lower().endswith(".pdf"):
        raise ValueError("Text extraction currently supports PDFs only")

    file_path = (STORAGE_DIR / filename).resolve()
    print(file_path)

    if file_path.parent != STORAGE_DIR:
        raise ValueError("Invalid document storage path")

    return extract_pdf(file_path)

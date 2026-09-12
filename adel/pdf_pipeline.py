# =============================================================================
# PDF Processing Pipeline - v2
# =============================================================================
#
# -- Python packages -----------------------------------------------------------
#   pip install pdfplumber pdf2image pytesseract langdetect Pillow deep-translator
#
# -- OS-level binaries (must be installed separately) --------------------------
#   Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki
#   Poppler: https://github.com/oschwartz10612/poppler-windows/releases
#
# -- Usage ---------------------------------------------------------------------
#   python pdf_pipeline_v2.py path/to/document.pdf
#   python pdf_pipeline_v2.py scan.pdf --ocr-langs eng+ara+fra --ocr-dpi 400
#   python pdf_pipeline_v2.py document.pdf --translate
# =============================================================================

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

import pdfplumber
import pytesseract
from deep_translator import MyMemoryTranslator
from langdetect import DetectorFactory, LangDetectException, detect_langs
from pdf2image import convert_from_path
from pdfminer.pdfdocument import PDFPasswordIncorrect
from PIL import Image

DetectorFactory.seed = 42

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pdf_pipeline")

DEFAULT_MIN_TEXT_LEN = 50
LANG_SAMPLE_SIZE = 2000
MIN_LANG_CONFIDENCE = 0.80
DEFAULT_OCR_LANGS = "eng+ara+fra+deu+spa+chi_sim"
# MyMemoryTranslator's free tier caps each request at ~500 characters.
# Chunking (and rejoining every chunk in order below) keeps full-page
# translation working within that limit.
DEFAULT_TRANSLATE_BATCH_SIZE = 500
TRANSLATE_MAX_RETRIES = 3
TRANSLATE_RETRY_BACKOFF_SECONDS = 2.0
TRANSLATE_CHUNK_DELAY_SECONDS = 0.3
TRANSLATE_PAGE_DELAY_SECONDS = 0.5
TRANSLATION_DISCLAIMER = (
    "Machine-translated via MyMemory (deep-translator). Review by a "
    "qualified human translator is recommended for legal, medical, or other "
    "high-stakes content."
)

LANGUAGE_NAMES: dict[str, str] = {
    "af": "Afrikaans", "ar": "Arabic", "bg": "Bulgarian", "bn": "Bengali",
    "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "fa": "Persian", "fi": "Finnish", "fr": "French",
    "gu": "Gujarati", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "id": "Indonesian", "it": "Italian", "ja": "Japanese",
    "kn": "Kannada", "ko": "Korean", "lt": "Lithuanian", "lv": "Latvian",
    "mk": "Macedonian", "ml": "Malayalam", "mr": "Marathi", "ne": "Nepali",
    "nl": "Dutch", "no": "Norwegian", "pa": "Punjabi", "pl": "Polish",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sk": "Slovak",
    "sl": "Slovenian", "so": "Somali", "sq": "Albanian", "sv": "Swedish",
    "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "th": "Thai",
    "tl": "Filipino", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
    "vi": "Vietnamese", "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)",
}


@dataclass
class PipelineResult:
    """Structured output produced for every processed PDF."""

    file_name: str
    used_ocr: bool
    embedded_images_ocr_used: bool
    embedded_images_ocr_counts: list[dict[str, int]]
    detected_language_code: str
    detected_language_name: str
    confidence: Optional[float]
    primary_language: dict[str, object]
    languages: list[dict[str, object]]
    page_languages: list[dict[str, object]]
    translated_pages: Optional[list[dict[str, object]]]
    translation_notice: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


# =============================================================================
# Translation
# =============================================================================

def _split_translation_chunks(text: str, batch_size: int) -> list[str]:
    """Return chunks no longer than batch_size, preferring natural boundaries."""
    if len(text) <= batch_size:
        return [text]

    pieces = re.split(r"(\n\s*\n|(?<=[.!?])\s+)", text)
    chunks: list[str] = []
    current = ""

    for piece in pieces:
        if not piece:
            continue
        while len(piece) > batch_size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece[:batch_size])
            piece = piece[batch_size:]

        if len(current) + len(piece) <= batch_size:
            current += piece
        else:
            if current:
                chunks.append(current)
            current = piece

    if current:
        chunks.append(current)
    return chunks


def _to_mymemory_language(lang_code: str) -> str:
    """Map langdetect variants to MyMemoryTranslator language codes."""
    language_map = {"zh-cn": "zh-CN", "zh-tw": "zh-TW"}
    return language_map.get(lang_code.lower(), lang_code.lower())


def translate_to_english(
    text: str,
    source_lang: str,
    batch_size: int = DEFAULT_TRANSLATE_BATCH_SIZE,
) -> Optional[str]:
    """Translate text to English, retaining failed chunks in their original form.

    The page text is always split into chunks (see _split_translation_chunks),
    each chunk is translated independently, and every chunk - translated or,
    on repeated failure, left in its original language - is rejoined in
    original order below. No content is ever dropped or truncated; only the
    rare chunk that cannot be translated after all retries stays untranslated.
    Returns None only for a setup-level failure (e.g. unknown source language
    or the translator object itself failing to construct), never because one
    chunk exhausted its retries.
    """
    if source_lang == "en":
        return text
    if source_lang == "unknown":
        logger.warning("Translation skipped: source language is unknown.")
        return None

    mymemory_source = _to_mymemory_language(source_lang)

    translated_chunks: list[str] = []
    fallback_chunk_count = 0
    chunks = _split_translation_chunks(text, batch_size)

    for chunk_index, chunk in enumerate(chunks, start=1):
        if chunk_index > 1:
            time.sleep(TRANSLATE_CHUNK_DELAY_SECONDS)

        for attempt_index in range(TRANSLATE_MAX_RETRIES):
            attempt_number = attempt_index + 1
            translated_chunk: Optional[str] = None
            error: Optional[Exception] = None
            try:
                translator = MyMemoryTranslator(
                    source=mymemory_source,
                    target="en",
                )
                translated_chunk = translator.translate(chunk)
            except Exception as exc:  # noqa: BLE001 - log and retry below
                error = exc

            if translated_chunk is not None:
                translated_chunks.append(translated_chunk)
                break

            if error is not None:
                logger.warning(
                    "MyMemory translation failed for chunk %d on attempt %d/%d: %s",
                    chunk_index,
                    attempt_number,
                    TRANSLATE_MAX_RETRIES,
                    error,
                )
            else:
                logger.warning(
                    "MyMemory returned no translation for chunk %d on attempt %d/%d.",
                    chunk_index,
                    attempt_number,
                    TRANSLATE_MAX_RETRIES,
                )

            if attempt_number == TRANSLATE_MAX_RETRIES:
                logger.warning(
                    "MyMemory could not translate chunk %d after %d attempts; "
                    "keeping the original text.",
                    chunk_index,
                    TRANSLATE_MAX_RETRIES,
                )
                translated_chunks.append(chunk)
                fallback_chunk_count += 1
                break

            time.sleep(TRANSLATE_RETRY_BACKOFF_SECONDS * (2 ** attempt_index))

    if fallback_chunk_count:
        logger.warning(
            "Page text translated with %d/%d chunk(s) kept in original language "
            "due to repeated translation failures.",
            fallback_chunk_count,
            len(translated_chunks),
        )

    return "".join(translated_chunks)


# =============================================================================
# Text extraction and OCR
# =============================================================================

_GARBAGE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")


def _is_garbage(text: str, garbage_ratio: float = 0.30) -> bool:
    """Detect actual extraction garbage without restricting text by script."""
    if not text:
        return True
    return len(_GARBAGE_RE.findall(text)) / len(text) > garbage_ratio


def extract_page_native(page) -> str:
    """Extract usable native text from one pdfplumber page."""
    text = page.extract_text() or ""
    if _is_garbage(text):
        logger.debug("Page %s - native text flagged as garbage.", page.page_number)
        return ""
    return text


def extract_page_ocr(image: Image.Image, page_num: int, ocr_langs: str) -> str:
    """OCR a single rendered page or embedded image."""
    try:
        return pytesseract.image_to_string(image, lang=ocr_langs).strip()
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            "Tesseract executable not found. Install Tesseract OCR and ensure it "
            "is on PATH, or pass --tesseract-cmd."
        ) from exc
    except pytesseract.TesseractError as exc:
        logger.warning("Tesseract error on page %d: %s", page_num, exc)
        return ""
    except Exception as exc:
        logger.exception("Unexpected OCR error on page %d: %s", page_num, exc)
        return ""


def _is_full_page_image(page, image_info: dict, area_ratio: float = 0.90) -> bool:
    """Return True for images that likely represent a whole scanned page."""
    x0, top, x1, bottom = page.bbox
    page_area = max((x1 - x0) * (bottom - top), 1)
    image_area = max(image_info.get("x1", 0) - image_info.get("x0", 0), 0) * max(
        image_info.get("bottom", 0) - image_info.get("top", 0), 0
    )
    return image_area / page_area >= area_ratio


def _render_embedded_image_with_pdfplumber(page, image_info: dict, ocr_dpi: int) -> Image.Image:
    """Render an embedded image's bounding box using pdfplumber."""
    bbox = (
        image_info["x0"], image_info["top"], image_info["x1"], image_info["bottom"]
    )
    return page.crop(bbox).to_image(resolution=ocr_dpi).original


def _render_embedded_image_with_fitz(
    pdf_path: str,
    page_num: int,
    image_info: dict,
    ocr_dpi: int,
) -> Optional[Image.Image]:
    """Render an embedded image bounding box with PyMuPDF as a fallback."""
    try:
        import fitz  # type: ignore
    except ImportError:
        logger.warning("PyMuPDF/fitz not installed; embedded-image fallback unavailable.")
        return None

    document = None
    try:
        document = fitz.open(pdf_path)
        page = document.load_page(page_num - 1)
        clip = fitz.Rect(
            image_info["x0"], image_info["top"], image_info["x1"], image_info["bottom"]
        )
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(ocr_dpi / 72, ocr_dpi / 72),
            clip=clip,
        )
        mode = "RGBA" if pixmap.alpha else "RGB"
        return Image.frombytes(mode, [pixmap.width, pixmap.height], pixmap.samples)
    except Exception as exc:
        logger.warning("PyMuPDF fallback failed for page %d: %s", page_num, exc)
        return None
    finally:
        if document is not None:
            document.close()


def extract_embedded_image_texts(
    pdf_path: str,
    page,
    page_num: int,
    ocr_langs: str,
    ocr_dpi: int,
) -> tuple[list[str], int]:
    """OCR each non-full-page embedded raster image on a PDF page."""
    image_texts: list[str] = []
    ocr_count = 0

    for image_index, image_info in enumerate(page.images, start=1):
        if _is_full_page_image(page, image_info):
            logger.debug("Page %d image %d - skipping likely full-page image.", page_num, image_index)
            continue

        try:
            image = _render_embedded_image_with_pdfplumber(page, image_info, ocr_dpi)
        except Exception as exc:
            logger.debug("pdfplumber crop failed for page %d image %d: %s", page_num, image_index, exc)
            image = _render_embedded_image_with_fitz(
                pdf_path, page_num, image_info, ocr_dpi
            )

        if image is None:
            logger.warning("Page %d image %d - could not render for OCR.", page_num, image_index)
            continue

        ocr_count += 1
        image_text = extract_page_ocr(image, page_num, ocr_langs)
        if image_text:
            image_texts.append(image_text)

    return image_texts, ocr_count


def _append_embedded_image_text(page_text: str, image_texts: list[str]) -> str:
    """Append embedded image OCR output under explicit markers."""
    if not image_texts:
        return page_text
    sections = [page_text.strip()] if page_text.strip() else []
    sections.extend(f"[image text]\n{text}" for text in image_texts)
    return "\n\n".join(sections)


def extract_full_text(
    pdf_path: str,
    min_text_len: int = DEFAULT_MIN_TEXT_LEN,
    ocr_langs: str = DEFAULT_OCR_LANGS,
    ocr_dpi: int = 300,
) -> tuple[str, bool, list[str], list[dict[str, int]]]:
    """Extract every page, rendering only pages that require full-page OCR."""
    ocr_page_indices: list[int] = []
    page_texts: list[str] = []
    embedded_texts_by_page: list[list[str]] = []
    embedded_counts: list[dict[str, int]] = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            logger.info("Opened PDF '%s' - %d page(s).", Path(pdf_path).name, len(pdf.pages))
            for index, page in enumerate(pdf.pages):
                page_num = index + 1
                native_text = extract_page_native(page)
                image_texts, image_count = extract_embedded_image_texts(
                    pdf_path, page, page_num, ocr_langs, ocr_dpi
                )
                embedded_texts_by_page.append(image_texts)
                embedded_counts.append(
                    {
                        "page_number": page_num,
                        "embedded_images_ocr_count": image_count,
                    }
                )

                if len(native_text.strip()) >= min_text_len:
                    page_texts.append(_append_embedded_image_text(native_text, image_texts))
                else:
                    page_texts.append("")
                    ocr_page_indices.append(index)
    except PDFPasswordIncorrect as exc:
        raise RuntimeError(
            "PDF is encrypted/password-protected. Provide an unencrypted PDF or "
            "add password support before processing."
        ) from exc

    used_ocr = bool(ocr_page_indices)
    if used_ocr:
        logger.info("%d page(s) require OCR at %d DPI.", len(ocr_page_indices), ocr_dpi)
        for index in ocr_page_indices:
            page_num = index + 1
            images = convert_from_path(
                pdf_path,
                dpi=ocr_dpi,
                first_page=page_num,
                last_page=page_num,
            )
            ocr_text = extract_page_ocr(images[0], page_num, ocr_langs) if images else ""
            if not images:
                logger.warning("Page %d - rendering produced no image.", page_num)
            page_texts[index] = _append_embedded_image_text(
                ocr_text,
                embedded_texts_by_page[index],
            )

    full_text = "\n\n".join(text for text in page_texts if text)
    logger.info(
        "Extraction complete - chars: %d | OCR used: %s | embedded image OCR count: %d.",
        len(full_text),
        used_ocr,
        sum(item["embedded_images_ocr_count"] for item in embedded_counts),
    )
    return full_text, used_ocr, page_texts, embedded_counts


# =============================================================================
# Language composition detection
# =============================================================================

_SCRIPT_RUN_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+|"
    r"[A-Za-z\u00C0-\u024F]+|[\u0370-\u03FF]+|[\u0400-\u04FF]+|"
    r"[\u0900-\u097F]+|[\u0E00-\u0E7F]+|[\u0590-\u05FF]+|"
    r"[\u3040-\u30FF]+|[\u3400-\u4DBF\u4E00-\u9FFF]+|[\uAC00-\uD7AF]+|"
    r"[\u0980-\u09FF]+|[\u0A00-\u0A7F]+|[\u0A80-\u0AFF]+|"
    r"[\u0B80-\u0BFF]+|[\u0C00-\u0C7F]+|[\u0C80-\u0CFF]+|[\u0D00-\u0D7F]+"
)


def detect_language(
    text: str,
    sample_size: int = LANG_SAMPLE_SIZE,
    min_confidence: float = MIN_LANG_CONFIDENCE,
) -> tuple[str, Optional[float]]:
    """Detect a language from one script-contiguous text run."""
    sample = text[:sample_size].strip()
    if not sample:
        return "unknown", None
    try:
        results = detect_langs(sample)
    except LangDetectException as exc:
        logger.warning("langdetect error: %s", exc)
        return "unknown", None
    if not results:
        return "unknown", None

    result = results[0]
    confidence = round(result.prob, 4)
    if confidence < min_confidence:
        logger.warning("Language confidence %.2f is below %.2f.", confidence, min_confidence)
        return "unknown", confidence
    return result.lang, confidence


def resolve_language_name(code: str) -> str:
    return "Unknown" if code == "unknown" else LANGUAGE_NAMES.get(code, code)


def _detect_token_script(token: str) -> str:
    codepoint = ord(token[0])
    if 0x0600 <= codepoint <= 0x08FF:
        return "Arabic"
    if token[0].isascii() or 0x00C0 <= codepoint <= 0x024F:
        return "Latin"
    if 0x0370 <= codepoint <= 0x03FF:
        return "Greek"
    if 0x0400 <= codepoint <= 0x04FF:
        return "Cyrillic"
    if 0x0900 <= codepoint <= 0x097F:
        return "Devanagari"
    if 0x0E00 <= codepoint <= 0x0E7F:
        return "Thai"
    if 0x0590 <= codepoint <= 0x05FF:
        return "Hebrew"
    if 0x3040 <= codepoint <= 0x30FF:
        return "Japanese"
    if 0x3400 <= codepoint <= 0x9FFF:
        return "CJK"
    if 0xAC00 <= codepoint <= 0xD7AF:
        return "Hangul"
    return "Other"


def _script_char_count(text: str) -> int:
    return sum(len(match.group(0)) for match in _SCRIPT_RUN_RE.finditer(text))


def split_text_by_script(text: str) -> list[str]:
    """Split text into contiguous runs of broad Unicode scripts."""
    runs: list[str] = []
    current = ""
    current_script = ""
    previous_end = 0

    for match in _SCRIPT_RUN_RE.finditer(text):
        token = match.group(0)
        script = _detect_token_script(token)
        gap = text[previous_end:match.start()]
        if current and script == current_script:
            current += gap + token
        else:
            if current:
                runs.append(current)
            current = token
            current_script = script
        previous_end = match.end()

    if current:
        runs.append(current)
    return runs


def _unknown_language_entry() -> dict[str, object]:
    return {
        "language_code": "unknown",
        "language_name": "Unknown",
        "char_ratio": 1.0,
        "confidence": None,
    }


def detect_language_composition(
    text: str,
    sample_size: int = LANG_SAMPLE_SIZE,
    min_confidence: float = MIN_LANG_CONFIDENCE,
) -> list[dict[str, object]]:
    """Return a character-weighted language composition for text."""
    runs = split_text_by_script(text)
    total_chars = sum(_script_char_count(run) for run in runs)
    if not total_chars:
        return [_unknown_language_entry()]

    stats: dict[str, dict[str, float]] = {}
    for run in runs:
        char_count = _script_char_count(run)
        if not char_count:
            continue
        code, confidence = detect_language(run, sample_size, min_confidence)
        language_stats = stats.setdefault(
            code,
            {"char_count": 0.0, "weighted_confidence": 0.0, "confidence_weight": 0.0},
        )
        language_stats["char_count"] += char_count
        if confidence is not None:
            language_stats["weighted_confidence"] += confidence * char_count
            language_stats["confidence_weight"] += char_count

    languages: list[dict[str, object]] = []
    for code, language_stats in stats.items():
        weight = language_stats["confidence_weight"]
        confidence = (
            round(language_stats["weighted_confidence"] / weight, 4)
            if weight
            else None
        )
        languages.append(
            {
                "language_code": code,
                "language_name": resolve_language_name(code),
                "char_ratio": round(language_stats["char_count"] / total_chars, 4),
                "confidence": confidence,
            }
        )

    languages.sort(key=lambda language: float(language["char_ratio"]), reverse=True)
    return languages or [_unknown_language_entry()]


def detect_page_languages(
    page_texts: list[str],
    sample_size: int = LANG_SAMPLE_SIZE,
    min_confidence: float = MIN_LANG_CONFIDENCE,
) -> list[dict[str, object]]:
    """Return language composition and primary language for every page."""
    page_languages: list[dict[str, object]] = []
    for index, page_text in enumerate(page_texts, start=1):
        languages = detect_language_composition(page_text, sample_size, min_confidence)
        primary_language = languages[0]
        page_languages.append(
            {
                "page_number": index,
                "language_code": primary_language["language_code"],
                "language_name": primary_language["language_name"],
                "confidence": primary_language["confidence"],
                "primary_language": primary_language,
                "languages": languages,
            }
        )
    return page_languages


def translate_pages_to_english(
    page_texts: list[str],
    page_languages: list[dict[str, object]],
    batch_size: int = DEFAULT_TRANSLATE_BATCH_SIZE,
) -> list[dict[str, object]]:
    """Translate non-English pages; rare failed chunks remain in original text."""
    translated_pages: list[dict[str, object]] = []
    translation_call_started = False

    for index, page_text in enumerate(page_texts, start=1):
        language = page_languages[index - 1]
        language_code = str(language["language_code"])
        language_name = str(language["language_name"])
        translated_text = page_text
        was_translated = False

        if language_code == "unknown":
            logger.warning(
                "Page %d - translation skipped: detected language is unknown.",
                index,
            )
        elif language_code == "en":
            logger.debug("Page %d - English text kept as-is.", index)
        else:
            logger.info(
                "Page %d - detected language: %s (%s); translating.",
                index,
                language_name,
                language_code,
            )
            if translation_call_started:
                time.sleep(TRANSLATE_PAGE_DELAY_SECONDS)
            translation_call_started = True
            translated_result = translate_to_english(
                page_text,
                language_code,
                batch_size,
            )
            if translated_result is not None:
                translated_text = translated_result
                was_translated = True
            else:
                logger.warning(
                    "Page %d - translation failed; keeping original text.",
                    index,
                )

        translated_pages.append(
            {
                "page_number": index,
                "detected_language_code": language_code,
                "detected_language_name": language_name,
                "was_translated": was_translated,
                "text": translated_text,
            }
        )

    return translated_pages


# =============================================================================
# Language routing and orchestration
# =============================================================================

def process_english(text: str) -> None:
    logger.info("Entering ENGLISH processing branch.")
    print(f"[English] Preview: {text[:300]!r}")


def process_arabic(text: str) -> None:
    logger.info("Entering ARABIC processing branch.")
    print(f"[Arabic] Preview: {text[:300]!r}")


def process_french(text: str) -> None:
    logger.info("Entering FRENCH processing branch.")
    print(f"[French] Preview: {text[:300]!r}")


def process_default(text: str, lang_code: str) -> None:
    logger.info("Entering DEFAULT processing branch (lang='%s').", lang_code)
    print(f"[Default | lang={lang_code}] Preview: {text[:300]!r}")


LANGUAGE_HANDLERS: Dict[str, Callable[[str], None]] = {
    "en": process_english,
    "ar": process_arabic,
    "fr": process_french,
}


def route_by_language(text: str, lang_code: str) -> None:
    handler = LANGUAGE_HANDLERS.get(lang_code)
    if handler:
        handler(text)
    else:
        process_default(text, lang_code)


def run_pipeline(
    pdf_path: str,
    min_text_len: int = DEFAULT_MIN_TEXT_LEN,
    ocr_langs: str = DEFAULT_OCR_LANGS,
    ocr_dpi: int = 300,
    sample_size: int = LANG_SAMPLE_SIZE,
    min_confidence: float = MIN_LANG_CONFIDENCE,
    translate: bool = False,
    translate_batch_size: int = DEFAULT_TRANSLATE_BATCH_SIZE,
) -> PipelineResult:
    """Run extraction, language analysis, optional translation, and routing."""
    if translate_batch_size <= 0:
        raise ValueError("translate_batch_size must be greater than zero.")

    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {pdf_path!r}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF file: {pdf_path!r}")

    logger.info("Pipeline START - %s", path.name)
    try:
        full_text, used_ocr, page_texts, embedded_counts = extract_full_text(
            pdf_path, min_text_len, ocr_langs, ocr_dpi
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Text extraction failed: {exc}") from exc

    if not full_text.strip():
        raise RuntimeError("Extraction produced no usable text.")

    languages = detect_language_composition(full_text, sample_size, min_confidence)
    primary_language = languages[0]
    language_code = str(primary_language["language_code"])
    language_name = str(primary_language["language_name"])
    primary_confidence = primary_language["confidence"]
    confidence = float(primary_confidence) if primary_confidence is not None else None
    page_languages = detect_page_languages(page_texts, sample_size, min_confidence)

    translated_pages = (
        translate_pages_to_english(
            page_texts,
            page_languages,
            translate_batch_size,
        )
        if translate
        else None
    )
    translation_notice = (
        TRANSLATION_DISCLAIMER if translated_pages is not None else None
    )

    result = PipelineResult(
        file_name=path.name,
        used_ocr=used_ocr,
        embedded_images_ocr_used=any(
            item["embedded_images_ocr_count"] > 0 for item in embedded_counts
        ),
        embedded_images_ocr_counts=embedded_counts,
        detected_language_code=language_code,
        detected_language_name=language_name,
        confidence=confidence,
        primary_language=primary_language,
        languages=languages,
        page_languages=page_languages,
        translated_pages=translated_pages,
        translation_notice=translation_notice,
    )

    logger.info(
        "Result - file: '%s' | OCR: %s | embedded image OCR: %s | lang: %s (%s).",
        result.file_name,
        result.used_ocr,
        result.embedded_images_ocr_used,
        result.detected_language_code,
        result.detected_language_name,
    )
    route_by_language(full_text, language_code)
    logger.info("Pipeline END - %s", path.name)
    return result


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="PDF extraction pipeline with OCR, language analysis, and translation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("pdf_path", type=str, help="Path to the input PDF file.")
    parser.add_argument("--min-text-len", type=int, default=DEFAULT_MIN_TEXT_LEN)
    parser.add_argument("--ocr-langs", type=str, default=DEFAULT_OCR_LANGS)
    parser.add_argument("--ocr-dpi", type=int, default=300)
    parser.add_argument("--sample-size", type=int, default=LANG_SAMPLE_SIZE)
    parser.add_argument("--min-confidence", type=float, default=MIN_LANG_CONFIDENCE)
    parser.add_argument("--tesseract-cmd", type=str, default=None)
    parser.add_argument(
        "--translate",
        action="store_true",
        default=False,
        help="Translate the complete extracted document to English (via MyMemory).",
    )
    parser.add_argument(
        "--translate-batch-size",
        type=int,
        default=DEFAULT_TRANSLATE_BATCH_SIZE,
        help="Maximum characters sent to the translator in a single request.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    return parser.parse_args()


def main() -> None:
    """Configure the process, run the pipeline, and print its result."""
    args = parse_args()
    logging.getLogger().setLevel(args.log_level)
    if args.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd

    try:
        result = run_pipeline(
            pdf_path=args.pdf_path,
            min_text_len=args.min_text_len,
            ocr_langs=args.ocr_langs,
            ocr_dpi=args.ocr_dpi,
            sample_size=args.sample_size,
            min_confidence=args.min_confidence,
            translate=args.translate,
            translate_batch_size=args.translate_batch_size,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("Pipeline error: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("Unexpected error: %s", exc, exc_info=True)
        sys.exit(1)

    print("\n-- Pipeline Result " + "-" * 42)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    print(f"\nDetected language: {result.detected_language_name} ({result.detected_language_code})")
    if result.translated_pages is not None:
        for page in result.translated_pages:
            status = "translated" if page["was_translated"] else "kept as-is"
            print(
                f"Page {page['page_number']}: "
                f"{page['detected_language_name']} ({status})"
            )


if __name__ == "__main__":
    main()
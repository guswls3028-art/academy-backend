# apps/worker/ai/detection/segment_dispatcher.py
from __future__ import annotations

import logging
import os
import tempfile
from typing import Dict, List, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai_worker.ai.config import AIConfig
from apps.worker.ai_worker.ai.detection.segment_opencv import segment_questions_opencv
from apps.worker.ai_worker.ai.detection.segment_yolo import segment_questions_yolo

logger = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]


def _is_pdf(file_path: str) -> bool:
    """파일 확장자 또는 매직 바이트로 PDF 여부 판단."""
    if file_path.lower().endswith(".pdf"):
        return True
    try:
        with open(file_path, "rb") as f:
            header = f.read(5)
            return header == b"%PDF-"
    except Exception:
        return False


def _pdf_to_images(pdf_path: str) -> List[str]:
    """
    PDF 파일의 각 페이지를 이미지로 변환하여 임시 파일 경로 리스트를 반환.
    PyMuPDF(fitz) 사용 — AI 워커에 설치됨 (worker-ai-tools.txt).
    """
    from academy.adapters.tools.pymupdf_renderer import PdfDocument

    image_paths: List[str] = []
    tmp_dir = tempfile.mkdtemp(prefix="pdf-seg-")

    with PdfDocument(pdf_path) as doc:
        page_count = doc.page_count()
        logger.info("PDF_TO_IMAGES | pages=%d | path=%s", page_count, pdf_path)

        for i in range(page_count):
            pil_img = doc.render_page(i, dpi=200)
            out_path = os.path.join(tmp_dir, f"page_{i:03d}.png")
            pil_img.save(out_path, "PNG")
            image_paths.append(out_path)

    return image_paths


def _segment_single_image(image_path: str) -> List[BBox]:
    """단일 이미지에 대한 세그멘테이션 (엔진 자동 선택)."""
    cfg = AIConfig.load()
    engine = (cfg.QUESTION_SEGMENTATION_ENGINE or "auto").lower()

    if engine == "opencv":
        return segment_questions_opencv(image_path)
    if engine == "yolo":
        return segment_questions_yolo(image_path)

    # auto: yolo -> opencv
    try:
        boxes = segment_questions_yolo(image_path)
        if boxes:
            return boxes
    except Exception:
        pass
    return segment_questions_opencv(image_path)


def segment_questions(image_path: str) -> List[BBox]:
    """
    worker-side segmentation single entrypoint.
    PDF 파일이면 페이지별로 이미지 변환 후 세그멘테이션.
    이미지 파일이면 직접 세그멘테이션.
    """
    if _is_pdf(image_path):
        page_images = _pdf_to_images(image_path)
        if not page_images:
            logger.warning("PDF_SEGMENT_NO_PAGES | path=%s", image_path)
            return []

        all_boxes: List[BBox] = []
        for page_idx, page_path in enumerate(page_images):
            boxes = _segment_single_image(page_path)
            logger.info(
                "PDF_SEGMENT_PAGE | page=%d | boxes=%d | path=%s",
                page_idx, len(boxes), page_path,
            )
            all_boxes.extend(boxes)

        return all_boxes

    return _segment_single_image(image_path)


def segment_questions_multipage(image_path: str) -> Dict[str, any]:
    """
    PDF 문항 분할 확장판 — 페이지별 결과 + 전체 이미지 경로 반환.
    question_segmentation 워커에서 사용.

    Returns:
        {
            "pages": [
                {"page_index": 0, "image_path": str, "boxes": [(x,y,w,h), ...]},
                ...
            ],
            "total_boxes": int,
            "is_pdf": bool,
        }
    """
    if _is_pdf(image_path):
        page_images = _pdf_to_images(image_path)
        if not page_images:
            return {"pages": [], "total_boxes": 0, "is_pdf": True}

        pages = []
        total = 0
        for idx, page_path in enumerate(page_images):
            boxes = _segment_single_image(page_path)
            pages.append({
                "page_index": idx,
                "image_path": page_path,
                "boxes": boxes,
            })
            total += len(boxes)

        return {"pages": pages, "total_boxes": total, "is_pdf": True}

    # 단일 이미지
    boxes = _segment_single_image(image_path)
    return {
        "pages": [{"page_index": 0, "image_path": image_path, "boxes": boxes}],
        "total_boxes": len(boxes),
        "is_pdf": False,
    }

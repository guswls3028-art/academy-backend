# apps/worker/ai/detection/segment_dispatcher.py
from __future__ import annotations

import logging
import os
import tempfile
from typing import Dict, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai_worker.ai.config import AIConfig
from apps.worker.ai_worker.ai.detection.segment_opencv import segment_questions_opencv
from apps.worker.ai_worker.ai.detection.segment_yolo import segment_questions_yolo
from apps.worker.ai_worker.ai.detection.segment_ocr import (
    is_ocr_available,
    segment_questions_ocr,
)

# PDF 200 DPI 렌더링 기준 좌표 변환 (points → pixels)
_PDF_TO_PIXEL_SCALE = 200.0 / 72.0

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


def _pdf_to_images(pdf_path: str) -> List[Dict]:
    """
    PDF 파일의 각 페이지를 이미지로 변환 + 텍스트 기반 문항 박스 사전 계산.

    Returns:
        [
          {
            "image_path": str,
            "has_embedded_text": bool,
            "text_boxes": List[BBox]  # 텍스트 기반 분할 박스 (픽셀 좌표계). 비었으면 실패.
          },
          ...
        ]
    """
    from academy.adapters.tools.pymupdf_renderer import PdfDocument
    from academy.domain.tools.question_splitter import (
        is_non_question_page,
        split_questions,
        TextBlock as SplitterTextBlock,
    )

    results: List[Dict] = []
    tmp_dir = tempfile.mkdtemp(prefix="pdf-seg-")

    with PdfDocument(pdf_path) as doc:
        page_count = doc.page_count()
        logger.info("PDF_TO_IMAGES | pages=%d | path=%s", page_count, pdf_path)

        for i in range(page_count):
            pil_img = doc.render_page(i, dpi=200)
            out_path = os.path.join(tmp_dir, f"page_{i:03d}.png")
            pil_img.save(out_path, "PNG")

            # 텍스트 존재 여부 검사 — 스캔본이면 False
            has_text = False
            text_boxes: List[BBox] = []
            try:
                raw_blocks = doc.extract_text_blocks(i)
                has_text = len(raw_blocks) > 0
            except Exception:
                raw_blocks = []

            # 텍스트 PDF의 경우 text-based 분할을 시도해서 per-question 박스 사전 계산
            if has_text:
                try:
                    tbs = [
                        SplitterTextBlock(text=b.text, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
                        for b in raw_blocks
                    ]
                    if not is_non_question_page(tbs):
                        pw, ph = doc.page_dimensions(i)
                        regions = split_questions(tbs, pw, ph, page_index=i)
                        scale = _PDF_TO_PIXEL_SCALE
                        for r in regions:
                            rx0, ry0, rx1, ry1 = r.bbox
                            text_boxes.append((
                                int(rx0 * scale),
                                int(ry0 * scale),
                                int((rx1 - rx0) * scale),
                                int((ry1 - ry0) * scale),
                            ))
                except Exception as e:
                    logger.warning(
                        "PDF_TEXT_BOXES_ERROR | page=%d | error=%s", i, e,
                    )

            results.append({
                "image_path": out_path,
                "has_embedded_text": has_text,
                "text_boxes": text_boxes,
            })

    return results


def _segment_single_image(
    image_path: str,
    *,
    skip_ocr: bool = False,
    is_pdf_page: bool = False,
) -> List[BBox]:
    """
    단일 이미지에 대한 세그멘테이션 (엔진 자동 선택).

    auto 모드 우선순위: YOLO(모델+PDF페이지) → OCR(크레덴셜 있을 때, skip_ocr=False) → OpenCV.
    OCR 경로는 스캔본 시험지에서 문항 번호 감지를 통해 페이지당 여러 문항을 분할.

    skip_ocr: PDF 페이지에 embedded text가 존재할 때 True. OCR 비용을 아낀다
              (pdf_question_pipeline이 PDF 텍스트로 별도 분할을 수행하기 때문).
    is_pdf_page: True면 PDF에서 렌더링된 페이지. False면 사용자가 직접 업로드한
                 단일 이미지(카메라 촬영일 가능성). 카메라 사진은 YOLO 학습 분포를
                 벗어나므로 YOLO를 건너뛰고 OCR/OpenCV 경로 사용.
    """
    cfg = AIConfig.load()
    engine = (cfg.QUESTION_SEGMENTATION_ENGINE or "auto").lower()

    if engine == "opencv":
        return segment_questions_opencv(image_path)
    if engine == "yolo":
        return segment_questions_yolo(image_path)
    if engine == "ocr":
        return segment_questions_ocr(image_path)

    # auto 모드: YOLO는 PDF 페이지에만 사용 (카메라 사진 오탐 방지)
    if is_pdf_page:
        try:
            boxes = segment_questions_yolo(image_path)
            if boxes:
                return boxes
        except Exception:
            pass

    if not skip_ocr and is_ocr_available():
        try:
            boxes = segment_questions_ocr(image_path)
            if boxes:
                return boxes
        except Exception as e:
            logger.warning("OCR_SEGMENT_AUTO_FAIL | path=%s | error=%s", image_path, e)

    return segment_questions_opencv(image_path)


def _boxes_for_pdf_page(page_info: Dict) -> List[BBox]:
    """
    PDF 페이지 1개에 대한 최종 박스 결정.

    우선순위:
      1. 텍스트 기반 분할 성공 (text_boxes 존재) → 그대로 사용
      2. 스캔본 (has_embedded_text=False) + OCR 가용 → OCR 결과 신뢰
         - OCR이 [] 반환해도 fallback 안 함 (표지/정답지 등 비문항 페이지의 정상 신호)
         - OCR 예외(API 실패)만 OpenCV로 폴백
      3. 텍스트는 있으나 분할 실패 (비문항 페이지 등) → OpenCV
      4. OCR 불가(크레덴셜 없음) + 스캔본 → OpenCV 안전망
    """
    if page_info["text_boxes"]:
        return list(page_info["text_boxes"])

    image_path = page_info["image_path"]

    # 스캔본에서 OCR 가용 시 — OCR 결과 신뢰
    if not page_info["has_embedded_text"] and is_ocr_available():
        try:
            boxes = segment_questions_ocr(image_path)
            return boxes  # 빈 결과도 trust (non-question page)
        except Exception as e:
            logger.warning(
                "PDF_PAGE_OCR_FAIL | path=%s | error=%s",
                image_path, e,
            )
            # fallthrough → OpenCV 안전망

    # 텍스트 있지만 분할 실패 OR OCR 크레덴셜 없음 OR OCR 예외
    skip_ocr = page_info["has_embedded_text"]
    return _segment_single_image(image_path, skip_ocr=skip_ocr, is_pdf_page=True)


def segment_questions(image_path: str) -> List[BBox]:
    """
    worker-side segmentation single entrypoint.
    PDF 파일이면 페이지별로 이미지 변환 후 세그멘테이션.
    이미지 파일이면 직접 세그멘테이션.
    """
    if _is_pdf(image_path):
        page_infos = _pdf_to_images(image_path)
        if not page_infos:
            logger.warning("PDF_SEGMENT_NO_PAGES | path=%s", image_path)
            return []

        all_boxes: List[BBox] = []
        for page_idx, info in enumerate(page_infos):
            boxes = _boxes_for_pdf_page(info)
            logger.info(
                "PDF_SEGMENT_PAGE | page=%d | boxes=%d | has_text=%s | text_boxes=%d",
                page_idx, len(boxes), info["has_embedded_text"], len(info["text_boxes"]),
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
                {
                    "page_index": 0,
                    "image_path": str,
                    "boxes": [(x,y,w,h), ...],
                    "has_embedded_text": bool,
                },
                ...
            ],
            "total_boxes": int,
            "is_pdf": bool,
        }
    """
    if _is_pdf(image_path):
        page_infos = _pdf_to_images(image_path)
        if not page_infos:
            return {"pages": [], "total_boxes": 0, "is_pdf": True}

        pages = []
        total = 0
        for idx, info in enumerate(page_infos):
            boxes = _boxes_for_pdf_page(info)
            pages.append({
                "page_index": idx,
                "image_path": info["image_path"],
                "boxes": boxes,
                "has_embedded_text": info["has_embedded_text"],
            })
            total += len(boxes)

        return {"pages": pages, "total_boxes": total, "is_pdf": True}

    # 단일 이미지
    boxes = _segment_single_image(image_path)
    return {
        "pages": [{
            "page_index": 0,
            "image_path": image_path,
            "boxes": boxes,
            "has_embedded_text": False,
        }],
        "total_boxes": len(boxes),
        "is_pdf": False,
    }

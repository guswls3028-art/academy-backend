# apps/worker/ai_worker/ai/pipelines/pdf_question_pipeline.py
"""
PDF 시험지 문항 분할 + 해설 인식·매칭 파이프라인.

처리 흐름:
  1. PDF → 페이지별 이미지 변환 (이미지 파일이면 단일 페이지 취급)
  2. 각 페이지에서 문항 영역 세그멘테이션 (OpenCV/YOLO)
  3. PDF 텍스트 블록 추출 (PyMuPDF) — 문항 번호·해설 마커 감지
  4. 문항-해설 매칭 (번호 기반)
  5. 결과 반환: { questions: [...], explanations: [...], boxes: [...] }

데이터 계약:
  - questions: [{ number, bbox, page_index, text? }]
  - explanations: [{ question_number, text, page_index }]
  - boxes: [[x,y,w,h], ...] (하위 호환)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from apps.worker.ai_worker.ai.detection.segment_dispatcher import (
    segment_questions_multipage,
)

logger = logging.getLogger(__name__)

# 문항 번호 패턴: "1.", "1)", "01.", "1 .", "문1.", "Q1." 등
_QUESTION_NUM_RE = re.compile(
    r"^[\s]*(?:문\s*)?(?:Q\.?\s*)?(\d{1,3})\s*[.).\s]",
    re.MULTILINE,
)

# 해설 섹션 마커 패턴
_EXPLANATION_MARKERS = re.compile(
    r"(?:^|\n)\s*(?:해설|풀이|정답\s*(?:및\s*)?해설|답\s*(?:및\s*)?풀이|explanation|answer\s*key)\s*",
    re.IGNORECASE | re.MULTILINE,
)

# 개별 해설 번호 패턴: "1.", "1)", "[1]" 등
_EXPLANATION_NUM_RE = re.compile(
    r"^[\s]*(?:해설\s*)?(\d{1,3})\s*[.):\]\s]",
    re.MULTILINE,
)


def run_pdf_question_pipeline(
    *,
    job: AIJob,
    local_path: str,
    payload: Dict[str, Any],
    tenant_id: Optional[str],
    record_progress: Callable,
) -> AIResult:
    """
    PDF 문항 분할 + 해설 인식 통합 파이프라인.
    """
    total_steps = 5

    # Step 1: 파일 분석 (PDF/이미지 감지)
    record_progress(
        job.id, "analyzing", 15,
        step_index=1, step_total=total_steps,
        step_name_display="파일 분석", step_percent=0,
        tenant_id=tenant_id,
    )

    seg_result = segment_questions_multipage(local_path)
    is_pdf = seg_result["is_pdf"]

    record_progress(
        job.id, "analyzing", 20,
        step_index=1, step_total=total_steps,
        step_name_display="파일 분석", step_percent=100,
        tenant_id=tenant_id,
    )

    # Step 2: 문항 영역 세그멘테이션 (이미 segment_questions_multipage에서 완료)
    record_progress(
        job.id, "segmenting", 40,
        step_index=2, step_total=total_steps,
        step_name_display="문항 분할", step_percent=100,
        tenant_id=tenant_id,
    )

    pages = seg_result["pages"]
    total_boxes = seg_result["total_boxes"]
    logger.info(
        "PDF_QUESTION_PIPELINE | job_id=%s | pages=%d | total_boxes=%d | is_pdf=%s",
        job.id, len(pages), total_boxes, is_pdf,
    )

    # Step 3: 텍스트 블록 추출 (PDF인 경우만)
    record_progress(
        job.id, "extracting_text", 55,
        step_index=3, step_total=total_steps,
        step_name_display="텍스트 추출", step_percent=0,
        tenant_id=tenant_id,
    )

    text_blocks_by_page: Dict[int, List[Dict]] = {}
    full_text_by_page: Dict[int, str] = {}

    if is_pdf:
        text_blocks_by_page, full_text_by_page = _extract_pdf_text(local_path)

    record_progress(
        job.id, "extracting_text", 65,
        step_index=3, step_total=total_steps,
        step_name_display="텍스트 추출", step_percent=100,
        tenant_id=tenant_id,
    )

    # Step 4: 문항 번호 부여 + 해설 매칭
    record_progress(
        job.id, "matching", 75,
        step_index=4, step_total=total_steps,
        step_name_display="문항·해설 매칭", step_percent=0,
        tenant_id=tenant_id,
    )

    questions = _build_question_list(pages, text_blocks_by_page)
    explanations = _extract_explanations(full_text_by_page) if is_pdf else []

    record_progress(
        job.id, "matching", 85,
        step_index=4, step_total=total_steps,
        step_name_display="문항·해설 매칭", step_percent=100,
        tenant_id=tenant_id,
    )

    # Step 5: 문항 이미지 크롭 + R2 업로드
    record_progress(
        job.id, "cropping", 90,
        step_index=5, step_total=total_steps,
        step_name_display="문항 이미지 저장", step_percent=0,
        tenant_id=tenant_id,
    )

    exam_id = payload.get("exam_id")
    question_image_keys = _crop_and_upload_question_images(
        questions=questions,
        pages=pages,
        tenant_id=tenant_id,
        exam_id=exam_id,
        job_id=job.id,
    )

    record_progress(
        job.id, "done", 100,
        step_index=5, step_total=total_steps,
        step_name_display="완료", step_percent=100,
        tenant_id=tenant_id,
    )

    # 하위 호환: boxes 필드 유지 (flat list)
    flat_boxes = []
    for page in pages:
        flat_boxes.extend(page["boxes"])

    # 매칭된 해설만 결과에 포함 (question_number=None은 DB에 저장 불가)
    matched_explanations = [e for e in explanations if e.get("question_number") is not None]
    unmatched_count = len(explanations) - len(matched_explanations)
    if unmatched_count > 0:
        logger.warning(
            "PDF_QUESTION_PIPELINE_UNMATCHED_EXPLANATIONS | job_id=%s | unmatched=%d",
            job.id, unmatched_count,
        )

    result = {
        "boxes": flat_boxes,
        "questions": [
            {
                "number": q["number"],
                "bbox": list(q["bbox"]),
                "page_index": q["page_index"],
                "text": q.get("text"),
            }
            for q in questions
        ],
        "explanations": matched_explanations,
        "question_image_keys": question_image_keys,
        "page_count": len(pages),
        "total_questions": len(questions),
        "is_pdf": is_pdf,
        "exam_id": payload.get("exam_id"),
    }

    logger.info(
        "PDF_QUESTION_PIPELINE_DONE | job_id=%s | questions=%d | explanations=%d (unmatched=%d)",
        job.id, len(questions), len(matched_explanations), unmatched_count,
    )

    return AIResult.done(job.id, result)


def _extract_pdf_text(
    pdf_path: str,
) -> Tuple[Dict[int, List[Dict]], Dict[int, str]]:
    """
    PDF에서 페이지별 텍스트 블록 추출.
    Returns: (text_blocks_by_page, full_text_by_page)
    """
    try:
        from academy.adapters.tools.pymupdf_renderer import PdfDocument

        blocks_by_page: Dict[int, List[Dict]] = {}
        text_by_page: Dict[int, str] = {}

        with PdfDocument(pdf_path) as doc:
            for i in range(doc.page_count()):
                raw_blocks = doc.extract_text_blocks(i)
                blocks = [
                    {
                        "text": b.text,
                        "x0": b.x0, "y0": b.y0,
                        "x1": b.x1, "y1": b.y1,
                    }
                    for b in raw_blocks
                ]
                blocks_by_page[i] = blocks
                text_by_page[i] = "\n".join(b.text for b in raw_blocks)

        return blocks_by_page, text_by_page

    except Exception as e:
        logger.warning("PDF_TEXT_EXTRACT_FAILED | error=%s", e)
        return {}, {}


def _build_question_list(
    pages: List[Dict],
    text_blocks_by_page: Dict[int, List[Dict]],
) -> List[Dict]:
    """
    세그멘테이션 박스에 문항 번호를 부여.

    전략:
    1. PDF 텍스트 블록이 있으면, 각 박스 영역과 겹치는 텍스트에서 번호 추출 시도.
    2. 텍스트가 없거나 번호 추출 실패 시, 순차 번호 부여 (1, 2, 3...).
    """
    questions = []
    global_number = 0

    for page in pages:
        page_idx = page["page_index"]
        boxes = page["boxes"]
        text_blocks = text_blocks_by_page.get(page_idx, [])

        for bbox in boxes:
            global_number += 1
            x, y, w, h = bbox

            # 텍스트 블록에서 번호 추출 시도
            detected_number = None
            matched_text = None

            if text_blocks:
                detected_number, matched_text = _match_text_to_bbox(
                    bbox, text_blocks,
                )

            questions.append({
                "number": detected_number or global_number,
                "bbox": bbox,
                "page_index": page_idx,
                "text": matched_text,
            })

    # 번호 중복 정리: 중복이 있으면 순차 번호로 폴백
    numbers = [q["number"] for q in questions]
    if len(set(numbers)) != len(numbers):
        logger.warning(
            "QUESTION_NUMBER_DEDUP | detected=%s → fallback to sequential",
            numbers,
        )
        for i, q in enumerate(questions):
            q["number"] = i + 1

    return questions


def _match_text_to_bbox(
    bbox: Tuple[int, int, int, int],
    text_blocks: List[Dict],
) -> Tuple[Optional[int], Optional[str]]:
    """
    바운딩 박스와 겹치는 텍스트 블록에서 문항 번호 추출.
    """
    x, y, w, h = bbox
    bx0, by0, bx1, by1 = x, y, x + w, y + h

    best_text = None
    best_overlap = 0

    for block in text_blocks:
        tx0, ty0, tx1, ty1 = block["x0"], block["y0"], block["x1"], block["y1"]

        # 좌표계가 다를 수 있으므로 넉넉한 겹침 판정
        overlap_x = max(0, min(bx1, tx1) - max(bx0, tx0))
        overlap_y = max(0, min(by1, ty1) - max(by0, ty0))
        overlap = overlap_x * overlap_y

        if overlap > best_overlap:
            best_overlap = overlap
            best_text = block["text"]

    if best_text:
        match = _QUESTION_NUM_RE.search(best_text)
        if match:
            return int(match.group(1)), best_text

    return None, best_text


def _crop_and_upload_question_images(
    *,
    questions: List[Dict],
    pages: List[Dict],
    tenant_id: Optional[str],
    exam_id: Optional[str],
    job_id: str,
) -> Dict[int, str]:
    """
    각 문항의 bbox를 페이지 이미지에서 크롭하여 R2 Storage에 업로드.

    Returns:
        {question_number: r2_key, ...}
    """
    if not tenant_id or not exam_id:
        logger.warning(
            "CROP_SKIP_NO_IDS | job_id=%s | tenant_id=%s | exam_id=%s",
            job_id, tenant_id, exam_id,
        )
        return {}

    import io
    import cv2
    import numpy as np

    # 페이지별 이미지 로드 캐시
    page_images: Dict[int, np.ndarray] = {}
    for page in pages:
        img_path = page.get("image_path")
        if img_path:
            img = cv2.imread(img_path)
            if img is not None:
                page_images[page["page_index"]] = img

    if not page_images:
        logger.warning("CROP_NO_PAGE_IMAGES | job_id=%s", job_id)
        return {}

    result_keys: Dict[int, str] = {}

    try:
        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
    except Exception as e:
        logger.warning("CROP_R2_IMPORT_FAILED | job_id=%s | error=%s", job_id, e)
        return {}

    for q in questions:
        q_num = q["number"]
        page_idx = q["page_index"]
        bbox = q["bbox"]

        img = page_images.get(page_idx)
        if img is None:
            continue

        x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        img_h, img_w = img.shape[:2]

        # bbox 경계 안전 처리
        x = max(0, x)
        y = max(0, y)
        x2 = min(img_w, x + w)
        y2 = min(img_h, y + h)

        if x2 <= x or y2 <= y:
            logger.warning(
                "CROP_INVALID_BBOX | job_id=%s | q=%d | bbox=%s | img_size=%sx%s",
                job_id, q_num, bbox, img_w, img_h,
            )
            continue

        # 크롭 + PNG 인코딩
        crop = img[y:y2, x:x2]
        success, buf = cv2.imencode(".png", crop)
        if not success:
            continue

        r2_key = f"tenants/{tenant_id}/exams/questions/{exam_id}/q{q_num:03d}.png"

        try:
            upload_fileobj_to_r2_storage(
                fileobj=io.BytesIO(buf.tobytes()),
                key=r2_key,
                content_type="image/png",
            )
            result_keys[q_num] = r2_key
        except Exception as e:
            logger.warning(
                "CROP_UPLOAD_FAILED | job_id=%s | q=%d | key=%s | error=%s",
                job_id, q_num, r2_key, e,
            )

    logger.info(
        "CROP_DONE | job_id=%s | uploaded=%d/%d",
        job_id, len(result_keys), len(questions),
    )
    return result_keys


def _extract_explanations(
    full_text_by_page: Dict[int, str],
) -> List[Dict]:
    """
    PDF 텍스트에서 해설 섹션을 찾고, 개별 해설을 번호별로 추출.

    Returns:
        [{ "question_number": int, "text": str, "page_index": int }]
    """
    explanations = []

    for page_idx, full_text in full_text_by_page.items():
        if not full_text:
            continue

        # 해설 섹션 마커 검색
        marker_match = _EXPLANATION_MARKERS.search(full_text)
        if not marker_match:
            continue

        # 해설 섹션 텍스트 (마커 이후)
        explanation_section = full_text[marker_match.start():]

        logger.info(
            "EXPLANATION_SECTION_FOUND | page=%d | start_pos=%d | length=%d",
            page_idx, marker_match.start(), len(explanation_section),
        )

        # 개별 해설 추출 (번호별)
        matches = list(_EXPLANATION_NUM_RE.finditer(explanation_section))

        if not matches:
            # 번호 없이 해설 전체를 하나로 취급
            clean_text = explanation_section[marker_match.end() - marker_match.start():].strip()
            if clean_text:
                explanations.append({
                    "question_number": None,
                    "text": clean_text[:2000],
                    "page_index": page_idx,
                })
            continue

        for i, m in enumerate(matches):
            q_num = int(m.group(1))
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(explanation_section)
            text = explanation_section[start:end].strip()

            if text:
                explanations.append({
                    "question_number": q_num,
                    "text": text[:2000],
                    "page_index": page_idx,
                })

    return explanations

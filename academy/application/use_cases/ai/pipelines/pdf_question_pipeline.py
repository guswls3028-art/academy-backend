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
from academy.adapters.ai.detection.segment_dispatcher import (
    register_pdf_seg_tmp_dirs,
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

# 문항 뒤에 붙은 교사용 정답·해설 묶음의 시작. 일반 문제 본문에 등장할 수
# 있는 단독 "풀이"보다 의도가 명확한 결합 표지만 문항 제외 경계로 사용한다.
_SOLUTION_TAIL_MARKER = re.compile(
    r"^[ \t]*정[ \t]*답[ \t]*(?:및|과|&)[ \t]*(?:해[ \t]*설|풀[ \t]*이)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
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

    seg_result의 tmp_dirs는 dispatcher의 finally가 정리(register_pdf_seg_tmp_dirs).
    """
    total_steps = 5

    # Step 1: 파일 분석 (PDF/이미지 감지)
    record_progress(
        job.id, "analyzing", 15,
        step_index=1, step_total=total_steps,
        step_name_display="파일 분석", step_percent=0,
        tenant_id=tenant_id,
    )

    # PDF/이미지 판별 + 페이지 이미지 렌더링 (크롭에 필요)
    seg_result = segment_questions_multipage(local_path)
    register_pdf_seg_tmp_dirs(seg_result.get("tmp_dirs") or [])
    is_pdf = seg_result["is_pdf"]
    pages = seg_result["pages"]  # [{page_index, image_path, boxes}, ...]

    record_progress(
        job.id, "analyzing", 20,
        step_index=1, step_total=total_steps,
        step_name_display="파일 분석", step_percent=100,
        tenant_id=tenant_id,
    )

    # Step 2: 텍스트 추출 + 텍스트 기반 문항 분할 (PDF만)
    record_progress(
        job.id, "segmenting", 35,
        step_index=2, step_total=total_steps,
        step_name_display="문항 분할", step_percent=0,
        tenant_id=tenant_id,
    )

    text_blocks_by_page: Dict[int, List[Dict]] = {}
    full_text_by_page: Dict[int, str] = {}
    questions: List[Dict] = []
    solution_tail_start: Optional[int] = None
    excluded_pages: set[int] = set()

    if is_pdf:
        text_blocks_by_page, full_text_by_page = _extract_pdf_text(local_path)

        # dispatcher가 이미 text/OCR/OpenCV 우선순위, cross-page 검증과
        # product-facing display bbox를 적용했다. 여기서 question_splitter를
        # 다시 호출하면 그래프 확장·품질 보정이 사라지므로 그 결과를 문항의
        # 단일 정본으로 사용한다.
        ocr_page_texts = _extract_ocr_page_texts(
            [p for p in pages if not p.get("has_embedded_text")]
        )
        for page_idx, page_text in ocr_page_texts.items():
            if not full_text_by_page.get(page_idx):
                full_text_by_page[page_idx] = page_text

        solution_tail_start = _find_solution_tail_start(
            full_text_by_page,
            pages,
        )
        excluded_pages = _find_academy_review_cover_pages(
            full_text_by_page,
            pages,
        )
        if solution_tail_start is not None:
            excluded_pages.update(
                {
                    p["page_index"]
                    for p in pages
                    if p["page_index"] >= solution_tail_start
                }
            )
        questions = _build_question_list(
            pages,
            text_blocks_by_page,
            excluded_page_indexes=excluded_pages,
        )
        logger.info(
            "PDF_SEGMENTATION_RESULT_USED | job_id=%s | questions=%d | "
            "solution_tail_start=%s",
            job.id,
            len(questions),
            solution_tail_start,
        )

    # 텍스트·OCR 둘 다 실패 → OpenCV fallback (페이지 단위)
    if not questions:
        logger.info(
            "PDF_FALLBACK_OPENCV | job_id=%s", job.id,
        )
        questions = _build_question_list(
            pages,
            text_blocks_by_page,
            excluded_page_indexes=excluded_pages,
        )

    total_boxes = len(questions)

    record_progress(
        job.id, "segmenting", 50,
        step_index=2, step_total=total_steps,
        step_name_display="문항 분할", step_percent=100,
        tenant_id=tenant_id,
    )

    logger.info(
        "PDF_QUESTION_PIPELINE | job_id=%s | pages=%d | questions=%d | is_pdf=%s",
        job.id, len(pages), total_boxes, is_pdf,
    )

    # Step 3: 해설 추출
    record_progress(
        job.id, "extracting_text", 65,
        step_index=3, step_total=total_steps,
        step_name_display="해설 추출", step_percent=0,
        tenant_id=tenant_id,
    )

    explanations = (
        _extract_explanations(
            full_text_by_page,
            solution_tail_start=solution_tail_start,
        )
        if is_pdf
        else []
    )

    record_progress(
        job.id, "extracting_text", 75,
        step_index=3, step_total=total_steps,
        step_name_display="해설 추출", step_percent=100,
        tenant_id=tenant_id,
    )

    # Step 4: 문항·해설 매칭 (이미 번호 기반으로 완료)
    record_progress(
        job.id, "matching", 85,
        step_index=4, step_total=total_steps,
        step_name_display="문항·해설 매칭", step_percent=100,
        tenant_id=tenant_id,
    )

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
        if page["page_index"] in excluded_pages:
            continue
        flat_boxes.extend(page["boxes"])

    # 실제 분리된 문항과 번호가 일치하는 해설만 결과에 포함한다. 번호가
    # 있어도 해당 문항이 없으면 callback의 FK/조회 경계로 넘기지 않는다.
    question_numbers = {q["number"] for q in questions}
    matched_explanations = [
        explanation
        for explanation in explanations
        if explanation.get("question_number") in question_numbers
    ]
    unmatched_count = len(explanations) - len(matched_explanations)
    if unmatched_count > 0:
        logger.warning(
            "PDF_QUESTION_PIPELINE_UNMATCHED_EXPLANATIONS | job_id=%s | unmatched=%d",
            job.id, unmatched_count,
        )

    # 세그멘테이션 방식 분류 — 사용자 UI 피드백 + 운영 관측용
    segmentation_method = _classify_segmentation_method(
        is_pdf=is_pdf,
        pages=pages,
        questions=questions,
    )

    result = {
        "boxes": flat_boxes,
        "questions": [
            {
                "number": q["number"],
                "bbox": list(q["bbox"]),
                "page_index": q["page_index"],
                "text": q.get("text"),
                "original_number": (q.get("meta") or {}).get(
                    "original_number", q["number"]
                ),
            }
            for q in questions
        ],
        "explanations": matched_explanations,
        "question_image_keys": question_image_keys,
        "page_count": len(pages),
        "total_questions": len(questions),
        "is_pdf": is_pdf,
        "exam_id": payload.get("exam_id"),
        "segmentation_method": segmentation_method,
    }

    logger.info(
        "PDF_QUESTION_PIPELINE_DONE | job_id=%s | questions=%d | explanations=%d (unmatched=%d)",
        job.id, len(questions), len(matched_explanations), unmatched_count,
    )

    return AIResult.done(job.id, result)


def _classify_segmentation_method(
    *,
    is_pdf: bool,
    pages: List[Dict],
    questions: List[Dict],
) -> str:
    """
    사용된 세그멘테이션 방식을 분류해서 meta로 노출.

    Returns:
        "text"  — PDF text blocks 기반 분할 (모든 페이지 text)
        "ocr"   — OCR 기반 분할 (스캔본 포함)
        "mixed" — 일부 text, 일부 OCR (하이브리드 PDF)
        "opencv"— OpenCV fallback만 사용 (OCR 크레덴셜 없음/실패)
        "image" — 단일 이미지 입력
    """
    if not is_pdf:
        return "image"

    if not questions:
        return "opencv"  # 아무것도 못 찾음 → _build_question_list 폴백 사용

    # 페이지별 텍스트 유무 기준
    has_text_pages = sum(1 for p in pages if p.get("has_embedded_text"))
    scan_pages = len(pages) - has_text_pages

    if has_text_pages == len(pages):
        return "text"
    if has_text_pages == 0:
        return "ocr"
    return "mixed"


def _find_solution_tail_start(
    full_text_by_page: Dict[int, str],
    pages: List[Dict],
) -> Optional[int]:
    """Return the first trailing teacher answer/solution page, if present.

    A marker is trusted only after at least one earlier page produced a problem
    box. This avoids treating a cover or table of contents that merely mentions
    answer material as the start of the document's problem-free tail.
    """
    question_page_indexes = {
        int(page.get("page_index", 0))
        for page in pages
        if page.get("boxes")
    }
    for page_idx in sorted(full_text_by_page):
        page_text = full_text_by_page.get(page_idx) or ""
        if not _SOLUTION_TAIL_MARKER.search(page_text):
            continue
        if not any(previous < page_idx for previous in question_page_indexes):
            continue
        logger.info("PDF_SOLUTION_TAIL_FOUND | page=%d", page_idx)
        return page_idx
    return None


def _find_academy_review_cover_pages(
    full_text_by_page: Dict[int, str],
    pages: List[Dict],
) -> set[int]:
    """Find short dated academy review covers without changing split routing.

    The range label on these covers (for example ``1. 평면좌표``) is useful to
    the dispatcher's document-level workbook detection, but it is not a saved
    question. Filtering after segmentation preserves that routing signal while
    keeping the cover out of exam questions and legacy boxes.
    """
    pages_with_boxes = {
        int(page.get("page_index", 0))
        for page in pages
        if page.get("boxes")
    }
    cover_pages: set[int] = set()
    for page_idx, page_text in full_text_by_page.items():
        if page_idx not in pages_with_boxes or len(page_text) > 240:
            continue
        if not re.search(
            r"\d{1,2}\s*/\s*\d{1,2}\s*\([월화수목금토일]\)",
            page_text,
        ):
            continue
        if not re.search(r"\bHyper\b", page_text, re.IGNORECASE):
            continue
        if not re.search(
            r"(?:Routine|Remake).*복습\s*Test",
            page_text,
            re.IGNORECASE | re.DOTALL,
        ):
            continue
        cover_pages.add(page_idx)
        logger.info("PDF_ACADEMY_REVIEW_COVER_FOUND | page=%d", page_idx)
    return cover_pages


def _resolve_number_conflicts(questions: List[Dict], *, source: str) -> None:
    """
    번호 중복을 in-place로 해결.

    페이지 순서(page_index, bbox.y) 기준으로 먼저 등장한 번호는 유지,
    뒤에 등장한 중복 번호만 다음 가용 번호로 재할당.

    기존의 "모든 번호 sequential 재할당"은 파괴적이어서 실제 번호 정보를 소실 →
    surgical dedup으로 교체.
    """
    if len(questions) <= 1:
        return

    before = [q["number"] for q in questions]
    if len(set(before)) == len(before):
        return  # 중복 없음

    # 페이지 순서로 정렬 (안전장치 — 호출자가 정렬했어도 보장)
    questions.sort(key=lambda q: (q.get("page_index", 0), q.get("bbox", [0, 0])[1]))

    max_num = max(before)
    seen: set[int] = set()
    next_free = max_num + 1
    changes: List[Tuple[int, int]] = []

    for q in questions:
        num = q["number"]
        if num not in seen:
            seen.add(num)
            # original_number 기록 (충돌 없던 경우에도 일관성 위해)
            q.setdefault("meta", {})["original_number"] = num
            continue
        # 중복 발견 — 다음 가용 번호 할당, 원본 번호는 meta에 보존
        while next_free in seen:
            next_free += 1
        q.setdefault("meta", {})["original_number"] = num
        changes.append((num, next_free))
        q["number"] = next_free
        seen.add(next_free)
        next_free += 1

    if changes:
        logger.warning(
            "%s_DEDUP | conflicts_resolved=%d | changes=%s",
            source, len(changes), changes[:10],
        )


def _extract_ocr_page_texts(pages: List[Dict]) -> Dict[int, str]:
    """Return OCR text needed for explanation parsing on scan-only pages.

    Question boxes and numbers already come from ``segment_questions_multipage``;
    this helper intentionally does not create a second segmentation result.
    """
    if not pages:
        return {}

    try:
        from academy.adapters.ai.detection.segment_ocr import is_ocr_available
        from academy.adapters.ai.ocr.google import google_ocr_blocks
    except ImportError as e:
        logger.warning("OCR_TEXT_IMPORT_FAIL | %s", e)
        return {}

    if not is_ocr_available():
        logger.info("OCR_TEXT_SKIP | reason=no_credentials")
        return {}

    page_texts: Dict[int, str] = {}

    for page in pages:
        image_path = page.get("image_path")
        page_idx = page.get("page_index", 0)
        if not image_path:
            continue

        try:
            blocks = google_ocr_blocks(image_path)
            if blocks:
                sorted_blocks = sorted(blocks, key=lambda b: (b.y0, b.x0))
                page_texts[page_idx] = "\n".join(b.text for b in sorted_blocks)
        except Exception as e:
            logger.warning(
                "OCR_TEXT_PAGE_FAIL | page=%d | error=%s",
                page_idx, e,
            )

    return page_texts


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
    *,
    excluded_page_indexes: Optional[set[int]] = None,
) -> List[Dict]:
    """
    공통 dispatcher가 확정한 박스와 번호로 문항 목록을 만든다.

    전략:
    1. dispatcher의 numbers를 우선 사용한다.
    2. 번호가 없는 OpenCV 박스만 텍스트 매칭 후 순차 번호를 사용한다.
    3. 정답·해설 꼬리 페이지는 문항 후보에서 제외한다.
    """
    questions = []
    global_number = 0
    excluded_page_indexes = excluded_page_indexes or set()

    for page in pages:
        page_idx = page["page_index"]
        if page_idx in excluded_page_indexes:
            logger.info("QUESTION_LIST_SKIP_SOLUTION_PAGE | page=%d", page_idx)
            continue
        boxes = page["boxes"]
        segmented_numbers = list(page.get("numbers") or [])
        text_blocks = text_blocks_by_page.get(page_idx, [])

        for box_index, bbox in enumerate(boxes):
            global_number += 1

            detected_number = (
                segmented_numbers[box_index]
                if box_index < len(segmented_numbers)
                else None
            )
            matched_text = None

            if detected_number is None and text_blocks:
                detected_number, matched_text = _match_text_to_bbox(
                    bbox, text_blocks,
                )

            number = int(detected_number or global_number)
            questions.append({
                "number": number,
                "bbox": bbox,
                "page_index": page_idx,
                "text": matched_text,
                "meta": {"original_number": number},
            })

    _resolve_number_conflicts(questions, source="SEGMENTATION_RESULT")

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
    *,
    solution_tail_start: Optional[int] = None,
) -> List[Dict]:
    """
    PDF 텍스트에서 해설 섹션을 찾고, 개별 해설을 번호별로 추출.

    Returns:
        [{ "question_number": int, "text": str, "page_index": int }]
    """
    explanations = []

    for page_idx in sorted(full_text_by_page):
        full_text = full_text_by_page[page_idx]
        if not full_text:
            continue

        in_solution_tail = (
            solution_tail_start is not None
            and page_idx >= solution_tail_start
        )
        marker_match = _EXPLANATION_MARKERS.search(full_text)
        if in_solution_tail:
            # 꼬리의 첫 장에만 표제가 있고 다음 장들은 번호부터 이어지는
            # 실사용 교사용 PDF를 끝까지 해설로 읽는다.
            explanation_section = (
                full_text[marker_match.end():]
                if marker_match
                else full_text
            )
        else:
            if not marker_match:
                continue
            explanation_section = full_text[marker_match.end():]

        logger.info(
            "EXPLANATION_SECTION_FOUND | page=%d | start_pos=%d | length=%d",
            page_idx,
            marker_match.start() if marker_match else 0,
            len(explanation_section),
        )

        # 개별 해설 추출 (번호별)
        matches = list(_EXPLANATION_NUM_RE.finditer(explanation_section))

        if not matches:
            # 번호 없이 해설 전체를 하나로 취급
            clean_text = explanation_section.strip()
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

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

    # PDF/이미지 판별 + 페이지 이미지 렌더링 (크롭에 필요)
    seg_result = segment_questions_multipage(local_path)
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

    if is_pdf:
        text_blocks_by_page, full_text_by_page = _extract_pdf_text(local_path)

        # 텍스트 기반 문항 분할 시도 (question_splitter 사용)
        questions = _split_questions_by_text(local_path, text_blocks_by_page)
        if questions:
            logger.info(
                "PDF_TEXT_SPLIT_OK | job_id=%s | questions=%d",
                job.id, len(questions),
            )
            # 텍스트 기반 성공 → pages의 boxes를 questions bbox로 교체
            # (pages의 image_path는 크롭에 사용)
        else:
            logger.info(
                "PDF_TEXT_SPLIT_EMPTY | job_id=%s | falling back to OpenCV",
                job.id,
            )

    # 텍스트 기반 실패 또는 이미지 파일 → OpenCV/YOLO fallback
    if not questions:
        questions = _build_question_list(pages, text_blocks_by_page)

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

    explanations = _extract_explanations(full_text_by_page) if is_pdf else []

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


def _is_non_question_page(blocks: List[Dict]) -> bool:
    """
    비문항 페이지 감지 — 표지, 진도표, 안내문, 강의 운영 방침 등.

    휴리스틱:
    - 문항 관련 키워드가 있으면 문항 페이지로 간주
    - 비문항 키워드만 있으면 비문항 페이지로 간주
    - 문항 번호 뒤에 보기 패턴(①②③④⑤, (1)(2)(3) 등)이 없으면 목차일 가능성 높음
    """
    full_text = " ".join(b["text"] for b in blocks).strip()
    if not full_text:
        return True

    import re

    # 정답지 감지 (최우선): "⑴ × ⑵ O" "⑴ ② ⑵ ④" 같은 정답 표기 패턴이 반복
    # 정답지에도 ①②③이 포함되므로 보기 체크보다 먼저 수행해야 함
    answer_pattern = re.findall(r"[⑴⑵⑶⑷⑸⑹⑺⑻⑼]\s*[×OX①②③④⑤]", full_text)
    if len(answer_pattern) >= 5:
        return True

    # 해설지 감지 (정답지 다음으로 우선): "번호. ⑴ ...이다." 소문항 패턴
    sub_q_pattern = re.findall(r"\d+\.\s*[⑴⑵⑶⑷⑸⑹⑺⑻⑼]", full_text)
    if len(sub_q_pattern) >= 2:
        # 문항 지시문이 없으면 해설지 확정
        question_indicators_early = [
            "옳은 것", "구하시오", "표시하시오", "고르시오", "서술하시오",
            "풀이 과정", "이에 대한 설명", "다음 중", "보기에서",
        ]
        if not any(kw in full_text for kw in question_indicators_early):
            return True

    # 문항 페이지 강력 지표: 보기 번호 패턴이 있으면 문항 페이지
    choice_patterns = [
        "①", "②", "③", "④", "⑤",
        "ㄱ.", "ㄴ.", "ㄷ.",
    ]
    has_choices = any(p in full_text for p in choice_patterns)

    # 문항 지표: "옳은 것", "구하시오", "표시하시오", "고르시오" 등
    question_indicators = [
        "옳은 것", "구하시오", "표시하시오", "고르시오", "서술하시오",
        "풀이 과정", "이에 대한 설명", "다음 중", "보기에서",
    ]
    has_question_indicator = any(kw in full_text for kw in question_indicators)

    if has_choices or has_question_indicator:
        return False  # 문항 페이지

    # 설명조 종결어미 빈도 기반 해설지 감지
    explanation_markers = re.findall(r"(?:이므로|때문이다|따라서|그러므로|해설|나타난다|관측된다|생성된다)", full_text)
    if len(explanation_markers) >= 3 and not has_question_indicator:
        return True

    # 비문항 지표: 진도표, 강의방침, 안내 등
    non_question_indicators = [
        "진도", "운영 방침", "재시험", "클리닉", "홈페이지",
        "대단원", "중단원", "세부 내용", "난이도",
        "주차", "복습과제", "워크북",
    ]
    non_q_count = sum(1 for kw in non_question_indicators if kw in full_text)

    # 비문항 키워드가 3개 이상이면 비문항 페이지
    if non_q_count >= 3:
        return True

    return False


def _split_questions_by_text(
    pdf_path: str,
    text_blocks_by_page: Dict[int, List[Dict]],
) -> List[Dict]:
    """
    PDF 텍스트 블록 기반 문항 분할 (question_splitter 활용).

    텍스트에서 문항 번호 패턴("1.", "2)", "(1)" 등)을 찾아
    문항 경계를 결정한다. OpenCV보다 정확하며 2단 레이아웃도 처리.

    bbox는 PDF 좌표계(points)이므로 이미지 좌표로 변환 필요.
    (200 DPI 렌더링 기준: scale = 200/72)

    Returns:
        [{ "number": int, "bbox": [x,y,w,h], "page_index": int, "text": str? }]
        빈 리스트면 텍스트 기반 분할 실패 → OpenCV fallback 사용.
    """
    try:
        from academy.adapters.tools.pymupdf_renderer import PdfDocument
        from academy.domain.tools.question_splitter import (
            split_questions,
            TextBlock as SplitterTextBlock,
        )
    except ImportError as e:
        logger.warning("TEXT_SPLIT_IMPORT_FAIL | %s", e)
        return []

    if not text_blocks_by_page:
        return []

    scale = 200.0 / 72.0  # PDF points → 200 DPI pixels

    all_questions: List[Dict] = []

    try:
        with PdfDocument(pdf_path) as doc:
            for page_idx in range(doc.page_count()):
                blocks_raw = text_blocks_by_page.get(page_idx, [])
                if not blocks_raw:
                    continue

                pw, ph = doc.page_dimensions(page_idx)

                # 비문항 페이지 필터: 표지, 진도표, 안내문 등 스킵
                if _is_non_question_page(blocks_raw):
                    logger.info("TEXT_SPLIT_SKIP_PAGE | page=%d | non-question page", page_idx)
                    continue

                # Dict → SplitterTextBlock 변환
                splitter_blocks = [
                    SplitterTextBlock(
                        text=b["text"],
                        x0=b["x0"], y0=b["y0"],
                        x1=b["x1"], y1=b["y1"],
                    )
                    for b in blocks_raw
                ]

                regions = split_questions(
                    text_blocks=splitter_blocks,
                    page_width=pw,
                    page_height=ph,
                    page_index=page_idx,
                )

                for region in regions:
                    # bbox: (x0, y0, x1, y1) in PDF points → (x, y, w, h) in pixels
                    rx0, ry0, rx1, ry1 = region.bbox
                    x = int(rx0 * scale)
                    y = int(ry0 * scale)
                    w = int((rx1 - rx0) * scale)
                    h = int((ry1 - ry0) * scale)

                    all_questions.append({
                        "number": region.number,
                        "bbox": [x, y, w, h],
                        "page_index": page_idx,
                        "text": None,
                    })
    except Exception as e:
        logger.warning("TEXT_SPLIT_ERROR | %s", e)
        return []

    if not all_questions:
        return []

    # 중복 번호 정리
    numbers = [q["number"] for q in all_questions]
    if len(set(numbers)) != len(numbers):
        logger.warning("TEXT_SPLIT_DEDUP | %s → sequential", numbers)
        for i, q in enumerate(all_questions):
            q["number"] = i + 1

    logger.info("TEXT_SPLIT_DONE | questions=%d", len(all_questions))
    return all_questions


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

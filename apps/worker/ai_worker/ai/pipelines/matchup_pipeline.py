# PATH: apps/worker/ai_worker/ai/pipelines/matchup_pipeline.py
# 매치업 분석 파이프라인 — 문제 분할 + OCR + 임베딩
"""
1. 다운로드     (10%)
2. 문제 분할    (30%)
3. OCR          (50%)
4. 임베딩       (80%)
5. 이미지 업로드 (90%)
6. 완료         (100%)
"""
from __future__ import annotations

import io
import logging
from typing import Any, Callable, Dict, List, Tuple

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)


def run_matchup_pipeline(
    *,
    job: AIJob,
    local_path: str,
    payload: Dict[str, Any],
    tenant_id: str | None,
    record_progress: Callable,
) -> AIResult:
    """매치업 문서 분석: 문제 분할 → OCR → 임베딩."""
    job_id = str(job.id)
    document_id = payload.get("document_id", "")

    # ── Step 1: 문제 분할 (30%) ──
    record_progress(
        job_id, "segmentation", 20,
        step_index=1, step_total=5,
        step_name_display="문제 분할",
        step_percent=0, tenant_id=tenant_id,
    )

    from apps.worker.ai_worker.ai.detection.segment_dispatcher import (
        segment_questions_multipage,
    )

    seg_result = segment_questions_multipage(local_path)
    pages = seg_result.get("pages", [])
    total_boxes = seg_result.get("total_boxes", 0)

    record_progress(
        job_id, "segmentation", 30,
        step_index=1, step_total=5,
        step_name_display="문제 분할",
        step_percent=100, tenant_id=tenant_id,
    )

    if total_boxes == 0:
        # 문제를 찾지 못한 경우 — 전체 페이지를 하나의 문제로 취급
        logger.info("MATCHUP_NO_BOXES | job_id=%s | treating whole pages as problems", job_id)
        questions_raw = _whole_pages_as_questions(pages)
    else:
        questions_raw = _boxes_to_questions(pages)

    if not questions_raw:
        return AIResult.done(job_id, {
            "problems": [],
            "document_id": document_id,
            "problem_count": 0,
        })

    # ── Step 2: OCR (50%) ──
    record_progress(
        job_id, "ocr", 40,
        step_index=2, step_total=5,
        step_name_display="텍스트 추출",
        step_percent=0, tenant_id=tenant_id,
    )

    _extract_texts(questions_raw, job_id)

    record_progress(
        job_id, "ocr", 50,
        step_index=2, step_total=5,
        step_name_display="텍스트 추출",
        step_percent=100, tenant_id=tenant_id,
    )

    # ── Step 3: 임베딩 (80%) ──
    record_progress(
        job_id, "embedding", 60,
        step_index=3, step_total=5,
        step_name_display="AI 분석",
        step_percent=0, tenant_id=tenant_id,
    )

    _generate_embeddings(questions_raw, job_id)

    record_progress(
        job_id, "embedding", 80,
        step_index=3, step_total=5,
        step_name_display="AI 분석",
        step_percent=100, tenant_id=tenant_id,
    )

    # ── Step 4: 이미지 업로드 (90%) ──
    record_progress(
        job_id, "upload_images", 85,
        step_index=4, step_total=5,
        step_name_display="이미지 저장",
        step_percent=0, tenant_id=tenant_id,
    )

    _upload_cropped_images(questions_raw, tenant_id, document_id, job_id)

    record_progress(
        job_id, "upload_images", 90,
        step_index=4, step_total=5,
        step_name_display="이미지 저장",
        step_percent=100, tenant_id=tenant_id,
    )

    # ── Step 5: 결과 반환 (100%) ──
    problems = []
    for q in questions_raw:
        problems.append({
            "number": q["number"],
            "text": q.get("text", ""),
            "image_key": q.get("image_key", ""),
            "embedding": q.get("embedding"),
            "meta": {
                "page_index": q.get("page_index", 0),
                "bbox": q.get("bbox"),
            },
        })

    record_progress(
        job_id, "done", 100,
        step_index=5, step_total=5,
        step_name_display="완료",
        step_percent=100, tenant_id=tenant_id,
    )

    # 세그멘테이션 방식 — UI 표시 + 운영 관측용
    has_text_pages = sum(1 for p in pages if p.get("has_embedded_text"))
    scan_pages = len(pages) - has_text_pages
    if not problems:
        segmentation_method = "none"
    elif seg_result.get("is_pdf"):
        if has_text_pages == len(pages):
            segmentation_method = "text"
        elif has_text_pages == 0:
            segmentation_method = "ocr"
        else:
            segmentation_method = "mixed"
    else:
        segmentation_method = "image"

    return AIResult.done(job_id, {
        "problems": problems,
        "document_id": document_id,
        "problem_count": len(problems),
        "segmentation_method": segmentation_method,
    })


# ── 내부 함수 ────────────────────────────────────────


def _boxes_to_questions(pages: List[Dict]) -> List[Dict]:
    """세그멘테이션 결과를 문제 리스트로 변환."""
    questions = []
    q_num = 1
    for page in pages:
        page_idx = page["page_index"]
        img_path = page["image_path"]
        for bbox in page.get("boxes", []):
            questions.append({
                "number": q_num,
                "page_index": page_idx,
                "image_path": img_path,
                "bbox": list(bbox),
            })
            q_num += 1
    return questions


def _whole_pages_as_questions(pages: List[Dict]) -> List[Dict]:
    """세그멘테이션 실패 시 전체 페이지를 하나의 문제로."""
    questions = []
    for i, page in enumerate(pages):
        questions.append({
            "number": i + 1,
            "page_index": page["page_index"],
            "image_path": page["image_path"],
            "bbox": None,  # 전체 페이지
        })
    return questions


def _extract_texts(questions: List[Dict], job_id: str) -> None:
    """
    bbox 기반 OCR 블록 매칭으로 문항별 텍스트 추출.

    접근:
      1. 페이지별 OCR 블록(줄 단위 bbox)을 한 번에 획득 (lru_cache 덕에 dispatcher와
         중복 호출 없음)
      2. 각 문항 bbox와 겹치는 블록을 모아 텍스트 연결
      3. bbox 없는 문항은 페이지 전체 텍스트 할당

    블록 기반은 페이지 전체 텍스트 + 정규식 번호 분할(legacy) 보다 정확.
    2단 레이아웃/그림/서답형 등에서 텍스트가 정확한 문항에 매핑된다.
    """
    blocks_backend = _load_ocr_blocks_backend()
    if blocks_backend is None:
        logger.info(
            "MATCHUP_TEXT_LEGACY | job_id=%s | OCR blocks unavailable, using legacy path",
            job_id,
        )
        _extract_texts_legacy(questions, job_id)
        return

    # 페이지별 OCR 블록 캐싱 (이미 google_ocr_blocks에 lru_cache 존재 — 추가 보험)
    page_blocks_cache: Dict[int, list] = {}
    page_images: Dict[int, str] = {}

    for q in questions:
        pi = q.get("page_index", 0)
        if pi not in page_images:
            page_images[pi] = q["image_path"]

    for pi, img_path in page_images.items():
        try:
            page_blocks_cache[pi] = blocks_backend(img_path)
        except Exception:
            logger.warning(
                "MATCHUP_TEXT_OCR_FAIL | job_id=%s | page=%d",
                job_id, pi, exc_info=True,
            )
            page_blocks_cache[pi] = []

    # 문항별로 bbox에 겹치는 블록만 연결
    for q in questions:
        pi = q.get("page_index", 0)
        blocks = page_blocks_cache.get(pi, [])
        bbox = q.get("bbox")

        if not blocks:
            q["text"] = ""
            continue

        if not bbox:
            q["text"] = "\n".join(b.text for b in blocks)
            continue

        bx, by, bw, bh = bbox
        bx1, by1 = bx + bw, by + bh

        relevant: List[Tuple[float, float, str]] = []
        for blk in blocks:
            ox = max(0.0, min(float(bx1), blk.x1) - max(float(bx), blk.x0))
            oy = max(0.0, min(float(by1), blk.y1) - max(float(by), blk.y0))
            overlap = ox * oy
            block_area = max(1.0, (blk.x1 - blk.x0) * (blk.y1 - blk.y0))
            if overlap / block_area >= 0.5:
                relevant.append((blk.y0, blk.x0, blk.text))

        relevant.sort(key=lambda t: (t[0], t[1]))
        q["text"] = "\n".join(t[2] for t in relevant)

    # 여전히 텍스트가 없는 문항은 페이지 전체 텍스트로 폴백
    for q in questions:
        if q.get("text"):
            continue
        pi = q.get("page_index", 0)
        blocks = page_blocks_cache.get(pi, [])
        if blocks:
            q["text"] = "\n".join(b.text for b in blocks)
        else:
            q["text"] = ""


def _load_ocr_blocks_backend():
    """google_ocr_blocks를 반환. 임포트 실패 시 None."""
    try:
        from apps.worker.ai_worker.ai.ocr.google import google_ocr_blocks
        return google_ocr_blocks
    except ImportError:
        return None


def _extract_texts_legacy(questions: List[Dict], job_id: str) -> None:
    """Vision SDK가 없는 환경용 레거시 경로 — 전체 페이지 OCR + 정규식 번호 분할."""
    try:
        from apps.worker.ai_worker.ai.ocr.google import google_ocr
    except ImportError:
        from apps.worker.ai_worker.ai.ocr.tesseract import tesseract_ocr as google_ocr

    page_texts: Dict[int, str] = {}
    page_images: Dict[int, str] = {}
    for q in questions:
        pi = q.get("page_index", 0)
        if pi not in page_images:
            page_images[pi] = q["image_path"]

    for pi, img_path in page_images.items():
        try:
            result = google_ocr(img_path)
            page_texts[pi] = result.text if hasattr(result, "text") else str(result)
        except Exception:
            logger.warning(
                "Page OCR failed for page %d in job %s",
                pi, job_id, exc_info=True,
            )
            page_texts[pi] = ""

    for q in questions:
        pi = q.get("page_index", 0)
        full_text = page_texts.get(pi, "")
        if not full_text:
            q["text"] = ""
            continue
        if not q.get("bbox"):
            q["text"] = full_text
            continue
        q["text"] = _extract_text_for_question(full_text, q["number"], len(questions))

    for q in questions:
        if not q.get("text") and questions:
            pi = q.get("page_index", 0)
            q["text"] = page_texts.get(pi, "")


def _extract_text_for_question(full_text: str, q_number: int, total: int) -> str:
    """전체 OCR 텍스트에서 문제 번호 기반으로 해당 문제 텍스트 추출."""
    import re
    lines = full_text.split("\n")

    # 문제 번호 패턴: "1.", "1)", "Q1", "문제 1" 등
    patterns = [
        rf"^{q_number}\s*[\.\):]",
        rf"^{q_number}\s",
        rf"^Q{q_number}[\.\s]",
    ]
    next_patterns = [
        rf"^{q_number + 1}\s*[\.\):]",
        rf"^{q_number + 1}\s",
        rf"^Q{q_number + 1}[\.\s]",
    ] if q_number < total else []

    start_idx = None
    end_idx = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if start_idx is None:
            for p in patterns:
                if re.match(p, stripped):
                    start_idx = i
                    break
        elif next_patterns:
            for p in next_patterns:
                if re.match(p, stripped):
                    end_idx = i
                    break
            if end_idx != len(lines):
                break

    if start_idx is not None:
        return "\n".join(lines[start_idx:end_idx]).strip()
    return ""


def _generate_embeddings(questions: List[Dict], job_id: str) -> None:
    """문제 텍스트에서 임베딩 생성 (in-place)."""
    from apps.worker.ai_worker.ai.embedding.service import get_embeddings

    texts = [q.get("text", "") for q in questions]
    non_empty = [(i, t) for i, t in enumerate(texts) if t.strip()]

    if not non_empty:
        for q in questions:
            q["embedding"] = None
        return

    try:
        batch = get_embeddings([t for _, t in non_empty])
        idx_map = {orig_idx: vec_idx for vec_idx, (orig_idx, _) in enumerate(non_empty)}

        for i, q in enumerate(questions):
            if i in idx_map:
                q["embedding"] = batch.vectors[idx_map[i]]
            else:
                q["embedding"] = None
    except Exception:
        logger.warning("Embedding generation failed for job %s", job_id, exc_info=True)
        for q in questions:
            q["embedding"] = None


def _upload_cropped_images(
    questions: List[Dict],
    tenant_id: str | None,
    document_id: str,
    job_id: str,
) -> None:
    """크롭된 문제 이미지를 R2에 업로드 (in-place로 image_key 설정)."""
    import cv2
    import uuid as _uuid

    try:
        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
    except ImportError:
        logger.warning("R2 storage not available, skipping image upload")
        return

    uuid_prefix = str(_uuid.uuid4())

    for q in questions:
        try:
            img = cv2.imread(q["image_path"])
            if img is None:
                continue

            if q.get("bbox"):
                x, y, w, h = q["bbox"]
                img_h, img_w = img.shape[:2]
                x, y = max(0, int(x)), max(0, int(y))
                x2, y2 = min(img_w, x + int(w)), min(img_h, y + int(h))
                if x2 > x and y2 > y:
                    img = img[y:y2, x:x2]

            success, buf = cv2.imencode(".png", img)
            if not success:
                continue

            r2_key = (
                f"tenants/{tenant_id}/matchup/{uuid_prefix}"
                f"/problems/{q['number']}.png"
            )

            upload_fileobj_to_r2_storage(
                fileobj=io.BytesIO(buf.tobytes()),
                key=r2_key,
                content_type="image/png",
            )
            q["image_key"] = r2_key

        except Exception:
            logger.warning(
                "Image upload failed for Q%d in job %s",
                q["number"], job_id, exc_info=True,
            )

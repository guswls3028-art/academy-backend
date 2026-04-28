# PATH: apps/worker/ai_worker/ai/pipelines/matchup_manual_index.py
# 수동 크롭으로 만든 단일 MatchupProblem 이미지 → OCR + 임베딩.
"""
payload: {problem_id, tenant_id, image_key}

수동 크롭은 동기적으로 problem 레코드 + R2 이미지를 만들지만 임베딩이 비어
있어 매치업 검색에 노출되지 않는다. 이 잡이 비동기로 OCR + 임베딩을 채워
검색 풀에 합류시킨다.

흐름:
  1. R2에서 problem 이미지 다운로드 (presigned URL)
  2. Google Vision OCR 호출 → 텍스트 추출
  3. text_for_embedding 정제 → 임베딩 생성
  4. 결과 dict 반환 → callback이 DB 업데이트
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Callable, Dict

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)


def _preprocess_camera_image(image_path: str) -> str:
    """카메라 사진 OCR 전처리 — deskew + 명도 균일화 + sharpen.

    LLM 사용 안 함, OpenCV 기반. 카메라 촬영본은 회전(EXIF 처리됨)에 더해
    작은 기울기 + 그림자 + 초점 흐림이 OCR 정확도를 떨어뜨림.

    적용 단계:
      1. CLAHE (Contrast Limited Adaptive Histogram Equalization)로 명도 균일화
         → 종이 한쪽 그림자 / 조명 불균일 보정
      2. minAreaRect 기반 deskew — 1~15도 기울기만 보정 (오버피팅 방지)
      3. Unsharp mask로 초점 흐림 보정

    PDF 페이지 같은 깨끗한 이미지는 영향 미미 (deskew 0도 → skip).

    Returns: 전처리된 임시 파일 경로 (호출자가 cleanup 책임).
             실패 시 원본 경로 반환.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        logger.warning("preprocess: cv2/numpy 미설치 — 전처리 skip")
        return image_path

    img = cv2.imread(image_path)
    if img is None:
        logger.warning("preprocess: cv2.imread 실패 — 전처리 skip")
        return image_path

    h, w = img.shape[:2]
    if h < 50 or w < 50:
        return image_path  # 너무 작은 이미지

    # 1. 명도 균일화 (CLAHE 적용)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_eq = clahe.apply(gray)

    # 2. Deskew — Otsu binary로 텍스트 픽셀 검출 후 minAreaRect 회전각 추정
    _, binary = cv2.threshold(gray_eq, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(binary > 0))
    angle_corrected = 0.0
    if len(coords) > 200:
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        # OpenCV minAreaRect angle 정규화 (-90~0 → 0~90 또는 작은 양수)
        if angle < -45:
            angle = 90 + angle
        # 1~15도 범위만 보정 (false positive 방어)
        if 1.0 < abs(angle) < 15.0:
            center = (w // 2, h // 2)
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            img = cv2.warpAffine(
                img, M, (w, h),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REPLICATE,
            )
            angle_corrected = angle

    # 3. Unsharp mask (초점 흐림 보정 — 약하게)
    blurred = cv2.GaussianBlur(img, (0, 0), 3)
    img = cv2.addWeighted(img, 1.4, blurred, -0.4, 0)

    # 임시 파일 저장 (PNG)
    fd, out_path = tempfile.mkstemp(suffix="_preocr.png")
    os.close(fd)
    if not cv2.imwrite(out_path, img):
        logger.warning("preprocess: cv2.imwrite 실패 — 원본 사용")
        return image_path

    logger.info(
        "MATCHUP_MANUAL_PREPROCESS | original=%s | preocr=%s | "
        "deskew=%.2fdeg | size=%dx%d",
        image_path, out_path, angle_corrected, w, h,
    )
    return out_path


def run_matchup_manual_index(
    *,
    job: AIJob,
    payload: Dict[str, Any],
    tenant_id: str | None,
    record_progress: Callable,
) -> AIResult:
    job_id = str(job.id)
    problem_id = payload.get("problem_id")
    image_key = payload.get("image_key") or ""
    is_camera_capture = bool(payload.get("is_camera_capture"))

    if not problem_id:
        return AIResult.failed(job_id, "problem_id missing")
    if not image_key:
        return AIResult.failed(job_id, "image_key missing")

    record_progress(
        job_id, "downloading", 10,
        step_index=1, step_total=3,
        step_name_display="이미지 다운로드",
        step_percent=0, tenant_id=tenant_id,
    )

    # 1) 이미지 다운로드
    try:
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
    except ImportError:
        return AIResult.failed(job_id, "R2 storage not available")

    from apps.worker.ai_worker.storage.downloader import (
        cleanup_tmp_for_path,
        download_to_tmp,
    )

    url = generate_presigned_get_url_storage(key=image_key, expires_in=600)
    if not url:
        return AIResult.failed(job_id, "presign failed")

    local_path = None
    try:
        local_path = download_to_tmp(download_url=url, job_id=job_id)
    except Exception as e:
        return AIResult.failed(job_id, f"download failed: {e}")

    record_progress(
        job_id, "ocr", 40,
        step_index=2, step_total=3,
        step_name_display="텍스트 추출",
        step_percent=0, tenant_id=tenant_id,
    )

    # 2) OCR — 카메라 사진이면 전처리 적용 (deskew + 명도 균일화 + sharpen)
    text = ""
    preprocessed_path: str | None = None
    try:
        from apps.worker.ai_worker.ai.ocr.google import google_ocr
        ocr_input_path = local_path
        if is_camera_capture:
            preprocessed_path = _preprocess_camera_image(local_path)
            if preprocessed_path != local_path:
                ocr_input_path = preprocessed_path
        result = google_ocr(ocr_input_path)
        text = (result.text or "").strip() if hasattr(result, "text") else ""
    except Exception:
        logger.warning("manual_index: google_ocr failed for problem=%s", problem_id, exc_info=True)
        text = ""
    finally:
        cleanup_tmp_for_path(local_path)
        if preprocessed_path and preprocessed_path != local_path:
            try:
                os.unlink(preprocessed_path)
            except OSError:
                pass

    record_progress(
        job_id, "embedding", 70,
        step_index=3, step_total=3,
        step_name_display="AI 분석",
        step_percent=0, tenant_id=tenant_id,
    )

    # 3) 임베딩 — 정제 텍스트 사용
    embedding = None
    cleaned = ""
    if text:
        try:
            from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import (
                normalize_text_for_embedding,
                detect_format,
            )
            cleaned = normalize_text_for_embedding(text)
        except Exception:
            cleaned = text

        if cleaned.strip():
            try:
                from apps.worker.ai_worker.ai.embedding.service import get_embeddings
                batch = get_embeddings([cleaned])
                if batch.vectors:
                    embedding = batch.vectors[0]
            except Exception:
                logger.warning("manual_index: embedding failed for problem=%s", problem_id, exc_info=True)

    fmt = "choice"
    try:
        from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import detect_format
        fmt = detect_format(text)
    except Exception:
        pass

    # 이미지 CLIP 임베딩 — OCR 텍스트 약해도 시각 매칭 보강
    image_embedding = None
    try:
        from apps.worker.ai_worker.ai.embedding.image_service import get_image_embeddings
        # local_path는 이미 cleanup_tmp_for_path로 정리됐을 수 있음. 다시 다운로드.
        local_path2 = download_to_tmp(download_url=url, job_id=job_id + "_img")
        try:
            ocr_input_path2 = local_path2
            if is_camera_capture:
                pre = _preprocess_camera_image(local_path2)
                if pre != local_path2:
                    ocr_input_path2 = pre
            batch = get_image_embeddings([ocr_input_path2])
            if batch.vectors and batch.vectors[0]:
                image_embedding = batch.vectors[0]
            if ocr_input_path2 != local_path2:
                try: os.unlink(ocr_input_path2)
                except OSError: pass
        finally:
            cleanup_tmp_for_path(local_path2)
    except Exception:
        logger.warning("manual_index: image embedding failed for problem=%s", problem_id, exc_info=True)

    record_progress(
        job_id, "done", 100,
        step_index=3, step_total=3,
        step_name_display="완료",
        step_percent=100, tenant_id=tenant_id,
    )

    return AIResult.done(job_id, {
        "problem_id": int(problem_id),
        "text": text,
        "embedding": embedding,
        "image_embedding": image_embedding,
        "format": fmt,
    })

# apps/worker/ai_worker/ai/pipelines/dispatcher.py
from __future__ import annotations

from typing import Any, Dict

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

from apps.worker.ai_worker.ai.config import AIConfig
from apps.worker.ai_worker.ai.ocr.google import google_ocr
from apps.worker.ai_worker.ai.ocr.tesseract import tesseract_ocr
from apps.worker.ai_worker.ai.detection.segment_dispatcher import (
    begin_pdf_seg_scope,
    cleanup_registered_pdf_seg_tmp_dirs,
    segment_questions,
    segment_questions_multipage,
)
from apps.worker.ai_worker.ai.handwriting.detector import analyze_handwriting
from apps.worker.ai_worker.ai.embedding.service import get_embeddings
from apps.worker.ai_worker.ai.problem.generator import generate_problem_from_ocr
from apps.worker.ai_worker.ai.pipelines.homework_video_analyzer import analyze_homework_video
from apps.worker.ai_worker.ai.pipelines.excel_handler import handle_excel_parsing_job
from apps.worker.ai_worker.ai.pipelines.excel_export_handler import (
    handle_attendance_excel_export,
    handle_staff_excel_export,
)
from apps.worker.ai_worker.ai.pipelines.ppt_handler import handle_ppt_generation_job
from apps.worker.ai_worker.ai.utils.image_resizer import resize_if_large, imread_exif_aware
from apps.worker.ai_worker.storage.downloader import cleanup_tmp_for_path, download_to_tmp
import logging

logger = logging.getLogger(__name__)

# 구간별 진행률 기록 함수 (공통)
def _record_progress(
    job_id: str,
    step: str,
    percent: int,
    step_index: int | None = None,
    step_total: int | None = None,
    step_name_display: str | None = None,
    step_percent: int | None = None,
    tenant_id: str | None = None,  # ✅ 추가: tenant_id 전달
) -> None:
    """Redis 진행률 기록 (우하단 실시간 프로그래스바용). 구간별 진행률 지원."""
    try:
        from academy.adapters.cache.redis_progress_adapter import RedisProgressAdapter
        extra = {"percent": percent}
        if step_index is not None and step_total is not None:
            extra.update({
                "step_index": step_index,
                "step_total": step_total,
                "step_name": step,
                "step_name_display": step_name_display or step,
                "step_percent": step_percent if step_percent is not None else 100,
            })
        # ✅ tenant_id 전달 (tenant namespace 키 사용)
        tenant_id_str = str(tenant_id) if tenant_id else None
        RedisProgressAdapter().record_progress(job_id, step, extra, tenant_id=tenant_id_str)
    except Exception as e:
        logger.debug("Redis progress record skip: %s", e)


def handle_ai_job(job: AIJob) -> AIResult:
    # download_to_tmp가 만든 mkdtemp 부모 디렉터리 + segment_questions_multipage가
    # 만든 pdf-seg-* 디렉터리들을 finally에서 일괄 제거.
    # 정리 누락 시 워커 인스턴스 디스크가 점진적으로 가득 차 연쇄 실패 발생.
    local_path: str | None = None
    begin_pdf_seg_scope()
    try:
        # ✅ tenant_id 추출 (payload 우선, 없으면 job.tenant_id)
        payload: Dict[str, Any] = job.payload or {}
        tenant_id = str(payload.get("tenant_id") or job.tenant_id or "") if (payload.get("tenant_id") or job.tenant_id) else None
        
        # 작업 분기: type / task / job_type 으로 EXCEL_PARSING vs AI 작업 분리
        job_type_lower = (job.type or "").strip().lower()
        if job_type_lower == "excel_parsing":
            return handle_excel_parsing_job(job)
        if job_type_lower == "attendance_excel_export":
            return handle_attendance_excel_export(job)
        if job_type_lower == "staff_excel_export":
            return handle_staff_excel_export(job)
        if job_type_lower == "ppt_generation":
            return handle_ppt_generation_job(job)

        # Matchup index exam (download 불필요 — DB에서 직접 읽음)
        if job.type == "matchup_index_exam":
            from apps.worker.ai_worker.ai.pipelines.matchup_index_exam import (
                run_matchup_index_exam,
            )
            return run_matchup_index_exam(
                job=job,
                payload=payload,
                tenant_id=tenant_id,
                record_progress=_record_progress,
            )

        # Matchup manual index (수동 크롭 problem → OCR + 임베딩)
        # download_url 불필요 — payload.image_key로 직접 R2 접근.
        if job.type == "matchup_manual_index":
            from apps.worker.ai_worker.ai.pipelines.matchup_manual_index import (
                run_matchup_manual_index,
            )
            return run_matchup_manual_index(
                job=job,
                payload=payload,
                tenant_id=tenant_id,
                record_progress=_record_progress,
            )

        cfg = AIConfig.load()

        download_url = payload.get("download_url")
        if not download_url:
            return AIResult.failed(job.id, "download_url missing")

        # 다운로드 단계 (모든 작업 공통)
        _record_progress(job.id, "downloading", 10, step_index=1, step_total=1, step_name_display="다운로드", step_percent=0, tenant_id=tenant_id)
        local_path = download_to_tmp(
            download_url=download_url,
            job_id=str(job.id),
        )
        _record_progress(job.id, "downloading", 10, step_index=1, step_total=1, step_name_display="다운로드", step_percent=100, tenant_id=tenant_id)

        # --------------------------------------------------
        # OCR
        # --------------------------------------------------
        if job.type == "ocr":
            engine = (payload.get("engine") or cfg.OCR_ENGINE or "auto").lower()

            if engine == "tesseract":
                r = tesseract_ocr(local_path)
            elif engine == "google":
                r = google_ocr(local_path)
            else:
                try:
                    r = google_ocr(local_path)
                    if not r.text.strip():
                        r = tesseract_ocr(local_path)
                except Exception:
                    r = tesseract_ocr(local_path)

            return AIResult.done(
                job.id,
                {"text": r.text, "confidence": r.confidence},
            )

        # --------------------------------------------------
        # Question segmentation (PDF 시험지 문항 분할 + 해설 인식)
        # --------------------------------------------------
        if job.type == "question_segmentation":
            from apps.worker.ai_worker.ai.pipelines.pdf_question_pipeline import (
                run_pdf_question_pipeline,
            )
            return run_pdf_question_pipeline(
                job=job,
                local_path=local_path,
                payload=payload,
                tenant_id=tenant_id,
                record_progress=_record_progress,
            )

        # --------------------------------------------------
        # Handwriting analysis
        # --------------------------------------------------
        if job.type == "handwriting_analysis":
            scores = analyze_handwriting(local_path)
            return AIResult.done(job.id, scores)

        # --------------------------------------------------
        # Embedding
        # --------------------------------------------------
        if job.type == "embedding":
            texts = payload.get("texts") or []
            batch = get_embeddings(list(texts))
            return AIResult.done(
                job.id,
                {"backend": batch.backend, "vectors": batch.vectors},
            )

        # --------------------------------------------------
        # Problem generation
        # --------------------------------------------------
        if job.type == "problem_generation":
            ocr_text = payload.get("ocr_text") or ""
            parsed = generate_problem_from_ocr(ocr_text)
            return AIResult.done(
                job.id,
                {
                    "body": parsed.body,
                    "choices": parsed.choices,
                    "answer": parsed.answer,
                    "difficulty": parsed.difficulty,
                    "tag": parsed.tag,
                    "summary": parsed.summary,
                    "explanation": parsed.explanation,
                },
            )

        # --------------------------------------------------
        # Homework video analysis (숙제 검사)
        # --------------------------------------------------
        if job.type == "homework_video_analysis":
            # 4단계: 다운로드(완료), 프레임추출, 분석, 완료
            frame_stride = int(payload.get("frame_stride") or 10)
            min_frame_count = int(payload.get("min_frame_count") or 30)
            use_key_frames = payload.get("use_key_frames", True)  # 기본값: 키 프레임 사용
            max_pages = int(payload.get("max_pages") or 10)
            processing_timeout = int(payload.get("processing_timeout") or 60)
            
            _record_progress(job.id, "extracting", 30, step_index=2, step_total=4, step_name_display="프레임추출", step_percent=0, tenant_id=tenant_id)
            
            # analyze_homework_video 내부에서 진행률 콜백을 받도록 수정 필요하지만,
            # 일단 단계별로만 표시
            analysis = analyze_homework_video(
                video_path=local_path,
                frame_stride=frame_stride,
                min_frame_count=min_frame_count,
                use_key_frames=use_key_frames,
                max_pages=max_pages,
                processing_timeout=processing_timeout,
            )
            _record_progress(job.id, "extracting", 50, step_index=2, step_total=4, step_name_display="프레임추출", step_percent=100, tenant_id=tenant_id)
            _record_progress(job.id, "analyzing", 70, step_index=3, step_total=4, step_name_display="분석", step_percent=100, tenant_id=tenant_id)
            _record_progress(job.id, "done", 100, step_index=4, step_total=4, step_name_display="완료", step_percent=100, tenant_id=tenant_id)
            return AIResult.done(job.id, analysis)

        # --------------------------------------------------
        # OMR grading (meta-aware) - production final
        # --------------------------------------------------
        if job.type == "omr_grading":
            """
            payload options (recommended):
              - mode: "scan" | "photo" | "auto" (default auto)
              - question_count: 10|20|30 (required if template_fetch is used)
              - template_meta: dict (inject meta directly; no API call)
              - template_fetch:
                    {
                      "base_url": "...",
                      "cookie": "...",
                      "bearer_token": "...",
                      "worker_token": "...",
                      "timeout": 10
                    }

            Output contract:
              {
                "version": "v1",
                "mode": "...",
                "aligned": true|false,
                "identifier": {...},
                "answers": [...],
                "meta_used": true|false
              }
            """
            # 7단계: 다운로드(완료), 메타가져오기, 이미지로드, 정렬, ROI빌드, 식별자감지, 답안감지
            import cv2  # type: ignore

            from apps.worker.ai_worker.ai.omr.engine import detect_omr_answers_v7, AnswerDetectConfig
            from apps.worker.omr.warp import align_to_a4_landscape
            from apps.worker.ai_worker.ai.omr.identifier import detect_identifier_v1, IdentifierConfigV1
            from apps.domains.assets.omr.services.meta_generator import build_omr_meta

            mode = str(payload.get("mode") or "auto").lower()
            if mode not in ("scan", "photo", "auto"):
                mode = "auto"

            # 1) meta 확보 — meta_generator.py가 SSOT
            _record_progress(job.id, "fetching_meta", 20, step_index=2, step_total=7, step_name_display="메타생성", step_percent=0, tenant_id=tenant_id)
            meta = payload.get("template_meta")

            if not meta:
                qc = int(payload.get("question_count") or payload.get("mc_count") or 30)
                ec = int(payload.get("essay_count") or 0)
                nc = int(payload.get("n_choices") or 5)
                meta = build_omr_meta(question_count=qc, n_choices=nc, essay_count=ec)

            _record_progress(job.id, "fetching_meta", 30, step_index=2, step_total=7, step_name_display="메타생성", step_percent=100, tenant_id=tenant_id)

            # 2) 이미지 로드 및 리사이징 — EXIF orientation 자동 보정 (휴대폰 사진 대응)
            _record_progress(job.id, "loading", 35, step_index=3, step_total=7, step_name_display="이미지로드", step_percent=0, tenant_id=tenant_id)
            img_bgr = imread_exif_aware(local_path)
            if img_bgr is None:
                return AIResult.failed(job.id, "cannot read image")
            
            # OMR: 식별자 버블(3.6mm)의 fill 정확도를 위해 A4 300dpi 해상도 유지
            # 다른 job type은 4MP로 제한하지만, OMR은 원본 해상도 필요
            if job.type == "omr_grading":
                img_bgr, was_resized = resize_if_large(img_bgr, max_width=4096, max_height=4096, max_megapixels=12.0)
            else:
                img_bgr, was_resized = resize_if_large(img_bgr, max_megapixels=4.0)
            _record_progress(job.id, "loading", 40, step_index=3, step_total=7, step_name_display="이미지로드", step_percent=100, tenant_id=tenant_id)

            aligned = img_bgr

            # 3) v9 정렬 (marker homography → contour warp → rotation fallback)
            _record_progress(job.id, "aligning", 45, step_index=4, step_total=7, step_name_display="정렬", step_percent=0, tenant_id=tenant_id)
            align_result = align_to_a4_landscape(image_bgr=img_bgr, meta=meta)
            aligned = align_result.image
            if mode == "photo" and not align_result.success:
                return AIResult.failed(job.id, f"alignment_failed_for_photo_mode (method={align_result.method})")
            _record_progress(job.id, "aligning", 55, step_index=4, step_total=7, step_name_display="정렬", step_percent=100, tenant_id=tenant_id)

            # 4) identifier 감지
            _record_progress(job.id, "detecting_id", 60, step_index=4, step_total=6, step_name_display="식별자감지", step_percent=0, tenant_id=tenant_id)
            ident = detect_identifier_v1(
                image_bgr=aligned,
                meta=meta,
                cfg=IdentifierConfigV1(),
            )
            _record_progress(job.id, "detecting_id", 70, step_index=4, step_total=6, step_name_display="식별자감지", step_percent=100, tenant_id=tenant_id)

            # 5) 객관식 답안 감지 — v7 엔진 (image_bgr + meta 직접 전달)
            _record_progress(job.id, "detecting_answers", 75, step_index=5, step_total=6, step_name_display="답안감지", step_percent=0, tenant_id=tenant_id)
            answer_results = detect_omr_answers_v7(
                image_bgr=aligned,
                meta=meta,
                config=AnswerDetectConfig(),
            )
            answers = [a.to_dict() for a in answer_results]
            _record_progress(job.id, "detecting_answers", 95, step_index=5, step_total=6, step_name_display="답안감지", step_percent=100, tenant_id=tenant_id)

            _record_progress(job.id, "done", 100, step_index=6, step_total=6, step_name_display="완료", step_percent=100, tenant_id=tenant_id)

            return AIResult.done(
                job.id,
                {
                    "version": str(meta.get("version") or "v9"),
                    "mode": mode,
                    "aligned": align_result.success,
                    "alignment_method": align_result.method,
                    "identifier": ident,
                    "answers": answers,
                },
            )

        # --------------------------------------------------
        # Matchup search QnA (학생 Q&A 사진 → 유사 문제 검색)
        # --------------------------------------------------
        if job.type == "matchup_search_qna":
            from apps.worker.ai_worker.ai.pipelines.matchup_search_qna import (
                run_matchup_search_qna,
            )
            return run_matchup_search_qna(
                job=job,
                local_path=local_path,
                payload=payload,
                tenant_id=tenant_id,
                record_progress=_record_progress,
            )

        # --------------------------------------------------
        # Matchup analysis (매치업 — 문제 분할 + OCR + 임베딩)
        # --------------------------------------------------
        if job.type == "matchup_analysis":
            from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import (
                run_matchup_pipeline,
            )
            return run_matchup_pipeline(
                job=job,
                local_path=local_path,
                payload=payload,
                tenant_id=tenant_id,
                record_progress=_record_progress,
            )

        return AIResult.failed(job.id, f"Unsupported job type: {job.type}")

    except Exception as e:
        return AIResult.failed(job.id, str(e))
    finally:
        cleanup_tmp_for_path(local_path)
        cleanup_registered_pdf_seg_tmp_dirs()

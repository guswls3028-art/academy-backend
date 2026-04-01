# apps/domains/ai/callbacks.py
"""
AI Job 완료 후 도메인별 후속 처리를 담당한다.

핵심 규칙:
- AI Job의 상태(DONE/FAILED)는 이미 UoW에서 처리된 상태로 진입한다.
- 이 모듈은 AI 결과를 "도메인 엔티티에 반영"하는 역할만 한다.
- 멱등성 보장: 동일 job에 대해 중복 호출해도 안전해야 한다.
- callback 실패가 AI Job 상태를 되돌리지 않는다.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def dispatch_ai_result_to_domain(
    *,
    job_id: str,
    status: str,
    result_payload: Optional[Dict[str, Any]],
    error: Optional[str],
    source_domain: Optional[str],
    source_id: Optional[str],
    tier: str = "basic",
) -> None:
    """
    AI Job 완료 후 도메인별 후속 처리 디스패처.
    source_domain에 따라 적절한 도메인 핸들러로 라우팅한다.
    """
    # exams 도메인: question_segmentation 결과 처리
    if source_domain == "exams":
        try:
            _handle_exam_ai_result(
                job_id=job_id,
                status=status,
                result_payload=result_payload or {},
                error=error,
                source_id=source_id,
            )
        except Exception:
            logger.exception(
                "AI_CALLBACK_EXAM_FAILED | job_id=%s | source_id=%s",
                job_id, source_id,
            )
        return

    if source_domain != "submissions":
        logger.debug(
            "AI_CALLBACK_SKIP | source_domain=%s job_id=%s (not submissions)",
            source_domain, job_id,
        )
        return

    if not source_id:
        logger.warning(
            "AI_CALLBACK_SKIP | source_id empty | job_id=%s",
            job_id,
        )
        return

    t0 = time.monotonic()
    logger.info(
        "AI_CALLBACK_START | job_id=%s | submission_id=%s | status=%s | tier=%s",
        job_id, source_id, status, tier,
    )

    try:
        _handle_submission_ai_result(
            job_id=job_id,
            submission_id=int(source_id),
            status=status,
            result_payload=result_payload or {},
            error=error,
            tier=tier,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "AI_CALLBACK_SUCCESS | job_id=%s | submission_id=%s | elapsed_ms=%d",
            job_id, source_id, elapsed_ms,
        )
    except Exception:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.exception(
            "AI_CALLBACK_FAILED | job_id=%s | submission_id=%s | elapsed_ms=%d",
            job_id, source_id, elapsed_ms,
        )


def _handle_submission_ai_result(
    *,
    job_id: str,
    submission_id: int,
    status: str,
    result_payload: Dict[str, Any],
    error: Optional[str],
    tier: str,
) -> None:
    """
    Submission 도메인의 AI 결과 처리.

    1. AI 결과를 Submission에 반영 (상태 전이: DISPATCHED → ANSWERS_READY/NEEDS_ID/FAILED)
    2. ANSWERS_READY가 되면 채점 파이프라인 실행
    """
    from apps.domains.submissions.services.ai_omr_result_mapper import apply_ai_result
    from apps.domains.results.tasks.grading_tasks import grade_submission_task

    # 🔐 tenant 교차검증: AI job의 tenant_id와 submission의 tenant_id 일치 확인
    ai_job = None
    if job_id:
        from apps.domains.ai.models import AIJobModel
        from apps.domains.submissions.models import Submission as SubModel
        ai_job = AIJobModel.objects.filter(job_id=job_id).first()
        if ai_job and ai_job.tenant_id:
            try:
                sub_tenant_id = SubModel.objects.filter(pk=submission_id).values_list("tenant_id", flat=True).first()
                if sub_tenant_id and str(ai_job.tenant_id) != str(sub_tenant_id):
                    logger.error(
                        "TENANT_ISOLATION_VIOLATION | _handle_submission_ai_result | "
                        "job_id=%s | job_tenant=%s | submission_tenant=%s | submission_id=%s",
                        job_id, ai_job.tenant_id, sub_tenant_id, submission_id,
                    )
                    return
            except Exception:
                logger.exception(
                    "TENANT_CHECK_ERROR | job_id=%s | submission_id=%s",
                    job_id, submission_id,
                )

    # apply_ai_result는 payload에서 submission_id를 꺼냄
    payload = dict(result_payload)
    payload["submission_id"] = submission_id
    payload["job_id"] = job_id
    payload["status"] = status
    payload["error"] = error

    # tenant_id를 payload에 전달하여 apply_omr_ai_result에서도 교차검증 가능
    if ai_job and ai_job.tenant_id:
        payload["tenant_id"] = str(ai_job.tenant_id)

    returned_id = apply_ai_result(payload)

    if not returned_id:
        logger.warning(
            "AI_CALLBACK_APPLY_NULL | submission_id=%s | job_id=%s",
            submission_id, job_id,
        )
        return

    # ANSWERS_READY가 된 경우에만 채점 실행
    from apps.domains.submissions.models import Submission
    try:
        sub_status = Submission.objects.filter(pk=returned_id).values_list("status", flat=True).first()
        if sub_status == Submission.Status.ANSWERS_READY:
            grade_submission_task(int(returned_id))
            logger.info(
                "AI_CALLBACK_GRADING_TRIGGERED | submission_id=%s | job_id=%s",
                returned_id, job_id,
            )
        else:
            logger.info(
                "AI_CALLBACK_GRADING_SKIPPED | submission_id=%s | status=%s | job_id=%s",
                returned_id, sub_status, job_id,
            )
    except Exception:
        logger.exception(
            "AI_CALLBACK_GRADING_ERROR | submission_id=%s | job_id=%s",
            returned_id, job_id,
        )


def _handle_exam_ai_result(
    *,
    job_id: str,
    status: str,
    result_payload: Dict[str, Any],
    error: Optional[str],
    source_id: Optional[str],
) -> None:
    """
    Exam 도메인 AI 결과 처리 (question_segmentation).

    결과에서 문항 박스 추출 → Sheet/ExamQuestion 자동 생성.
    해설이 포함되어 있으면 QuestionExplanation도 생성.
    """
    if status == "FAILED":
        logger.warning(
            "AI_CALLBACK_EXAM_FAILED_STATUS | job_id=%s | error=%s",
            job_id, error,
        )
        return

    exam_id = result_payload.get("exam_id") or source_id
    if not exam_id:
        logger.warning("AI_CALLBACK_EXAM_NO_EXAM_ID | job_id=%s", job_id)
        return

    boxes = result_payload.get("boxes", [])
    questions_data = result_payload.get("questions", [])
    explanations_data = result_payload.get("explanations", [])
    question_image_keys = result_payload.get("question_image_keys") or {}

    if not boxes and not questions_data:
        logger.info(
            "AI_CALLBACK_EXAM_NO_BOXES | job_id=%s | exam_id=%s",
            job_id, exam_id,
        )
        return

    try:
        from django.db import transaction
        from apps.domains.exams.models import Exam, Sheet, ExamQuestion, QuestionExplanation

        with transaction.atomic():
            exam = Exam.objects.select_for_update().get(id=int(exam_id))

            # 🔐 tenant 교차검증: AI job의 tenant_id와 exam의 tenant_id 일치 확인
            if job_id:
                from apps.domains.ai.models import AIJobModel
                ai_job = AIJobModel.objects.filter(job_id=job_id).first()
                if ai_job and ai_job.tenant_id and hasattr(exam, "tenant_id"):
                    if str(ai_job.tenant_id) != str(exam.tenant_id):
                        logger.error(
                            "AI_CALLBACK_TENANT_MISMATCH | job_id=%s | job_tenant=%s | exam_tenant=%s | exam_id=%s",
                            job_id, ai_job.tenant_id, exam.tenant_id, exam_id,
                        )
                        return

            # Template exam만 구조 변경 가능
            if exam.exam_type != Exam.ExamType.TEMPLATE:
                logger.warning(
                    "AI_CALLBACK_EXAM_NOT_TEMPLATE | job_id=%s | exam_id=%s | type=%s",
                    job_id, exam_id, exam.exam_type,
                )
                return

            # Sheet 가져오기 또는 생성
            sheet, _ = Sheet.objects.get_or_create(
                exam=exam,
                defaults={"name": "MAIN", "total_questions": 0},
            )

            # 문항 개수 결정
            total = len(questions_data) if questions_data else len(boxes)
            if total == 0:
                return

            # total_questions 동기화
            if sheet.total_questions != total:
                sheet.total_questions = total
                sheet.save(update_fields=["total_questions", "updated_at"])

            # 기존 문항 정리 (범위 밖 삭제)
            existing_numbers = set(
                ExamQuestion.objects.filter(sheet=sheet).values_list("number", flat=True)
            )
            new_numbers = set(range(1, total + 1))
            to_delete = existing_numbers - new_numbers
            if to_delete:
                ExamQuestion.objects.filter(sheet=sheet, number__in=to_delete).delete()

            # 문항 생성/갱신
            created_questions = []
            for idx in range(1, total + 1):
                # questions_data가 있으면 사용, 없으면 boxes에서 직접
                if questions_data and idx <= len(questions_data):
                    q_data = questions_data[idx - 1]
                    bbox = q_data.get("bbox", [0, 0, 0, 0])
                    region_meta = {
                        "x": int(bbox[0]) if len(bbox) > 0 else 0,
                        "y": int(bbox[1]) if len(bbox) > 1 else 0,
                        "w": int(bbox[2]) if len(bbox) > 2 else 0,
                        "h": int(bbox[3]) if len(bbox) > 3 else 0,
                    }
                elif boxes and idx <= len(boxes):
                    b = boxes[idx - 1]
                    region_meta = {
                        "x": int(b[0]) if len(b) > 0 else 0,
                        "y": int(b[1]) if len(b) > 1 else 0,
                        "w": int(b[2]) if len(b) > 2 else 0,
                        "h": int(b[3]) if len(b) > 3 else 0,
                    }
                else:
                    region_meta = {"x": 0, "y": 0, "w": 0, "h": 0}

                # image_key: 워커가 크롭하여 R2에 업로드한 이미지 키
                # question_image_keys는 {문항번호(int): r2_key(str)} 형태
                q_image_key = question_image_keys.get(idx) or question_image_keys.get(str(idx)) or ""

                obj, _ = ExamQuestion.objects.update_or_create(
                    sheet=sheet,
                    number=idx,
                    defaults={
                        "region_meta": region_meta,
                        "image_key": q_image_key,
                    },
                )
                created_questions.append(obj)

            # 해설 생성 (있는 경우만)
            if explanations_data:
                # question_number → ExamQuestion 매핑
                q_by_number = {q.number: q for q in created_questions}

                for exp in explanations_data:
                    q_num = exp.get("question_number")
                    text = exp.get("text", "")
                    if not q_num or q_num not in q_by_number:
                        continue

                    QuestionExplanation.objects.update_or_create(
                        question=q_by_number[q_num],
                        defaults={
                            "text": text[:2000],
                            "source": QuestionExplanation.Source.AI_EXTRACTED,
                            "match_confidence": 1.0 if text else 0.5,
                        },
                    )

            logger.info(
                "AI_CALLBACK_EXAM_SUCCESS | job_id=%s | exam_id=%s | questions=%d | explanations=%d",
                job_id, exam_id, len(created_questions), len(explanations_data),
            )

    except Exam.DoesNotExist:
        logger.warning(
            "AI_CALLBACK_EXAM_NOT_FOUND | job_id=%s | exam_id=%s",
            job_id, exam_id,
        )
    except Exception:
        logger.exception(
            "AI_CALLBACK_EXAM_ERROR | job_id=%s | exam_id=%s",
            job_id, exam_id,
        )
        raise


def detect_stuck_dispatched() -> list[dict]:
    """
    AIJob이 완료되었는데 Submission이 아직 DISPATCHED인 건을 감지한다.
    운영 모니터링/reconcile 전 진단용.
    """
    from datetime import timedelta
    from django.utils import timezone
    from apps.domains.submissions.models import Submission
    from apps.domains.ai.models import AIJobModel

    cutoff = timezone.now() - timedelta(minutes=30)
    stuck = Submission.objects.filter(
        status=Submission.Status.DISPATCHED,
        updated_at__lt=cutoff,
    ).values_list("id", flat=True)

    results = []
    for sub_id in stuck[:100]:
        ai_job = (
            AIJobModel.objects
            .filter(source_domain="submissions", source_id=str(sub_id))
            .order_by("-created_at")
            .first()
        )
        results.append({
            "submission_id": sub_id,
            "ai_job_id": ai_job.job_id if ai_job else None,
            "ai_job_status": ai_job.status if ai_job else None,
            "stuck": ai_job and ai_job.status in ("DONE", "FAILED", "REJECTED_BAD_INPUT"),
        })

    stuck_count = sum(1 for r in results if r["stuck"])
    if stuck_count > 0:
        logger.error(
            "AI_STUCK_DISPATCHED_DETECTED | count=%d | submissions=%s",
            stuck_count,
            [r["submission_id"] for r in results if r["stuck"]],
        )

    return results

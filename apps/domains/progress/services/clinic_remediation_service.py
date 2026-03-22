# apps/domains/progress/services/clinic_remediation_service.py
"""
클리닉 재시도 점수 입력 서비스

역할:
- 클리닉 페이지에서 시험/과제 재시도 점수를 직접 입력
- 합격 시 ClinicLink 자동 해소
- ExamAttempt / HomeworkScore에 재시도 기록 저장

핵심 규칙:
- 클리닉 재시험은 exam.allow_retake / max_attempts 와 무관하게 허용
- 재시험 ExamAttempt는 is_representative=False (성적 산출에 포함 안 됨)
- 재시도 HomeworkScore는 attempt_index >= 2 (성적 산출은 attempt_index=1만)
- Result 모델은 업데이트하지 않음 (1차 결과=성적 산출 SSOT 유지)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from django.db import transaction
from django.db.models import Max

from apps.domains.progress.models import ClinicLink
from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService

logger = logging.getLogger(__name__)


@dataclass
class RetakeResult:
    """재시도 결과"""
    passed: bool
    score: float
    max_score: float
    attempt_index: int
    resolution_type: Optional[str] = None
    resolved_at: Optional[str] = None
    clinic_link_id: Optional[int] = None


class ClinicRemediationService:
    """
    클리닉 페이지에서의 재시도 점수 입력 서비스.
    시험/과제 모두 이 서비스를 통해 처리.
    """

    @staticmethod
    @transaction.atomic
    def submit_exam_retake(
        *,
        clinic_link_id: int,
        score: float,
        graded_by_user_id: int,
    ) -> RetakeResult:
        """
        클리닉에서 시험 재응시 점수 입력.

        1. ClinicLink 조회 (unresolved, source_type=exam 검증)
        2. 해당 exam의 기존 attempt 수 확인 → 새 attempt_index 계산
        3. ExamAttempt 생성 (is_retake=True, is_representative=False)
        4. ExamResult 생성 (total_score=score, is_passed 계산)
        5. is_passed=True → ClinicLink 자동 해소
        """
        from apps.domains.results.models import ExamAttempt, ExamResult
        from apps.domains.exams.models import Exam

        # 1. ClinicLink 조회
        link = ClinicLink.objects.select_for_update().get(
            id=clinic_link_id,
            resolved_at__isnull=True,
        )

        if link.source_type != "exam":
            raise ValueError(f"ClinicLink {clinic_link_id}는 시험 유형이 아닙니다 (source_type={link.source_type})")

        exam = Exam.objects.get(id=link.source_id)
        pass_score = float(getattr(exam, "pass_score", 0) or 0)
        max_score_val = float(getattr(exam, "max_score", 100) or 100)

        # 2. 다음 attempt_index 계산
        max_attempt = (
            ExamAttempt.objects.filter(
                exam_id=exam.id,
                enrollment_id=link.enrollment_id,
            ).aggregate(Max("attempt_index"))["attempt_index__max"]
        ) or 0
        next_attempt = max_attempt + 1

        # 3. ExamAttempt 생성 (is_representative=False: 성적 산출에 포함 안 됨)
        attempt = ExamAttempt.objects.create(
            exam_id=exam.id,
            enrollment_id=link.enrollment_id,
            submission_id=0,  # 클리닉 직접 입력 — submission 없음
            attempt_index=next_attempt,
            is_retake=True,
            is_representative=False,  # ← 핵심: 성적 산출 제외
            clinic_link=link,
            status="done",
            meta={
                "source": "clinic_remediation",
                "graded_by_user_id": graded_by_user_id,
                "total_score": score,
                "pass_score": pass_score,
            },
        )

        # 4. 합격 판정
        is_passed = score >= pass_score if pass_score > 0 else False

        # ExamResult는 submission 1:1이라 클리닉 직접 입력에는 생성하지 않음
        # 대신 attempt.meta에 점수를 기록 (위에서 이미 저장)

        # 5. 합격 시 ClinicLink 해소
        result = RetakeResult(
            passed=is_passed,
            score=score,
            max_score=max_score_val,
            attempt_index=next_attempt,
            clinic_link_id=link.id,
        )

        if is_passed:
            ClinicResolutionService.resolve_by_exam_pass(
                enrollment_id=link.enrollment_id,
                session_id=link.session_id,
                exam_id=exam.id,
                attempt_id=attempt.id,
                score=score,
                pass_score=pass_score,
            )
            link.refresh_from_db()
            result.resolution_type = link.resolution_type
            result.resolved_at = link.resolved_at.isoformat() if link.resolved_at else None

        logger.info(
            "clinic_remediation: exam retake (link=%s, exam=%s, enrollment=%s, "
            "attempt=%d, score=%s, passed=%s)",
            clinic_link_id, exam.id, link.enrollment_id,
            next_attempt, score, is_passed,
        )

        return result

    @staticmethod
    @transaction.atomic
    def submit_homework_retake(
        *,
        clinic_link_id: int,
        score: float,
        max_score: Optional[float] = None,
        graded_by_user_id: int,
    ) -> RetakeResult:
        """
        클리닉에서 과제 재제출 점수 입력.

        1. ClinicLink 조회 (unresolved, source_type=homework 검증)
        2. 해당 homework의 기존 HomeworkScore 중 최대 attempt_index 확인
        3. HomeworkScore 생성 (attempt_index=N+1)
        4. 합격 판정
        5. 합격 시 ClinicLink 해소
        """
        from apps.domains.homework_results.models import HomeworkScore, Homework
        from apps.domains.homework.utils.homework_policy import calc_homework_passed_and_clinic

        # 1. ClinicLink 조회
        link = ClinicLink.objects.select_for_update().get(
            id=clinic_link_id,
            resolved_at__isnull=True,
        )

        if link.source_type != "homework":
            raise ValueError(f"ClinicLink {clinic_link_id}는 과제 유형이 아닙니다 (source_type={link.source_type})")

        homework = Homework.objects.get(id=link.source_id)
        session = link.session

        # max_score 결정: 전달받은 값 > 1차 HomeworkScore.max_score > 100
        if max_score is None:
            first_score = HomeworkScore.objects.filter(
                enrollment_id=link.enrollment_id,
                session=session,
                homework=homework,
                attempt_index=1,
            ).first()
            max_score = float(first_score.max_score or 100) if first_score and first_score.max_score else 100.0

        # 2. 다음 attempt_index 계산
        max_attempt = (
            HomeworkScore.objects.filter(
                enrollment_id=link.enrollment_id,
                session=session,
                homework=homework,
            ).aggregate(Max("attempt_index"))["attempt_index__max"]
        ) or 0
        next_attempt = max_attempt + 1

        # 3. 합격 판정
        passed, _, _ = calc_homework_passed_and_clinic(
            session=session,
            score=score,
            max_score=max_score,
        )

        # 4. HomeworkScore 생성
        HomeworkScore.objects.create(
            enrollment_id=link.enrollment_id,
            session=session,
            homework=homework,
            attempt_index=next_attempt,
            clinic_link=link,
            score=score,
            max_score=max_score,
            passed=passed,
            clinic_required=False,  # 클리닉 재시도는 clinic_required 의미 없음
            updated_by_user_id=graded_by_user_id,
        )

        # 5. 합격 시 ClinicLink 해소
        result = RetakeResult(
            passed=passed,
            score=score,
            max_score=max_score,
            attempt_index=next_attempt,
            clinic_link_id=link.id,
        )

        if passed:
            ClinicResolutionService.resolve_by_homework_pass(
                enrollment_id=link.enrollment_id,
                session_id=session.id,
                homework_id=homework.id,
                score=score,
                max_score=max_score,
            )
            link.refresh_from_db()
            result.resolution_type = link.resolution_type
            result.resolved_at = link.resolved_at.isoformat() if link.resolved_at else None

        logger.info(
            "clinic_remediation: homework retake (link=%s, homework=%s, enrollment=%s, "
            "attempt=%d, score=%s, passed=%s)",
            clinic_link_id, homework.id, link.enrollment_id,
            next_attempt, score, passed,
        )

        return result

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

        # 1. ClinicLink 조회 — resolved 여부를 분리 검사하여 운영 메시지 명확화
        link = ClinicLink.objects.select_for_update().filter(id=clinic_link_id).first()
        if link is None:
            raise ClinicLink.DoesNotExist(f"ClinicLink {clinic_link_id}를 찾을 수 없습니다.")
        if link.resolved_at is not None:
            raise ValueError(
                f"ClinicLink {clinic_link_id}는 이미 해소된 상태입니다 "
                f"(resolution_type={link.resolution_type}). "
                f"재시도하려면 먼저 관리자가 복원 처리해야 합니다."
            )

        if link.source_type != "exam":
            raise ValueError(f"ClinicLink {clinic_link_id}는 시험 유형이 아닙니다 (source_type={link.source_type})")

        exam = Exam.objects.get(id=link.source_id)
        pass_score = float(getattr(exam, "pass_score", 0) or 0)
        max_score_val = float(getattr(exam, "max_score", 100) or 100)

        # 점수 검증: 음수/만점 초과 차단
        if score < 0:
            raise ValueError(f"점수({score})는 0 이상이어야 합니다.")
        if max_score_val > 0 and score > max_score_val:
            raise ValueError(f"점수({score})가 만점({max_score_val})을 초과할 수 없습니다.")

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
            submission_id=None,  # 클리닉 직접 입력 — submission 없음
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
        is_passed = score >= pass_score  # pass_score=0 → 모든 점수 합격

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

        # 1. ClinicLink 조회 (session→lecture→tenant 프리로드) — resolved 분리 검사
        link = (
            ClinicLink.objects
            .select_for_update()
            .select_related("session", "session__lecture", "session__lecture__tenant")
            .filter(id=clinic_link_id)
            .first()
        )
        if link is None:
            raise ClinicLink.DoesNotExist(f"ClinicLink {clinic_link_id}를 찾을 수 없습니다.")
        if link.resolved_at is not None:
            raise ValueError(
                f"ClinicLink {clinic_link_id}는 이미 해소된 상태입니다 "
                f"(resolution_type={link.resolution_type}). "
                f"재시도하려면 먼저 관리자가 복원 처리해야 합니다."
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

        # 점수 검증: 음수/만점 초과 차단
        if score < 0:
            raise ValueError(f"점수({score})는 0 이상이어야 합니다.")
        if max_score > 0 and score > max_score:
            raise ValueError(f"점수({score})가 만점({max_score})을 초과할 수 없습니다.")

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

    # ─── 기존 시도 점수 수정 ───

    @staticmethod
    @transaction.atomic
    def update_exam_retake(
        *,
        clinic_link_id: int,
        attempt_index: int,
        score: float,
        graded_by_user_id: int,
    ) -> RetakeResult:
        """
        기존 시험 재시도의 점수를 수정한다.
        attempt_index >= 2인 ExamAttempt만 수정 가능 (1차는 성적표 편집으로 수정).
        수정 후 합격 여부를 재판정하고, 합격 시 ClinicLink를 통과 처리한다.
        """
        from apps.domains.results.models import ExamAttempt
        from apps.domains.exams.models import Exam

        if attempt_index < 2:
            raise ValueError("1차 시도는 이 API로 수정할 수 없습니다. 성적표 편집을 사용하세요.")

        link = ClinicLink.objects.select_for_update().get(id=clinic_link_id)

        if link.source_type != "exam":
            raise ValueError(f"ClinicLink {clinic_link_id}는 시험 유형이 아닙니다")

        exam = Exam.objects.get(id=link.source_id)
        pass_score = float(getattr(exam, "pass_score", 0) or 0)
        max_score_val = float(getattr(exam, "max_score", 100) or 100)

        attempt = ExamAttempt.objects.select_for_update().get(
            exam_id=exam.id,
            enrollment_id=link.enrollment_id,
            attempt_index=attempt_index,
        )

        # 점수 업데이트
        attempt.meta = attempt.meta or {}
        attempt.meta["total_score"] = score
        attempt.meta["pass_score"] = pass_score
        attempt.meta["updated_by_user_id"] = graded_by_user_id
        attempt.save(update_fields=["meta", "updated_at"])

        is_passed = score >= pass_score  # pass_score=0 → 모든 점수 합격

        result = RetakeResult(
            passed=is_passed,
            score=score,
            max_score=max_score_val,
            attempt_index=attempt_index,
            clinic_link_id=link.id,
        )

        # 합격으로 변경 + 아직 미통과 상태면 → 통과 처리
        if is_passed and link.resolved_at is None:
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
        elif not is_passed and link.resolved_at is not None:
            # 합격→불합격으로 수정: 통과 취소
            ClinicResolutionService.unresolve(clinic_link_id=link.id)
            link.refresh_from_db()

        logger.info(
            "clinic_remediation: exam retake UPDATE (link=%s, exam=%s, attempt=%d, score=%s, passed=%s)",
            clinic_link_id, exam.id, attempt_index, score, is_passed,
        )

        return result

    @staticmethod
    @transaction.atomic
    def update_homework_retake(
        *,
        clinic_link_id: int,
        attempt_index: int,
        score: float,
        max_score: Optional[float] = None,
        graded_by_user_id: int,
    ) -> RetakeResult:
        """
        기존 과제 재시도의 점수를 수정한다.
        attempt_index >= 2인 HomeworkScore만 수정 가능.
        """
        from apps.domains.homework_results.models import HomeworkScore, Homework
        from apps.domains.homework.utils.homework_policy import calc_homework_passed_and_clinic

        if attempt_index < 2:
            raise ValueError("1차 시도는 이 API로 수정할 수 없습니다. 성적표 편집을 사용하세요.")

        link = (
            ClinicLink.objects
            .select_for_update()
            .select_related("session", "session__lecture", "session__lecture__tenant")
            .get(id=clinic_link_id)
        )

        if link.source_type != "homework":
            raise ValueError(f"ClinicLink {clinic_link_id}는 과제 유형이 아닙니다")

        homework = Homework.objects.get(id=link.source_id)
        session = link.session

        hs = HomeworkScore.objects.select_for_update().get(
            enrollment_id=link.enrollment_id,
            session=session,
            homework=homework,
            attempt_index=attempt_index,
        )

        if max_score is None:
            max_score = float(hs.max_score or 100)

        passed, _, _ = calc_homework_passed_and_clinic(
            session=session,
            score=score,
            max_score=max_score,
        )

        hs.score = score
        hs.max_score = max_score
        hs.passed = passed
        hs.updated_by_user_id = graded_by_user_id
        hs.save(update_fields=["score", "max_score", "passed", "updated_by_user_id", "updated_at"])

        result = RetakeResult(
            passed=passed,
            score=score,
            max_score=max_score,
            attempt_index=attempt_index,
            clinic_link_id=link.id,
        )

        if passed and link.resolved_at is None:
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
        elif not passed and link.resolved_at is not None:
            ClinicResolutionService.unresolve(clinic_link_id=link.id)
            link.refresh_from_db()

        logger.info(
            "clinic_remediation: homework retake UPDATE (link=%s, homework=%s, attempt=%d, score=%s, passed=%s)",
            clinic_link_id, homework.id, attempt_index, score, passed,
        )

        return result

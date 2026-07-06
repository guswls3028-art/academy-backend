# apps/domains/progress/services/progress_pipeline.py
from __future__ import annotations

import logging
from typing import Any, Optional

from django.db import transaction

from academy.adapters.db.django.repositories_progress_inputs import (
    get_passed_homework_score,
    get_representative_exam_attempt_id,
    get_session_with_lecture,
    get_submission_progress_target_for_update,
    has_unresolved_clinic_link,
    list_enrollment_ids_with_exam_result,
    list_sessions_for_exam,
    list_sessions_for_homework,
    list_unresolved_homework_source_ids,
)
from apps.domains.progress.services.session_calculator import SessionProgressCalculator
from apps.domains.progress.services.lecture_calculator import LectureProgressCalculator
from apps.domains.progress.services.risk_evaluator import RiskEvaluator
from apps.domains.progress.services.clinic_trigger_service import ClinicTriggerService
from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService

logger = logging.getLogger(__name__)


class ProgressPipelineService:
    """
    ✅ Synchronous progress pipeline (Enterprise-grade)

    Properties:
    - Queue-less (no Celery)
    - Idempotent (safe to call repeatedly)
    - Defensive (handles partial data without crashing the entire system)
    """

    # ---------------------------------------------------------
    # Public entry
    # ---------------------------------------------------------
    @transaction.atomic
    def apply(
        self,
        *,
        exam_id: Optional[int] = None,
        submission_id: Optional[int] = None,
        enrollment_id: Optional[int] = None,
        session_id: Optional[int] = None,
    ) -> None:
        """
        Entry point.
        - submission_id: compute progress based on that submission's target.
        - exam_id      : compute progress for sessions that include that exam (all takers).
        - enrollment_id + session_id : recompute a single (student × session) point.
          homework ClinicLink 해소나 admin manual resolve 처럼 exam_id 가 없는
          진입점에서 특정 학생 한 명의 세션 집계만 갱신할 때 사용.
        """
        if submission_id is not None:
            self._apply_by_submission(submission_id=int(submission_id))
            return

        if exam_id is not None:
            self._apply_by_exam(exam_id=int(exam_id))
            return

        if enrollment_id is not None and session_id is not None:
            self._apply_by_enrollment_session(
                enrollment_id=int(enrollment_id),
                session_id=int(session_id),
            )
            return

        logger.warning("ProgressPipelineService.apply called with no args")
        return

    # ---------------------------------------------------------
    # Internal: enrollment × session point recompute
    # ---------------------------------------------------------
    def _apply_by_enrollment_session(self, *, enrollment_id: int, session_id: int) -> None:
        session = get_session_with_lecture(int(session_id))
        if not session:
            logger.warning(
                "progress pipeline: session not found (enroll=%s, session=%s)",
                enrollment_id, session_id,
            )
            return

        self._recompute_for_session(
            enrollment_id=int(enrollment_id),
            session=session,
            exam_id=None,
        )

    # ---------------------------------------------------------
    # Internal: submission-based
    # ---------------------------------------------------------
    def _apply_by_submission(self, *, submission_id: int) -> None:
        target = get_submission_progress_target_for_update(int(submission_id))
        if not target:
            logger.warning("progress pipeline: submission not found (id=%s)", submission_id)
            return

        enroll_id = target.enrollment_id
        if not enroll_id:
            logger.warning("progress pipeline: submission has no enrollment_id (id=%s)", submission_id)
            return

        # 현재 시스템에서 시험/숙제 모두 target_id를 가진다고 가정
        target_type = target.target_type
        target_id = target.target_id
        if not target_type or not target_id:
            logger.warning(
                "progress pipeline: invalid target on submission (id=%s, type=%s, target_id=%s)",
                submission_id, target_type, target_id
            )
            return

        # 시험인 경우: exam_id 기반으로 세션 찾기
        if target_type == "exam":
            sessions = self._find_sessions_for_exam(exam_id=target_id)
            if not sessions:
                logger.warning("progress pipeline: no sessions matched exam_id=%s (submission_id=%s)", target_id, submission_id)
                return

            for s in sessions:
                self._recompute_for_session(enrollment_id=int(enroll_id), session=s, exam_id=target_id)

            return

        # 숙제인 경우: homework는 target_id가 homework_id일 수 있음.
        # 여기서는 "숙제 제출 여부는 homework 도메인/교사 승인과 결합"이 필요하므로
        # ✅ 보수적으로: session을 찾을 수 있으면 SessionProgressCalculator에 homework flags를 넣어서 업데이트,
        # 없으면 로그만 남기고 종료.
        if target_type == "homework":
            sessions = self._find_sessions_for_homework(homework_id=target_id)
            if not sessions:
                logger.info("progress pipeline: no sessions matched homework_id=%s (submission_id=%s)", target_id, submission_id)
                return

            for s in sessions:
                # homework는 정책이 다양하므로 최소한 제출 사실만 반영 (승인/점수는 별도 이벤트로 recompute)
                self._recompute_for_session(enrollment_id=int(enroll_id), session=s, exam_id=None, homework_submitted=True)

            return

        # homework 외 unknown target — sub.target_type 이 enum 변경 등으로 처리 안 됨
        logger.info("progress pipeline: unsupported target_type=%s (submission_id=%s)", target_type, submission_id)

    # ---------------------------------------------------------
    # Internal: exam-based
    # ---------------------------------------------------------
    def _apply_by_exam(self, *, exam_id: int) -> None:
        # 누가 봐도 "이 시험을 본 사람들"만 recompute
        enroll_ids = list_enrollment_ids_with_exam_result(int(exam_id))

        if not enroll_ids:
            logger.info("progress pipeline: no enrollments for exam_id=%s", exam_id)
            return

        sessions = self._find_sessions_for_exam(exam_id=exam_id)
        if not sessions:
            logger.warning("progress pipeline: no sessions matched exam_id=%s", exam_id)
            return

        for enroll_id in enroll_ids:
            for s in sessions:
                self._recompute_for_session(enrollment_id=int(enroll_id), session=s, exam_id=exam_id)

    # ---------------------------------------------------------
    # Mapping helpers (defensive)
    # ---------------------------------------------------------
    def _find_sessions_for_exam(self, *, exam_id: int) -> list[Any]:
        """
        ✅ 단일 진실이 있다면 그 유틸을 사용하고,
        없으면 fallback 시도.
        """
        return list_sessions_for_exam(int(exam_id))

    def _find_sessions_for_homework(self, *, homework_id: int) -> list[Any]:
        """
        Homework ↔ Session 매핑.
        SSOT: `Homework.session` (FK, 단일). M2M sessions (legacy) 있으면 합집합.
        """
        return list_sessions_for_homework(int(homework_id))

    # ---------------------------------------------------------
    # Recompute core
    # ---------------------------------------------------------
    def _recompute_for_session(
        self,
        *,
        enrollment_id: int,
        session,
        exam_id: Optional[int] = None,
        homework_submitted: Optional[bool] = None,
    ) -> None:
        """
        Recompute:
        1) SessionProgress (exam aggregate uses Result SSOT)
        2) Clinic triggers (failed / exam risk)
        3) LectureProgress + Risk evaluation

        Args:
            homework_submitted:
                - True: 제출 사실 반영(예: submission DONE 시그널)
                - False: 제출 취소 등 명시적 해제
                - None: 변경 없음 (기존 SessionProgress 값 그대로 유지)
        """
        # 기존 SessionProgress에서 출결/영상/과제 상태를 보존 (덮어쓰기 방지)
        from apps.domains.progress.models import SessionProgress as _SP
        _existing = _SP.objects.filter(
            enrollment_id=int(enrollment_id), session=session,
        ).first()

        # attendance_type/video_progress_rate:
        # 신규 SessionProgress 행이면 모델 default(ONLINE/0)에 맡긴다.
        # 강제 "online" 박기 금지 — 학원장이 출석을 입력하지 않은 빈 상태를 유지.
        _attendance = _existing.attendance_type if _existing else _SP.AttendanceType.ONLINE
        _video_rate = int(_existing.video_progress_rate or 0) if _existing else 0

        # homework_submitted:
        #   None  → 기존 값 유지 (OR-merge 금지: 한 번 True 가 잠기지 않도록)
        #   True  → 덮어쓰기 (제출됨)
        #   False → 덮어쓰기 (해제됨; 제출 취소·삭제 path 에서 사용 가능)
        if homework_submitted is None:
            _hw_submitted = bool(_existing.homework_submitted) if _existing else False
        else:
            _hw_submitted = bool(homework_submitted)

        sp = SessionProgressCalculator.calculate(
            enrollment_id=int(enrollment_id),
            session=session,
            attendance_type=_attendance,
            video_progress_rate=_video_rate,
            homework_submitted=_hw_submitted,
        )

        # failed trigger (idempotent via get_or_create in service)
        ClinicTriggerService.auto_create_if_failed(sp)

        # exam risk trigger: only if we know exam_id
        if exam_id is not None:
            try:
                ClinicTriggerService.auto_create_if_exam_risk(
                    enrollment_id=int(enrollment_id),
                    session=session,
                    exam_id=int(exam_id),
                )
            except Exception:
                logger.exception("clinic exam risk trigger failed (enroll=%s, exam=%s, session=%s)", enrollment_id, exam_id, session.id)

        # ✅ Auto-resolution: 시험/과제 통과 시 ClinicLink 자동 해소
        # 핵심: 예약/출석이 아닌, 실제 pass 여부로만 해소
        self._try_auto_resolve(
            enrollment_id=int(enrollment_id),
            session=session,
            session_progress=sp,
            exam_id=exam_id,
        )

        # lecture aggregates + risk
        try:
            lp = LectureProgressCalculator.calculate(enrollment_id=int(enrollment_id), lecture=session.lecture)
            RiskEvaluator.evaluate(lp)
        except Exception:
            logger.exception("lecture/risk recompute failed (enroll=%s, lecture=%s)", enrollment_id, getattr(session, "lecture_id", None))
            raise

    # ---------------------------------------------------------
    # Auto-resolution: pass 시 ClinicLink 자동 해소
    # ---------------------------------------------------------
    def _try_auto_resolve(
        self,
        *,
        enrollment_id: int,
        session,
        session_progress,
        exam_id: Optional[int] = None,
    ) -> None:
        """
        SessionProgress 계산 후, 시험/과제 통과 시 ClinicLink를 자동 해소.
        해소 조건:
        - exam_passed=True → EXAM_PASS로 해소
        - homework_passed=True → HOMEWORK_PASS로 해소
        예약/출석은 해소 트리거가 아님.
        """
        try:
            # 미해소 ClinicLink가 없으면 skip
            if not has_unresolved_clinic_link(enrollment_id, session.id):
                return

            # V1.1.2: 개별 시험 단위로 해소 (세션 집계와 독립적)
            exam_meta = session_progress.exam_meta or {}
            exam_rows = exam_meta.get("exams", [])
            for exam_row in exam_rows:
                eid = int(exam_row.get("exam_id", 0) or 0)
                if not eid:
                    continue
                if not exam_row.get("passed", False):
                    continue  # 불합격 시험은 해소하지 않음

                # 합격한 시험의 ClinicLink 해소
                attempt_id = get_representative_exam_attempt_id(
                    exam_id=eid,
                    enrollment_id=int(enrollment_id),
                )

                ClinicResolutionService.resolve_by_exam_pass(
                    enrollment_id=enrollment_id,
                    session_id=session.id,
                    exam_id=eid,
                    attempt_id=attempt_id,
                    score=exam_row.get("score"),
                    pass_score=exam_row.get("pass_score"),
                    max_score=exam_row.get("max_score"),
                )

            # 과제별 해소: 세션의 homework ClinicLink를 순회하며
            # 해당 homework의 1차 HomeworkScore.passed=True 확인 후 해소
            homework_source_ids = list_unresolved_homework_source_ids(
                enrollment_id=enrollment_id,
                session_id=session.id,
            )
            for hw_id in homework_source_ids:
                hw_score = get_passed_homework_score(
                    enrollment_id=int(enrollment_id),
                    session_id=session.id,
                    homework_id=int(hw_id),
                )
                if not hw_score:
                    continue
                ClinicResolutionService.resolve_by_homework_pass(
                    enrollment_id=enrollment_id,
                    session_id=session.id,
                    homework_id=int(hw_id),
                    score=hw_score.score,
                    max_score=hw_score.max_score,
                )

        except Exception:
            # Auto-resolution failure must not block the pipeline
            logger.exception(
                "clinic auto-resolve failed (enrollment=%s, session=%s)",
                enrollment_id, getattr(session, "id", None),
            )

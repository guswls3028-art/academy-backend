# PATH: apps/domains/results/views/session_scores_view.py
"""
SessionScores API (FOR FRONTEND SCORE TAB)

GET /api/v1/sessions/{session_id}/scores/

✅ 목적
- "성적 탭" 메인 테이블에서 학생별 시험/과제 요약 + 편집 상태 표시
- results(시험) + homework(과제) + progress(클리닉) 데이터를 "조합"만 한다.

🚫 절대 금지
- 프론트에서 점수 계산/판정 요구
- submissions.status로 합불/통과 판단
- progress 결과(SessionProgress.completed 등)를 score API에서 직접 노출
- 새로운 비즈니스 로직/정책 생성

✅ 단일 진실(불변)
- 시험 점수/합불: results(Result + Exam.pass_score)
- 과제 점수: homework(HomeworkScore)
- clinic_required: progress(ClinicLink, is_auto=True 기준)

✅ LOCK / null 규칙
- score == null : 미산출/미응시/처리중 (0점과 다름)
- exam.is_locked : 대표 attempt.status == "grading" 이면 true
- homework.is_locked : HomeworkScore.is_locked 이면 true
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from django.utils import timezone
from django.db.models import Max
from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.domains.results.utils.result_queries import latest_results_per_enrollment

from apps.domains.results.serializers.session_scores import SessionScoreRowSerializer

from apps.domains.lectures.models import Session
from apps.domains.progress.models import SessionProgress, ClinicLink
from apps.domains.exams.models import Exam

from apps.domains.homework.models import HomeworkScore

# Enrollment은 프로젝트마다 구조가 다를 수 있어 방어적으로 접근
from apps.domains.enrollment.models import Enrollment


def _safe_student_name(enrollment: Enrollment) -> str:
    """
    프로젝트별 Enrollment 연결 방식(user/student/name 필드)을 방어적으로 처리.

    ✅ 목표
    - 가능한 경우 학생 이름을 최대한 표시
    - 불가능하면 "-" 로 안전하게
    """
    try:
        # 1) Enrollment.student relation
        if hasattr(enrollment, "student") and enrollment.student:
            s = enrollment.student
            for key in ("name", "full_name", "username"):
                v = getattr(s, key, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()

        # 2) Enrollment.user relation
        if hasattr(enrollment, "user") and enrollment.user:
            u = enrollment.user
            for key in ("name", "full_name", "username", "first_name"):
                v = getattr(u, key, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()

        # 3) Enrollment 자체에 name 필드가 있는 경우
        for key in ("student_name", "name", "title"):
            v = getattr(enrollment, key, None)
            if isinstance(v, str) and v.strip():
                return v.strip()

    except Exception:
        pass

    return "-"


class SessionScoresView(APIView):
    """
    ✅ SessionScores (Teacher/Admin only)

    GET /api/v1/sessions/{session_id}/scores/

    Query Params (optional):
    - enrollment_id= : 특정 학생 필터
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, session_id: int):
        session = get_object_or_404(Session, id=int(session_id))

        # -------------------------------------------------
        # 1) 대상 enrollment 모수: SessionProgress 기준(원본 존중)
        # -------------------------------------------------
        sp_qs = SessionProgress.objects.filter(session=session)

        enrollment_id_param = request.query_params.get("enrollment_id")
        if enrollment_id_param:
            try:
                sp_qs = sp_qs.filter(enrollment_id=int(enrollment_id_param))
            except Exception:
                pass

        enrollment_ids = list(
            sp_qs.values_list("enrollment_id", flat=True).distinct()
        )
        if not enrollment_ids:
            return Response([])

        # -------------------------------------------------
        # 2) Session에 연결된 Exam 목록 (단일 진실)
        # -------------------------------------------------
        exams = list(get_exams_for_session(session))
        exam_ids = [int(e.id) for e in exams]

        # exam_id가 0개일 수도 있음 (세션에 시험이 없을 수 있음)
        # 이 경우에도 homework row는 내려줄 수 있으나, 프론트 계약이 "exam_id 포함"이라
        # 최소 1개 exam이 있어야 행 생성이 가능함.
        #
        # ✅ 정책(보수적):
        # - 시험이 0개면 빈 배열 반환
        #   (프론트가 row key로 exam_id를 필수 사용하기 때문)
        if not exam_ids:
            return Response([])

        # -------------------------------------------------
        # 3) Clinic 대상자 (단일 진실: ClinicLink, is_auto=True)
        # -------------------------------------------------
        clinic_ids: Set[int] = set(
            ClinicLink.objects.filter(session=session, is_auto=True)
            .values_list("enrollment_id", flat=True)
            .distinct()
        )

        # -------------------------------------------------
        # 4) Enrollment → student_name 매핑
        # -------------------------------------------------
        enrollments = Enrollment.objects.filter(id__in=[int(x) for x in enrollment_ids])
        enrollment_map: Dict[int, Enrollment] = {int(e.id): e for e in enrollments}

        student_name_map: Dict[int, str] = {}
        for eid in enrollment_ids:
            enr = enrollment_map.get(int(eid))
            student_name_map[int(eid)] = _safe_student_name(enr) if enr else "-"

        # -------------------------------------------------
        # 5) HomeworkScore 매핑 (enrollment x session)
        # -------------------------------------------------
        hw_qs = HomeworkScore.objects.filter(
            session=session,
            enrollment_id__in=[int(x) for x in enrollment_ids],
        )
        hw_map: Dict[int, HomeworkScore] = {
            int(h.enrollment_id): h for h in hw_qs
        }

        # -------------------------------------------------
        # 6) Exam Result 매핑 (exam_id별로 enrollment당 최신 1개)
        #    - results.utils.latest_results_per_enrollment 사용 (중복 enrollment 방어)
        # -------------------------------------------------
        # exam_id -> { enrollment_id -> Result }
        result_map: Dict[int, Dict[int, Result]] = {}

        # 조회 최적화: 시험 수가 적지 않을 수 있으나, 일단 안정성을 우선한 명시적 루프
        for exid in exam_ids:
            rs = (
                latest_results_per_enrollment(target_type="exam", target_id=int(exid))
                .filter(enrollment_id__in=[int(x) for x in enrollment_ids])
            )
            bucket: Dict[int, Result] = {}
            for r in rs:
                bucket[int(r.enrollment_id)] = r
            result_map[int(exid)] = bucket

        # -------------------------------------------------
        # 7) Attempt lock 상태 매핑 (대표 attempt.status == grading)
        # -------------------------------------------------
        attempt_ids: Set[int] = set()
        for exid in exam_ids:
            for eid, r in result_map.get(int(exid), {}).items():
                if getattr(r, "attempt_id", None):
                    try:
                        attempt_ids.add(int(r.attempt_id))
                    except Exception:
                        pass

        attempt_status_map: Dict[int, str] = {}
        if attempt_ids:
            for a in ExamAttempt.objects.filter(id__in=list(attempt_ids)).only("id", "status"):
                attempt_status_map[int(a.id)] = str(a.status or "")

        # -------------------------------------------------
        # 8) Exam.pass_score 로딩 (시험 단위 합불)
        # -------------------------------------------------
        exam_pass_score_map: Dict[int, float] = {}
        for ex in exams:
            exam_pass_score_map[int(ex.id)] = float(getattr(ex, "pass_score", 0.0) or 0.0)

        # -------------------------------------------------
        # 9) Row 생성: (exam_id, enrollment_id) 단위
        # -------------------------------------------------
        rows: List[Dict[str, Any]] = []

        for exid in exam_ids:
            pass_score = float(exam_pass_score_map.get(int(exid), 0.0) or 0.0)
            per_exam_results = result_map.get(int(exid), {})

            for eid in enrollment_ids:
                eid_i = int(eid)
                r: Optional[Result] = per_exam_results.get(eid_i)

                # -----------------------------
                # exam block
                # -----------------------------
                if r is None:
                    exam_score = None
                    exam_max = None
                    exam_passed = False
                    exam_updated_at = None
                    attempt_status = ""
                    exam_locked = False
                    exam_lock_reason = None
                else:
                    exam_score = float(r.total_score or 0.0)
                    exam_max = float(r.max_score or 0.0)
                    exam_passed = bool(float(r.total_score or 0.0) >= float(pass_score))
                    exam_updated_at = getattr(r, "updated_at", None)

                    attempt_status = ""
                    if getattr(r, "attempt_id", None):
                        attempt_status = attempt_status_map.get(int(r.attempt_id), "") or ""

                    # 대표 attempt grading이면 locked
                    exam_locked = bool((attempt_status or "").lower() == "grading")
                    exam_lock_reason = "GRADING" if exam_locked else None

                # -----------------------------
                # homework block
                # -----------------------------
                hw: Optional[HomeworkScore] = hw_map.get(eid_i)
                if hw is None:
                    hw_score = None
                    hw_max = None
                    hw_passed = False
                    hw_updated_at = None
                    hw_locked = False
                    hw_lock_reason = None
                else:
                    # score는 null 허용
                    hw_score = hw.score if hw.score is not None else None
                    hw_max = hw.max_score if hw.max_score is not None else None
                    hw_passed = bool(hw.passed)
                    hw_updated_at = getattr(hw, "updated_at", None)

                    hw_locked = bool(hw.is_locked)
                    hw_lock_reason = str(hw.lock_reason) if hw.lock_reason else None

                # -----------------------------
                # clinic_required (단일 진실: ClinicLink, is_auto=True)
                # - 시험/과제 구분 없이 "세션 단위 대상자 사실"만 노출
                # -----------------------------
                clinic_required = bool(eid_i in clinic_ids)

                # -----------------------------
                # updated_at = max(exam_updated_at, hw_updated_at)
                # - 둘 다 없으면 session.updated_at fallback
                # -----------------------------
                updated_candidates = [d for d in [exam_updated_at, hw_updated_at, getattr(session, "updated_at", None)] if d]
                updated_at = max(updated_candidates) if updated_candidates else timezone.now()

                rows.append({
                    "exam_id": int(exid),

                    "enrollment_id": eid_i,
                    "student_name": student_name_map.get(eid_i, "-"),

                    "exam": {
                        "score": exam_score,
                        "max_score": exam_max,
                        "passed": bool(exam_passed),
                        "clinic_required": bool(clinic_required),
                        "is_locked": bool(exam_locked),
                        "lock_reason": exam_lock_reason,
                    },

                    "homework": {
                        "score": hw_score,
                        "max_score": hw_max,
                        "passed": bool(hw_passed),
                        "clinic_required": bool(clinic_required),
                        "is_locked": bool(hw_locked),
                        "lock_reason": hw_lock_reason,
                    },

                    "updated_at": updated_at,
                })

        return Response(SessionScoreRowSerializer(rows, many=True).data)

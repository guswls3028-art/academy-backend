# PATH: apps/domains/results/views/session_scores_view.py
"""
SessionScores API (FOR FRONTEND SCORE TAB)

GET /api/v1/results/admin/sessions/<session_id>/scores/

✅ 목적
- 성적 탭 메인 테이블에서 학생별 시험/과제 요약 + 편집 상태 표시
- results + homework_results + progress 데이터를 "조합"만 한다.

🚫 금지
- 점수 계산/정책 생성
- homework percent / cutline 계산
- progress 결과 직접 노출

✅ 단일 진실
- exam: results(Result + Exam.pass_score)
- homework: homework_results.HomeworkScore
- clinic_required: progress.ClinicLink(is_auto=True)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from django.utils import timezone
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

# ✅ 단일 진실
from apps.domains.homework_results.models import HomeworkScore
from apps.domains.enrollment.models import Enrollment


def _safe_student_name(enrollment: Enrollment) -> str:
    try:
        if hasattr(enrollment, "student") and enrollment.student:
            s = enrollment.student
            for key in ("name", "full_name", "username"):
                v = getattr(s, key, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()

        if hasattr(enrollment, "user") and enrollment.user:
            u = enrollment.user
            for key in ("name", "full_name", "username", "first_name"):
                v = getattr(u, key, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()

        for key in ("student_name", "name", "title"):
            v = getattr(enrollment, key, None)
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception:
        pass

    return "-"


class SessionScoresView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, session_id: int):
        session = get_object_or_404(Session, id=int(session_id))

        # -------------------------------------------------
        # 1) enrollment 모수 (SessionProgress 기준)
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

        # -------------------------------------------------
        # 2) Session ↔ Exam
        # -------------------------------------------------
        exams = list(get_exams_for_session(session))
        exam_ids = [int(e.id) for e in exams]

        meta = {
            "exams": [
                {
                    "exam_id": int(ex.id),
                    "title": str(getattr(ex, "title", "")),
                    "pass_score": float(getattr(ex, "pass_score", 0.0) or 0.0),
                }
                for ex in exams
            ],
            "homework": {"title": "과제", "unit": "%"},
        }

        if not enrollment_ids:
            return Response({"meta": meta, "rows": []})

        # -------------------------------------------------
        # 3) Clinic 대상자
        # -------------------------------------------------
        clinic_ids: Set[int] = set(
            ClinicLink.objects.filter(session=session, is_auto=True)
            .values_list("enrollment_id", flat=True)
            .distinct()
        )

        # -------------------------------------------------
        # 4) Enrollment → student_name
        # -------------------------------------------------
        enrollments = Enrollment.objects.filter(id__in=enrollment_ids)
        enrollment_map = {int(e.id): e for e in enrollments}

        student_name_map = {
            int(eid): _safe_student_name(enrollment_map.get(int(eid)))
            for eid in enrollment_ids
        }

        # -------------------------------------------------
        # 5) HomeworkScore
        # -------------------------------------------------
        hw_qs = HomeworkScore.objects.filter(
            session=session,
            enrollment_id__in=enrollment_ids,
        )
        hw_map = {int(h.enrollment_id): h for h in hw_qs}

        # -------------------------------------------------
        # 6) Exam Result map
        # -------------------------------------------------
        result_map: Dict[int, Dict[int, Result]] = {}
        for exid in exam_ids:
            rs = (
                latest_results_per_enrollment(
                    target_type="exam",
                    target_id=int(exid),
                )
                .filter(enrollment_id__in=enrollment_ids)
            )
            result_map[int(exid)] = {int(r.enrollment_id): r for r in rs}

        # -------------------------------------------------
        # 7) Attempt LOCK 상태
        # -------------------------------------------------
        attempt_ids: Set[int] = set()
        for per_exam in result_map.values():
            for r in per_exam.values():
                if getattr(r, "attempt_id", None):
                    attempt_ids.add(int(r.attempt_id))

        attempt_status_map: Dict[int, str] = {}
        if attempt_ids:
            for a in ExamAttempt.objects.filter(id__in=attempt_ids).only("id", "status"):
                attempt_status_map[int(a.id)] = str(a.status or "")

        # -------------------------------------------------
        # 8) Exam 메타 map
        # -------------------------------------------------
        exam_pass_score_map = {
            int(ex.id): float(getattr(ex, "pass_score", 0.0) or 0.0)
            for ex in exams
        }
        exam_title_map = {
            int(ex.id): str(getattr(ex, "title", "") or "")
            for ex in exams
        }

        # -------------------------------------------------
        # 9) Row 생성 (enrollment 기준)
        # -------------------------------------------------
        rows: List[Dict[str, Any]] = []

        for eid in enrollment_ids:
            eid_i = int(eid)
            clinic_required = bool(eid_i in clinic_ids)

            exams_payload: List[Dict[str, Any]] = []
            exam_updated_ats: List[Any] = []

            for exid in exam_ids:
                pass_score = exam_pass_score_map.get(int(exid), 0.0)
                r: Optional[Result] = result_map.get(int(exid), {}).get(eid_i)

                if r is None:
                    exam_score = None
                    exam_max = None
                    exam_passed = False
                    exam_updated_at = None
                    exam_locked = False
                    exam_lock_reason = None
                else:
                    exam_score = float(r.total_score or 0.0)
                    exam_max = float(r.max_score or 0.0)
                    exam_passed = bool(exam_score >= float(pass_score))
                    exam_updated_at = getattr(r, "updated_at", None)

                    attempt_status = ""
                    if getattr(r, "attempt_id", None):
                        attempt_status = attempt_status_map.get(
                            int(r.attempt_id), ""
                        )

                    exam_locked = bool((attempt_status or "").lower() == "grading")
                    exam_lock_reason = "GRADING" if exam_locked else None

                if exam_updated_at:
                    exam_updated_ats.append(exam_updated_at)

                exams_payload.append(
                    {
                        "exam_id": int(exid),
                        "title": exam_title_map.get(int(exid), ""),
                        "pass_score": float(pass_score),
                        "block": {
                            "score": exam_score,
                            "max_score": exam_max,
                            "passed": bool(exam_passed),
                            "clinic_required": bool(clinic_required),
                            "is_locked": bool(exam_locked),
                            "lock_reason": exam_lock_reason,
                        },
                    }
                )

            # ---------- homework ----------
            hw: Optional[HomeworkScore] = hw_map.get(eid_i)
            if hw is None:
                hw_score = None
                hw_max = None
                hw_passed = False
                hw_updated_at = None
                hw_locked = False
                hw_lock_reason = None
            else:
                hw_score = hw.score
                hw_max = hw.max_score
                hw_passed = bool(hw.passed)
                hw_updated_at = getattr(hw, "updated_at", None)
                hw_locked = bool(hw.is_locked)
                hw_lock_reason = str(hw.lock_reason) if hw.lock_reason else None

            updated_candidates = [
                d
                for d in (
                    max(exam_updated_ats) if exam_updated_ats else None,
                    hw_updated_at,
                    getattr(session, "updated_at", None),
                )
                if d
            ]
            updated_at = max(updated_candidates) if updated_candidates else timezone.now()

            rows.append(
                {
                    "enrollment_id": eid_i,
                    "student_name": student_name_map.get(eid_i, "-"),
                    "exams": exams_payload,
                    "homework": {
                        "score": hw_score,
                        "max_score": hw_max,
                        "passed": bool(hw_passed),
                        "clinic_required": bool(clinic_required),
                        "is_locked": bool(hw_locked),
                        "lock_reason": hw_lock_reason,
                    },
                    "updated_at": updated_at,
                }
            )

        return Response(
            {
                "meta": meta,
                "rows": SessionScoreRowSerializer(rows, many=True).data,
            }
        )

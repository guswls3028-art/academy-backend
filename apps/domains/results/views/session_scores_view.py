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

📌 중요 설계 결정
- enrollment 모수는 SessionProgress ❌
- 시험 OR 과제에 한 번이라도 연결된 Enrollment 기준 ✅
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db.models import Q

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.domains.results.utils.result_queries import latest_results_per_enrollment
from apps.domains.results.serializers.session_scores import SessionScoreRowSerializer

from apps.domains.lectures.models import Session
from apps.domains.progress.models import ClinicLink

# ✅ 단일 진실
from apps.domains.homework_results.models import HomeworkScore, Homework
from apps.domains.enrollment.models import Enrollment

# 모수
from apps.domains.exams.models import ExamEnrollment
from apps.domains.homework.models import HomeworkEnrollment


def _safe_student_name(enrollment: Optional[Enrollment]) -> str:
    if not enrollment:
        return "-"

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
        # 0) Session ↔ Exam
        # -------------------------------------------------
        exams = list(get_exams_for_session(session))
        exam_ids = [int(e.id) for e in exams]

        # -------------------------------------------------
        # 1) enrollment 모수 (시험 OR 과제)
        # -------------------------------------------------
        hw_enrollment_ids_qs = (
            HomeworkEnrollment.objects.filter(session_id=int(session.id))
            .values_list("enrollment_id", flat=True)
        )

        if exam_ids:
            ex_enrollment_ids_qs = (
                ExamEnrollment.objects.filter(exam_id__in=exam_ids)
                .values_list("enrollment_id", flat=True)
            )
            enrollment_qs = Enrollment.objects.filter(
                Q(id__in=hw_enrollment_ids_qs) | Q(id__in=ex_enrollment_ids_qs)
            ).distinct()
        else:
            enrollment_qs = Enrollment.objects.filter(
                Q(id__in=hw_enrollment_ids_qs)
            ).distinct()

        enrollment_ids = list(enrollment_qs.values_list("id", flat=True))

        # -------------------------------------------------
        # 2) Meta (프론트 표시용)
        # -------------------------------------------------
        homeworks = list(
            Homework.objects.filter(session=session).order_by("id")
        )

        meta = {
            "exams": [
                {
                    "exam_id": int(ex.id),
                    "title": str(getattr(ex, "title", "")),
                    "pass_score": float(getattr(ex, "pass_score", 0.0) or 0.0),
                }
                for ex in exams
            ],
            "homeworks": [
                {
                    "homework_id": int(hw.id),
                    "title": str(hw.title),
                    "unit": None,  # ❗ 단위 판단 안 함 (서버 정책 유지)
                }
                for hw in homeworks
            ],
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
        # 5) HomeworkScore map (enrollment_id → homework_id → score)
        # -------------------------------------------------
        hw_scores = HomeworkScore.objects.filter(
            session=session,
            enrollment_id__in=enrollment_ids,
        )

        hw_map: Dict[int, Dict[int, HomeworkScore]] = {}
        for hw in hw_scores:
            hw_map.setdefault(int(hw.enrollment_id), {})[int(hw.homework_id)] = hw

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

        exam_pass_score_map = {
            int(ex.id): float(getattr(ex, "pass_score", 0.0) or 0.0)
            for ex in exams
        }
        exam_title_map = {int(ex.id): str(getattr(ex, "title", "") or "") for ex in exams}

        # -------------------------------------------------
        # 8) Row 생성
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
                    exam_passed = None
                    exam_updated_at = None
                    exam_locked = False
                    exam_lock_reason = None
                else:
                    exam_score = float(getattr(r, "total_score", None) or 0.0)
                    exam_max = float(getattr(r, "max_score", None) or 0.0)
                    exam_passed = getattr(r, "passed", None)
                    if exam_passed is not None:
                        exam_passed = bool(exam_passed)

                    exam_updated_at = getattr(r, "updated_at", None)
                    attempt_status = ""
                    if getattr(r, "attempt_id", None):
                        attempt_status = attempt_status_map.get(int(r.attempt_id), "")

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
                            "passed": exam_passed,
                            "clinic_required": bool(clinic_required),
                            "is_locked": bool(exam_locked),
                            "lock_reason": exam_lock_reason,
                        },
                    }
                )

            # ---------- homework (1:N) ----------
            homeworks_payload: List[Dict[str, Any]] = []

            for hw in homeworks:
                hw_score_obj = hw_map.get(eid_i, {}).get(int(hw.id))

                if hw_score_obj is None:
                    hw_score = None
                    hw_max = None
                    hw_passed = None
                    hw_updated_at = None
                    hw_locked = False
                    hw_lock_reason = None
                else:
                    hw_score = hw_score_obj.score
                    hw_max = hw_score_obj.max_score
                    hw_passed = getattr(hw_score_obj, "passed", None)
                    if hw_passed is not None:
                        hw_passed = bool(hw_passed)

                    hw_updated_at = getattr(hw_score_obj, "updated_at", None)
                    hw_locked = bool(getattr(hw_score_obj, "is_locked", False))
                    hw_lock_reason = (
                        str(hw_score_obj.lock_reason)
                        if getattr(hw_score_obj, "lock_reason", None)
                        else None
                    )

                homeworks_payload.append(
                    {
                        "homework_id": int(hw.id),
                        "title": str(hw.title),
                        "block": {
                            "score": hw_score,
                            "max_score": hw_max,
                            "passed": hw_passed,
                            "clinic_required": bool(clinic_required),
                            "is_locked": bool(hw_locked),
                            "lock_reason": hw_lock_reason,
                        },
                    }
                )

            updated_candidates = [
                d
                for d in (
                    (max(exam_updated_ats) if exam_updated_ats else None),
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
                    "homeworks": homeworks_payload,
                    "updated_at": updated_at,
                }
            )

        return Response(
            {
                "meta": meta,
                "rows": SessionScoreRowSerializer(rows, many=True).data,
            }
        )

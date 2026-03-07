# PATH: apps/domains/results/views/session_scores_view.py
"""
SessionScores API (FOR FRONTEND SCORE TAB)

GET /api/v1/results/admin/sessions/<session_id>/scores/

✅ 목적
- 성적 탭 메인 테이블에서 학생별 시험/과제 요약 + 편집 상태 표시
- results + homework_results + progress 데이터를 "조합"만 한다.

✅ 삭제된 학생(유령 학생) 제외
- student.deleted_at IS NOT NULL 인 학생은 수강/성적 목록에서 제외 (Enrollment 필터).
- 수강·출결·클리닉 파이프라인과 동일 정합성 유지.

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

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.models import Result, ExamAttempt
from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.domains.results.utils.result_queries import latest_results_per_enrollment
from apps.domains.results.serializers.session_scores import SessionScoreRowSerializer

from apps.domains.lectures.models import Session
from apps.domains.progress.models import ClinicLink

from apps.domains.homework_results.models import HomeworkScore
from apps.domains.homework_results.models import Homework
from apps.domains.homework.models import HomeworkAssignment

from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.exams.models import ExamEnrollment


def _safe_student_name(enrollment: Optional[Enrollment]) -> str:
    if not enrollment:
        return "-"

    try:
        if hasattr(enrollment, "student") and enrollment.student:
            for k in ("name", "full_name", "username"):
                v = getattr(enrollment.student, k, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()

        if hasattr(enrollment, "user") and enrollment.user:
            for k in ("name", "full_name", "username", "first_name"):
                v = getattr(enrollment.user, k, None)
                if isinstance(v, str) and v.strip():
                    return v.strip()

        for k in ("student_name", "name", "title"):
            v = getattr(enrollment, k, None)
            if isinstance(v, str) and v.strip():
                return v.strip()
    except Exception:
        pass

    return "-"


class SessionScoresView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, session_id: int):
        session = get_object_or_404(Session, id=int(session_id))

        # -------------------------------------------------
        # 0) Exams
        # -------------------------------------------------
        exams = list(get_exams_for_session(session))
        exam_ids = [int(e.id) for e in exams]

        # -------------------------------------------------
        # 1) Enrollment 모수 (시험 OR 과제)
        # -------------------------------------------------
        # ❗️FIX: HomeworkEnrollment ❌
        # ✅ 과제 대상자의 단일 진실은 HomeworkAssignment
        hw_enrollment_ids_qs = HomeworkAssignment.objects.filter(
            session=session
        ).values_list("enrollment_id", flat=True)

        if exam_ids:
            ex_enrollment_ids_qs = ExamEnrollment.objects.filter(
                exam_id__in=exam_ids
            ).values_list("enrollment_id", flat=True)

            enrollment_qs = (
                Enrollment.objects.filter(
                    Q(id__in=hw_enrollment_ids_qs)
                    | Q(id__in=ex_enrollment_ids_qs)
                )
                .filter(student__deleted_at__isnull=True)
                .distinct()
            )
        else:
            enrollment_qs = (
                Enrollment.objects.filter(id__in=hw_enrollment_ids_qs)
                .filter(student__deleted_at__isnull=True)
                .distinct()
            )

        enrollment_ids = list(enrollment_qs.values_list("id", flat=True))

        # 시험/과제 연결 전: 세션 수강생(SessionEnrollment) 폴백 — 삭제된 학생 제외
        if not enrollment_ids:
            session_enrollment_ids = list(
                SessionEnrollment.objects.filter(session=session)
                .values_list("enrollment_id", flat=True)
                .distinct()
            )
            enrollment_ids = list(
                Enrollment.objects.filter(
                    id__in=session_enrollment_ids
                )
                .filter(student__deleted_at__isnull=True)
                .values_list("id", flat=True)
            )

        # -------------------------------------------------
        # 2) Meta (프론트 계약)
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
                    "unit": None,  # 서버 단일 진실
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
        enrollment_map = {
            int(e.id): e
            for e in Enrollment.objects.filter(id__in=enrollment_ids)
        }

        student_name_map = {
            eid: _safe_student_name(enrollment_map.get(eid))
            for eid in enrollment_ids
        }

        # -------------------------------------------------
        # 5) HomeworkScore map (enrollment → homework → score)
        # -------------------------------------------------
        hw_scores = HomeworkScore.objects.filter(
            session=session,
            enrollment_id__in=enrollment_ids,
        )

        hw_map: Dict[int, Dict[int, HomeworkScore]] = {}
        for hs in hw_scores:
            hw_map.setdefault(int(hs.enrollment_id), {})[int(hs.homework_id)] = hs

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
        attempt_ids = {
            int(r.attempt_id)
            for per_exam in result_map.values()
            for r in per_exam.values()
            if r.attempt_id
        }

        attempt_status_map = {
            int(a.id): str(a.status or "")
            for a in ExamAttempt.objects.filter(id__in=attempt_ids)
        }

        # -------------------------------------------------
        # 8) Exam 메타
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
        # 9) Rows
        # -------------------------------------------------
        rows: List[Dict[str, Any]] = []

        for eid in enrollment_ids:
            clinic_required = eid in clinic_ids

            exams_payload = []
            exam_updated_ats = []

            for exid in exam_ids:
                r = result_map.get(exid, {}).get(eid)

                if r is None:
                    block = {
                        "score": None,
                        "max_score": None,
                        "passed": None,
                        "clinic_required": clinic_required,
                        "is_locked": False,
                        "lock_reason": None,
                    }
                    updated_at = None
                else:
                    attempt_status = attempt_status_map.get(
                        int(r.attempt_id), ""
                    )
                    locked = attempt_status.lower() == "grading"

                    block = {
                        "score": float(r.total_score or 0.0),
                        "max_score": float(r.max_score or 0.0),
                        "passed": (
                            bool(r.passed)
                            if r.passed is not None
                            else None
                        ),
                        "clinic_required": clinic_required,
                        "is_locked": locked,
                        "lock_reason": "GRADING" if locked else None,
                    }
                    updated_at = r.updated_at

                if updated_at:
                    exam_updated_ats.append(updated_at)

                exams_payload.append(
                    {
                        "exam_id": exid,
                        "title": exam_title_map.get(exid, ""),
                        "pass_score": exam_pass_score_map.get(exid, 0.0),
                        "block": block,
                    }
                )

            homeworks_payload = []
            for hw in homeworks:
                hs = hw_map.get(eid, {}).get(int(hw.id))

                if hs is None:
                    block = {
                        "score": None,
                        "max_score": None,
                        "passed": None,
                        "clinic_required": clinic_required,
                        "is_locked": False,
                        "lock_reason": None,
                    }
                    updated_at = None
                else:
                    block = {
                        "score": hs.score,
                        "max_score": hs.max_score,
                        "passed": (
                            bool(hs.passed)
                            if hs.passed is not None
                            else None
                        ),
                        "clinic_required": clinic_required,
                        "is_locked": bool(hs.is_locked),
                        "lock_reason": hs.lock_reason,
                    }
                    updated_at = hs.updated_at

                homeworks_payload.append(
                    {
                        "homework_id": int(hw.id),
                        "title": str(hw.title),
                        "block": block,
                    }
                )

            updated_at = max(
                d
                for d in [
                    *(exam_updated_ats or []),
                    *(hs.updated_at for hs in hw_scores if hs.enrollment_id == eid),
                    getattr(session, "updated_at", None),
                ]
                if d
            )

            rows.append(
                {
                    "enrollment_id": eid,
                    "student_name": student_name_map.get(eid, "-"),
                    "exams": exams_payload,
                    "homeworks": homeworks_payload,
                    "updated_at": updated_at or timezone.now(),
                }
            )

        return Response(
            {
                "meta": meta,
                "rows": SessionScoreRowSerializer(rows, many=True).data,
            }
        )

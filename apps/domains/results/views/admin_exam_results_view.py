# apps/domains/results/views/admin_exam_results_view.py
from __future__ import annotations

from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.models import Result, ResultFact, ExamAttempt
from apps.domains.results.serializers.admin_exam_result_row import (
    AdminExamResultRowSerializer,
)

from apps.domains.lectures.models import Session
from apps.domains.submissions.models import Submission
from apps.domains.exams.models import Exam
from apps.domains.enrollment.models import Enrollment

# ✅ 단일 진실 유틸
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.clinic import is_clinic_required
from apps.domains.results.utils.result_queries import latest_results_per_enrollment
from apps.domains.results.views.session_scores_view import _safe_student_name


class AdminExamResultsView(ListAPIView):
    """
    GET /results/admin/exams/<exam_id>/results/

    ✅ 목표(원본 유지 + 정합성 강화)
    - Result(스냅샷) 기반 점수 리스트
    - Attempt/Submission 상태 연결
    - Clinic 기준 통일(ClinicLink)
    - Session↔Exam 매핑 단일화(utils.session_exam)

    ⚠️ pass 기준 정의:
    - 이 화면은 "시험(exam) 단위 결과"이므로
      pass/fail은 Exam.pass_score 기준으로 제공한다.
    - 세션 종합 통과(SessionProgress.exam_passed)는
      /admin/sessions/... summary API에서 제공하는 것이 정석.

    응답: { "count", "next", "previous", "results": AdminExamResultRow[] }
    ?page=1 로 페이지 접근 가능.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    pagination_class = PageNumberPagination
    serializer_class = AdminExamResultRowSerializer

    def get_queryset(self):
        exam_id = self.kwargs.get("exam_id")
        if exam_id is None:
            return Result.objects.none()

        # ✅ tenant isolation: verify exam belongs to tenant
        if not Exam.objects.filter(id=int(exam_id), sessions__lecture__tenant=self.request.tenant).exists():
            return Result.objects.none()

        return (
            latest_results_per_enrollment(
                target_type="exam",
                target_id=int(exam_id),
            )
            .order_by("enrollment_id")
        )

    def list(self, request, *args, **kwargs):
        exam_id = int(self.kwargs["exam_id"])

        exam = Exam.objects.filter(id=exam_id).first()
        pass_score = float(getattr(exam, "pass_score", 0.0) or 0.0) if exam else 0.0

        queryset = self.get_queryset()

        # -------------------------------------------------
        # 페이지네이션: Result queryset을 먼저 페이지 처리
        # -------------------------------------------------
        page = self.paginate_queryset(queryset)
        if page is None:
            return self.get_paginated_response([])

        results = page

        # -------------------------------------------------
        # enrollment_id → student_name (Enrollment 단일 진실)
        # -------------------------------------------------
        enrollment_ids_page = [int(r.enrollment_id) for r in results]
        enrollment_map = {
            int(e.id): e
            for e in Enrollment.objects.filter(id__in=enrollment_ids_page).select_related("student")
        }
        student_name_map = {
            eid: _safe_student_name(enrollment_map.get(eid))
            for eid in enrollment_ids_page
        }

        # -------------------------------------------------
        # Session 찾기 (clinic 판단용)
        # -------------------------------------------------
        session = get_primary_session_for_exam(exam_id)

        # -------------------------------------------------
        # enrollment_id → 최신 attempt/submission 맵 (exam 전체 기준)
        # -------------------------------------------------
        fact_qs = (
            ResultFact.objects
            .filter(target_type="exam", target_id=exam_id)
            .exclude(attempt_id__isnull=True)
            .order_by("-attempt_id", "-id")
            .values("enrollment_id", "attempt_id", "submission_id")
        )

        latest_map = {}
        for row in fact_qs:
            eid = int(row["enrollment_id"])
            if eid not in latest_map:
                latest_map[eid] = {
                    "attempt_id": int(row["attempt_id"]),
                    "submission_id": int(row["submission_id"]),
                }

        # Result.attempt_id fallback (현재 페이지 결과만)
        attempt_ids = [r.attempt_id for r in results if getattr(r, "attempt_id", None)]
        attempt_map = {
            a.id: a
            for a in ExamAttempt.objects.filter(id__in=attempt_ids)
        }

        for r in results:
            eid = int(r.enrollment_id)
            aid = getattr(r, "attempt_id", None)
            if not aid:
                continue
            a = attempt_map.get(int(aid))
            if not a:
                continue
            if (eid not in latest_map) or (not latest_map[eid].get("submission_id")):
                latest_map[eid] = {
                    "attempt_id": int(a.id),
                    "submission_id": int(a.submission_id),
                }

        # Submission.status (현재 페이지에서 참조하는 submission만)
        submission_ids = [
            v["submission_id"]
            for v in latest_map.values()
            if v.get("submission_id")
        ]
        submission_status_map = (
            {s.id: s.status for s in Submission.objects.filter(id__in=submission_ids)}
            if submission_ids
            else {}
        )

        # -------------------------------------------------
        # rows 구성 (기존 로직 유지)
        # -------------------------------------------------
        rows = []
        for r in results:
            enrollment_id = int(r.enrollment_id)
            student_name = student_name_map.get(enrollment_id, "-")

            latest = latest_map.get(enrollment_id, {})
            submission_id = latest.get("submission_id")
            submission_status = (
                submission_status_map.get(submission_id) if submission_id else None
            )

            passed = bool(float(r.total_score or 0.0) >= float(pass_score))

            clinic_required = bool(
                session
                and is_clinic_required(
                    session=session,
                    enrollment_id=enrollment_id,
                    include_manual=False,
                )
            )

            rows.append({
                "enrollment_id": enrollment_id,
                "student_name": student_name,

                "exam_score": float(r.total_score or 0.0),
                "exam_max_score": float(r.max_score or 0.0),

                "final_score": float(r.total_score or 0.0),

                "passed": passed,
                "clinic_required": clinic_required,

                "submitted_at": r.submitted_at,

                "submission_id": submission_id,
                "submission_status": submission_status,
            })

        serializer = AdminExamResultRowSerializer(rows, many=True)
        return self.get_paginated_response(serializer.data)

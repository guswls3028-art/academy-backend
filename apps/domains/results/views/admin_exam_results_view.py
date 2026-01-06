# PATH: apps/domains/results/views/admin_exam_results_view.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ResultFact
from apps.domains.results.serializers.admin_exam_result_row import (
    AdminExamResultRowSerializer,
)

from apps.domains.progress.models import SessionProgress
from apps.domains.lectures.models import Session
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission


class AdminExamResultsView(APIView):
    """
    GET /results/admin/exams/<exam_id>/results/

    🔥 attempt 중심 설계 반영 버전

    변경 포인트 요약:
    - ResultFact 기준 "최신 submission" 판단 시
      submission_id 단독이 아니라 attempt_id 기준으로 판단
    - 재시험 / 재채점 / 대표 attempt 변경에도 의미적으로 올바른 최신값 보장
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        exam_id = int(exam_id)

        # -------------------------------------------------
        # 1️⃣ Result (최신 스냅샷)
        # -------------------------------------------------
        results = Result.objects.filter(
            target_type="exam",
            target_id=exam_id,
        )

        # -------------------------------------------------
        # 2️⃣ Session → Progress (enrollment 기준)
        # -------------------------------------------------
        session = Session.objects.filter(exam__id=exam_id).first()
        progress_map = {
            sp.enrollment_id: sp
            for sp in SessionProgress.objects.filter(session=session)
        }

        # -------------------------------------------------
        # 3️⃣ Student 조회 최적화
        # -------------------------------------------------
        student_ids = [
            sp.student_id
            for sp in progress_map.values()
            if getattr(sp, "student_id", None)
        ]

        student_map = {
            s.id: s
            for s in Student.objects.filter(id__in=student_ids)
        }

        # -------------------------------------------------
        # 4️⃣ 🔥 enrollment_id → 최신 attempt/submission 맵
        # -------------------------------------------------
        """
        ❗ 핵심 변경 포인트

        기존:
        - ResultFact.id DESC 기준 → submission_id 최신 판단
        문제:
        - 재시험/재채점 시 의미상 최신이 아닐 수 있음

        변경:
        - ResultFact.attempt_id 기준으로 "시험 응시 단위 최신" 판단
        """

        fact_qs = (
            ResultFact.objects
            .filter(
                target_type="exam",
                target_id=exam_id,
            )
            .exclude(attempt_id__isnull=True)
            .order_by("-attempt_id", "-id")
            .values(
                "enrollment_id",
                "attempt_id",
                "submission_id",
            )
        )

        latest_map = {}
        for row in fact_qs:
            eid = row["enrollment_id"]
            if eid not in latest_map:
                latest_map[eid] = {
                    "attempt_id": row["attempt_id"],
                    "submission_id": row["submission_id"],
                }

        # -------------------------------------------------
        # 5️⃣ Submission.status 조회
        # -------------------------------------------------
        submission_ids = [
            v["submission_id"]
            for v in latest_map.values()
            if v.get("submission_id")
        ]

        submission_status_map = {
            s.id: s.status
            for s in Submission.objects.filter(id__in=submission_ids)
        }

        # -------------------------------------------------
        # 6️⃣ 최종 rows 구성 (응답 스펙 변경 없음)
        # -------------------------------------------------
        rows = []

        for r in results:
            enrollment_id = r.enrollment_id
            sp = progress_map.get(enrollment_id)
            student = student_map.get(
                getattr(sp, "student_id", None)
            )

            latest = latest_map.get(enrollment_id, {})
            submission_id = latest.get("submission_id")
            submission_status = (
                submission_status_map.get(submission_id)
                if submission_id
                else None
            )

            rows.append({
                "enrollment_id": enrollment_id,
                "student_name": student.name if student else "-",

                "total_score": r.total_score,
                "max_score": r.max_score,

                "passed": bool(sp and not sp.failed),
                "clinic_required": bool(sp and sp.clinic_required),

                "submitted_at": r.submitted_at,

                # 🔥 Submission 연동 (기존 프론트 호환)
                "submission_id": submission_id,
                "submission_status": submission_status,
            })

        return Response(
            AdminExamResultRowSerializer(rows, many=True).data
        )

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

    🔥 변경 사항:
    - ResultFact 기준으로 최신 submission_id 추출
    - Submission.status 조인
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        exam_id = int(exam_id)

        # 1️⃣ Results (snapshot)
        results = Result.objects.filter(
            target_type="exam",
            target_id=exam_id,
        )

        # 2️⃣ Session → Progress
        session = Session.objects.filter(exam__id=exam_id).first()
        progress_map = {
            sp.enrollment_id: sp
            for sp in SessionProgress.objects.filter(session=session)
        }

        # 3️⃣ 학생 조회 최적화
        student_ids = [
            sp.student_id
            for sp in progress_map.values()
            if getattr(sp, "student_id", None)
        ]

        student_map = {
            s.id: s
            for s in Student.objects.filter(id__in=student_ids)
        }

        # 4️⃣ 🔥 enrollment_id → latest submission_id 맵
        fact_qs = (
            ResultFact.objects
            .filter(
                target_type="exam",
                target_id=exam_id,
            )
            .order_by("-id")
            .values("enrollment_id", "submission_id")
        )

        latest_submission_map = {}
        for row in fact_qs:
            eid = row["enrollment_id"]
            if eid not in latest_submission_map:
                latest_submission_map[eid] = row["submission_id"]

        # 5️⃣ Submission status 맵
        submission_ids = list(latest_submission_map.values())
        submission_status_map = {
            s.id: s.status
            for s in Submission.objects.filter(id__in=submission_ids)
        }

        # 6️⃣ 최종 rows
        rows = []

        for r in results:
            enrollment_id = r.enrollment_id
            sp = progress_map.get(enrollment_id)
            student = student_map.get(
                getattr(sp, "student_id", None)
            )

            submission_id = latest_submission_map.get(enrollment_id)
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

                # 🔥 Submission 연동
                "submission_id": submission_id,
                "submission_status": submission_status,
            })

        return Response(
            AdminExamResultRowSerializer(rows, many=True).data
        )

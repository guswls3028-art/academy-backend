# apps/domains/results/views/admin_exam_results_view.py

from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ResultFact, ExamAttempt
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

        if not session:
            progress_map = {}
        else:
            progress_map = {
                sp.enrollment_id: sp
                for sp in SessionProgress.objects.filter(session=session)
            }

        # -------------------------------------------------
        # 3️⃣ Student 조회 최적화
        # -------------------------------------------------
        student_ids = set()

        for sp in progress_map.values():
            if hasattr(sp, "student_id") and getattr(sp, "student_id", None):
                student_ids.add(int(sp.student_id))
            elif hasattr(sp, "user_id") and getattr(sp, "user_id", None):
                student_ids.add(int(sp.user_id))

        student_map = {
            s.id: s
            for s in Student.objects.filter(id__in=list(student_ids))
        }

        # -------------------------------------------------
        # 4️⃣ enrollment_id → 최신 attempt/submission 맵
        # -------------------------------------------------
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
        # 4-1️⃣ Fact 없는 경우 Result.attempt_id fallback
        # -------------------------------------------------
        attempt_ids = [
            r.attempt_id
            for r in results
            if getattr(r, "attempt_id", None)
        ]

        attempt_map = {
            a.id: a
            for a in ExamAttempt.objects.filter(id__in=attempt_ids)
        }

        for r in results:
            eid = r.enrollment_id
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
        # 6️⃣ 최종 rows 구성
        # -------------------------------------------------
        rows = []

        for r in results:
            enrollment_id = r.enrollment_id
            sp = progress_map.get(enrollment_id)

            sid = None
            if sp is not None:
                sid = getattr(sp, "student_id", None) or getattr(sp, "user_id", None)
            student = student_map.get(int(sid)) if sid else None

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

                # =====================================
                # 🔧 PATCH: 점수 의미 분리
                # =====================================
                "exam_score": r.total_score,
                "exam_max_score": r.max_score,

                # 🔥 현재는 동일하지만
                # 이후 session aggregation / 가중치 가능
                "final_score": r.total_score,

                "passed": bool(sp and not getattr(sp, "failed", False)),
                "clinic_required": bool(sp and getattr(sp, "clinic_required", False)),

                "submitted_at": r.submitted_at,

                "submission_id": submission_id,
                "submission_status": submission_status,
            })

        return Response(
            AdminExamResultRowSerializer(rows, many=True).data
        )

"""학생 단위 enrollment matrix view — Phase #11/#12 (2026-05-12).

학원장이 학생 1명 시점으로 강의 세션 list + 각 세션의 시험/과제 enrolled 여부 한 화면.

API:
  GET /api/v1/students/{id}/enrollment-matrix/?lecture_id=N
    → {enrollment_id, lecture: {id, title}, sessions: [{id, title, exams[], homeworks[]}]}

  POST /api/v1/students/{id}/enrollment-matrix/toggle/
    body: {target_type: "exam"|"homework"|"session", target_id, action: "add"|"remove"}

tenant 격리 — request.tenant + student.tenant + lecture.tenant 3중.
"""
from __future__ import annotations

from rest_framework import status, views
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.enrollment.selectors import build_student_enrollment_matrix
from apps.domains.enrollment.services.lifecycle import toggle_student_learning_access


class StudentEnrollmentMatrixView(views.APIView):
    """GET /students/{id}/enrollment-matrix/?lecture_id=N"""

    permission_classes = [TenantResolvedAndStaff]

    def get(self, request, student_id: int):
        tenant = request.tenant
        try:
            lecture_id = int(request.query_params.get("lecture_id") or 0)
        except ValueError:
            return Response({"detail": "lecture_id 잘못됨"}, status=status.HTTP_400_BAD_REQUEST)
        if lecture_id <= 0:
            return Response({"detail": "lecture_id 필수"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            build_student_enrollment_matrix(
                tenant=tenant,
                student_id=student_id,
                lecture_id=lecture_id,
            )
        )


class StudentEnrollmentMatrixToggleView(views.APIView):
    """POST /students/{id}/enrollment-matrix/toggle/
    body: {target_type: "exam"|"homework"|"session", target_id, action: "add"|"remove", lecture_id}
    """

    permission_classes = [TenantResolvedAndStaff]

    def post(self, request, student_id: int):
        tenant = request.tenant
        target_type = (request.data.get("target_type") or "").strip()
        action = (request.data.get("action") or "").strip()
        try:
            target_id = int(request.data.get("target_id") or 0)
            lecture_id = int(request.data.get("lecture_id") or 0)
        except (TypeError, ValueError):
            return Response({"detail": "target_id / lecture_id 잘못됨"}, status=status.HTTP_400_BAD_REQUEST)
        if target_type not in ("exam", "homework", "session"):
            return Response({"detail": "target_type 잘못됨"}, status=status.HTTP_400_BAD_REQUEST)
        if action not in ("add", "remove"):
            return Response({"detail": "action 잘못됨"}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            toggle_student_learning_access(
                tenant=tenant,
                student_id=student_id,
                lecture_id=lecture_id,
                target_type=target_type,
                target_id=target_id,
                action=action,
            )
        )

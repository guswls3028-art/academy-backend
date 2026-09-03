from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.exam_result_excel_import import (
    ExamResultWorkbookError,
)
from apps.domains.results.services.manual_exam_grading import (
    ManualExamGradingError,
    apply_manual_grading,
    build_manual_grading_sheet,
    plan_manual_grading,
)
from apps.support.results.admin_exam_dependencies import (
    get_regular_active_exam_for_tenant,
)


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class AdminExamManualGradingView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        exam = get_regular_active_exam_for_tenant(
            exam_id=int(exam_id),
            tenant=request.tenant,
        )
        try:
            payload = build_manual_grading_sheet(
                exam=exam,
                tenant=request.tenant,
            )
        except ExamResultWorkbookError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(payload)

    def post(self, request, exam_id: int):
        exam = get_regular_active_exam_for_tenant(
            exam_id=int(exam_id),
            tenant=request.tenant,
        )
        try:
            plan = plan_manual_grading(
                exam=exam,
                tenant=request.tenant,
                payload=request.data,
            )
        except ExamResultWorkbookError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        should_apply = _truthy(request.data.get("apply"))
        if not should_apply:
            return Response(plan.as_payload(), status=status.HTTP_200_OK)
        if not plan.can_apply:
            return Response(
                plan.as_payload(),
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = apply_manual_grading(
                plan=plan,
                user_id=int(request.user.id),
            )
        except ManualExamGradingError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(result, status=status.HTTP_200_OK)

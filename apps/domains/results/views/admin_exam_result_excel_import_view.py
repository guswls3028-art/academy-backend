from __future__ import annotations

from django.http import HttpResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.exam_result_excel_import import (
    ExamResultWorkbookError,
    MAX_UPLOAD_BYTES,
    apply_exam_result_import,
    build_exam_result_template,
    plan_exam_result_import,
)
from apps.support.results.admin_exam_dependencies import (
    get_regular_active_exam_for_tenant,
)


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class AdminExamResultExcelTemplateView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        exam = get_regular_active_exam_for_tenant(
            exam_id=int(exam_id),
            tenant=request.tenant,
        )
        try:
            payload = build_exam_result_template(exam=exam, tenant=request.tenant)
        except ExamResultWorkbookError as exc:
            raise ValidationError({"detail": str(exc)}) from exc

        response = HttpResponse(
            payload,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        response["Content-Disposition"] = (
            f'attachment; filename="exam_{int(exam.id)}_results.xlsx"'
        )
        return response


class AdminExamResultExcelImportView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def post(self, request, exam_id: int):
        exam = get_regular_active_exam_for_tenant(
            exam_id=int(exam_id),
            tenant=request.tenant,
        )
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"detail": "엑셀 파일을 선택해 주세요."})
        filename = str(getattr(uploaded, "name", "") or "")
        if not filename.lower().endswith(".xlsx"):
            raise ValidationError({"detail": ".xlsx 파일만 업로드할 수 있습니다."})
        if int(getattr(uploaded, "size", 0) or 0) > MAX_UPLOAD_BYTES:
            raise ValidationError({"detail": "엑셀 파일은 10MB 이하만 업로드할 수 있습니다."})

        try:
            plan = plan_exam_result_import(
                exam=exam,
                tenant=request.tenant,
                filename=filename,
                workbook_bytes=uploaded.read(),
            )
        except ExamResultWorkbookError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        should_apply = _truthy(request.data.get("apply"))
        if not should_apply:
            return Response(plan.as_payload(), status=status.HTTP_200_OK)
        if not plan.can_apply:
            return Response(plan.as_payload(), status=status.HTTP_400_BAD_REQUEST)

        try:
            result = apply_exam_result_import(plan=plan)
        except ExamResultWorkbookError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(result, status=status.HTTP_200_OK)

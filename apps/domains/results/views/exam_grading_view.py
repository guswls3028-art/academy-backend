# PATH: apps/domains/results/views/exam_grading_view.py
from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from django.db import transaction
from django.db.models import QuerySet

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff

from apps.domains.results.models.exam_result import ExamResult
from apps.domains.results.serializers.exam_result import (
    ExamResultSerializer,
    ManualGradeSerializer,
)
from apps.domains.results.services.exam_grading_service import ExamGradingService

logger = logging.getLogger(__name__)


def _resolve_student_filter_path(user: Any) -> Tuple[str, Dict[str, Any]]:
    """
    프로젝트별 student ↔ submission 연결 경로 차이를 방어적으로 탐색.
    """
    candidates = [
        ("submission__student__user", user),
        ("submission__student", user),
        ("submission__enrollment__student__user", user),
        ("submission__enrollment__student", user),
        ("submission__session_enrollment__enrollment__student__user", user),
        ("submission__session_enrollment__enrollment__student", user),
    ]

    for path, value in candidates:
        try:
            qs = ExamResult.objects.filter(**{path: value}).only("id")[:1]
            if qs:
                return path, {path: value}
        except Exception:
            continue

    return "id__isnull", {"id__isnull": True}


class AutoGradeSubmissionView(APIView):
    """
    ✅ 서비스 메서드명/계약과 일치:
    - ExamGradingService.auto_grade_objective(submission_id=...)
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @transaction.atomic
    def post(self, request, submission_id: int):
        service = ExamGradingService()
        out = service.auto_grade_objective(submission_id=int(submission_id))
        serializer = ExamResultSerializer(out.result)
        return Response(
            {"created": (not out.updated), "updated": bool(out.updated), "result": serializer.data},
            status=status.HTTP_201_CREATED if not out.updated else status.HTTP_200_OK,
        )


class ManualGradeSubmissionView(APIView):
    """
    ✅ 서비스 메서드명/계약과 일치:
    - ExamGradingService.apply_manual_overrides(submission_id=..., overrides=...)
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @transaction.atomic
    def put(self, request, submission_id: int):
        serializer = ManualGradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        overrides = serializer.validated_data.get("overrides")
        if overrides is None:
            # legacy callers might send overrides at top-level
            overrides = serializer.validated_data

        # overrides는 dict 형태 기대 (서비스에서 dict[str, Any])
        if not isinstance(overrides, dict):
            return Response(
                {"detail": "overrides must be an object/dict"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service = ExamGradingService()
        result = service.apply_manual_overrides(
            submission_id=int(submission_id),
            overrides=overrides,
        )

        return Response(ExamResultSerializer(result).data, status=status.HTTP_200_OK)


class FinalizeResultView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @transaction.atomic
    def post(self, request, submission_id: int):
        service = ExamGradingService()
        result = service.finalize(submission_id=int(submission_id))
        return Response(ExamResultSerializer(result).data, status=status.HTTP_200_OK)


class ExamResultAdminListView(ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = ExamResultSerializer

    def get_queryset(self) -> QuerySet[ExamResult]:
        # ✅ ExamResult 모델 스키마에 맞게 select_related 최소화
        qs = ExamResult.objects.select_related("submission", "exam").all().order_by("-id")

        exam_id = self.request.query_params.get("exam_id")
        if exam_id:
            qs = qs.filter(exam_id=exam_id)

        # ExamResult에 student_id FK가 없을 수 있어 안전 처리:
        # (필드가 실제 존재하는 프로젝트면 아래 필터를 활성화 가능)
        student_id = self.request.query_params.get("student_id")
        if student_id and hasattr(ExamResult, "student_id"):
            qs = qs.filter(student_id=student_id)

        submission_id = self.request.query_params.get("submission_id")
        if submission_id:
            qs = qs.filter(submission_id=submission_id)

        return qs


class MyExamResultListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        qs = ExamResult.objects.select_related("exam", "submission").order_by("-id")

        exam_id = request.query_params.get("exam_id")
        if exam_id:
            qs = qs.filter(exam_id=exam_id)

        _, filter_kwargs = _resolve_student_filter_path(user)
        qs = qs.filter(**filter_kwargs)

        serializer = ExamResultSerializer(qs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

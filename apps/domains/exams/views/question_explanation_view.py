# apps/domains/exams/views/question_explanation_view.py
"""
문항 해설 CRUD API.

GET  /exams/<exam_id>/explanations/      → 해당 시험의 모든 해설 조회
POST /exams/<exam_id>/explanations/bulk/  → 여러 문항 해설 일괄 저장/수정
PUT  /exams/questions/<question_id>/explanation/  → 단일 문항 해설 저장/수정
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.exams.models import Exam, ExamQuestion, QuestionExplanation
from apps.domains.exams.serializers.question_explanation import (
    QuestionExplanationSerializer,
    QuestionExplanationWriteSerializer,
    BulkExplanationSerializer,
)
from apps.domains.exams.services.template_resolver import resolve_template_exam

logger = logging.getLogger(__name__)


class ExamExplanationListView(APIView):
    """GET /exams/<exam_id>/explanations/ — 시험 문항 해설 전체 조회."""

    permission_classes = [IsAuthenticated]

    def get(self, request, exam_id: int):
        tenant = request.tenant
        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
                | Q(tenant=tenant)
            ).distinct(),
            id=int(exam_id),
        )
        template = resolve_template_exam(exam)

        explanations = (
            QuestionExplanation.objects
            .filter(question__sheet__exam=template)
            .select_related("question")
            .order_by("question__number")
        )

        return Response(QuestionExplanationSerializer(explanations, many=True).data)


class ExamExplanationBulkView(APIView):
    """POST /exams/<exam_id>/explanations/bulk/ — 해설 일괄 저장."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @transaction.atomic
    def post(self, request, exam_id: int):
        tenant = request.tenant
        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
                | Q(tenant=tenant)
            ).distinct(),
            id=int(exam_id),
        )
        template = resolve_template_exam(exam)

        serializer = BulkExplanationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        items = serializer.validated_data["explanations"]
        results = []

        for item in items:
            question_id = item.get("question_id")
            text = item.get("text", "")
            image_key = item.get("image_key", "")

            question = get_object_or_404(
                ExamQuestion.objects.filter(sheet__exam=template),
                id=int(question_id),
            )

            obj, _ = QuestionExplanation.objects.update_or_create(
                question=question,
                defaults={
                    "text": text,
                    "image_key": image_key,
                    "source": QuestionExplanation.Source.MANUAL,
                },
            )
            results.append(obj)

        return Response(
            QuestionExplanationSerializer(results, many=True).data,
            status=status.HTTP_200_OK,
        )


class QuestionExplanationDetailView(APIView):
    """
    GET/PUT /exams/questions/<question_id>/explanation/
    단일 문항 해설 조회/수정.
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    def _get_tenant_filtered_question(self, request, question_id: int) -> ExamQuestion:
        """ExamQuestion을 테넌트 범위 내에서만 조회. 크로스 테넌트 차단."""
        tenant = request.tenant
        return get_object_or_404(
            ExamQuestion.objects.filter(
                Q(sheet__exam__tenant=tenant)
                | Q(sheet__exam__sessions__lecture__tenant=tenant)
                | Q(sheet__exam__derived_exams__sessions__lecture__tenant=tenant)
            ).distinct(),
            id=int(question_id),
        )

    def get(self, request, question_id: int):
        question = self._get_tenant_filtered_question(request, question_id)
        try:
            explanation = question.explanation
        except QuestionExplanation.DoesNotExist:
            return Response(
                {"text": "", "image_key": "", "source": "manual", "match_confidence": None},
                status=status.HTTP_200_OK,
            )
        return Response(QuestionExplanationSerializer(explanation).data)

    @transaction.atomic
    def put(self, request, question_id: int):
        question = self._get_tenant_filtered_question(request, question_id)

        serializer = QuestionExplanationWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        obj, _ = QuestionExplanation.objects.update_or_create(
            question=question,
            defaults={
                "text": serializer.validated_data.get("text", ""),
                "image_key": serializer.validated_data.get("image_key", ""),
                "source": QuestionExplanation.Source.MANUAL,
            },
        )

        return Response(QuestionExplanationSerializer(obj).data)

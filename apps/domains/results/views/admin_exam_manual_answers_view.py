from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.views import APIView

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.manual_exam_answers import (
    apply_manual_answers,
    get_manual_answers,
    preview_manual_answers,
)
from apps.support.results.admin_exam_dependencies import get_regular_active_exam_for_tenant


class ManualAnswersDraftSerializer(serializers.Serializer):
    exam_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()
    expected_version = serializers.CharField(allow_null=True)
    answers = serializers.DictField(child=serializers.CharField(allow_blank=True))


class ManualAnswersInputSerializer(serializers.Serializer):
    answers = serializers.DictField(child=serializers.CharField(allow_blank=True))
    expected_version = serializers.CharField(allow_null=True)
    note = serializers.CharField()
    apply = serializers.BooleanField()
    preview_token = serializers.CharField(required=False)


class ManualAnswerQuestionSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    number = serializers.IntegerField()
    answer = serializers.CharField(allow_blank=True)
    is_correct = serializers.BooleanField()
    score = serializers.FloatField()
    max_score = serializers.FloatField()


class ManualAnswersPreviewSerializer(serializers.Serializer):
    exam_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()
    expected_version = serializers.CharField(allow_null=True)
    objective_score = serializers.FloatField()
    total_score = serializers.FloatField()
    max_score = serializers.FloatField()
    subjective_pending = serializers.BooleanField()
    questions = ManualAnswerQuestionSerializer(many=True)
    preview_token = serializers.CharField()
    applied = serializers.BooleanField()


class AdminExamManualAnswersView(APIView):
    """Preview or atomically confirm one student's offline objective answers."""

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @extend_schema(request=None, responses=ManualAnswersDraftSerializer)
    def get(self, request, exam_id: int, enrollment_id: int):
        exam = get_regular_active_exam_for_tenant(
            exam_id=int(exam_id), tenant=request.tenant,
        )
        return Response(get_manual_answers(
            exam=exam, tenant=request.tenant, enrollment_id=int(enrollment_id),
        ))

    @extend_schema(request=ManualAnswersInputSerializer, responses=ManualAnswersPreviewSerializer)
    def post(self, request, exam_id: int, enrollment_id: int):
        exam = get_regular_active_exam_for_tenant(
            exam_id=int(exam_id), tenant=request.tenant,
        )
        if request.data.get("apply") is True:
            return Response(apply_manual_answers(
                exam=exam, tenant=request.tenant,
                enrollment_id=int(enrollment_id), payload=request.data,
                user_id=int(request.user.id),
            ))
        preview, _ = preview_manual_answers(
            exam=exam, tenant=request.tenant,
            enrollment_id=int(enrollment_id), payload=request.data,
        )
        return Response({**preview, "applied": False})

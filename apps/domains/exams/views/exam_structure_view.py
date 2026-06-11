from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam import ExamSerializer
from apps.domains.exams.services.structure_copy_service import (
    ensure_regular_exam_owns_structure,
)
from apps.domains.exams.services.template_resolver import resolve_structure_exam
from apps.domains.results.permissions import IsTeacherOrAdmin


class ExamStructureEnsureView(APIView):
    """
    POST /api/v1/exams/<exam_id>/structure/ensure/

    Ensures a regular exam owns its editable Sheet/Question/AnswerKey snapshot.
    Existing no-copy legacy regular exams are copied from their source template
    and any existing question-id references are remapped by question number.
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

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

        result = ensure_regular_exam_owns_structure(exam)
        exam.refresh_from_db()
        owner = resolve_structure_exam(exam)
        data = ExamSerializer(exam).data
        data["structure_owner_id"] = int(owner.id)
        data["structure_copied"] = bool(result.copied)
        data["structure_remapped_counts"] = result.remapped_counts
        return Response(data)

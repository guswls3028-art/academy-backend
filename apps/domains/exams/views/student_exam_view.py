from __future__ import annotations

from django.utils import timezone
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam_list_student import StudentExamListSerializer


class StudentAvailableExamListView(APIView):
    """
    학생 기준 접근 가능한 시험 목록

    봉인 규칙:
    - regular exam만 노출
    - ExamEnrollment에 포함된 시험만 노출
    - is_active/open_at/close_at 기간 필터 적용
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        now = timezone.now()

        qs = (
            Exam.objects.filter(
                exam_type=Exam.ExamType.REGULAR,
                exam_enrollments__enrollment__student__user=user,
                is_active=True,
            )
            .filter(
                Q(open_at__isnull=True) | Q(open_at__lte=now),
                Q(close_at__isnull=True) | Q(close_at__gte=now),
            )
            .distinct()
            .order_by("open_at", "id")
        )

        return Response(StudentExamListSerializer(qs, many=True).data)

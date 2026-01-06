# apps/domains/results/views/exam_attempt_view.py
"""
ExamAttemptViewSet

❗ 치명적 보안 이슈 수정:
- 기존: IsAuthenticated 만 걸려서 학생도 전체 Attempt 열람 가능
- 변경: Teacher/Admin만 접근 가능

필요하면 추후:
- 학생 본인 attempt만 조회하는 별도 View를 /me/* 로 따로 만들 것
"""

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.models import ExamAttempt
from apps.domains.results.serializers.exam_attempt import ExamAttemptSerializer
from apps.domains.results.permissions import IsTeacherOrAdmin


class ExamAttemptViewSet(ModelViewSet):
    """
    시험 시도(Attempt) 관리 API (관리자/교사용)
    """

    queryset = ExamAttempt.objects.all().order_by("-created_at")
    serializer_class = ExamAttemptSerializer

    # ✅ 보안 수정
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

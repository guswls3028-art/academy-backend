# PATH: apps/domains/homework_results/views/homework_view.py
"""
Homework API (List/Retrieve/Create)

✅ 프론트 요구사항 (즉시 해결 포인트)
- GET /homeworks/?session_id={sessionId}
- GET /homeworks/{id}/

(선택)
- POST /homeworks/   ← "과제 추가" 모달이 실제 생성하려면 필요
"""

from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import OrderingFilter

from django.db.models import QuerySet

from apps.domains.results.permissions import IsTeacherOrAdmin

from apps.domains.homework_results.models import Homework
from apps.domains.homework_results.serializers.homework import HomeworkSerializer

class HomeworkViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]
    serializer_class = HomeworkSerializer

    filter_backends = [OrderingFilter]
    ordering_fields = ["id", "created_at", "updated_at", "status"]
    ordering = ["-updated_at", "-id"]

    def get_queryset(self) -> QuerySet[Homework]:
        qs = Homework.objects.select_related("session", "session__lecture")

        # ✅ 프론트가 session_id로 필터링
        session_id = self.request.query_params.get("session_id")
        if session_id:
            try:
                sid = int(session_id)
                qs = qs.filter(session_id=sid)
            except Exception:
                qs = qs.none()

        return qs

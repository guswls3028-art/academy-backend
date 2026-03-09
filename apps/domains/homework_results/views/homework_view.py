# PATH: apps/domains/homework_results/views/homework_view.py
"""
Homework API (List/Retrieve/Create)

✅ 프론트 요구사항
- GET /homeworks/?session_id={sessionId}
- GET /homeworks/{id}/
- POST /homeworks/ (session_id, title; optional template_homework_id로 템플릿 불러오기)
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import OrderingFilter
from rest_framework.exceptions import ValidationError

from django.db.models import QuerySet

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.lectures.models import Session

from apps.domains.homework_results.models import Homework
from apps.domains.homework_results.serializers.homework import HomeworkSerializer


class HomeworkViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]
    serializer_class = HomeworkSerializer

    filter_backends = [OrderingFilter]
    ordering_fields = ["id", "created_at", "updated_at", "status"]
    ordering = ["-updated_at", "-id"]

    def get_queryset(self) -> QuerySet[Homework]:
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return Homework.objects.none()
        # 세션 소속 regular만 (템플릿은 session=None이라 제외)
        qs = Homework.objects.filter(
            session__lecture__tenant=tenant
        ).select_related("session", "session__lecture", "template_homework")

        session_id = self.request.query_params.get("session_id")
        if session_id:
            try:
                sid = int(session_id)
                qs = qs.filter(session_id=sid)
            except Exception:
                qs = qs.none()

        return qs

    def perform_create(self, serializer):
        data = self.request.data
        template_id = data.get("template_homework_id") or data.get("template_homework")
        session_id = data.get("session_id")
        if not session_id:
            raise ValidationError({"session_id": "필수입니다."})

        if template_id:
            try:
                template = Homework.objects.get(
                    id=int(template_id),
                    homework_type=Homework.HomeworkType.TEMPLATE,
                )
            except (ValueError, TypeError, Homework.DoesNotExist):
                raise ValidationError({"template_homework_id": "유효한 과제 템플릿이 아닙니다."})
            tenant = getattr(self.request, "tenant", None)
            if tenant:
                from apps.domains.homework_results.views.homework_template_with_usage import template_visible_to_tenant
                if not template_visible_to_tenant(template, tenant):
                    raise ValidationError({"template_homework_id": "해당 템플릿에 접근할 수 없습니다."})
            title = (data.get("title") or "").strip() or template.title
            serializer.save(
                homework_type=Homework.HomeworkType.REGULAR,
                session_id=int(session_id),
                template_homework=template,
                title=title,
                status=Homework.Status.DRAFT,
            )
        else:
            serializer.save(
                homework_type=Homework.HomeworkType.REGULAR,
                session_id=int(session_id),
                title=(data.get("title") or "").strip() or "제목 없음",
            )

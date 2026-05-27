# PATH: apps/domains/homework_results/views/homework_view.py
"""
Homework API (List/Retrieve/Create)

✅ 프론트 요구사항
- GET /homeworks/?session_id={sessionId}
- GET /homeworks/{id}/
- POST /homeworks/ (session_id, title; optional template_homework_id로 템플릿 불러오기)
"""

from __future__ import annotations

from importlib import import_module

from rest_framework import status
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import OrderingFilter
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from django.db import transaction
from django.db.models import Max, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.core.permissions import TenantResolvedAndMember

from apps.domains.results.permissions import IsTeacherOrAdmin

from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework
from apps.domains.homework_results.serializers.homework import HomeworkSerializer
from apps.domains.lectures.models import Session


class HomeworkViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]
    serializer_class = HomeworkSerializer

    filter_backends = [OrderingFilter]
    ordering_fields = ["id", "created_at", "updated_at", "status", "display_order"]
    ordering = ["display_order", "created_at", "id"]

    def _next_display_order(self, *, tenant, session_id: int) -> int:
        max_order = (
            Homework.objects
            .filter(tenant=tenant, session_id=int(session_id))
            .exclude(meta__removed_from_session_at__isnull=False)
            .aggregate(value=Max("display_order"))
            .get("value")
        )
        return int(max_order or 0) + 1

    def _resolve_removed_homework_clinic_links(self, *, request, homework: Homework) -> int:
        if homework.session_id is None:
            return 0
        resolve_removed_source_clinic_links = import_module(
            "apps.domains.progress.dispatcher"
        ).resolve_removed_source_clinic_links

        return resolve_removed_source_clinic_links(
            tenant_id=int(request.tenant.id),
            session_id=int(homework.session_id),
            source_type="homework",
            source_id=int(homework.id),
            user_id=getattr(request.user, "id", None),
            reason="homework_removed_from_session",
        )

    def get_queryset(self) -> QuerySet[Homework]:
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return Homework.objects.none()
        qs = Homework.objects.filter(
            tenant=tenant
        ).select_related("session", "session__lecture", "template_homework")

        session_id = self.request.query_params.get("session_id")
        if session_id:
            try:
                sid = int(session_id)
                qs = qs.filter(session_id=sid)
            except Exception:
                qs = qs.none()

        homework_type = self.request.query_params.get("homework_type")
        if homework_type:
            qs = qs.filter(homework_type=str(homework_type).lower())

        if session_id:
            qs = qs.filter(homework_type=Homework.HomeworkType.REGULAR)

        include_removed = str(self.request.query_params.get("include_removed") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        if not include_removed:
            qs = qs.exclude(meta__removed_from_session_at__isnull=False)

        return qs

    def create(self, request, *args, **kwargs):
        """템플릿 불러오기 시 serializer 검증 없이 생성."""
        data = request.data
        template_id = data.get("template_homework_id") or data.get("template_homework")
        session_id = data.get("session_id") or data.get("session")
        if not session_id:
            return Response(
                {"session_id": "필수입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant is required."}, status=status.HTTP_403_FORBIDDEN)
        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            return Response(
                {"session_id": "정수여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        session = Session.objects.filter(id=session_id, lecture__tenant=tenant).first()
        if session is None:
            return Response(
                {"detail": "해당 차시를 찾을 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if template_id:
            try:
                template = Homework.objects.get(
                    id=int(template_id),
                    tenant=tenant,
                    homework_type=Homework.HomeworkType.TEMPLATE,
                )
            except (ValueError, TypeError, Homework.DoesNotExist):
                return Response(
                    {"template_homework_id": "유효한 과제 템플릿이 아닙니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            with transaction.atomic():
                session = Session.objects.select_for_update().get(id=session.id)
                title = (data.get("title") or "").strip() or template.title
                instance = Homework.objects.create(
                    tenant=tenant,
                    homework_type=Homework.HomeworkType.REGULAR,
                    session=session,
                    template_homework=template,
                    title=title,
                    status=Homework.Status.OPEN,
                    display_order=self._next_display_order(
                        tenant=tenant,
                        session_id=int(session.id),
                    ),
                )
            return Response(
                HomeworkSerializer(instance).data,
                status=status.HTTP_201_CREATED,
            )
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        data = self.request.data
        session_id = data.get("session_id") or data.get("session")
        if not session_id:
            raise ValidationError({"session_id": "필수입니다."})
        tenant = getattr(self.request, "tenant", None)
        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            raise ValidationError({"session_id": "정수여야 합니다."})
        with transaction.atomic():
            session = (
                Session.objects
                .select_for_update()
                .filter(id=session_id, lecture__tenant=tenant)
                .first()
            )
            if session is None:
                raise ValidationError({"detail": "해당 차시를 찾을 수 없습니다."})
            serializer.save(
                tenant=tenant,
                homework_type=Homework.HomeworkType.REGULAR,
                session=session,
                title=(data.get("title") or "").strip() or "제목 없음",
                display_order=self._next_display_order(
                    tenant=tenant,
                    session_id=int(session.id),
                ),
            )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        """Remove a homework from the live session without deleting score/submission history."""
        tenant = getattr(request, "tenant", None)
        homework = get_object_or_404(
            Homework.objects.select_for_update()
            .filter(tenant=tenant)
            .exclude(meta__removed_from_session_at__isnull=False),
            pk=kwargs["pk"],
        )

        removed_clinic_link_count = self._resolve_removed_homework_clinic_links(
            request=request,
            homework=homework,
        )
        assignment_count, _ = HomeworkAssignment.objects.filter(
            tenant=tenant,
            homework=homework,
        ).delete()

        meta = dict(homework.meta or {})
        meta["removed_from_session_at"] = timezone.now().isoformat()
        meta["removed_from_session_by_user_id"] = getattr(request.user, "id", None)
        meta["removed_assignment_count"] = int(assignment_count)
        meta["removed_clinic_link_count"] = int(removed_clinic_link_count)
        homework.meta = meta
        homework.status = Homework.Status.CLOSED
        homework.save(update_fields=["meta", "status", "updated_at"])

        return Response(status=status.HTTP_204_NO_CONTENT)

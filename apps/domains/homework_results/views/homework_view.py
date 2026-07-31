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
from math import isfinite

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

from apps.domains.homework_results.models import Homework
from apps.domains.homework_results.serializers.homework import HomeworkSerializer
from apps.domains.homework_results.services.max_score_sync import (
    sync_homework_primary_score_max,
    validate_homework_max_score,
)
from apps.support.homework_results.homework_view_dependencies import (
    delete_homework_assignments,
    get_homework_raw_score_cutline,
    get_session_for_homework,
    get_teacher_or_admin_permission,
)


class HomeworkViewSet(ModelViewSet):
    permission_classes = [
        IsAuthenticated,
        TenantResolvedAndMember,
        get_teacher_or_admin_permission(),
    ]
    serializer_class = HomeworkSerializer

    filter_backends = [OrderingFilter]
    ordering_fields = ["id", "created_at", "updated_at", "display_order"]
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
        session = get_session_for_homework(session_id=session_id, tenant=tenant)
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
                session = get_session_for_homework(
                    session_id=int(session.id),
                    tenant=tenant,
                    for_update=True,
                )
                if session is None:
                    return Response(
                        {"detail": "해당 차시를 찾을 수 없습니다."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                requested_max_score = data.get("max_score")
                if requested_max_score is None:
                    candidate_max_score = template.default_max_score
                else:
                    try:
                        candidate_max_score = float(requested_max_score)
                    except (TypeError, ValueError, OverflowError):
                        raise ValidationError({"max_score": "만점은 1 이상의 숫자여야 합니다."})
                    if not isfinite(candidate_max_score) or candidate_max_score < 1:
                        raise ValidationError({"max_score": "만점은 1 이상이어야 합니다."})
                raw_cutline = get_homework_raw_score_cutline(session=session)
                if raw_cutline is not None and raw_cutline > candidate_max_score:
                    raise ValidationError(
                        {
                            "max_score": (
                                f"점수 커트라인({raw_cutline:g}점)보다 만점"
                                f"({candidate_max_score:g}점)을 낮게 설정할 수 없습니다."
                            )
                        }
                    )
                title = (data.get("title") or "").strip() or template.title
                instance = Homework.objects.create(
                    tenant=tenant,
                    homework_type=Homework.HomeworkType.REGULAR,
                    session=session,
                    template_homework=template,
                    title=title,
                    meta={"default_max_score": candidate_max_score},
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
            session = get_session_for_homework(
                session_id=session_id,
                tenant=tenant,
                for_update=True,
            )
            if session is None:
                raise ValidationError({"detail": "해당 차시를 찾을 수 없습니다."})
            candidate_meta = serializer.validated_data.get("meta")
            candidate_max_score = Homework.max_score_from_meta(candidate_meta)
            raw_cutline = get_homework_raw_score_cutline(session=session)
            if raw_cutline is not None and raw_cutline > candidate_max_score:
                raise ValidationError(
                    {
                        "max_score": (
                            f"점수 커트라인({raw_cutline:g}점)보다 만점"
                            f"({candidate_max_score:g}점)을 낮게 설정할 수 없습니다."
                        )
                    }
                )
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
    def partial_update(self, request, *args, **kwargs):
        instance = get_object_or_404(
            self.filter_queryset(self.get_queryset().select_for_update()),
            pk=kwargs["pk"],
        )
        self.check_object_permissions(request, instance)
        old_max_score = instance.default_max_score
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        candidate_meta = serializer.validated_data.get("meta", instance.meta)
        candidate_max_score = Homework.max_score_from_meta(candidate_meta)

        explicit_max_score = "max_score" in request.data or (
            isinstance(request.data.get("meta"), dict)
            and "default_max_score" in request.data["meta"]
        )
        should_sync_scores = candidate_max_score != old_max_score or explicit_max_score

        if should_sync_scores:
            try:
                validate_homework_max_score(
                    homework=instance,
                    max_score=candidate_max_score,
                )
            except ValueError as exc:
                raise ValidationError({"max_score": str(exc)}) from exc

        self.perform_update(serializer)
        if should_sync_scores:
            sync_homework_primary_score_max(
                homework=serializer.instance,
                max_score=candidate_max_score,
            )

        return Response(self.get_serializer(serializer.instance).data)

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
        assignment_count = delete_homework_assignments(
            tenant=tenant,
            homework=homework,
        )

        meta = dict(homework.meta or {})
        meta["removed_from_session_at"] = timezone.now().isoformat()
        meta["removed_from_session_by_user_id"] = getattr(request.user, "id", None)
        meta["removed_assignment_count"] = int(assignment_count)
        meta["removed_clinic_link_count"] = int(removed_clinic_link_count)
        homework.meta = meta
        homework.save(update_fields=["meta", "updated_at"])

        return Response(status=status.HTTP_204_NO_CONTENT)

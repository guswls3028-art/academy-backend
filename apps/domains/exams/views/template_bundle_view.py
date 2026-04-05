# apps/domains/exams/views/template_bundle_view.py
"""
TemplateBundle CRUD + Apply API

⚠️ Tenant isolation: 모든 조회/생성/수정/삭제는 request.tenant 기준으로 격리됨.
"""

from __future__ import annotations

import logging

from django.db import transaction
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import Exam
from apps.domains.exams.models.template_bundle import TemplateBundle, TemplateBundleItem
from apps.domains.exams.serializers.template_bundle import (
    ApplyBundleSerializer,
    TemplateBundleCreateSerializer,
    TemplateBundleSerializer,
)
from apps.domains.homework_results.models.homework import Homework
from apps.domains.lectures.models import Session
from apps.domains.results.permissions import IsTeacherOrAdmin

logger = logging.getLogger(__name__)


class TemplateBundleViewSet(ModelViewSet):
    """템플릿 묶음 CRUD"""

    serializer_class = TemplateBundleSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]
    # PATCH 비활성화 — update(PUT)만 사용. 아이템 교체 로직이 PUT에만 있음.
    http_method_names = ["get", "post", "put", "delete", "head", "options"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return TemplateBundle.objects.none()
        return (
            TemplateBundle.objects
            .filter(tenant=tenant)
            .prefetch_related("items__exam_template", "items__homework_template")
        )

    def create(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required."}, status=status.HTTP_403_FORBIDDEN)

        ser = TemplateBundleCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        with transaction.atomic():
            bundle = TemplateBundle.objects.create(
                tenant=tenant,
                name=ser.validated_data["name"],
                description=ser.validated_data.get("description", ""),
            )
            self._save_items(bundle, ser.validated_data.get("items", []), tenant)

        result = TemplateBundleSerializer(
            TemplateBundle.objects
            .prefetch_related("items__exam_template", "items__homework_template")
            .get(pk=bundle.pk)
        )
        return Response(result.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        bundle = self.get_object()
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required."}, status=status.HTTP_403_FORBIDDEN)

        ser = TemplateBundleCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        with transaction.atomic():
            bundle.name = ser.validated_data["name"]
            bundle.description = ser.validated_data.get("description", "")
            bundle.save(update_fields=["name", "description", "updated_at"])

            bundle.items.all().delete()
            self._save_items(bundle, ser.validated_data.get("items", []), tenant)

        result = TemplateBundleSerializer(
            TemplateBundle.objects
            .prefetch_related("items__exam_template", "items__homework_template")
            .get(pk=bundle.pk)
        )
        return Response(result.data)

    def _save_items(self, bundle: TemplateBundle, items: list[dict], tenant) -> None:
        for idx, item_data in enumerate(items):
            item_type = item_data["item_type"]
            exam_template = None
            homework_template = None

            if item_type == "exam":
                exam_template = Exam.objects.filter(
                    pk=item_data["exam_template_id"],
                    tenant=tenant,
                    exam_type=Exam.ExamType.TEMPLATE,
                ).first()
                if not exam_template:
                    raise ValidationError(
                        f"시험 템플릿(ID={item_data['exam_template_id']})을 찾을 수 없거나 권한이 없습니다."
                    )

            elif item_type == "homework":
                homework_template = Homework.objects.filter(
                    pk=item_data["homework_template_id"],
                    tenant=tenant,
                    homework_type=Homework.HomeworkType.TEMPLATE,
                ).first()
                if not homework_template:
                    raise ValidationError(
                        f"과제 템플릿(ID={item_data['homework_template_id']})을 찾을 수 없거나 권한이 없습니다."
                    )

            TemplateBundleItem.objects.create(
                bundle=bundle,
                item_type=item_type,
                exam_template=exam_template,
                homework_template=homework_template,
                title_override=item_data.get("title_override", ""),
                display_order=item_data.get("display_order", idx),
                config=item_data.get("config"),
            )


class ApplyBundleView(GenericAPIView):
    """
    POST /exams/bundles/<bundle_id>/apply/
    묶음을 차시에 적용 → 시험/과제 일괄 생성
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]
    serializer_class = ApplyBundleSerializer

    def post(self, request, bundle_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required."}, status=status.HTTP_403_FORBIDDEN)

        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        session_id = ser.validated_data["session_id"]

        # Validate bundle
        try:
            bundle = (
                TemplateBundle.objects
                .filter(tenant=tenant)
                .prefetch_related("items__exam_template", "items__homework_template")
                .get(pk=bundle_id)
            )
        except TemplateBundle.DoesNotExist:
            return Response({"detail": "묶음을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        # Validate session
        try:
            session = Session.objects.select_related("lecture").get(
                pk=session_id,
                lecture__tenant=tenant,
            )
        except Session.DoesNotExist:
            return Response({"detail": "차시를 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        created_exams = []
        created_homeworks = []
        skipped_items = []

        with transaction.atomic():
            for item in bundle.items.all():
                if item.item_type == TemplateBundleItem.ItemType.EXAM:
                    if not item.exam_template:
                        skipped_items.append({
                            "item_id": item.id,
                            "item_type": "exam",
                            "reason": "삭제된 템플릿",
                        })
                        continue
                    title = item.title_override or item.exam_template.title
                    config = item.config or {}

                    exam = Exam.objects.create(
                        tenant=tenant,
                        title=title,
                        exam_type=Exam.ExamType.REGULAR,
                        template_exam=item.exam_template,
                        subject=item.exam_template.subject,
                        status=Exam.Status.OPEN,
                        max_score=config.get("max_score", item.exam_template.max_score),
                        pass_score=config.get("pass_score", item.exam_template.pass_score),
                    )
                    exam.sessions.add(session)
                    created_exams.append({"id": exam.id, "title": exam.title})

                elif item.item_type == TemplateBundleItem.ItemType.HOMEWORK:
                    if not item.homework_template:
                        skipped_items.append({
                            "item_id": item.id,
                            "item_type": "homework",
                            "reason": "삭제된 템플릿",
                        })
                        continue
                    title = item.title_override or item.homework_template.title
                    config = item.config or {}

                    hw = Homework.objects.create(
                        tenant=tenant,
                        title=title,
                        homework_type=Homework.HomeworkType.REGULAR,
                        template_homework=item.homework_template,
                        session=session,
                        status=Homework.Status.OPEN,
                        meta={"default_max_score": config.get("max_score", 100)},
                    )
                    created_homeworks.append({"id": hw.id, "title": hw.title})

        return Response({
            "created_exams": created_exams,
            "created_homeworks": created_homeworks,
            "skipped_items": skipped_items,
            "total": len(created_exams) + len(created_homeworks),
        }, status=status.HTTP_201_CREATED)

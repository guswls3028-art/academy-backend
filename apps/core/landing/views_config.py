"""LandingPage Config CRUD view (Public + Admin + Upload + Templates).

분리 출처: apps/core/views_landing.py:50-337 (P1 audit step 4b, 2026-05-14).

7 view:
- LandingPublicView: GET 공개 랜딩 (인증 X)
- LandingHasPublishedView: GET 게시 여부 빠른 확인
- LandingAdminView: GET/PUT draft (학원장)
- LandingPublishView: POST 게시
- LandingUnpublishView: POST 게시 중단
- LandingUploadImageView: POST 이미지 (hero/logo/hero_slot 0-5, transaction.atomic)
- LandingTemplatesView: GET 4 template 목록
"""
from __future__ import annotations

import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import LandingPage
from apps.core.permissions import TenantResolved, TenantResolvedAndStaff

from ._helpers import check_landing_admin_role
from .config_helpers import (
    TEMPLATE_META,
    backfill_missing_sections,
    default_draft_config,
    resolve_image_urls,
    validate_config,
)

logger = logging.getLogger(__name__)


class LandingPublicView(APIView):
    """GET /api/v1/core/landing/public/ — 게시된 랜딩 조회 (인증 X)."""
    permission_classes = [TenantResolved]

    def get(self, request):
        tenant = request.tenant
        try:
            landing = LandingPage.objects.get(tenant=tenant)
        except LandingPage.DoesNotExist:
            return Response({"has_landing": False}, status=200)

        if not landing.is_published:
            return Response({"has_landing": False}, status=200)

        pub_config = landing.published_config or {}
        if not pub_config.get("brand_name"):
            return Response({"has_landing": False}, status=200)

        config = resolve_image_urls(pub_config)
        return Response({
            "has_landing": True,
            "template_key": config.get("template_key", landing.template_key),
            "config": config,
        })


class LandingHasPublishedView(APIView):
    """GET /api/v1/core/landing/has-published/ — 게시 여부 빠른 확인 (RootRedirect용)."""
    permission_classes = [TenantResolved]

    def get(self, request):
        tenant = request.tenant
        try:
            landing = LandingPage.objects.get(tenant=tenant, is_published=True)
        except LandingPage.DoesNotExist:
            return Response({"has_published": False})
        pub_config = landing.published_config or {}
        has = bool(pub_config.get("brand_name"))
        return Response({"has_published": has})


class LandingAdminView(APIView):
    """GET/PUT /api/v1/core/landing/admin/ — 전체 랜딩 설정 (학원장)."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("랜딩페이지 편집은 원장/관리자만 가능합니다.")

    def get(self, request):
        tenant = request.tenant
        landing, _created = LandingPage.objects.get_or_create(
            tenant=tenant,
            defaults={"draft_config": default_draft_config(tenant)},
        )
        # 신규 섹션 타입 자동 backfill — 기존 학원도 nav에 즉시 노출.
        backfilled = backfill_missing_sections(landing.draft_config)
        if backfilled is not landing.draft_config:
            landing.draft_config = backfilled
            landing.save(update_fields=["draft_config", "updated_at"])
        draft = resolve_image_urls(landing.draft_config)
        published = resolve_image_urls(landing.published_config) if landing.published_config else None

        return Response({
            "template_key": landing.template_key,
            "is_published": landing.is_published,
            "draft_config": draft,
            "published_config": published,
            "updated_at": landing.updated_at.isoformat() if landing.updated_at else None,
        })

    def put(self, request):
        tenant = request.tenant
        landing, _created = LandingPage.objects.get_or_create(
            tenant=tenant,
            defaults={"draft_config": default_draft_config(tenant)},
        )

        data = request.data
        template_key = data.get("template_key")
        draft_config = data.get("draft_config")

        if template_key:
            valid_keys = [c[0] for c in LandingPage.TemplateKey.choices]
            if template_key not in valid_keys:
                return Response({"detail": f"유효하지 않은 템플릿: {template_key}"}, status=400)
            landing.template_key = template_key

        if draft_config is not None:
            errors = validate_config(draft_config)
            if errors:
                return Response({"detail": errors}, status=400)
            landing.draft_config = draft_config

        landing.save(update_fields=["template_key", "draft_config", "updated_at"])

        draft = resolve_image_urls(landing.draft_config)
        return Response({
            "template_key": landing.template_key,
            "is_published": landing.is_published,
            "draft_config": draft,
            "updated_at": landing.updated_at.isoformat(),
        })


class LandingPublishView(APIView):
    """POST /api/v1/core/landing/publish/ — 현재 draft를 게시."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("랜딩페이지 편집은 원장/관리자만 가능합니다.")

    def post(self, request):
        tenant = request.tenant
        try:
            landing = LandingPage.objects.get(tenant=tenant)
        except LandingPage.DoesNotExist:
            return Response({"detail": "랜딩페이지가 아직 생성되지 않았습니다."}, status=404)

        if not landing.draft_config:
            return Response({"detail": "저장된 초안이 없습니다."}, status=400)

        # 게시 전 재검증
        errors = validate_config(landing.draft_config)
        if errors:
            return Response({"detail": errors}, status=400)

        landing.publish()
        logger.info("Landing page published: tenant=%s", tenant.id)
        return Response({
            "is_published": True,
            "template_key": landing.template_key,
            "updated_at": landing.updated_at.isoformat(),
        })


class LandingUnpublishView(APIView):
    """POST /api/v1/core/landing/unpublish/ — 게시 중단."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("랜딩페이지 편집은 원장/관리자만 가능합니다.")

    def post(self, request):
        tenant = request.tenant
        try:
            landing = LandingPage.objects.get(tenant=tenant)
        except LandingPage.DoesNotExist:
            return Response({"detail": "랜딩페이지가 아직 생성되지 않았습니다."}, status=404)

        landing.unpublish()
        logger.info("Landing page unpublished: tenant=%s", tenant.id)
        return Response({
            "is_published": False,
            "updated_at": landing.updated_at.isoformat(),
        })


class LandingUploadImageView(APIView):
    """POST /api/v1/core/landing/upload-image/ — hero/logo/hero_slot 이미지 업로드 → R2."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("랜딩페이지 편집은 원장/관리자만 가능합니다.")

    def post(self, request):
        tenant = request.tenant
        file = request.FILES.get("file")
        field = request.data.get("field", "hero")

        if not file:
            return Response({"detail": "file은 필수입니다."}, status=400)
        if not (getattr(file, "content_type", "") or "").startswith("image/"):
            return Response({"detail": "이미지 파일만 업로드 가능합니다."}, status=400)
        if file.size > 5 * 1024 * 1024:
            return Response({"detail": "이미지 크기는 5MB 이하여야 합니다."}, status=400)

        ext = (file.name or "").rsplit(".", 1)[-1].lower()
        if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
            return Response({"detail": "허용되지 않는 파일 형식입니다. (PNG, JPG, GIF, WebP)"}, status=400)
        # 매직바이트 검증 — Content-Type 위장 차단.
        from apps.api.common.image_validator import is_real_image
        if not is_real_image(file):
            return Response({"detail": "이미지 파일이 손상되었거나 이미지 형식이 아닙니다."}, status=400)

        # P0 audit (2026-05-13): slot 파싱 단일 + R2 업로드 + draft 갱신을 transaction
        # 안에서 select_for_update 로 묶음. 동시 hero_slot upload race 방어.
        slot: int | None = None
        if field == "hero_slot":
            try:
                slot = int(request.data.get("slot", -1))
            except (TypeError, ValueError):
                return Response({"detail": "slot은 정수여야 합니다."}, status=400)
            if not (0 <= slot <= 5):
                return Response({"detail": "slot은 0~5 사이여야 합니다."}, status=400)
            key = f"landing/{tenant.id}/hero_{slot}.{ext}"
        elif field in ("hero", "logo"):
            key = f"landing/{tenant.id}/{field}.{ext}"
        else:
            return Response({"detail": "field는 'hero' | 'logo' | 'hero_slot' 이어야 합니다."}, status=400)

        from apps.infrastructure.storage import r2 as r2_storage
        r2_storage.upload_fileobj_to_r2_admin(
            fileobj=file,
            key=key,
            content_type=file.content_type or "image/png",
        )

        url = r2_storage.generate_presigned_get_url_admin(key=key, expires_in=86400 * 7)

        from django.db import transaction
        with transaction.atomic():
            landing, _ = LandingPage.objects.select_for_update().get_or_create(
                tenant=tenant,
                defaults={"draft_config": default_draft_config(tenant)},
            )
            draft = dict(landing.draft_config or {})
            if field == "hero":
                draft["hero_image_url"] = key
            elif field == "logo":
                draft["logo_url"] = key
            elif field == "hero_slot" and slot is not None:
                arr = list(draft.get("hero_images") or [])
                while len(arr) <= slot:
                    arr.append("")
                arr[slot] = key
                draft["hero_images"] = arr
            landing.draft_config = draft
            landing.save(update_fields=["draft_config", "updated_at"])

        return Response({
            "key": key,
            "url": url,
            "field": field,
        })


class LandingTemplatesView(APIView):
    """GET /api/v1/core/landing/templates/ — 4 template 목록 (갤러리)."""
    permission_classes = [TenantResolved]

    def get(self, request):
        return Response({"templates": TEMPLATE_META})

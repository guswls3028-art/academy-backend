# PATH: apps/core/views_landing.py
#
# 선생님별 랜딩페이지 API.
# - Public: 게시된 랜딩 조회 (인증 불필요)
# - Admin: Draft CRUD, Publish/Unpublish, 이미지 업로드

import copy
import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import LandingPage
from apps.core.permissions import TenantResolved, TenantResolvedAndStaff

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────
# 허용 색상 팔레트 (가드레일)
# ─────────────────────────────────────────────────
ALLOWED_COLORS = {
    "#2563EB",  # Blue
    "#4F46E5",  # Indigo
    "#7C3AED",  # Purple
    "#EC4899",  # Pink
    "#EF4444",  # Red
    "#F97316",  # Orange
    "#F59E0B",  # Amber
    "#10B981",  # Emerald
    "#14B8A6",  # Teal
    "#06B6D4",  # Cyan
    "#1E3A5F",  # Navy
    "#475569",  # Slate
    "#18181B",  # Black
    "#0EA5E9",  # Sky
    "#8B5CF6",  # Violet
    "#D946EF",  # Fuchsia
}

SECTION_TYPES = {"hero", "features", "testimonials", "about", "programs", "faq", "contact", "notice"}
MAX_SECTION_ITEMS = 6
MAX_SECTIONS = 8

# ─────────────────────────────────────────────────
# 템플릿 메타데이터 (프론트 갤러리용)
# ─────────────────────────────────────────────────
TEMPLATE_META = [
    {
        "key": "minimal_tutor",
        "name": "Minimal Tutor",
        "description": "밝고 깔끔한 미니멀 디자인. 넓은 여백과 신뢰감 있는 톤.",
        "mood": "밝음 · 깔끔 · 신뢰",
        "preview_color": "#2563EB",
    },
    {
        "key": "premium_dark",
        "name": "Premium Dark",
        "description": "네이비/다크 기반의 세련된 프리미엄 톤.",
        "mood": "프리미엄 · 세련 · 고급",
        "preview_color": "#1E3A5F",
    },
    {
        "key": "academic_trust",
        "name": "Academic Trust",
        "description": "성적 관리와 체계적 교육을 강조하는 신뢰형 디자인.",
        "mood": "체계 · 관리 · 성과",
        "preview_color": "#4F46E5",
    },
    {
        "key": "program_promo",
        "name": "Program Promo",
        "description": "프로그램 설명과 CTA 중심의 홍보형 디자인.",
        "mood": "홍보 · 활기 · 행동유도",
        "preview_color": "#F97316",
    },
]

# ─────────────────────────────────────────────────
# 기본 draft config (새 랜딩 생성 시)
# ─────────────────────────────────────────────────
def _default_draft_config(tenant):
    """tenant 정보 기반 기본 draft config 생성."""
    return {
        "brand_name": tenant.name or "",
        "tagline": "",
        "subtitle": "",
        "primary_color": "#2563EB",
        "hero_image_url": "",
        "logo_url": "",
        "cta_text": "로그인",
        "cta_link": "/login",
        "contact": {
            "phone": tenant.phone or "",
            "email": "",
            "address": tenant.address or "",
        },
        "sections": [
            {"type": "hero", "enabled": True, "order": 0},
            {"type": "features", "enabled": True, "order": 1, "items": [
                {"icon": "book", "title": "체계적인 커리큘럼", "description": "학생 수준에 맞춘 단계별 학습 설계"},
                {"icon": "chart", "title": "성적 관리", "description": "실시간 성적 추적과 분석 리포트"},
                {"icon": "users", "title": "소통과 상담", "description": "학부모님과의 원활한 소통 채널"},
            ]},
            {"type": "about", "enabled": True, "order": 2, "title": "소개", "description": ""},
            {"type": "testimonials", "enabled": False, "order": 3, "items": []},
            {"type": "programs", "enabled": False, "order": 4, "items": []},
            {"type": "faq", "enabled": False, "order": 5, "items": []},
            {"type": "contact", "enabled": True, "order": 6},
        ],
    }


def _resolve_image_urls(config: dict) -> dict:
    """R2 key → presigned URL 변환."""
    from apps.infrastructure.storage import r2 as r2_storage

    result = copy.deepcopy(config)
    for field in ("hero_image_url", "logo_url"):
        val = result.get(field, "")
        if val and val.startswith("landing/"):
            result[field] = r2_storage.generate_presigned_get_url_admin(
                key=val, expires_in=86400 * 7
            )
    return result


def _validate_config(data: dict) -> list[str]:
    """draft config 유효성 검증. 위반 사항 목록 반환."""
    errors = []
    color = data.get("primary_color", "")
    if color and color not in ALLOWED_COLORS:
        errors.append(f"허용되지 않은 색상입니다: {color}")

    sections = data.get("sections", [])
    if len(sections) > MAX_SECTIONS:
        errors.append(f"섹션은 최대 {MAX_SECTIONS}개까지 가능합니다.")

    for sec in sections:
        stype = sec.get("type", "")
        if stype not in SECTION_TYPES:
            errors.append(f"알 수 없는 섹션 타입: {stype}")
        items = sec.get("items", [])
        if len(items) > MAX_SECTION_ITEMS:
            errors.append(f"'{stype}' 섹션의 항목은 최대 {MAX_SECTION_ITEMS}개입니다.")

    brand_name = data.get("brand_name", "")
    if brand_name and len(brand_name) > 50:
        errors.append("브랜드명은 50자 이내여야 합니다.")
    tagline = data.get("tagline", "")
    if tagline and len(tagline) > 100:
        errors.append("한 줄 소개는 100자 이내여야 합니다.")

    # cta_link XSS 방지: / 또는 https:// 로 시작해야 함
    cta_link = data.get("cta_link", "")
    if cta_link and not (cta_link.startswith("/") or cta_link.startswith("https://")):
        errors.append("CTA 링크는 /로 시작하거나 https:// URL이어야 합니다.")

    return errors


# ─────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────

class LandingPublicView(APIView):
    """
    GET /api/v1/core/landing/public/
    게시된 랜딩페이지 조회 (인증 불필요, tenant resolved만 필요).
    """
    permission_classes = [TenantResolved]

    def get(self, request):
        tenant = request.tenant
        try:
            landing = LandingPage.objects.get(tenant=tenant)
        except LandingPage.DoesNotExist:
            return Response({"has_landing": False}, status=200)

        if not landing.is_published:
            return Response({"has_landing": False}, status=200)

        # 빈 published_config 방어
        pub_config = landing.published_config or {}
        if not pub_config.get("brand_name"):
            return Response({"has_landing": False}, status=200)

        config = _resolve_image_urls(pub_config)
        return Response({
            "has_landing": True,
            "template_key": config.get("template_key", landing.template_key),
            "config": config,
        })


class LandingHasPublishedView(APIView):
    """
    GET /api/v1/core/landing/has-published/
    랜딩페이지 게시 여부만 빠르게 확인 (RootRedirect용).
    """
    permission_classes = [TenantResolved]

    def get(self, request):
        tenant = request.tenant
        has = LandingPage.objects.filter(tenant=tenant, is_published=True).exists()
        return Response({"has_published": has})


# ─────────────────────────────────────────────────
# Admin API
# ─────────────────────────────────────────────────

class LandingAdminView(APIView):
    """
    GET  /api/v1/core/landing/admin/ — 전체 랜딩 설정 (draft + published + 상태)
    PUT  /api/v1/core/landing/admin/ — draft_config 업데이트
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        landing, created = LandingPage.objects.get_or_create(
            tenant=tenant,
            defaults={"draft_config": _default_draft_config(tenant)},
        )
        draft = _resolve_image_urls(landing.draft_config)
        published = _resolve_image_urls(landing.published_config) if landing.published_config else None

        return Response({
            "template_key": landing.template_key,
            "is_published": landing.is_published,
            "draft_config": draft,
            "published_config": published,
            "updated_at": landing.updated_at.isoformat() if landing.updated_at else None,
        })

    def put(self, request):
        tenant = request.tenant
        landing, created = LandingPage.objects.get_or_create(
            tenant=tenant,
            defaults={"draft_config": _default_draft_config(tenant)},
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
            errors = _validate_config(draft_config)
            if errors:
                return Response({"detail": errors}, status=400)
            landing.draft_config = draft_config

        landing.save(update_fields=["template_key", "draft_config", "updated_at"])

        draft = _resolve_image_urls(landing.draft_config)
        return Response({
            "template_key": landing.template_key,
            "is_published": landing.is_published,
            "draft_config": draft,
            "updated_at": landing.updated_at.isoformat(),
        })


class LandingPublishView(APIView):
    """
    POST /api/v1/core/landing/publish/
    현재 draft를 게시.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        tenant = request.tenant
        try:
            landing = LandingPage.objects.get(tenant=tenant)
        except LandingPage.DoesNotExist:
            return Response({"detail": "랜딩페이지가 아직 생성되지 않았습니다."}, status=404)

        if not landing.draft_config:
            return Response({"detail": "저장된 초안이 없습니다."}, status=400)

        landing.publish()
        logger.info("Landing page published: tenant=%s", tenant.id)
        return Response({
            "is_published": True,
            "template_key": landing.template_key,
            "updated_at": landing.updated_at.isoformat(),
        })


class LandingUnpublishView(APIView):
    """
    POST /api/v1/core/landing/unpublish/
    게시 중단.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

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
    """
    POST /api/v1/core/landing/upload-image/
    랜딩페이지용 이미지 업로드 → R2 admin 버킷.
    field: "hero" | "logo"
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

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

        if field not in ("hero", "logo"):
            return Response({"detail": "field는 'hero' 또는 'logo'여야 합니다."}, status=400)

        key = f"landing/{tenant.id}/{field}.{ext}"

        from apps.infrastructure.storage import r2 as r2_storage
        r2_storage.upload_fileobj_to_r2_admin(
            fileobj=file,
            key=key,
            content_type=file.content_type or "image/png",
        )

        url = r2_storage.generate_presigned_get_url_admin(key=key, expires_in=86400 * 7)

        # draft_config에 URL 자동 반영
        landing, _ = LandingPage.objects.get_or_create(
            tenant=tenant,
            defaults={"draft_config": _default_draft_config(tenant)},
        )
        draft = dict(landing.draft_config or {})
        if field == "hero":
            draft["hero_image_url"] = key
        else:
            draft["logo_url"] = key
        landing.draft_config = draft
        landing.save(update_fields=["draft_config", "updated_at"])

        return Response({
            "key": key,
            "url": url,
            "field": field,
        })


class LandingTemplatesView(APIView):
    """
    GET /api/v1/core/landing/templates/
    사용 가능한 템플릿 목록 (갤러리용).
    """
    permission_classes = [TenantResolved]

    def get(self, request):
        return Response({"templates": TEMPLATE_META})

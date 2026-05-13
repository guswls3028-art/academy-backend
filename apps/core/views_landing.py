# PATH: apps/core/views_landing.py
#
# 선생님별 랜딩페이지 API.
# - Public: 게시된 랜딩 조회 (인증 불필요)
# - Admin: Draft CRUD, Publish/Unpublish, 이미지 업로드

import copy
import logging

from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import LandingPage
from apps.core.permissions import TenantResolved, TenantResolvedAndStaff


def _tenant_required(view_func):
    """Plain Django view용 tenant 가드."""
    from django.http import JsonResponse
    def wrapped(request, *args, **kwargs):
        if not getattr(request, "tenant", None):
            return JsonResponse({"detail": "Tenant required"}, status=400)
        return view_func(request, *args, **kwargs)
    return wrapped

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

# 섹션 타입 SSOT — 추가는 SECTION_TYPES_ORDERED 한 곳만 수정.
# (frontend types/index.ts SECTION_META와 list 동기화 필요 — 두 언어 사이 자동 import 불가)
SECTION_TYPES_ORDERED = [
    "hero",
    "hero_carousel",        # 2026-05-12 #63 — 매치업 외 일반 게시물·커스텀 카드 mix
    "features",
    "instructor_profile",   # v1.2.x 1인 강사 사이트 보강
    "about",
    "management_system",    # v1.2.x
    "process_timeline",     # v1.2.x
    "testimonials",
    "hit_reports",          # v1.2.x
    "programs",
    "faq",
    "contact",
    "notice",
]
SECTION_TYPES = set(SECTION_TYPES_ORDERED)
MAX_SECTION_ITEMS = 12  # process_timeline 7+주차 / management_system 6+카드 수용
MAX_SECTIONS = len(SECTION_TYPES_ORDERED) + 2  # 자동 — 신규 추가 시 자동 갱신

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
        "hero_images": [],
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
            {"type": "hit_reports", "enabled": False, "order": 4, "title": "최근 적중 사례", "description": "우리 학원의 시험지 적중 결과를 소개합니다.", "items": []},
            {"type": "programs", "enabled": False, "order": 5, "items": []},
            {"type": "faq", "enabled": False, "order": 6, "items": []},
            {"type": "contact", "enabled": True, "order": 7},
        ],
    }


# SECTION_TYPES_ORDERED를 SSOT으로 사용 — notice는 default backfill 대상에서 제외 (학원 자율 추가).
_REQUIRED_SECTION_TYPES = [t for t in SECTION_TYPES_ORDERED if t != "notice"]


def _backfill_missing_sections(draft: dict) -> dict:
    """기존 학원 draft에 신규 섹션 타입을 enabled=False로 자동 추가.

    학원장이 어드민 콘솔 진입 시 새로 추가된 섹션(예: hit_reports)이 sidebar nav에 즉시 노출되도록 보장.
    값 변경 X — 누락 섹션만 추가, 기존 섹션은 그대로.
    """
    sections = list(draft.get("sections") or [])
    existing_types = {s.get("type") for s in sections if isinstance(s, dict)}
    max_order = max((s.get("order", 0) for s in sections), default=-1)
    for sec_type in _REQUIRED_SECTION_TYPES:
        if sec_type in existing_types:
            continue
        max_order += 1
        if sec_type == "hit_reports":
            sections.append({"type": "hit_reports", "enabled": False, "order": max_order, "title": "최근 적중 사례", "description": "우리 학원의 시험지 적중 결과를 소개합니다.", "items": []})
        elif sec_type == "instructor_profile":
            sections.append({"type": "instructor_profile", "enabled": False, "order": max_order, "title": "강사 프로필", "description": "", "items": []})
        elif sec_type == "management_system":
            sections.append({"type": "management_system", "enabled": False, "order": max_order, "title": "학생 관리 시스템", "description": "수업 외 시간에도 학생을 끊김 없이 챙깁니다.", "items": []})
        elif sec_type == "process_timeline":
            sections.append({"type": "process_timeline", "enabled": False, "order": max_order, "title": "수업 진행 흐름", "description": "한 사이클이 어떻게 진행되는지 한눈에 보세요.", "items": []})
        elif sec_type == "about":
            sections.append({"type": "about", "enabled": False, "order": max_order, "title": "소개", "description": ""})
        elif sec_type == "contact":
            sections.append({"type": "contact", "enabled": False, "order": max_order})
        elif sec_type == "hero":
            sections.append({"type": "hero", "enabled": False, "order": max_order})
        else:
            sections.append({"type": sec_type, "enabled": False, "order": max_order, "items": []})
    if len(sections) != len(draft.get("sections") or []):
        draft = {**draft, "sections": sections}
    return draft


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
    hero_images = result.get("hero_images") or []
    if isinstance(hero_images, list):
        resolved = []
        for v in hero_images:
            if isinstance(v, str) and v.startswith("landing/"):
                resolved.append(
                    r2_storage.generate_presigned_get_url_admin(
                        key=v, expires_in=86400 * 7
                    )
                )
            elif isinstance(v, str):
                resolved.append(v)
        result["hero_images"] = resolved
    return result


def _validate_config(data: dict) -> list[str]:
    """draft config 유효성 검증. 위반 사항 목록 반환."""
    errors = []
    color = data.get("primary_color", "")
    if color and color not in ALLOWED_COLORS:
        errors.append(f"허용되지 않은 색상입니다: {color}")

    sections = data.get("sections", [])
    if not isinstance(sections, list):
        errors.append("sections는 배열이어야 합니다.")
        return errors
    if len(sections) > MAX_SECTIONS:
        errors.append(f"섹션은 최대 {MAX_SECTIONS}개까지 가능합니다.")

    for sec in sections:
        if not isinstance(sec, dict):
            errors.append("각 섹션은 객체여야 합니다.")
            continue
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

    hero_images = data.get("hero_images")
    if hero_images is not None:
        if not isinstance(hero_images, list):
            errors.append("hero_images는 배열이어야 합니다.")
        elif len(hero_images) > 6:
            errors.append("히어로 이미지는 최대 6장입니다.")
        else:
            for v in hero_images:
                if not isinstance(v, str) or len(v) > 600:
                    errors.append("히어로 이미지 값이 올바르지 않습니다.")
                    break

    # cta_link XSS 방지: 허용된 프로토콜만 (내부 path / https / tel / mailto)
    cta_link = data.get("cta_link", "")
    if cta_link and not (cta_link.startswith("/") or cta_link.startswith("https://") or cta_link.startswith("tel:") or cta_link.startswith("mailto:")):
        errors.append("CTA 링크는 /로 시작하거나 https://·tel:·mailto: URL이어야 합니다.")

    # 개인 휴대폰 번호 가드 — tel: 010-xxxx-xxxx 또는 010xxxxxxxx 패턴은 학원 대표번호 X.
    # 학원장 사고 방지: 외부 노출되면 안 되는 개인 폰을 cta/contact에 박지 않게.
    import re
    _personal_mobile = re.compile(r"01[016789][- ]?\d{3,4}[- ]?\d{4}")
    if cta_link.startswith("tel:") and _personal_mobile.match(cta_link[4:].replace("-", "").replace(" ", "")):
        # 010/011/016/017/018/019 시작 = 개인 휴대폰. 학원 대표번호(02-/0n-)만 허용.
        digits = cta_link[4:].replace("-", "").replace(" ", "").replace("+82", "0")
        if digits.startswith(("010", "011", "016", "017", "018", "019")):
            errors.append("CTA 링크에 개인 휴대폰 번호는 사용할 수 없습니다. 학원 대표번호(02-, 031- 등)를 사용해주세요.")
    contact = data.get("contact") or {}
    if isinstance(contact, dict):
        for k in ("phone", "email"):
            v = str(contact.get(k) or "")
            digits = re.sub(r"[^\d]", "", v)
            if digits and len(digits) >= 10 and digits[:3] in ("010", "011", "016", "017", "018", "019"):
                # email 필드는 "010-xxxx-xxxx (문자전용)" 같은 학원 별도 안내용 번호로 사용 가능 — 경고만, 차단 X.
                # phone(메인 대표번호)에 개인 휴대폰 박는 건 차단.
                if k == "phone":
                    errors.append(f"문의 전화번호({k})에 개인 휴대폰 번호는 사용할 수 없습니다. 학원 대표번호를 사용해주세요.")

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
        try:
            landing = LandingPage.objects.get(tenant=tenant, is_published=True)
        except LandingPage.DoesNotExist:
            return Response({"has_published": False})
        # LandingPublicView와 동일한 판정: brand_name 있어야 유효
        pub_config = landing.published_config or {}
        has = bool(pub_config.get("brand_name"))
        return Response({"has_published": has})


# ─────────────────────────────────────────────────
# Admin API (owner/admin 전용)
# ─────────────────────────────────────────────────

LANDING_ADMIN_ROLES = {"owner", "admin"}


def _check_landing_admin_role(request):
    """owner/admin만 랜딩 편집 허용. teacher/staff 차단."""
    from apps.core.models import TenantMembership
    tenant = request.tenant
    user = request.user
    try:
        membership = TenantMembership.objects.get(user=user, tenant=tenant, is_active=True)
    except TenantMembership.DoesNotExist:
        return False
    return membership.role in LANDING_ADMIN_ROLES


class LandingAdminView(APIView):
    """
    GET  /api/v1/core/landing/admin/ — 전체 랜딩 설정 (draft + published + 상태)
    PUT  /api/v1/core/landing/admin/ — draft_config 업데이트
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("랜딩페이지 편집은 원장/관리자만 가능합니다.")

    def get(self, request):
        tenant = request.tenant
        landing, created = LandingPage.objects.get_or_create(
            tenant=tenant,
            defaults={"draft_config": _default_draft_config(tenant)},
        )
        # 신규 섹션 타입(hit_reports 등) 자동 backfill — 기존 학원도 nav에 즉시 노출.
        backfilled = _backfill_missing_sections(landing.draft_config)
        if backfilled is not landing.draft_config:
            landing.draft_config = backfilled
            landing.save(update_fields=["draft_config", "updated_at"])
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

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _check_landing_admin_role(request):
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
        errors = _validate_config(landing.draft_config)
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
    """
    POST /api/v1/core/landing/unpublish/
    게시 중단.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _check_landing_admin_role(request):
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
    """
    POST /api/v1/core/landing/upload-image/
    랜딩페이지용 이미지 업로드 → R2 admin 버킷.
    field: "hero" | "logo"
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _check_landing_admin_role(request):
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

        # field=hero_slot 의미: 다중 히어로 슬롯(0~5). slot 인덱스를 같이 받음.
        # field=hero / logo 는 기존 단일 이미지(backward compat).
        # P0 audit (2026-05-13): slot 파싱 단일 + R2 업로드 + draft 갱신을 transaction
        # 안에서 select_for_update 로 묶음. 이전: slot 두 번 재파싱 + read-modify-write
        # 비원자 → 동시 hero_slot upload 시 slot 덮어쓰임 + R2 객체만 잔존 가능.
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

        # draft_config 자동 반영 — 동시 upload race 방어용 atomic + select_for_update.
        from django.db import transaction
        with transaction.atomic():
            landing, _ = LandingPage.objects.select_for_update().get_or_create(
                tenant=tenant,
                defaults={"draft_config": _default_draft_config(tenant)},
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
    """
    GET /api/v1/core/landing/templates/
    사용 가능한 템플릿 목록 (갤러리용).
    """
    permission_classes = [TenantResolved]

    def get(self, request):
        return Response({"templates": TEMPLATE_META})


# ─────────────────────────────────────────────────
# 상담 요청 (학원 홈페이지 contact form)
# ─────────────────────────────────────────────────

import re as _re

_PERSONAL_MOBILE_PREFIX = ("010", "011", "016", "017", "018", "019")
_NAME_MAX = 50
_INTEREST_MAX = 80
_MESSAGE_MAX = 2000


def _validate_consult(data: dict) -> list[str]:
    errs: list[str] = []
    name = str(data.get("name") or "").strip()
    phone = str(data.get("phone") or "").strip()
    interest = str(data.get("interest") or "").strip()
    message = str(data.get("message") or "").strip()
    if not name or len(name) > _NAME_MAX:
        errs.append(f"이름은 1~{_NAME_MAX}자여야 합니다.")
    digits = _re.sub(r"[^\d]", "", phone)
    if not digits or len(digits) < 9 or len(digits) > 15:
        errs.append("올바른 전화번호를 입력해주세요.")
    # P2 audit (2026-05-14): +82 외국인 학부모 prefix 통과.
    # +82 10..., +82 2... → digits에 "82..." 잔존. 0으로 정규화 후 검증.
    elif not (
        digits.startswith(_PERSONAL_MOBILE_PREFIX)
        or digits.startswith("02")
        or digits[0] == "0"
        or digits.startswith("82")  # +82 외국인/해외 학부모
    ):
        errs.append("올바른 전화번호 형식이 아닙니다.")
    if interest and len(interest) > _INTEREST_MAX:
        errs.append(f"관심 분야는 {_INTEREST_MAX}자 이내여야 합니다.")
    if message and len(message) > _MESSAGE_MAX:
        errs.append(f"메시지는 {_MESSAGE_MAX}자 이내여야 합니다.")
    return errs


# Rate limit — DB-level dedup (LandingConsultRequest row 자체).
# P0 audit (2026-05-13 → 2026-05-14): cache backend 도입 시도했으나 settings.CACHES
# 미설정 → Django default LocMemCache(process-local) → ASG 3 인스턴스 결국 같은 문제.
# DB-level 이 ASG 다중 인스턴스 자연 공유 + Redis 인프라 추가 부담 X.
# 정합: (tenant, phone, created_at) 으로 같은 학원에 같은 phone 1분 내 limit 초과면 차단.
#
# 본 패턴 한계: 봇이 phone 다양화 시 우회. 실 spam 방어는 Cloudflare/WAF rate limit
# 인프라 단으로 보강. 정상 사용자 실수 중복 클릭 + 단순 봇은 본 fix 로 충분.


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR") or ""
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def _is_rate_limited(tenant, phone: str, limit: int = 5, window_sec: int = 60) -> bool:
    """tenant + phone 별 window_sec 안 limit 초과면 True.

    ASG 3 인스턴스에서 공유되는 RDS row 자체를 카운터로 사용 → 자연 공유.
    인덱스: (tenant, -created_at) 기존 — 1분 cutoff 범위라 100건/일 학원 부담 0.
    """
    if not phone:
        return False
    from datetime import timedelta
    from django.utils import timezone as dj_tz
    from apps.core.models import LandingConsultRequest
    cutoff = dj_tz.now() - timedelta(seconds=window_sec)
    try:
        count = LandingConsultRequest.objects.filter(
            tenant=tenant, phone=phone, created_at__gte=cutoff,
        ).count()
        return count >= limit
    except Exception:
        logger.exception("CONSULT_RATE_LIMIT_DB_FAIL phone=%s", phone[-4:])
        return False  # fail-open


class LandingConsultPublicView(APIView):
    """
    POST /api/v1/core/landing/consult/
    공개 상담 요청 폼 — 인증 X, tenant 격리(subdomain), rate limit 적용.
    """
    permission_classes = [TenantResolved]
    authentication_classes = []  # 인증 없이 작동

    def post(self, request):
        # honeypot — 사람은 안 채우는 hidden field. 채워졌으면 봇으로 간주, 201처럼 위장 응답 후 무시.
        if (request.data or {}).get("website") or (request.data or {}).get("hp"):
            logger.info("CONSULT_HONEYPOT_TRAP ip=%s", _client_ip(request))
            return Response({"id": 0, "ok": True}, status=201)

        # rate limit — tenant + phone 기준 (DB-level). phone 없으면 validate 에서 차단.
        phone_raw = str((request.data or {}).get("phone") or "").strip()
        if phone_raw and _is_rate_limited(request.tenant, phone_raw):
            return Response({"detail": "너무 빠른 요청입니다. 잠시 후 다시 시도해주세요."}, status=429)

        errs = _validate_consult(request.data or {})
        if errs:
            return Response({"detail": errs}, status=400)

        from apps.core.models import LandingConsultRequest
        obj = LandingConsultRequest.objects.create(
            tenant=request.tenant,
            name=str(request.data.get("name") or "").strip()[:_NAME_MAX],
            phone=str(request.data.get("phone") or "").strip()[:20],
            interest=str(request.data.get("interest") or "").strip()[:_INTEREST_MAX],
            message=str(request.data.get("message") or "").strip()[:_MESSAGE_MAX],
            source=str(request.data.get("source") or "landing").strip()[:40],
        )
        logger.info("LandingConsultRequest created tenant=%s id=%s ip=%s", request.tenant.id, obj.id, _client_ip(request))
        return Response({"id": obj.id, "ok": True}, status=201)


class LandingConsultAdminListView(APIView):
    """
    GET /api/v1/core/landing/admin/consult/
    학원 owner/admin이 받은 상담 요청 리스트.
    PATCH /api/v1/core/landing/admin/consult/<id>/  → 읽음/메모 update.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("상담 요청 조회는 원장/관리자만 가능합니다.")

    def get(self, request):
        from apps.core.models import LandingConsultRequest
        qs = LandingConsultRequest.objects.filter(tenant=request.tenant)[:200]
        items = [{
            "id": r.id,
            "name": r.name,
            "phone": r.phone,
            "interest": r.interest,
            "message": r.message,
            "source": r.source,
            "read_at": r.read_at.isoformat() if r.read_at else None,
            "admin_memo": r.admin_memo,
            "created_at": r.created_at.isoformat(),
        } for r in qs]
        unread = sum(1 for r in qs if r.read_at is None)
        return Response({"items": items, "summary": {"total": len(items), "unread": unread}})


class LandingConsultAdminDetailView(APIView):
    """PATCH /api/v1/core/landing/admin/consult/<id>/ — 읽음/메모 update."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("상담 요청 처리는 원장/관리자만 가능합니다.")

    def patch(self, request, item_id):
        from apps.core.models import LandingConsultRequest
        from django.utils import timezone
        try:
            r = LandingConsultRequest.objects.get(id=item_id, tenant=request.tenant)
        except LandingConsultRequest.DoesNotExist:
            return Response({"detail": "Not found"}, status=404)
        data = request.data or {}
        if "mark_read" in data and data["mark_read"]:
            r.read_at = r.read_at or timezone.now()
        if "admin_memo" in data:
            r.admin_memo = str(data["admin_memo"] or "")[:2000]
        r.save(update_fields=["read_at", "admin_memo", "updated_at"])
        return Response({"ok": True})


# ─────────────────────────────────────────────────
# SEO sitemap.xml — 학원 도메인 단위
# ─────────────────────────────────────────────────

# ─────────────────────────────────────────────────
# 학부모 후기 제출
# ─────────────────────────────────────────────────

_TEST_NAME_MAX = 50
_TEST_ROLE_MAX = 80
_TEST_TEXT_MAX = 1000


def _validate_testimonial(data: dict) -> list[str]:
    """학부모 후기 제출 검증."""
    errs: list[str] = []
    name = str(data.get("name") or "").strip()
    text = str(data.get("text") or "").strip()
    role = str(data.get("role") or "").strip()
    if not name or len(name) > _TEST_NAME_MAX:
        errs.append(f"이름은 1~{_TEST_NAME_MAX}자여야 합니다.")
    if not text or len(text) < 10:
        errs.append("후기는 10자 이상 입력해주세요.")
    if len(text) > _TEST_TEXT_MAX:
        errs.append(f"후기는 {_TEST_TEXT_MAX}자 이내여야 합니다.")
    if role and len(role) > _TEST_ROLE_MAX:
        errs.append(f"학년/관계는 {_TEST_ROLE_MAX}자 이내여야 합니다.")
    return errs


class LandingTestimonialPublicView(APIView):
    """POST /api/v1/core/landing/testimonial/ — 학부모 직접 후기 제출. honeypot + rate limit."""
    permission_classes = [TenantResolved]
    authentication_classes = []

    def post(self, request):
        if (request.data or {}).get("website") or (request.data or {}).get("hp"):
            logger.info("TESTIMONIAL_HONEYPOT_TRAP ip=%s", _client_ip(request))
            return Response({"id": 0, "ok": True}, status=201)

        # testimonial 은 phone 필드 없음 — rate limit 적용 불가. 학원장이 검수 stage
        # 에서 reject 가능 (pending status). spam 보호 본질은 honeypot + validate 로 차단.
        # 본격 spam 방어는 별 cycle에서 testimonial 전용 dedup (name+text hash) 도입.

        errs = _validate_testimonial(request.data or {})
        if errs:
            return Response({"detail": errs}, status=400)

        from apps.core.models import LandingTestimonialSubmission
        obj = LandingTestimonialSubmission.objects.create(
            tenant=request.tenant,
            name=str(request.data.get("name") or "").strip()[:_TEST_NAME_MAX],
            role=str(request.data.get("role") or "").strip()[:_TEST_ROLE_MAX],
            text=str(request.data.get("text") or "").strip()[:_TEST_TEXT_MAX],
            status=LandingTestimonialSubmission.Status.PENDING,
        )
        logger.info("LandingTestimonialSubmission created tenant=%s id=%s", request.tenant.id, obj.id)
        return Response({"id": obj.id, "ok": True}, status=201)


class LandingTestimonialAdminListView(APIView):
    """GET /api/v1/core/landing/admin/testimonial/ — 학원장 후기 승인 큐."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("후기 승인은 원장/관리자만 가능합니다.")

    def get(self, request):
        from apps.core.models import LandingTestimonialSubmission
        status_filter = (request.GET.get("status") or "").strip()
        qs = LandingTestimonialSubmission.objects.filter(tenant=request.tenant)
        if status_filter in ("pending", "approved", "rejected"):
            qs = qs.filter(status=status_filter)
        items = [{
            "id": r.id, "name": r.name, "role": r.role, "text": r.text,
            "status": r.status,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "created_at": r.created_at.isoformat(),
        } for r in qs[:200]]
        all_qs = LandingTestimonialSubmission.objects.filter(tenant=request.tenant)
        return Response({
            "items": items,
            "summary": {
                "total": all_qs.count(),
                "pending": all_qs.filter(status="pending").count(),
                "approved": all_qs.filter(status="approved").count(),
            },
        })


class LandingTestimonialAdminDetailView(APIView):
    """PATCH /api/v1/core/landing/admin/testimonial/<id>/ — 승인/거절."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("후기 승인은 원장/관리자만 가능합니다.")

    def patch(self, request, item_id):
        from apps.core.models import LandingTestimonialSubmission
        from django.utils import timezone
        try:
            r = LandingTestimonialSubmission.objects.get(id=item_id, tenant=request.tenant)
        except LandingTestimonialSubmission.DoesNotExist:
            return Response({"detail": "Not found"}, status=404)
        new_status = (request.data or {}).get("status")
        if new_status not in ("approved", "rejected", "pending"):
            return Response({"detail": "유효하지 않은 status"}, status=400)
        r.status = new_status
        r.reviewed_at = timezone.now() if new_status in ("approved", "rejected") else None
        r.reviewed_by = request.user if new_status in ("approved", "rejected") else None
        r.save(update_fields=["status", "reviewed_at", "reviewed_by", "updated_at"])
        return Response({"ok": True})


class LandingTestimonialPublicListView(APIView):
    """GET /api/v1/core/landing/testimonial/public/ — 외부에 노출되는 승인된 후기 (testimonials 섹션)."""
    permission_classes = [TenantResolved]
    authentication_classes = []

    def get(self, request):
        from apps.core.models import LandingTestimonialSubmission
        qs = LandingTestimonialSubmission.objects.filter(
            tenant=request.tenant, status=LandingTestimonialSubmission.Status.APPROVED,
        ).order_by("-created_at")[:30]
        items = [{
            "id": r.id, "name": r.name, "role": r.role, "text": r.text,
        } for r in qs]
        return Response({"items": items})


class LandingHitReportError(Exception):
    """toggle helper 도메인 에러 — 상위에서 status code + detail 매핑."""
    def __init__(self, status_code: int, detail: str, code: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.code = code
        super().__init__(detail)


def toggle_hit_report_on_landing(
    tenant, report_id: int, action: str,
    *, auto_publish: bool = True,
) -> dict:
    """학원 홈페이지(LandingPage) 에 적중보고서 add/remove + auto-publish.

    LandingHitReportToggleView.post 의 핵심 로직을 helper 로 추출 — 매치업 submit
    (HitReportSubmitView) 흐름에서도 동일 path 사용 (2026-05-11 학원장 mental model 정합:
    submit=학원 홈페이지 게시).

    Returns: {ok, registered, noop, total_registered, published, max_reached}
    Raises: LandingHitReportError — 보고서 없음(404) / action 잘못(400) / 상한 초과(400).
    """
    from apps.domains.matchup.models import MatchupHitReport

    if action not in ("add", "remove"):
        raise LandingHitReportError(400, "action은 add 또는 remove")

    # 보고서 검증 — 본 학원 보고서만
    try:
        MatchupHitReport.objects.get(id=int(report_id), tenant=tenant)
    except MatchupHitReport.DoesNotExist:
        raise LandingHitReportError(404, "보고서를 찾을 수 없습니다")

    landing, _ = LandingPage.objects.get_or_create(
        tenant=tenant,
        defaults={"draft_config": _default_draft_config(tenant)},
    )
    # backfill — hit_reports section이 없으면 추가
    landing.draft_config = _backfill_missing_sections(landing.draft_config)
    sections = list(landing.draft_config.get("sections") or [])
    hit_idx = None
    for i, s in enumerate(sections):
        if s.get("type") == "hit_reports":
            hit_idx = i
            break
    if hit_idx is None:
        raise LandingHitReportError(500, "hit_reports 섹션 누락(backfill 실패)")
    hit_sec = dict(sections[hit_idx])
    items = list(hit_sec.get("items") or [])
    existing_ids = [
        int(it.get("report_id"))
        for it in items
        if isinstance(it.get("report_id"), int)
    ]

    changed = False
    MAX_REPORTS = 12
    rid = int(report_id)
    if action == "add":
        if rid in existing_ids:
            return {"ok": True, "noop": True, "registered": True,
                    "total_registered": len(existing_ids),
                    "published": landing.is_published}
        if len(existing_ids) >= MAX_REPORTS:
            raise LandingHitReportError(
                400,
                f"홈페이지에는 최대 {MAX_REPORTS}개 보고서까지 노출 가능합니다.",
                code="max_reached",
            )
        items.append({"report_id": rid})
        hit_sec["items"] = items
        hit_sec["enabled"] = True  # auto-enable
        changed = True
    else:  # remove
        if rid not in existing_ids:
            return {"ok": True, "noop": True, "registered": False,
                    "total_registered": len(existing_ids),
                    "published": landing.is_published}
        items = [it for it in items if int(it.get("report_id") or -1) != rid]
        hit_sec["items"] = items
        changed = True

    if changed:
        sections[hit_idx] = hit_sec
        landing.draft_config = {**landing.draft_config, "sections": sections}
        landing.save(update_fields=["draft_config", "updated_at"])
        if auto_publish:
            landing.publish()

    return {
        "ok": True,
        "registered": action == "add",
        "total_registered": len([
            it for it in (hit_sec.get("items") or [])
            if isinstance(it.get("report_id"), int)
        ]),
        "published": auto_publish and landing.is_published,
    }


class LandingHitReportToggleView(APIView):
    """
    POST /api/v1/core/landing/admin/hit-report-toggle/
    body: { report_id: int, action: "add"|"remove", auto_publish?: bool=true }

    학원장(owner/admin)이 적중보고서 리스트에서 한 클릭으로 홈페이지 노출 토글.
    - draft_config.sections[hit_reports].items에 add/remove
    - hit_reports section 자동 enable
    - auto_publish=True (기본): publish 즉시 외부 노출 갱신

    내부 로직은 toggle_hit_report_on_landing helper — 매치업 submit 흐름에서도 재사용.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("홈페이지 노출 토글은 원장/관리자만 가능합니다.")

    def post(self, request):
        try:
            report_id = int(request.data.get("report_id"))
        except (TypeError, ValueError):
            return Response({"detail": "report_id 필수"}, status=400)
        action = (request.data.get("action") or "").strip()
        auto_publish = bool(request.data.get("auto_publish", True))

        try:
            result = toggle_hit_report_on_landing(
                request.tenant, report_id, action,
                auto_publish=auto_publish,
            )
        except LandingHitReportError as e:
            return Response({"detail": e.detail, "code": e.code}, status=e.status_code)
        return Response(result)


# LandingManifestView + LandingSitemapView 는 apps/core/landing/ 패키지로 분리
# (2026-05-14 P1 audit 점진 리팩토링). import 경로 보존 위해 본 파일에서 re-export.
from apps.core.landing.views_manifest import LandingManifestView  # noqa: E402,F401
from apps.core.landing.views_sitemap import LandingSitemapView  # noqa: E402,F401

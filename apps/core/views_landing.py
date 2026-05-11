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

# 섹션 타입 SSOT — 추가는 SECTION_TYPES_ORDERED 한 곳만 수정.
# (frontend types/index.ts SECTION_META와 list 동기화 필요 — 두 언어 사이 자동 import 불가)
SECTION_TYPES_ORDERED = [
    "hero",
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
    elif not (digits.startswith(_PERSONAL_MOBILE_PREFIX) or digits.startswith("02") or digits[0] == "0"):
        errs.append("올바른 전화번호 형식이 아닙니다.")
    if interest and len(interest) > _INTEREST_MAX:
        errs.append(f"관심 분야는 {_INTEREST_MAX}자 이내여야 합니다.")
    if message and len(message) > _MESSAGE_MAX:
        errs.append(f"메시지는 {_MESSAGE_MAX}자 이내여야 합니다.")
    return errs


# 간단한 in-memory rate limit — IP당 1분에 5건. 외부 form spam 완화.
_consult_rate_window: dict[str, list[float]] = {}


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR") or ""
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or "unknown"


def _is_rate_limited(ip: str, limit: int = 5, window_sec: int = 60) -> bool:
    import time
    now = time.time()
    bucket = _consult_rate_window.setdefault(ip, [])
    # 윈도우 밖 항목 제거
    cutoff = now - window_sec
    bucket[:] = [t for t in bucket if t > cutoff]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


class LandingConsultPublicView(APIView):
    """
    POST /api/v1/core/landing/consult/
    공개 상담 요청 폼 — 인증 X, tenant 격리(subdomain), rate limit 적용.
    """
    permission_classes = [TenantResolved]
    authentication_classes = []  # 인증 없이 작동

    def post(self, request):
        ip = _client_ip(request)
        if _is_rate_limited(ip):
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
        logger.info("LandingConsultRequest created tenant=%s id=%s ip=%s", request.tenant.id, obj.id, ip)
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

class LandingSitemapView(APIView):
    """GET /api/v1/core/landing/sitemap.xml — 학원 홈페이지 + 적중 보고서 URL 모음."""
    permission_classes = [TenantResolved]
    authentication_classes = []
    renderer_classes = []  # plain HttpResponse

    def get(self, request):
        from apps.core.models import LandingPage
        from django.http import HttpResponse
        host = request.get_host()
        scheme = "https"
        urls = [f"{scheme}://{host}/landing"]
        try:
            lp = LandingPage.objects.get(tenant=request.tenant, is_published=True)
            pub = lp.published_config or {}
            for sec in (pub.get("sections") or []):
                if sec.get("type") == "hit_reports" and sec.get("enabled"):
                    items = sec.get("items") or []
                    if items:
                        urls.append(f"{scheme}://{host}/landing/reports")
                        for it in items:
                            rid = it.get("report_id")
                            if isinstance(rid, int):
                                urls.append(f"{scheme}://{host}/landing/reports/{rid}")
                    break
        except LandingPage.DoesNotExist:
            pass

        body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        for u in urls:
            body += f"  <url><loc>{u}</loc></url>\n"
        body += "</urlset>\n"
        resp = HttpResponse(body, content_type="application/xml; charset=utf-8")
        resp["Cache-Control"] = "public, max-age=600"
        return resp

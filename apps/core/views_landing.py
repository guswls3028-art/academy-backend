# PATH: apps/core/views_landing.py
#
# 선생님별 랜딩페이지 API.
# - Public: 게시된 랜딩 조회 (인증 불필요)
# - Admin: Draft CRUD, Publish/Unpublish, 이미지 업로드
#
# 2026-05-14 P1 audit 점진 리팩토링 — Manifest/Sitemap/Consult/Testimonial/Config helper
# 는 apps/core/landing/ 패키지로 분리. 본 파일은 view 잔재 + facade re-export.

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

# config 도메인 상수 + helper 패키지로 분리 — alias 로 옛 이름 보존.
from apps.core.landing.config_helpers import (
    ALLOWED_COLORS,  # noqa: F401 — TEMPLATE_META 같이 외부 참조 보존
    SECTION_TYPES_ORDERED,  # noqa: F401
    SECTION_TYPES,  # noqa: F401
    MAX_SECTION_ITEMS,  # noqa: F401
    MAX_SECTIONS,  # noqa: F401
    TEMPLATE_META,
    default_draft_config as _default_draft_config,
    backfill_missing_sections as _backfill_missing_sections,
    resolve_image_urls as _resolve_image_urls,
    validate_config as _validate_config,
)
# tenant_required + admin role check — _helpers 공용.
from apps.core.landing._helpers import (
    tenant_required as _tenant_required,
    check_landing_admin_role as _check_landing_admin_role,
    LANDING_ADMIN_ROLES,  # noqa: F401
)

logger = logging.getLogger(__name__)


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
# — LANDING_ADMIN_ROLES + _check_landing_admin_role 은 _helpers 에서 import 됨 (상단 참조)
# ─────────────────────────────────────────────────


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

# Consult 도메인 (validate / rate limit / 3 view) 은 apps/core/landing/views_consult.py
# 패키지로 분리 (P1 audit step 2, 2026-05-14). import path 보존 위해 re-export.
from apps.core.landing.views_consult import (  # noqa: E402,F401
    LandingConsultPublicView,
    LandingConsultAdminListView,
    LandingConsultAdminDetailView,
)
# testimonial 등 본 파일 잔재가 사용하는 client_ip 헬퍼 — 공용 _helpers 로 통일.
from apps.core.landing._helpers import client_ip as _client_ip  # noqa: E402


# ─────────────────────────────────────────────────
# SEO sitemap.xml — 학원 도메인 단위
# ─────────────────────────────────────────────────

# Testimonial 도메인 (validate + 4 view) 은 apps/core/landing/views_testimonial.py 분리.
# P1 audit step 3 (2026-05-14). import path 보존 위해 re-export.
from apps.core.landing.views_testimonial import (  # noqa: E402,F401
    LandingTestimonialPublicView,
    LandingTestimonialPublicListView,
    LandingTestimonialAdminListView,
    LandingTestimonialAdminDetailView,
)


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

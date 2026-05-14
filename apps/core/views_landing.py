# PATH: apps/core/views_landing.py
#
# 선생님별 랜딩페이지 API — facade.
#
# 2026-05-14 P1 audit 점진 리팩토링 완료. 모든 view/helper 는 apps/core/landing/
# 패키지로 분리됨. 본 파일은 import path 보존을 위한 re-export 만 유지.
#
# 외부 import:
# - apps/core/urls.py (15+ view 클래스)
# - apps/domains/matchup/views_hit_report.py (toggle_hit_report_on_landing, LandingHitReportError)

# Config CRUD + 공개 view
from apps.core.landing.views_config import (  # noqa: F401
    LandingPublicView,
    LandingHasPublishedView,
    LandingAdminView,
    LandingPublishView,
    LandingUnpublishView,
    LandingUploadImageView,
    LandingTemplatesView,
)
# Consult (상담 폼)
from apps.core.landing.views_consult import (  # noqa: F401
    LandingConsultPublicView,
    LandingConsultAdminListView,
    LandingConsultAdminDetailView,
)
# Testimonial (학부모 후기)
from apps.core.landing.views_testimonial import (  # noqa: F401
    LandingTestimonialPublicView,
    LandingTestimonialPublicListView,
    LandingTestimonialAdminListView,
    LandingTestimonialAdminDetailView,
)
# SEO manifest + sitemap
from apps.core.landing.views_manifest import LandingManifestView  # noqa: F401
from apps.core.landing.views_sitemap import LandingSitemapView  # noqa: F401
# HitReport ↔ Landing toggle (매치업 도메인에서도 helper 재사용)
from apps.core.landing.views_hit_report import (  # noqa: F401
    LandingHitReportError,
    LandingHitReportToggleView,
    toggle_hit_report_on_landing,
)
# helper / config 상수 — 본 파일 외부 직접 참조 (매치업 등) 보존용.
from apps.core.landing.config_helpers import (  # noqa: F401
    ALLOWED_COLORS,
    SECTION_TYPES_ORDERED,
    SECTION_TYPES,
    MAX_SECTION_ITEMS,
    MAX_SECTIONS,
    TEMPLATE_META,
    default_draft_config as _default_draft_config,
    backfill_missing_sections as _backfill_missing_sections,
    resolve_image_urls as _resolve_image_urls,
    validate_config as _validate_config,
)
from apps.core.landing._helpers import (  # noqa: F401
    tenant_required as _tenant_required,
    check_landing_admin_role as _check_landing_admin_role,
    LANDING_ADMIN_ROLES,
    client_ip as _client_ip,
)

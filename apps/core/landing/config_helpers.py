"""LandingPage config 도메인 상수 + helper.

분리 출처: apps/core/views_landing.py:33-281 (P1 audit step 4a, 2026-05-14).

상수:
- ALLOWED_COLORS / SECTION_TYPES_ORDERED / SECTION_TYPES / MAX_SECTION_ITEMS / MAX_SECTIONS
- TEMPLATE_META (frontend 갤러리용 4 template 메타)

헬퍼:
- default_draft_config(tenant): 새 랜딩 기본 config
- backfill_missing_sections(draft): 기존 학원 draft에 신규 섹션 자동 추가
- resolve_image_urls(config): R2 key → presigned URL
- validate_config(data): draft 유효성 검증 (색상 / sections / cta_link / 개인 번호 가드)
"""
from __future__ import annotations

import copy
import re


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
MAX_SECTION_ITEMS = 12
MAX_SECTIONS = len(SECTION_TYPES_ORDERED) + 2

# SECTION_TYPES_ORDERED를 SSOT으로 사용 — notice는 default backfill 대상에서 제외 (학원 자율 추가).
_REQUIRED_SECTION_TYPES = [t for t in SECTION_TYPES_ORDERED if t != "notice"]


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
# Helpers
# ─────────────────────────────────────────────────


def default_draft_config(tenant):
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


def backfill_missing_sections(draft: dict) -> dict:
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


def resolve_image_urls(config: dict) -> dict:
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


_PERSONAL_MOBILE_RE = re.compile(r"01[016789][- ]?\d{3,4}[- ]?\d{4}")


def validate_config(data: dict) -> list[str]:
    """draft config 유효성 검증. 위반 사항 목록 반환."""
    errors: list[str] = []
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

    # 개인 휴대폰 번호 가드 — tel: 010-xxxx-xxxx 패턴은 학원 대표번호 X.
    if cta_link.startswith("tel:") and _PERSONAL_MOBILE_RE.match(cta_link[4:].replace("-", "").replace(" ", "")):
        digits = cta_link[4:].replace("-", "").replace(" ", "").replace("+82", "0")
        if digits.startswith(("010", "011", "016", "017", "018", "019")):
            errors.append("CTA 링크에 개인 휴대폰 번호는 사용할 수 없습니다. 학원 대표번호(02-, 031- 등)를 사용해주세요.")
    contact = data.get("contact") or {}
    if isinstance(contact, dict):
        for k in ("phone", "email"):
            v = str(contact.get(k) or "")
            digits = re.sub(r"[^\d]", "", v)
            if digits and len(digits) >= 10 and digits[:3] in ("010", "011", "016", "017", "018", "019"):
                if k == "phone":
                    errors.append(f"문의 전화번호({k})에 개인 휴대폰 번호는 사용할 수 없습니다. 학원 대표번호를 사용해주세요.")

    return errors

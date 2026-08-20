"""LandingHitReportToggle — 적중보고서 → 학원 홈페이지 노출 토글 + helper.

분리 출처: apps/core/views_landing.py:369-504 (P1 audit step 5, 2026-05-14).

- LandingHitReportError: helper 도메인 에러 (외부 status_code + detail 매핑)
- toggle_hit_report_on_landing(tenant, report_id, action, *, auto_publish): 핵심 helper.
  매치업 submit (HitReportSubmitView) 와 toggle view 둘 다 재사용.
- LandingHitReportToggleView: POST endpoint
"""
from __future__ import annotations

from copy import deepcopy

from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.parsing import parse_bool
from apps.core.models import LandingPage
from apps.core.permissions import TenantResolvedAndStaff

from ._helpers import check_landing_admin_role
from .config_helpers import backfill_missing_sections, default_draft_config


class LandingHitReportError(Exception):
    """toggle helper 도메인 에러 — 상위에서 status code + detail 매핑."""
    def __init__(self, status_code: int, detail: str, code: str = ""):
        self.status_code = status_code
        self.detail = detail
        self.code = code
        super().__init__(detail)


def _hit_report_ids_from_landing_config(config: dict) -> set[int]:
    report_ids: set[int] = set()
    for section in (config or {}).get("sections") or []:
        if section.get("type") != "hit_reports" or not section.get("enabled"):
            continue
        for item in section.get("items") or []:
            try:
                report_ids.add(int(item.get("report_id")))
            except (AttributeError, TypeError, ValueError):
                continue
    return report_ids


def prewarm_hit_report_previews_for_landing(tenant, config: dict) -> dict[int, str]:
    """Prepare previews outside row locks and return their content-addressed keys."""
    from apps.domains.matchup.models import MatchupHitReport
    from apps.domains.matchup.views_hit_report import (
        _get_or_generate_hit_report_preview,
        _hit_report_public_preview_pdf_key,
    )

    report_ids = _hit_report_ids_from_landing_config(config)
    if not report_ids:
        return {}

    reports = {
        report.id: report
        for report in MatchupHitReport.objects.select_related(
            "document",
            "author",
        ).filter(tenant=tenant, id__in=report_ids).order_by("id")
    }
    missing_ids = report_ids.difference(reports)
    if missing_ids:
        raise LandingHitReportError(
            400,
            f"존재하지 않는 적중보고서가 포함되어 있습니다: {min(missing_ids)}",
            code="hit_report_not_found",
        )

    prepared: dict[int, str] = {}
    for report_id in sorted(report_ids):
        try:
            report = reports[report_id]
            preview_pdf_key = _hit_report_public_preview_pdf_key(report)
            _get_or_generate_hit_report_preview(
                report,
                require_cache_write=True,
                preview_pdf_key=preview_pdf_key,
            )
            prepared[report_id] = preview_pdf_key
        except Exception as exc:
            raise LandingHitReportError(
                503,
                "대표 비교 화면을 준비하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                code="preview_prepare_failed",
            ) from exc
    return prepared


def verify_prepared_hit_report_previews(
    tenant,
    config: dict,
    prepared: dict[int, str],
) -> None:
    """Lock report rows briefly and reject publication if content changed."""
    from apps.domains.matchup.models import MatchupHitReport
    from apps.domains.matchup.views_hit_report import (
        _hit_report_public_preview_pdf_key,
    )

    report_ids = _hit_report_ids_from_landing_config(config)
    if report_ids != set(prepared):
        raise LandingHitReportError(
            409,
            "적중보고서 구성이 변경되었습니다. 다시 시도해 주세요.",
            code="hit_report_changed",
        )
    if not report_ids:
        return

    reports = list(
        MatchupHitReport.objects.select_for_update(of=("self",)).select_related(
            "document",
            "author",
        ).filter(tenant=tenant, id__in=report_ids).order_by("id"),
    )
    current = {
        report.id: _hit_report_public_preview_pdf_key(report)
        for report in reports
    }
    if current != prepared:
        raise LandingHitReportError(
            409,
            "적중보고서가 변경되었습니다. 다시 게시해 주세요.",
            code="hit_report_changed",
        )


def toggle_hit_report_on_landing(
    tenant, report_id: int, action: str,
    *, auto_publish: bool = True,
) -> dict:
    """학원 홈페이지(LandingPage) 에 적중보고서 add/remove + auto-publish.

    매치업 submit (HitReportSubmitView) + toggle view 양쪽 재사용 (2026-05-11 학원장 mental
    model 정합: submit=학원 홈페이지 게시).

    Returns: {ok, registered, noop, total_registered, published, max_reached}
    Raises: LandingHitReportError — 보고서 없음(404) / action 잘못(400) / 상한 초과(400).
    """
    from apps.domains.matchup.models import MatchupHitReport

    if action not in ("add", "remove"):
        raise LandingHitReportError(400, "action은 add 또는 remove")

    # 보고서 검증 — 본 학원 보고서만
    if not MatchupHitReport.objects.filter(
        id=int(report_id),
        tenant=tenant,
    ).exists():
        raise LandingHitReportError(404, "보고서를 찾을 수 없습니다")

    landing, _ = LandingPage.objects.get_or_create(
        tenant=tenant,
        defaults={"draft_config": default_draft_config(tenant)},
    )
    # backfill — hit_reports section이 없으면 추가
    landing.draft_config = backfill_missing_sections(landing.draft_config)
    original_draft_config = deepcopy(landing.draft_config)
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
            if auto_publish:
                prewarm_hit_report_previews_for_landing(
                    tenant,
                    landing.draft_config,
                )
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
            if auto_publish:
                prewarm_hit_report_previews_for_landing(
                    tenant,
                    landing.draft_config,
                )
            return {"ok": True, "noop": True, "registered": False,
                    "total_registered": len(existing_ids),
                    "published": landing.is_published}
        items = [it for it in items if int(it.get("report_id") or -1) != rid]
        hit_sec["items"] = items
        changed = True

    if changed:
        sections[hit_idx] = hit_sec
        next_draft_config = {**landing.draft_config, "sections": sections}
        prepared_previews = (
            prewarm_hit_report_previews_for_landing(
                tenant,
                next_draft_config,
            )
            if auto_publish
            else {}
        )
        with transaction.atomic():
            locked = LandingPage.objects.select_for_update().get(pk=landing.pk)
            current_draft_config = backfill_missing_sections(locked.draft_config)
            if current_draft_config != original_draft_config:
                raise LandingHitReportError(
                    409,
                    "홈페이지 초안이 변경되었습니다. 다시 시도해 주세요.",
                    code="draft_changed",
                )
            if auto_publish:
                verify_prepared_hit_report_previews(
                    tenant,
                    next_draft_config,
                    prepared_previews,
                )
            locked.draft_config = next_draft_config
            locked.save(update_fields=["draft_config", "updated_at"])
            if auto_publish:
                locked.publish()
            landing = locked

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
    """POST /api/v1/core/landing/admin/hit-report-toggle/

    body: { report_id: int, action: "add"|"remove", auto_publish?: bool=true }
    학원장(owner/admin)이 적중보고서 리스트에서 한 클릭으로 홈페이지 노출 토글.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_landing_admin_role(request):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("홈페이지 노출 토글은 원장/관리자만 가능합니다.")

    def post(self, request):
        try:
            report_id = int(request.data.get("report_id"))
        except (TypeError, ValueError):
            return Response({"detail": "report_id 필수"}, status=400)
        action = (request.data.get("action") or "").strip()
        auto_publish = parse_bool(
            request.data.get("auto_publish", True),
            field_name="auto_publish",
        )

        try:
            result = toggle_hit_report_on_landing(
                request.tenant, report_id, action,
                auto_publish=auto_publish,
            )
        except LandingHitReportError as e:
            return Response({"detail": e.detail, "code": e.code}, status=e.status_code)
        return Response(result)

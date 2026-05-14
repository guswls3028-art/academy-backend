"""LandingHitReportToggle — 적중보고서 → 학원 홈페이지 노출 토글 + helper.

분리 출처: apps/core/views_landing.py:369-504 (P1 audit step 5, 2026-05-14).

- LandingHitReportError: helper 도메인 에러 (외부 status_code + detail 매핑)
- toggle_hit_report_on_landing(tenant, report_id, action, *, auto_publish): 핵심 helper.
  매치업 submit (HitReportSubmitView) 와 toggle view 둘 다 재사용.
- LandingHitReportToggleView: POST endpoint
"""
from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
    try:
        MatchupHitReport.objects.get(id=int(report_id), tenant=tenant)
    except MatchupHitReport.DoesNotExist:
        raise LandingHitReportError(404, "보고서를 찾을 수 없습니다")

    landing, _ = LandingPage.objects.get_or_create(
        tenant=tenant,
        defaults={"draft_config": default_draft_config(tenant)},
    )
    # backfill — hit_reports section이 없으면 추가
    landing.draft_config = backfill_missing_sections(landing.draft_config)
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
        auto_publish = bool(request.data.get("auto_publish", True))

        try:
            result = toggle_hit_report_on_landing(
                request.tenant, report_id, action,
                auto_publish=auto_publish,
            )
        except LandingHitReportError as e:
            return Response({"detail": e.detail, "code": e.code}, status=e.status_code)
        return Response(result)

"""매치업 적중보고서 공개 게시판 ViewSet (Phase #69, 2026-05-13).

학원장이 작성 완료한 MatchupHitReport를 게시 시점에 PDF로 R2 snapshot copy → 게시판에 노출.
원본 보고서가 이후 변경되어도 게시물은 박힌 그대로.

URL:
  - POST   /api/v1/landing-public/matchup-showcase/publish/         (staff: hit_report_id → snapshot publish)
  - GET    /api/v1/landing-public/matchup-showcase/                  (public list, status+window 필터)
  - GET    /api/v1/landing-public/matchup-showcase/{id}/             (public detail, expired 시 카드만)
  - GET    /api/v1/landing-public/matchup-showcase/{id}/pdf/         (public PDF stream, xframe_exempt)
  - PATCH  /api/v1/landing-public/matchup-showcase/{id}/             (staff: title/desc/visibility)
  - POST   /api/v1/landing-public/matchup-showcase/{id}/unpublish/   (staff hide)
  - DELETE /api/v1/landing-public/matchup-showcase/{id}/             (staff: hide; soft)

학원장 데이터 immutable 정책 ([[project_matchup_immutable_policy_2026_05_06]]):
  - MatchupHitReport / MatchupHitReportEntry 본체는 READ ONLY (SELECT only)
  - PDF는 한 번 R2에 박히고 게시물 entity 가 별도 보관 — 원본 변동 무관 스냅샷
"""
from __future__ import annotations

import io
import logging
from typing import Any

from django.http import HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.clickjacking import xframe_options_exempt
from django.utils.decorators import method_decorator
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.permissions import TenantResolved, TenantResolvedAndStaff

from ...models import PublicMatchupShowcase

logger = logging.getLogger(__name__)


def _viewer_is_staff(request) -> bool:
    """학원 staff 인지 판단 (TenantResolved 통과 전제)."""
    user = request.user
    if not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False):
        return True
    tenant = getattr(request, "tenant", None)
    if not tenant:
        return False
    try:
        from academy.adapters.db.django import repositories_core as core_repo
        return core_repo.membership_exists_staff(
            tenant=tenant, user=user,
            staff_roles=("owner", "admin"),
        )
    except Exception:
        return False


def _build_snapshot_for_hit_report(tenant, hit_report_id: int) -> tuple[str, int, dict]:
    """적중보고서 PDF를 R2 storage에 별도 key로 박는다. (snapshot_pdf_key, bytes, meta) 반환.

    원본 MatchupHitReport는 read-only. PDF generate → R2 put.
    """
    from apps.domains.matchup.models import MatchupHitReport
    from apps.domains.matchup.pdf_report import generate_curated_hit_report_pdf
    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

    report = MatchupHitReport.objects.select_related("document", "author").get(
        id=hit_report_id, tenant=tenant,
    )
    pdf_bytes = generate_curated_hit_report_pdf(report)

    now = timezone.now()
    # key: matchup-showcase-snapshots/tenant_{id}/hit_report_{id}/{epoch}.pdf
    key = (
        f"matchup-showcase-snapshots/tenant_{tenant.id}/"
        f"hit_report_{report.id}/{int(now.timestamp())}.pdf"
    )
    upload_fileobj_to_r2_storage(
        fileobj=io.BytesIO(pdf_bytes),
        key=key,
        content_type="application/pdf",
    )

    # snapshot meta — entries 카운트 + 적중률.
    entries_qs = report.entries.all()
    total_entries = entries_qs.count()
    excluded = entries_qs.filter(excluded=True).count()
    counted = total_entries - excluded
    hit = 0
    for e in entries_qs:
        if not e.excluded and isinstance(e.selected_problem_ids, list) and len(e.selected_problem_ids) > 0:
            hit += 1
    meta: dict[str, Any] = {
        "document_title": (report.document.title or "") if report.document_id else "",
        "document_id": report.document_id,
        "author_name": (report.author.name if report.author and getattr(report.author, "name", None)
                        else (report.submitted_by_name or "")),
        "report_title": report.title or "",
        "report_status": report.status,
        "total_entries": total_entries,
        "counted_entries": counted,
        "hit_count": hit,
        "hit_rate": round(hit / counted, 3) if counted else 0.0,
        "snapshot_at_iso": now.isoformat(),
    }
    return key, len(pdf_bytes), meta


def _parse_dt(raw: Any):
    if not raw:
        return None
    if isinstance(raw, str):
        return parse_datetime(raw)
    return None


class PublicMatchupShowcaseViewSet(viewsets.GenericViewSet):
    """공개 매치업 적중보고서 게시판.

    list/retrieve/pdf_stream: 비로그인 OK (PUBLISHED + window 만 노출 / EXPIRED는 카드만)
    publish/unpublish/destroy/partial_update: staff (owner/admin) only
    """

    queryset = PublicMatchupShowcase.objects.all()

    def get_permissions(self):
        if self.action in ("list", "retrieve", "pdf_stream"):
            return [TenantResolved()]
        return [TenantResolvedAndStaff()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return PublicMatchupShowcase.objects.none()
        qs = PublicMatchupShowcase.objects.filter(tenant=tenant)
        if not _viewer_is_staff(self.request):
            qs = qs.filter(status__in=[
                PublicMatchupShowcase.Status.PUBLISHED,
                PublicMatchupShowcase.Status.EXPIRED,
            ])
        return qs.order_by("-published_at", "-created_at")

    def _serialize_card(self, obj: PublicMatchupShowcase, *, viewer_is_staff: bool) -> dict:
        """카드 메타 (list / expired retrieve)."""
        now = timezone.now()
        expired = bool(obj.published_until and now > obj.published_until)
        visible = obj.is_publicly_visible() or viewer_is_staff
        return {
            "id": obj.id,
            "title": obj.title,
            "description": obj.description,
            "status": "expired" if (expired and not viewer_is_staff) else obj.status,
            "published_at": obj.published_at.isoformat() if obj.published_at else None,
            "published_until": obj.published_until.isoformat() if obj.published_until else None,
            "snapshot_at": obj.snapshot_at.isoformat() if obj.snapshot_at else None,
            "snapshot_meta": obj.snapshot_meta or {},
            "view_count": obj.view_count,
            "expired": expired,
            "visible": visible,
            "hit_report_id_ref": obj.hit_report_id_ref,
        }

    def list(self, request, *args, **kwargs):
        viewer_is_staff = _viewer_is_staff(request)
        qs = self.get_queryset()
        items = [self._serialize_card(o, viewer_is_staff=viewer_is_staff) for o in qs[:50]]
        return Response({"results": items, "count": len(items)})

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        viewer_is_staff = _viewer_is_staff(request)
        payload = self._serialize_card(obj, viewer_is_staff=viewer_is_staff)
        # 상세 진입 시 view_count + (staff 본인 제외)
        if not viewer_is_staff:
            from django.db.models import F
            PublicMatchupShowcase.objects.filter(pk=obj.pk).update(view_count=F("view_count") + 1)
        # PDF URL — 일반 외부 visible 시점에만 inclusion. staff는 항상.
        if payload["visible"]:
            payload["pdf_url"] = (
                f"/api/v1/landing-public/matchup-showcase/{obj.id}/pdf/"
                f"?tenant={request.tenant.code}"
            )
        else:
            payload["pdf_url"] = None
        return Response(payload)

    @action(detail=True, methods=["get"], url_path="pdf")
    @method_decorator(xframe_options_exempt)
    def pdf_stream(self, request, pk=None):
        """게시물 스냅샷 PDF stream. iframe embed 용 (xframe_exempt).

        - PUBLISHED + window 안: 비로그인 OK
        - 기간 밖 / DRAFT / HIDDEN: staff 만
        - 원본 R2 storage 객체를 in-memory로 fetch 후 반환 (signed CDN URL은 follow-up)
        """
        obj = self.get_object()
        viewer_is_staff = _viewer_is_staff(request)
        if not (obj.is_publicly_visible() or viewer_is_staff):
            return Response({"detail": "비공개"}, status=status.HTTP_403_FORBIDDEN)
        if not obj.snapshot_pdf_key:
            return Response({"detail": "스냅샷 없음"}, status=status.HTTP_404_NOT_FOUND)
        try:
            from apps.infrastructure.storage.r2 import get_object_bytes_r2_storage
            pdf_bytes = get_object_bytes_r2_storage(key=obj.snapshot_pdf_key)
        except Exception:
            logger.exception("matchup_showcase_pdf_fetch_failed id=%s", obj.id)
            return Response({"detail": "PDF 조회 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if pdf_bytes is None:
            return Response({"detail": "스냅샷 객체 없음"}, status=status.HTTP_404_NOT_FOUND)
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="matchup-showcase-{obj.id}.pdf"'
        resp["Cache-Control"] = "public, max-age=300"
        return resp

    @action(detail=False, methods=["post"], url_path="publish")
    def publish(self, request):
        """staff publish (1버튼). body:
        { hit_report_id, title?, description?, published_at?, published_until? }

        server-side PDF generate path. 학원장이 콘솔에서 작성한 적중보고서를
        그 자체로 게시 (서버가 curated PDF generate → R2 copy).
        """
        tenant = request.tenant
        try:
            hit_report_id = int(request.data.get("hit_report_id"))
        except (TypeError, ValueError):
            return Response({"detail": "hit_report_id 잘못됨."}, status=status.HTTP_400_BAD_REQUEST)

        from apps.domains.matchup.models import MatchupHitReport
        try:
            report = MatchupHitReport.objects.select_related("document").get(
                id=hit_report_id, tenant=tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return Response({"detail": "적중보고서 없음."}, status=status.HTTP_404_NOT_FOUND)

        title = (request.data.get("title") or "").strip() or (
            report.title or (report.document.title if report.document_id else "") or f"적중보고서 #{report.id}"
        )
        description = (request.data.get("description") or "").strip()
        published_at = _parse_dt(request.data.get("published_at")) or timezone.now()
        published_until = _parse_dt(request.data.get("published_until"))

        # snapshot 생성 (PDF generate → R2 upload)
        try:
            snapshot_key, snapshot_bytes, snapshot_meta = _build_snapshot_for_hit_report(tenant, hit_report_id)
        except Exception:
            logger.exception("matchup_showcase_snapshot_build_failed report=%s", hit_report_id)
            return Response({"detail": "스냅샷 생성 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        now = timezone.now()
        obj = PublicMatchupShowcase.objects.create(
            tenant=tenant,
            hit_report_id_ref=hit_report_id,
            title=title[:200],
            description=description,
            status=PublicMatchupShowcase.Status.PUBLISHED,
            published_at=published_at,
            published_until=published_until,
            snapshot_pdf_key=snapshot_key,
            snapshot_pdf_bytes=snapshot_bytes,
            snapshot_meta=snapshot_meta,
            snapshot_at=now,
            created_by=request.user if request.user.is_authenticated else None,
        )
        return Response(self._serialize_card(obj, viewer_is_staff=True), status=status.HTTP_201_CREATED)

    @action(
        detail=False,
        methods=["post"],
        url_path="publish-upload",
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def publish_upload(self, request):
        """학원장이 PC에서 직접 편집한 PDF 업로드 path (Phase #71, 2026-05-13).

        본질 (박철T 학원장 호소): 작성한 적중보고서 PDF를 다운받아 출처 부분 등을
        포토샵으로 지우고 다시 업로드 — "내가 만든 PDF 게시판에 직접 올림".

        multipart/form-data:
          - file (required): PDF 파일 (application/pdf, ≤20MB)
          - title (optional): 게시 제목 (비우면 파일명)
          - description (optional)
          - published_at / published_until (optional ISO)
          - source_hit_report_id (optional): 원본 적중보고서 ID 참조 (학원장이 어떤 보고서를
            편집했는지 추적용. server-side regenerate 안 함)
          - meta (optional JSON string): { hit_count, exam_count, document_title, author_name, ... }

        snapshot_pdf_key = 업로드한 그대로의 R2 key. server PDF generate 안 함.
        """
        tenant = request.tenant
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": "PDF 파일이 필요합니다 (field: file)."}, status=status.HTTP_400_BAD_REQUEST)
        # 크기 제한 20MB
        if upload.size > 20 * 1024 * 1024:
            return Response({"detail": "PDF는 20MB 이하만 업로드 가능합니다."}, status=status.HTTP_400_BAD_REQUEST)
        # content-type 또는 확장자 검증
        ct = (upload.content_type or "").lower()
        name = (upload.name or "").lower()
        if not (ct in ("application/pdf", "application/x-pdf") or name.endswith(".pdf")):
            return Response({"detail": "PDF 파일만 업로드 가능합니다."}, status=status.HTTP_400_BAD_REQUEST)

        title = (request.data.get("title") or "").strip() or (upload.name.rsplit(".", 1)[0] if upload.name else "게시물")
        description = (request.data.get("description") or "").strip()
        published_at = _parse_dt(request.data.get("published_at")) or timezone.now()
        published_until = _parse_dt(request.data.get("published_until"))

        source_hit_report_id: int | None = None
        raw_src = request.data.get("source_hit_report_id")
        if raw_src:
            try:
                source_hit_report_id = int(raw_src)
            except (TypeError, ValueError):
                source_hit_report_id = None

        # meta — optional JSON string 또는 dict
        meta: dict[str, Any] = {}
        raw_meta = request.data.get("meta")
        if raw_meta:
            try:
                if isinstance(raw_meta, str):
                    import json
                    meta = json.loads(raw_meta) or {}
                elif isinstance(raw_meta, dict):
                    meta = raw_meta
            except (ValueError, TypeError):
                meta = {}
        # 원본 보고서 참조 시 메타 일부 자동 채움 (학원장이 source_hit_report_id만 던지면 자동 enrich)
        if source_hit_report_id and not meta:
            try:
                from apps.domains.matchup.models import MatchupHitReport
                report = MatchupHitReport.objects.select_related("document", "author").filter(
                    id=source_hit_report_id, tenant=tenant,
                ).first()
                if report:
                    entries_qs = report.entries.all()
                    total_entries = entries_qs.count()
                    excluded = entries_qs.filter(excluded=True).count()
                    counted = total_entries - excluded
                    hit = sum(
                        1 for e in entries_qs
                        if not e.excluded and isinstance(e.selected_problem_ids, list)
                        and len(e.selected_problem_ids) > 0
                    )
                    meta = {
                        "document_title": (report.document.title or "") if report.document_id else "",
                        "document_id": report.document_id,
                        "author_name": (
                            report.author.name if report.author and getattr(report.author, "name", None)
                            else (report.submitted_by_name or "")
                        ),
                        "report_title": report.title or "",
                        "total_entries": total_entries,
                        "counted_entries": counted,
                        "hit_count": hit,
                        "hit_rate": round(hit / counted, 3) if counted else 0.0,
                        "source": "user_upload_with_ref",
                    }
            except Exception:
                logger.exception("matchup_showcase_meta_enrich_failed source=%s", source_hit_report_id)
        meta.setdefault("source", "user_upload")
        meta["snapshot_at_iso"] = timezone.now().isoformat()

        # R2 upload — 사용자 PDF 그대로
        try:
            from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
            now = timezone.now()
            key = (
                f"matchup-showcase-snapshots/tenant_{tenant.id}/"
                f"user_upload/{int(now.timestamp())}_{upload.name[:60]}"
            )
            # InMemory 또는 TemporaryUploaded — read() 후 BytesIO로 재포장 또는 직접 fileobj 사용
            upload.seek(0)
            upload_fileobj_to_r2_storage(
                fileobj=upload,
                key=key,
                content_type="application/pdf",
            )
            size = upload.size
        except Exception:
            logger.exception("matchup_showcase_user_pdf_upload_failed")
            return Response({"detail": "PDF 업로드 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        obj = PublicMatchupShowcase.objects.create(
            tenant=tenant,
            hit_report_id_ref=source_hit_report_id,
            title=title[:200],
            description=description,
            status=PublicMatchupShowcase.Status.PUBLISHED,
            published_at=published_at,
            published_until=published_until,
            snapshot_pdf_key=key,
            snapshot_pdf_bytes=size,
            snapshot_meta=meta,
            snapshot_at=timezone.now(),
            created_by=request.user if request.user.is_authenticated else None,
        )
        return Response(self._serialize_card(obj, viewer_is_staff=True), status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        """staff: title/description/published_at/published_until/status 만 수정.
        스냅샷(snapshot_pdf_key/snapshot_meta/snapshot_at)은 immutable.
        """
        obj = self.get_object()
        updates: dict[str, Any] = {}
        if "title" in request.data:
            v = (request.data.get("title") or "").strip()
            if not v:
                return Response({"detail": "title 비어있음."}, status=status.HTTP_400_BAD_REQUEST)
            updates["title"] = v[:200]
        if "description" in request.data:
            updates["description"] = (request.data.get("description") or "").strip()
        if "published_at" in request.data:
            updates["published_at"] = _parse_dt(request.data.get("published_at"))
        if "published_until" in request.data:
            updates["published_until"] = _parse_dt(request.data.get("published_until"))
        if "status" in request.data:
            v = (request.data.get("status") or "").strip()
            if v not in {c[0] for c in PublicMatchupShowcase.Status.choices}:
                return Response({"detail": "status 잘못됨."}, status=status.HTTP_400_BAD_REQUEST)
            updates["status"] = v
        if not updates:
            return Response({"detail": "변경 필드 없음."}, status=status.HTTP_400_BAD_REQUEST)
        for k, v in updates.items():
            setattr(obj, k, v)
        obj.save(update_fields=[*updates.keys(), "updated_at"])
        return Response(self._serialize_card(obj, viewer_is_staff=True))

    @action(detail=True, methods=["post"], url_path="unpublish")
    def unpublish(self, request, pk=None):
        obj = self.get_object()
        obj.status = PublicMatchupShowcase.Status.HIDDEN
        obj.save(update_fields=["status", "updated_at"])
        return Response(self._serialize_card(obj, viewer_is_staff=True))

    def destroy(self, request, *args, **kwargs):
        """soft delete — status HIDDEN으로 전환. snapshot 보존 (실제 R2 객체 삭제는 별도 cleanup)."""
        obj = self.get_object()
        obj.status = PublicMatchupShowcase.Status.HIDDEN
        obj.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

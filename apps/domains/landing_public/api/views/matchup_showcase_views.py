"""매치업 적중보고서 공개 게시판 ViewSet (Phase #69, 2026-05-13).

학원장이 작성 완료한 MatchupHitReport를 게시 시점에 PDF로 R2 snapshot copy → 게시판에 노출.
원본 보고서가 이후 변경되어도 게시물은 박힌 그대로.

URL:
  - POST   /api/v1/landing-public/matchup-showcase/publish/         (staff: hit_report_id → snapshot publish)
  - GET    /api/v1/landing-public/matchup-showcase/                  (public list, status+window 필터)
  - GET    /api/v1/landing-public/matchup-showcase/{id}/             (public detail, expired 시 카드만)
  - GET    /api/v1/landing-public/matchup-showcase/{id}/pdf/         (public PDF stream, xframe_exempt)
  - GET    /api/v1/landing-public/matchup-showcase/{id}/preview/     (public cached JPEG)
  - PATCH  /api/v1/landing-public/matchup-showcase/{id}/             (staff: title/desc/visibility)
  - POST   /api/v1/landing-public/matchup-showcase/{id}/unpublish/   (staff hide)
  - DELETE /api/v1/landing-public/matchup-showcase/{id}/             (staff: hide; soft)

학원장 데이터 immutable 정책 ([[project_matchup_immutable_policy_2026_05_06]]):
  - MatchupHitReport / MatchupHitReportEntry 본체는 READ ONLY (SELECT only)
  - PDF는 한 번 R2에 박히고 게시물 entity 가 별도 보관 — 원본 변동 무관 스냅샷
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from django.http import HttpResponse, StreamingHttpResponse
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.clickjacking import xframe_options_exempt
from django.utils.decorators import method_decorator
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.permissions import TenantResolved, TenantResolvedAndStaff, is_effective_staff
from apps.support.landing_public.matchup_preview import (
    delete_matchup_preview_assets,
    get_cached_matchup_preview,
    get_or_create_matchup_preview,
    preview_etag_for_pdf,
    render_matchup_pdf_preview,
    store_matchup_preview,
)
from apps.support.landing_public.matchup_showcase_dependencies import (
    build_matchup_snapshot_for_hit_report,
    get_matchup_hit_report_for_showcase,
    matchup_showcase_upload_meta_from_report,
)

from ...models import PublicMatchupShowcase
from ..serializers import PublicViewCountSerializer

logger = logging.getLogger(__name__)


def _lock_matchup_showcase_publish(*, tenant_id: int, hit_report_id: int) -> None:
    """Serialize one tenant/report publish boundary before any R2 write."""
    if connection.vendor != "postgresql":
        # Production is PostgreSQL-only. SQLite remains available for the isolated unit suite;
        # the PostgreSQL contract job exercises the real concurrent lock path.
        return
    digest = hashlib.blake2b(
        f"matchup-showcase:{tenant_id}:{hit_report_id}".encode(),
        digest_size=8,
    ).digest()
    lock_id = int.from_bytes(digest, byteorder="big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])


def _published_showcase_for_retry(*, tenant_id: int, hit_report_id: int):
    """Return the deterministic current/scheduled snapshot, excluding expired versions."""
    now = timezone.now()
    eligible = PublicMatchupShowcase.objects.filter(
        tenant_id=tenant_id,
        hit_report_id_ref=hit_report_id,
        status=PublicMatchupShowcase.Status.PUBLISHED,
    ).filter(Q(published_until__isnull=True) | Q(published_until__gt=now))
    current = (
        eligible.filter(Q(published_at__isnull=True) | Q(published_at__lte=now))
        .order_by("-published_at", "-created_at", "-pk")
        .first()
    )
    return current or eligible.order_by("published_at", "created_at", "pk").first()


def _matchup_upload_snapshot_key(*, tenant_id: int, file_name: str) -> str:
    """Return a collision-safe immutable snapshot key for a user PDF upload."""
    safe_name = file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:60] or "upload.pdf"
    return (
        f"matchup-showcase-snapshots/tenant_{tenant_id}/user_upload/"
        f"{uuid.uuid4().hex}_{safe_name}"
    )


def _viewer_is_staff(request) -> bool:
    """학원 staff 인지 판단 (TenantResolved 통과 전제)."""
    user = request.user
    if not user.is_authenticated:
        return False
    tenant = getattr(request, "tenant", None)
    return is_effective_staff(user, tenant)


def _parse_dt_strict(raw: Any, field_name: str):
    if raw in (None, ""):
        return None, None
    if isinstance(raw, str):
        parsed = parse_datetime(raw)
        if parsed is not None:
            return parsed, None
    return None, Response(
        {field_name: "날짜/시간 형식이 잘못되었습니다. ISO 8601 형식으로 입력해 주세요."},
        status=status.HTTP_400_BAD_REQUEST,
    )


@method_decorator(xframe_options_exempt, name="dispatch")
class PublicMatchupShowcaseViewSet(viewsets.GenericViewSet):
    """공개 매치업 적중보고서 게시판.

    list/retrieve/pdf_stream/preview_image: 비로그인 OK (PUBLISHED + window 만 노출 / EXPIRED는 카드만)
    publish/unpublish/destroy/partial_update: staff (owner/admin) only

    xframe_exempt: pdf_stream 학생 카톡 iframe embed 용. DRF action method-level
    @method_decorator는 dispatch 우회되어 미작동 — class-level dispatch decorator로 강제.
    (commit 4638d55a)
    """

    queryset = PublicMatchupShowcase.objects.all()

    def get_permissions(self):
        if self.action in ("list", "retrieve", "record_view", "pdf_stream", "preview_image"):
            return [TenantResolved()]
        return [TenantResolvedAndStaff()]

    def _resolve_tenant_for_public_access(self):
        """학생 카톡 share URL (iframe PDF) 비로그인 진입 path 보장.

        - iframe.src 는 browser native fetch — custom header X-Tenant-Code 박을 수 없음
        - retrieve 응답의 pdf_url 은 `?tenant=<code>` query param 만 박혀있음
        - middleware `_resolve_tenant_from_header` 는 query param 인식 안 함 → 비로그인 학생 click 시 404

        Fix: query param `?tenant=<code>` 도 인식. tenant scoped queryset 이므로 cross-tenant leak X.
        """
        tenant = getattr(self.request, "tenant", None)
        if tenant:
            return tenant
        code = (self.request.GET.get("tenant") or "").strip()
        if not code:
            return None
        from academy.adapters.db.django import repositories_core as core_repo
        resolved = core_repo.tenant_get_by_code(code)
        if resolved:
            # downstream (retrieve _serialize_card / pdf_url 빌드) 가 request.tenant 참조 — 채워넣음
            self.request.tenant = resolved
        return resolved

    def get_queryset(self):
        tenant = self._resolve_tenant_for_public_access()
        if not tenant:
            return PublicMatchupShowcase.objects.none()
        qs = PublicMatchupShowcase.objects.filter(tenant=tenant)
        if not _viewer_is_staff(self.request):
            now = timezone.now()
            has_snapshot = ~Q(snapshot_pdf_key="") & Q(snapshot_at__isnull=False)
            started = Q(published_at__isnull=True) | Q(published_at__lte=now)
            qs = qs.filter(
                Q(status=PublicMatchupShowcase.Status.EXPIRED)
                | (
                    Q(status=PublicMatchupShowcase.Status.PUBLISHED)
                    & started
                    & has_snapshot
                )
            )
        return qs.order_by("-published_at", "-created_at")

    def _serialize_card(self, obj: PublicMatchupShowcase, *, viewer_is_staff: bool) -> dict:
        """카드 메타 (list / expired retrieve)."""
        now = timezone.now()
        expired = bool(obj.published_until and now > obj.published_until)
        visible = obj.is_publicly_visible() or viewer_is_staff
        payload = {
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
        if visible:
            tenant_code = self.request.tenant.code
            base = f"/api/v1/landing-public/matchup-showcase/{obj.id}"
            payload["pdf_url"] = f"{base}/pdf/?tenant={tenant_code}"
            payload["preview_url"] = f"{base}/preview/?tenant={tenant_code}"
        else:
            payload["pdf_url"] = None
            payload["preview_url"] = None
        return payload

    def list(self, request, *args, **kwargs):
        viewer_is_staff = _viewer_is_staff(request)
        qs = self.get_queryset()
        items = [self._serialize_card(o, viewer_is_staff=viewer_is_staff) for o in qs[:50]]
        return Response({"results": items, "count": len(items)})

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        viewer_is_staff = _viewer_is_staff(request)
        return Response(self._serialize_card(obj, viewer_is_staff=viewer_is_staff))

    @extend_schema(request=None, responses={200: PublicViewCountSerializer})
    @action(detail=True, methods=["post"], url_path="view")
    def record_view(self, request, pk=None):
        obj = self.get_object()
        if not _viewer_is_staff(request):
            from django.db.models import F

            PublicMatchupShowcase.objects.filter(pk=obj.pk).update(view_count=F("view_count") + 1)
            obj.refresh_from_db(fields=["view_count"])
        return Response({"view_count": obj.view_count})

    @action(detail=True, methods=["get"], url_path="pdf")
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
        resp["Cache-Control"] = "private, must-revalidate"
        return resp

    @action(detail=True, methods=["get"], url_path="preview")
    def preview_image(self, request, pk=None):
        """Return one cached JPEG comparison page instead of browser PDF rendering."""
        obj = self.get_object()
        viewer_is_staff = _viewer_is_staff(request)
        if not (obj.is_publicly_visible() or viewer_is_staff):
            return Response({"detail": "비공개"}, status=status.HTTP_403_FORBIDDEN)
        if not obj.snapshot_pdf_key:
            return Response({"detail": "스냅샷 없음"}, status=status.HTTP_404_NOT_FOUND)

        etag = preview_etag_for_pdf(
            obj.snapshot_pdf_key,
            namespace="showcase-preview",
        )
        if request.META.get("HTTP_IF_NONE_MATCH") == etag:
            response = HttpResponse(status=304)
            response["ETag"] = etag
            response["Cache-Control"] = "private, must-revalidate"
            return response

        preview_bytes = get_cached_matchup_preview(pdf_key=obj.snapshot_pdf_key)
        if not preview_bytes:
            response = Response(
                {"detail": "미리보기 준비 중"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
            response["Cache-Control"] = "no-store"
            response["Retry-After"] = "30"
            return response

        response = HttpResponse(preview_bytes, content_type="image/jpeg")
        response["Content-Disposition"] = f'inline; filename="matchup-showcase-{obj.id}-preview.jpg"'
        response["Cache-Control"] = "private, must-revalidate"
        response["ETag"] = etag
        response["X-Matchup-Preview-Cache"] = "hit"
        return response

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

        report = get_matchup_hit_report_for_showcase(
            tenant=tenant,
            hit_report_id=hit_report_id,
        )
        if not report:
            return Response({"detail": "적중보고서 없음."}, status=status.HTTP_404_NOT_FOUND)

        title = (request.data.get("title") or "").strip() or (
            report.title or (report.document.title if report.document_id else "") or f"적중보고서 #{report.id}"
        )
        description = (request.data.get("description") or "").strip()
        published_at, error_response = _parse_dt_strict(request.data.get("published_at"), "published_at")
        if error_response is not None:
            return error_response
        published_until, error_response = _parse_dt_strict(request.data.get("published_until"), "published_until")
        if error_response is not None:
            return error_response
        published_at = published_at or timezone.now()

        with transaction.atomic():
            _lock_matchup_showcase_publish(
                tenant_id=tenant.id,
                hit_report_id=hit_report_id,
            )
            existing = _published_showcase_for_retry(
                tenant_id=tenant.id,
                hit_report_id=hit_report_id,
            )
            if existing is not None:
                duplicate_count = (
                    PublicMatchupShowcase.objects.filter(
                        tenant_id=tenant.id,
                        hit_report_id_ref=hit_report_id,
                        status=PublicMatchupShowcase.Status.PUBLISHED,
                    )
                    .filter(
                        Q(published_until__isnull=True)
                        | Q(published_until__gt=timezone.now())
                    )
                    .count()
                )
                if duplicate_count > 1:
                    logger.error(
                        "matchup_showcase_existing_duplicate tenant=%s report=%s count=%s survivor=%s",
                        tenant.id,
                        hit_report_id,
                        duplicate_count,
                        existing.id,
                    )
                return Response(
                    self._serialize_card(existing, viewer_is_staff=True),
                    status=status.HTTP_200_OK,
                    headers={"X-Idempotent-Replay": "true"},
                )

            # The exact advisory lock stays held across snapshot generation and row creation.
            # A retry therefore observes the committed row before it can write another R2 object.
            try:
                snapshot_key, snapshot_bytes, snapshot_meta = build_matchup_snapshot_for_hit_report(
                    tenant,
                    hit_report_id,
                )
            except Exception:
                logger.exception("matchup_showcase_snapshot_build_failed report=%s", hit_report_id)
                return Response({"detail": "스냅샷 생성 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            now = timezone.now()
            try:
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
            except Exception:
                delete_matchup_preview_assets(pdf_key=snapshot_key)
                logger.exception("matchup_showcase_snapshot_publish_failed report=%s", hit_report_id)
                return Response(
                    {"detail": "스냅샷 게시 실패"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        published_at, error_response = _parse_dt_strict(request.data.get("published_at"), "published_at")
        if error_response is not None:
            return error_response
        published_until, error_response = _parse_dt_strict(request.data.get("published_until"), "published_until")
        if error_response is not None:
            return error_response
        published_at = published_at or timezone.now()

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
                meta = matchup_showcase_upload_meta_from_report(
                    tenant=tenant,
                    hit_report_id=source_hit_report_id,
                )
            except Exception:
                logger.exception("matchup_showcase_meta_enrich_failed source=%s", source_hit_report_id)
        # The preview-page policy is server-owned. Never let client metadata
        # turn an uploaded PDF into the generated-report cover-skip path.
        meta["source"] = "user_upload"
        meta["snapshot_at_iso"] = timezone.now().isoformat()

        upload.seek(0)
        pdf_bytes = upload.read()
        upload.seek(0)
        try:
            if b"%PDF-" not in pdf_bytes[:1024]:
                raise ValueError("missing PDF header")
            preview_bytes = render_matchup_pdf_preview(
                pdf_bytes,
                first_body_page=False,
            )
        except Exception:
            return Response(
                {"detail": "열 수 있는 PDF 파일을 올려 주세요. 암호화되거나 손상된 파일은 지원하지 않습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # R2 upload — 검증한 사용자 PDF와 대표 JPEG를 함께 저장
        key = ""
        try:
            from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
            key = _matchup_upload_snapshot_key(
                tenant_id=tenant.id,
                file_name=upload.name,
            )
            upload_fileobj_to_r2_storage(
                fileobj=upload,
                key=key,
                content_type="application/pdf",
            )
            store_matchup_preview(
                pdf_key=key,
                preview_bytes=preview_bytes,
            )
            size = len(pdf_bytes)
        except Exception:
            if key:
                delete_matchup_preview_assets(pdf_key=key)
            logger.exception("matchup_showcase_user_pdf_upload_failed")
            return Response({"detail": "PDF 업로드 실패"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
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
        except Exception:
            delete_matchup_preview_assets(pdf_key=key)
            logger.exception("matchup_showcase_user_pdf_publish_failed tenant=%s", tenant.id)
            return Response(
                {"detail": "PDF 게시 실패"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
            parsed, error_response = _parse_dt_strict(request.data.get("published_at"), "published_at")
            if error_response is not None:
                return error_response
            updates["published_at"] = parsed
        if "published_until" in request.data:
            parsed, error_response = _parse_dt_strict(request.data.get("published_until"), "published_until")
            if error_response is not None:
                return error_response
            updates["published_until"] = parsed
        if "status" in request.data:
            v = (request.data.get("status") or "").strip()
            if v not in {c[0] for c in PublicMatchupShowcase.Status.choices}:
                return Response({"detail": "status 잘못됨."}, status=status.HTTP_400_BAD_REQUEST)
            if v == PublicMatchupShowcase.Status.PUBLISHED:
                if not obj.snapshot_pdf_key or not obj.snapshot_at:
                    return Response({"detail": "스냅샷이 없는 게시물은 공개할 수 없습니다."}, status=status.HTTP_400_BAD_REQUEST)
                try:
                    from apps.infrastructure.storage.r2 import get_object_bytes_r2_storage

                    def load_pdf_bytes():
                        data = get_object_bytes_r2_storage(key=obj.snapshot_pdf_key)
                        if data is None:
                            raise FileNotFoundError("snapshot PDF missing")
                        return data

                    source = str((obj.snapshot_meta or {}).get("source") or "")
                    get_or_create_matchup_preview(
                        pdf_key=obj.snapshot_pdf_key,
                        load_pdf_bytes=load_pdf_bytes,
                        first_body_page=not source.startswith("user_upload"),
                        require_cache_write=True,
                    )
                except Exception:
                    logger.exception(
                        "matchup_showcase_republish_preview_failed id=%s",
                        obj.id,
                    )
                    return Response(
                        {"detail": "대표 비교 화면을 준비하지 못했습니다."},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )
                if "published_at" not in request.data and obj.published_at is None:
                    updates["published_at"] = timezone.now()
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

from django.db.models import F, Q
from django.http import HttpResponse

try:
    from drf_spectacular.types import OpenApiTypes
    from drf_spectacular.utils import extend_schema
except ModuleNotFoundError as exc:
    if exc.name != "drf_spectacular":
        raise

    class OpenApiTypes:  # type: ignore[no-redef]
        BINARY = bytes

    def extend_schema(*args, **kwargs):  # type: ignore[no-redef]
        def decorator(view):
            return view

        return decorator
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from academy.adapters.db.django import repositories_core as core_repo
from apps.core.permissions import is_effective_staff
from apps.infrastructure.storage.r2 import get_object_bytes_r2_storage

from ...models import PublicProblemReviewShowcase
from ..serializers import PublicProblemReviewShowcaseSerializer


LEGACY_COMPATIBILITY_MARKER = "pre-verification-publication"


class PublicProblemReviewShowcaseViewSet(viewsets.GenericViewSet):
    """Public, tenant-scoped snapshots of teacher-reviewed exam analyses."""

    permission_classes = [AllowAny]
    queryset = PublicProblemReviewShowcase.objects.all()
    serializer_class = PublicProblemReviewShowcaseSerializer

    def _resolve_tenant(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant:
            return tenant
        code = (self.request.GET.get("tenant") or "").strip()
        if not code:
            return None
        tenant = core_repo.tenant_get_by_code(code)
        if tenant:
            self.request.tenant = tenant
        return tenant

    def get_queryset(self):
        tenant = self._resolve_tenant()
        if not tenant:
            return PublicProblemReviewShowcase.objects.none()
        queryset = PublicProblemReviewShowcase.objects.filter(tenant=tenant)
        if not is_effective_staff(self.request.user, tenant):
            queryset = queryset.filter(
                status=PublicProblemReviewShowcase.Status.PUBLISHED,
                published_at__isnull=False,
                snapshot_at__isnull=False,
            ).filter(
                Q(snapshot__verification__status="verified")
                | Q(
                    snapshot__verification__status="legacy_published",
                    snapshot__verification__compatibility=LEGACY_COMPATIBILITY_MARKER,
                )
            )
        return queryset.order_by("-published_at", "-created_at")

    def _serialize(self, obj: PublicProblemReviewShowcase, *, include_snapshot: bool) -> dict:
        tenant_code = obj.tenant.code
        payload = {
            "id": obj.id,
            "title": obj.title,
            "description": obj.description,
            "status": obj.status,
            "published_at": obj.published_at.isoformat() if obj.published_at else None,
            "snapshot_at": obj.snapshot_at.isoformat() if obj.snapshot_at else None,
            "view_count": obj.view_count,
            "pdf_url": (
                f"/api/v1/landing-public/problem-review-showcase/{obj.id}/pdf/?tenant={tenant_code}"
                if obj.snapshot_pdf_key
                else None
            ),
        }
        snapshot = obj.snapshot if isinstance(obj.snapshot, dict) else {}
        payload["metadata"] = snapshot.get("metadata") or {}
        payload["summary"] = snapshot.get("summary") or {}
        payload["difficulty"] = snapshot.get("difficulty") or {}
        if include_snapshot:
            payload["snapshot"] = snapshot
        return payload

    def list(self, request, *args, **kwargs):
        items = [self._serialize(item, include_snapshot=False) for item in self.get_queryset()[:30]]
        return Response({"results": items, "count": len(items)})

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        if not is_effective_staff(request.user, obj.tenant):
            PublicProblemReviewShowcase.objects.filter(pk=obj.pk).update(view_count=F("view_count") + 1)
            obj.refresh_from_db(fields=["view_count"])
        return Response(self._serialize(obj, include_snapshot=True))

    @action(detail=True, methods=["get"], url_path="pdf")
    @extend_schema(responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    def pdf_stream(self, request, pk=None):
        obj = self.get_object()
        if not obj.snapshot_pdf_key:
            return Response({"detail": "공개 PDF가 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        try:
            pdf_bytes = get_object_bytes_r2_storage(
                key=obj.snapshot_pdf_key,
                max_bytes=20 * 1024 * 1024,
            )
        except Exception:
            return Response({"detail": "PDF를 불러오지 못했습니다."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if pdf_bytes is None:
            return Response({"detail": "공개 PDF가 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="problem-analysis-{obj.id}.pdf"'
        response["Cache-Control"] = (
            "public, max-age=300"
            if obj.status == PublicProblemReviewShowcase.Status.PUBLISHED
            else "private, no-store"
        )
        return response

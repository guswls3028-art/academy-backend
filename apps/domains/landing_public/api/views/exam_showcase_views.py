"""성적 통계 ViewSet — Phase #13.

- POST /api/v1/landing-public/showcase/publish/ — staff publish (1버튼)
- GET /api/v1/landing-public/showcase/ — 외부 list (status=published, published_until 체크)
- GET /api/v1/landing-public/showcase/{id}/ — 외부 detail (rows + summary)
- POST /api/v1/landing-public/showcase/{id}/unpublish/ — staff
- DELETE /api/v1/landing-public/showcase/{id}/ — staff hide
- POST /api/v1/landing-public/showcase/{id}/refresh/ — staff snapshot 재생성
"""
from datetime import date

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import TenantResolved, TenantResolvedAndStaff

from ...models import PublicExamShowcase
from ...services.exam_showcase_builder import build_showcase_snapshot


class PublicExamShowcaseViewSet(viewsets.GenericViewSet):
    """공개 시험 성적 showcase.

    list/retrieve: 비로그인 OK (published 만, expired 시 list 메타만)
    publish/unpublish/refresh/destroy: staff only
    """

    queryset = PublicExamShowcase.objects.all()

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [TenantResolved()]
        return [TenantResolvedAndStaff()]

    def _viewer_is_staff(self, request) -> bool:
        """학원 staff(owner/admin/staff/teacher) 인지. list/retrieve 일관 사용.
        이전 retrieve 가 동일 roles 검사하던 것과 정합. owner/admin 외 staff/teacher 도
        자기 학원 미공개 showcase 풀 열람 권한 보유 (학원장 spec — 학원 내부 운영진).
        """
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
                staff_roles=("owner", "admin", "staff", "teacher"),
            )
        except Exception:
            return False

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return PublicExamShowcase.objects.none()
        qs = PublicExamShowcase.objects.filter(tenant=tenant)
        # 외부 시점: published 또는 expired 만 노출. staff(owner/admin)는 draft/hidden 까지.
        # 이전: is_superuser만 검사해 학원 owner/admin이 자기 draft 못 봤음(retrieve 와 정합 X).
        if not self._viewer_is_staff(self.request):
            qs = qs.filter(status__in=[
                PublicExamShowcase.Status.PUBLISHED,
                PublicExamShowcase.Status.EXPIRED,
            ])
        return qs.order_by("-published_at", "-created_at")

    def _is_expired(self, obj: PublicExamShowcase) -> bool:
        if not obj.published_until:
            return False
        return date.today() > obj.published_until

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        items = []
        for obj in qs[:50]:
            expired = self._is_expired(obj)
            items.append({
                "id": obj.id,
                "title": obj.title,
                "anonymization_mode": obj.anonymization_mode,
                "status": "expired" if expired else obj.status,
                "published_at": obj.published_at.isoformat() if obj.published_at else None,
                "published_until": obj.published_until.isoformat() if obj.published_until else None,
                "summary": obj.summary,
                "view_count": obj.view_count,
                "expired": expired,
            })
        return Response({"results": items, "count": len(items)})

    def retrieve(self, request, *args, **kwargs):
        obj = self.get_object()
        expired = self._is_expired(obj)
        # expired 시 rows 노출 차단 (staff 만 열람 가능). list 와 동일한 _viewer_is_staff 사용.
        viewer_is_staff = self._viewer_is_staff(request)

        # view_count + (작성자 본인 제외 추후). P2 audit: refresh로 stale -1 회피.
        if not viewer_is_staff:
            from django.db.models import F
            PublicExamShowcase.objects.filter(pk=obj.pk).update(view_count=F("view_count") + 1)
            obj.refresh_from_db(fields=["view_count"])

        payload = {
            "id": obj.id,
            "title": obj.title,
            "description": obj.description,
            "anonymization_mode": obj.anonymization_mode,
            "status": "expired" if (expired and not viewer_is_staff) else obj.status,
            "published_at": obj.published_at.isoformat() if obj.published_at else None,
            "published_until": obj.published_until.isoformat() if obj.published_until else None,
            "summary": obj.summary,
            "view_count": obj.view_count,
            "expired": expired,
            # rows: expired 시 외부 차단, staff/작성자는 영구 열람
            "rows": obj.rows if (viewer_is_staff or not expired) else [],
        }
        return Response(payload)

    @action(detail=False, methods=["post"], url_path="publish")
    def publish(self, request):
        """staff 1버튼 publish. body:
        { exam_id, title, description?, anonymization_mode?, published_until? }
        """
        tenant = request.tenant
        try:
            exam_id = int(request.data.get("exam_id"))
        except (TypeError, ValueError):
            return Response({"detail": "exam_id 잘못됨."}, status=status.HTTP_400_BAD_REQUEST)
        title = (request.data.get("title") or "").strip()
        if not title:
            return Response({"detail": "title 필수."}, status=status.HTTP_400_BAD_REQUEST)
        description = (request.data.get("description") or "").strip()
        # P1 audit (2026-05-14): 익명화 default 강화 — pseudonym 권장.
        # 이전: default "initial" (성+○○). 작은 학원(<10명)에서 박씨 1명이면 즉시 식별.
        # 학원장이 명시 mode 안 주면 "pseudonym" — small-N PII 보호.
        anonymization_mode = (request.data.get("anonymization_mode") or "pseudonym").strip()
        if anonymization_mode not in {c[0] for c in PublicExamShowcase.AnonymizationMode.choices}:
            return Response({"detail": "anonymization_mode 잘못됨."}, status=status.HTTP_400_BAD_REQUEST)
        published_until_raw = request.data.get("published_until")
        published_until = None
        if published_until_raw:
            try:
                published_until = date.fromisoformat(str(published_until_raw)[:10])
            except (TypeError, ValueError):
                return Response({"detail": "published_until 형식 잘못됨 (YYYY-MM-DD)."}, status=status.HTTP_400_BAD_REQUEST)

        # snapshot build
        try:
            rows, summary = build_showcase_snapshot(
                tenant=tenant, exam_id=exam_id, anonymization_mode=anonymization_mode,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        obj = PublicExamShowcase.objects.create(
            tenant=tenant,
            exam_id_ref=exam_id,
            title=title,
            description=description,
            anonymization_mode=anonymization_mode,
            status=PublicExamShowcase.Status.PUBLISHED,
            published_at=now,
            published_until=published_until,
            rows=rows,
            summary=summary,
            snapshot_at=now,
            created_by=request.user,
        )
        return Response({
            "id": obj.id,
            "title": obj.title,
            "status": obj.status,
            "summary": obj.summary,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="unpublish")
    def unpublish(self, request, pk=None):
        obj = self.get_object()
        obj.status = PublicExamShowcase.Status.HIDDEN
        obj.save(update_fields=["status", "updated_at"])
        return Response({"id": obj.id, "status": obj.status})

    @action(detail=True, methods=["post"], url_path="refresh")
    def refresh_snapshot(self, request, pk=None):
        """staff 가 수동으로 snapshot 재생성 (점수 수정 후 갱신)."""
        obj = self.get_object()
        if not obj.exam_id_ref:
            return Response({"detail": "원본 exam_id 없음. 재생성 불가."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            rows, summary = build_showcase_snapshot(
                tenant=request.tenant, exam_id=obj.exam_id_ref,
                anonymization_mode=obj.anonymization_mode,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        obj.rows = rows
        obj.summary = summary
        obj.snapshot_at = timezone.now()
        obj.save(update_fields=["rows", "summary", "snapshot_at", "updated_at"])
        return Response({"id": obj.id, "summary": obj.summary})

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        obj.status = PublicExamShowcase.Status.HIDDEN
        obj.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

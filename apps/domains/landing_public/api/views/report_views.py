"""신고/블랙리스트 ViewSet — Phase 4-B.

- POST /api/v1/landing-public/reports/ — 누구나(비로그인 OK) 신고 가능. tenant 격리.
- GET /api/v1/landing-public/reports/ — staff inbox. pending list (status 필터).
- POST /api/v1/landing-public/reports/{id}/review/ — staff 처리(action_taken).
- GET /api/v1/landing-public/reports/summary/ — pending count badge용.

블랙리스트 (PublicUserBlock):
- GET/POST/DELETE /api/v1/landing-public/blocks/ — staff only.
"""
from django.db import transaction
from django.utils import timezone
from rest_framework import status as drf_status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolved, TenantResolvedAndMember, TenantResolvedAndStaff

from ..serializers import _resolve_role, _is_staff_role
from ...models import (
    PublicBoardPost,
    PublicPostReply,
    PublicReport,
    PublicReview,
    PublicUserBlock,
)


def _is_user_in_tenant(tenant, user) -> bool:
    """user가 본 학원 멤버인지 확인 (cross-tenant 블랙리스트 차단용, P0 audit 2026-05-13).

    1) 핵사고날 repo의 membership_exists 우선 — 정도 패턴.
    2) fallback: user.tenant_id == tenant.id (legacy User 모델).
    """
    if user is None or tenant is None:
        return False
    try:
        from academy.adapters.db.django import repositories_core as core_repo
        if core_repo.membership_exists(tenant=tenant, user=user):
            return True
    except Exception:
        pass
    return getattr(user, "tenant_id", None) == getattr(tenant, "id", None)


def _get_client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return (request.META.get("REMOTE_ADDR") or "")[:45] or None


def _target_queryset(tenant, kind: str, target_id: int):
    if kind == PublicReport.TargetKind.BOARD:
        return PublicBoardPost.objects.filter(tenant=tenant, pk=target_id)
    if kind == PublicReport.TargetKind.REVIEW:
        return PublicReview.objects.filter(tenant=tenant, pk=target_id)
    if kind == PublicReport.TargetKind.REPLY:
        return PublicPostReply.objects.filter(tenant=tenant, pk=target_id)
    return None


class PublicReportViewSet(viewsets.GenericViewSet):
    """신고 접수 + staff 처리.

    POST create: 비로그인 OK (TenantResolved). 동일 IP의 중복 신고는 최근 1시간 내 1건 제한.
    list/summary/review: staff only.
    """

    queryset = PublicReport.objects.all()

    def get_permissions(self):
        if self.action == "create":
            return [TenantResolved()]
        return [TenantResolvedAndStaff()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return PublicReport.objects.none()
        qs = PublicReport.objects.filter(tenant=tenant)
        st = (self.request.query_params.get("status") or "").strip()
        if st in (PublicReport.Status.PENDING, PublicReport.Status.REVIEWED, PublicReport.Status.DISMISSED):
            qs = qs.filter(status=st)
        kind = (self.request.query_params.get("target_kind") or "").strip()
        if kind:
            qs = qs.filter(target_kind=kind)
        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        tenant = request.tenant
        kind = (request.data.get("target_kind") or "").strip()
        target_id_raw = request.data.get("target_id")
        reason = (request.data.get("reason") or "").strip()
        description = (request.data.get("description") or "").strip()

        if kind not in {PublicReport.TargetKind.BOARD, PublicReport.TargetKind.REVIEW, PublicReport.TargetKind.REPLY}:
            return Response({"detail": "target_kind 잘못됨"}, status=drf_status.HTTP_400_BAD_REQUEST)
        try:
            target_id = int(target_id_raw)
        except (TypeError, ValueError):
            return Response({"detail": "target_id 잘못됨"}, status=drf_status.HTTP_400_BAD_REQUEST)
        if reason not in {c[0] for c in PublicReport.Reason.choices}:
            return Response({"detail": "reason 잘못됨"}, status=drf_status.HTTP_400_BAD_REQUEST)
        ip = _get_client_ip(request)
        user = request.user if request.user.is_authenticated else None

        with transaction.atomic():
            target_qs = _target_queryset(tenant, kind, target_id)
            if target_qs is None or target_qs.select_for_update().first() is None:
                return Response({"detail": "대상이 존재하지 않습니다"}, status=drf_status.HTTP_404_NOT_FOUND)

            # 스팸 방지: 동일 IP + 동일 target에 최근 1시간 내 신고 1건 제한
            from datetime import timedelta
            recent_cut = timezone.now() - timedelta(hours=1)
            dup_q = PublicReport.objects.filter(
                tenant=tenant, target_kind=kind, target_id=target_id, created_at__gte=recent_cut,
            )
            if user is not None:
                dup_q = dup_q.filter(reporter=user)
            elif ip:
                dup_q = dup_q.filter(reporter_ip=ip)
            if dup_q.exists():
                return Response({"detail": "이미 신고된 글입니다. 학원장이 곧 검토합니다."}, status=drf_status.HTTP_409_CONFLICT)

            # P2 audit (2026-05-14): 로그인 유저도 reporter_ip 저장 — 사후 spam 패턴 분석
            # (단일 user가 동일 IP로 다수 신고 등) 시 admin UI 노출은 별도 정책 (현재 모두 노출).
            # 익명/로그인 모두 ip 보존 → reporter+ip dedup 일관성.
            obj = PublicReport.objects.create(
                tenant=tenant,
                target_kind=kind,
                target_id=target_id,
                reporter=user,
                reporter_ip=ip,
                reason=reason,
                description=description[:2000],
            )
        return Response({"id": obj.id, "status": obj.status}, status=drf_status.HTTP_201_CREATED)

    def list(self, request, *args, **kwargs):
        tenant = request.tenant
        qs = self.get_queryset()
        page = self.paginate_queryset(qs)
        items = page if page is not None else qs[:50]
        # target preview inline (제목/카테고리)
        # tenant 필터 명시 — target_id가 PositiveIntegerField(FK 아님)라 future drift 방어
        board_ids = [r.target_id for r in items if r.target_kind == PublicReport.TargetKind.BOARD]
        review_ids = [r.target_id for r in items if r.target_kind == PublicReport.TargetKind.REVIEW]
        reply_ids = [r.target_id for r in items if r.target_kind == PublicReport.TargetKind.REPLY]
        boards = {p.id: p for p in PublicBoardPost.objects.filter(tenant=tenant, pk__in=board_ids).only("id", "title", "status")}
        reviews = {p.id: p for p in PublicReview.objects.filter(tenant=tenant, pk__in=review_ids).only("id", "title", "rating", "status")}
        replies = {p.id: p for p in PublicPostReply.objects.filter(tenant=tenant, pk__in=reply_ids).only("id", "content", "is_hidden")}

        results = []
        for r in items:
            preview = None
            tstatus = None
            if r.target_kind == PublicReport.TargetKind.BOARD:
                b = boards.get(r.target_id)
                if b:
                    preview = b.title; tstatus = b.status
            elif r.target_kind == PublicReport.TargetKind.REVIEW:
                v = reviews.get(r.target_id)
                if v:
                    preview = v.title or f"★ {v.rating}"; tstatus = v.status
            else:
                rp = replies.get(r.target_id)
                if rp:
                    preview = (rp.content or "")[:80]; tstatus = "hidden" if rp.is_hidden else "visible"
            results.append({
                "id": r.id,
                "target_kind": r.target_kind,
                "target_id": r.target_id,
                "target_preview": preview,
                "target_status": tstatus,
                "reason": r.reason,
                "description": r.description,
                "status": r.status,
                "action_taken": r.action_taken,
                "reporter_id": r.reporter_id,
                "reporter_ip": r.reporter_ip,
                "reviewed_by_id": r.reviewed_by_id,
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                "created_at": r.created_at.isoformat(),
            })
        if page is not None:
            return self.get_paginated_response(results)
        return Response({"results": results, "count": len(results)})

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        tenant = request.tenant
        pending = PublicReport.objects.filter(tenant=tenant, status=PublicReport.Status.PENDING).count()
        pending_review = PublicReview.objects.filter(tenant=tenant, status=PublicReview.Status.PENDING).count()
        return Response({
            "pending_reports": pending,
            "pending_reviews": pending_review,
        })

    @action(detail=True, methods=["post"], url_path="review")
    def review_report(self, request, pk=None):
        """staff가 신고 처리 — action: reviewed(처리완료) / dismissed(기각).
        body: { action: 'reviewed'|'dismissed', target_action?: 'hide'|'reject' }
        target_action 지정 시 대상 모델까지 함께 hidden 처리.
        """
        obj = self.get_object()
        if obj.status != PublicReport.Status.PENDING:
            return Response({"detail": "이미 처리된 신고입니다."}, status=drf_status.HTTP_400_BAD_REQUEST)
        action_name = (request.data.get("action") or "").strip()
        if action_name not in (PublicReport.Status.REVIEWED, PublicReport.Status.DISMISSED):
            return Response({"detail": "action 잘못됨(reviewed/dismissed)"}, status=drf_status.HTTP_400_BAD_REQUEST)

        target_action = (request.data.get("target_action") or "").strip()
        action_taken_label = ""
        if action_name == PublicReport.Status.REVIEWED and target_action:
            tenant = request.tenant
            if obj.target_kind == PublicReport.TargetKind.BOARD and target_action == "hide":
                PublicBoardPost.objects.filter(tenant=tenant, pk=obj.target_id).update(
                    status=PublicBoardPost.Status.HIDDEN, moderated_by=request.user, moderated_at=timezone.now(),
                )
                action_taken_label = "board:hidden"
            elif obj.target_kind == PublicReport.TargetKind.REVIEW and target_action in ("hide", "reject"):
                new_status = PublicReview.Status.REJECTED if target_action == "reject" else PublicReview.Status.HIDDEN
                PublicReview.objects.filter(tenant=tenant, pk=obj.target_id).update(
                    status=new_status, reviewed_by=request.user, reviewed_at=timezone.now(),
                )
                action_taken_label = f"review:{new_status}"
            elif obj.target_kind == PublicReport.TargetKind.REPLY and target_action == "hide":
                PublicPostReply.objects.filter(tenant=tenant, pk=obj.target_id).update(is_hidden=True)
                action_taken_label = "reply:hidden"

        obj.status = action_name
        obj.reviewed_by = request.user
        obj.reviewed_at = timezone.now()
        obj.action_taken = action_taken_label or action_name
        obj.save(update_fields=["status", "reviewed_by", "reviewed_at", "action_taken", "updated_at"])
        return Response({"id": obj.id, "status": obj.status, "action_taken": obj.action_taken})


class PublicUserBlockView(APIView):
    """staff 전용 — 작성자 차단/해제/목록.

    GET → 활성 차단 목록.
    POST { user_id, reason? } → 차단.
    DELETE { user_id } → 차단 해제.
    """

    permission_classes = [TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        rows = PublicUserBlock.objects.filter(tenant=tenant).select_related("blocked_user")
        return Response({
            "results": [{
                "id": r.id,
                "blocked_user_id": r.blocked_user_id,
                "blocked_user_name": getattr(r.blocked_user, "name", None) or getattr(r.blocked_user, "username", "") or "",
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            } for r in rows],
            "count": rows.count(),
        })

    def post(self, request):
        tenant = request.tenant
        try:
            user_id = int(request.data.get("user_id"))
        except (TypeError, ValueError):
            return Response({"detail": "user_id 잘못됨"}, status=drf_status.HTTP_400_BAD_REQUEST)
        from apps.core.models import User
        try:
            u = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"detail": "사용자가 없습니다"}, status=drf_status.HTTP_404_NOT_FOUND)
        # P0 audit (2026-05-13): cross-tenant 블랙리스트 차단. user_id가 다른 학원 사용자면
        # 본 학원 staff가 임의 ID guess/leak로 차단 처리할 수 없도록 tenant 멤버십 검증.
        # 이전: User.objects.get 만 검사 → 다른 학원 user 도 본 학원에 차단 row 생성 가능.
        if not _is_user_in_tenant(tenant, u):
            return Response({"detail": "사용자가 없습니다"}, status=drf_status.HTTP_404_NOT_FOUND)
        reason = (request.data.get("reason") or "")[:200]
        obj, _created = PublicUserBlock.objects.get_or_create(
            tenant=tenant, blocked_user=u,
            defaults={"blocked_by": request.user, "reason": reason},
        )
        return Response({"id": obj.id, "blocked_user_id": obj.blocked_user_id}, status=drf_status.HTTP_201_CREATED)

    def delete(self, request):
        tenant = request.tenant
        try:
            user_id = int(request.data.get("user_id") or request.query_params.get("user_id"))
        except (TypeError, ValueError):
            return Response({"detail": "user_id 잘못됨"}, status=drf_status.HTTP_400_BAD_REQUEST)
        deleted, _ = PublicUserBlock.objects.filter(tenant=tenant, blocked_user_id=user_id).delete()
        return Response({"deleted": deleted}, status=drf_status.HTTP_200_OK)

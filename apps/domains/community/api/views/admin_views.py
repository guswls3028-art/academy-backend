from datetime import timedelta
from django.db.models import Count, F, Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.community.api.serializers import PostEntitySerializer
from apps.domains.community.models import CommunityReport, PostEntity, PostReply, PostLike, PostReplyLike, CommunityUserBlock
from apps.domains.community.selectors import (
    get_admin_post_list,
    get_all_posts_for_tenant,
    get_empty_post_queryset,
)
from apps.core.permissions import TenantResolvedAndStaff


class AdminPostViewSet(viewsets.GenericViewSet):
    """Admin list with filters. post_type, lecture_id, q, page, page_size."""
    permission_classes = [TenantResolvedAndStaff]
    serializer_class = PostEntitySerializer

    def list(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        def _int_or_none(val):
            if val is None or val == "":
                return None
            try:
                return int(val)
            except (TypeError, ValueError):
                return None

        post_type = (request.query_params.get("post_type") or "").strip().lower() or None
        lecture_id = _int_or_none(request.query_params.get("lecture_id"))
        q = (request.query_params.get("q") or "").strip() or None
        try:
            page = int(request.query_params.get("page") or 1)
            page_size = int(request.query_params.get("page_size") or 20)
        except (TypeError, ValueError):
            page, page_size = 1, 20
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        qs, total = get_admin_post_list(
            tenant,
            post_type=post_type,
            lecture_id=lecture_id,
            q=q,
            page=page,
            page_size=page_size,
        )
        serializer = self.get_serializer(qs, many=True)
        return Response({"results": serializer.data, "count": total})

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_post_queryset()
        return get_all_posts_for_tenant(tenant, include_unpublished=True)

    @action(detail=False, methods=["post"], url_path="bulk-status")
    def bulk_status(self, request):
        """POST /community/admin/posts/bulk-status/ — 다중 선택 글 일괄 status 변경.

        학원장 운영 도구(#41): 오래된 글 일괄 archive, draft 일괄 게시 등.
        body: {ids: [int], status: "published"|"archived"|"draft"}
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        ids = request.data.get("ids") or []
        new_status = (request.data.get("status") or "").strip().lower()
        if not isinstance(ids, list) or not ids:
            return Response({"detail": "ids 비어있습니다."}, status=status.HTTP_400_BAD_REQUEST)
        if new_status not in {"published", "archived", "draft"}:
            return Response({"detail": f"허용되지 않는 status: {new_status}"}, status=status.HTTP_400_BAD_REQUEST)
        # tenant 격리 보장 — 입력 id가 다른 tenant의 글이어도 update 안 됨
        updated = PostEntity.objects.filter(tenant=tenant, id__in=ids).update(status=new_status)
        return Response({"updated": updated, "status": new_status})


class AdminReportsViewSet(viewsets.GenericViewSet):
    """학원장 admin: 신고함 console.

    GET    /community/admin/reports/        — pending/processed 신고 list (page+filter)
    PATCH  /community/admin/reports/<id>/   — status 변경 (resolved/dismissed)
    """
    permission_classes = [TenantResolvedAndStaff]

    @action(detail=False, methods=["get"], url_path="pending-count")
    def pending_count(self, request):
        """학원장 알림 헤더용 — pending 신고 카운트만(빠른 응답)."""
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"count": 0})
        c = CommunityReport.objects.filter(tenant=tenant, status=CommunityReport.STATUS_PENDING).count()
        return Response({"count": c})

    def list(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        status_filter = (request.query_params.get("status") or "").strip().lower()
        target_type = (request.query_params.get("target_type") or "").strip().lower()
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(int(request.query_params.get("page_size") or 20), 100)
        except (TypeError, ValueError):
            page, page_size = 1, 20

        qs = CommunityReport.objects.filter(tenant=tenant).select_related("reporter")
        if status_filter in dict(CommunityReport.STATUS_CHOICES):
            qs = qs.filter(status=status_filter)
        if target_type in dict(CommunityReport.TARGET_CHOICES):
            qs = qs.filter(target_type=target_type)
        total = qs.count()
        offset = (page - 1) * page_size
        items = list(qs[offset : offset + page_size])

        # target 글/댓글 fetch — 한 번에 (N+1 방지). Student.user_id 차단 액션에 필요.
        post_ids = [r.target_id for r in items if r.target_type == CommunityReport.TARGET_POST]
        reply_ids = [r.target_id for r in items if r.target_type == CommunityReport.TARGET_REPLY]
        posts_map = {p.id: p for p in PostEntity.objects.filter(tenant=tenant, id__in=post_ids).select_related("created_by").only("id", "title", "post_type", "status", "created_by", "created_by__user_id", "created_by__name")} if post_ids else {}
        replies_map = {r.id: r for r in PostReply.objects.filter(tenant=tenant, id__in=reply_ids).select_related("post", "created_by").only("id", "content", "post_id", "created_by", "created_by__user_id", "created_by__name", "post__post_type", "post__title")} if reply_ids else {}

        from apps.domains.community.services.report_triage import triage_report
        results = []
        for r in items:
            target_info = None
            target_excerpt = ""
            if r.target_type == CommunityReport.TARGET_POST:
                p = posts_map.get(r.target_id)
                if p:
                    author_user_id = getattr(getattr(p, "created_by", None), "user_id", None)
                    author_name = getattr(getattr(p, "created_by", None), "name", None)
                    target_info = {"kind": "post", "id": p.id, "title": p.title, "post_type": p.post_type, "status": p.status, "author_user_id": author_user_id, "author_name": author_name}
                    target_excerpt = (p.title or "")
            elif r.target_type == CommunityReport.TARGET_REPLY:
                rep = replies_map.get(r.target_id)
                if rep:
                    author_user_id = getattr(getattr(rep, "created_by", None), "user_id", None)
                    author_name = getattr(getattr(rep, "created_by", None), "name", None)
                    target_info = {"kind": "reply", "id": rep.id, "post_id": rep.post_id, "post_title": rep.post.title, "post_type": rep.post.post_type, "content_excerpt": (rep.content or "")[:200], "author_user_id": author_user_id, "author_name": author_name}
                    target_excerpt = (rep.content or "")[:200]
            verdict = triage_report(r.reason, r.detail or "", target_excerpt)
            results.append({
                "id": r.id,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "target": target_info,
                "reason": r.reason,
                "reason_label": r.get_reason_display(),
                "detail": r.detail,
                "reporter_id": r.reporter_id,
                "reporter_name": getattr(r.reporter, "username", None) or getattr(r.reporter, "email", None) or None,
                "status": r.status,
                "status_label": r.get_status_display(),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                "triage": verdict,
            })

        return Response({"results": results, "count": total})

    def partial_update(self, request, pk=None):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        try:
            r = CommunityReport.objects.get(tenant=tenant, id=int(pk))
        except (CommunityReport.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        new_status = (request.data.get("status") or "").strip().lower()
        if new_status not in dict(CommunityReport.STATUS_CHOICES):
            return Response({"detail": f"허용되지 않는 status: {new_status}"}, status=status.HTTP_400_BAD_REQUEST)
        r.status = new_status
        if new_status in (CommunityReport.STATUS_RESOLVED, CommunityReport.STATUS_DISMISSED):
            r.resolved_at = timezone.now()
        r.save(update_fields=["status", "resolved_at"])
        return Response({"id": r.id, "status": r.status})


class CommunityUserBlockView(APIView):
    """학원장 사용자 차단 admin(#49 G2).

    POST   /api/v1/community/admin/user-blocks/   body: {user_id, reason?}  — 차단
    DELETE /api/v1/community/admin/user-blocks/<user_id>/                    — 차단 해제
    GET    /api/v1/community/admin/user-blocks/                              — 차단 사용자 list
    """
    permission_classes = [TenantResolvedAndStaff]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        items = (
            CommunityUserBlock.objects.filter(tenant=tenant)
            .select_related("user", "blocked_by").order_by("-created_at")[:200]
        )
        return Response({
            "results": [{
                "id": b.id,
                "user_id": b.user_id,
                "user_name": getattr(b.user, "username", None) or getattr(b.user, "email", None) or None,
                "blocked_by_id": b.blocked_by_id,
                "blocked_by_name": getattr(b.blocked_by, "username", None) if b.blocked_by_id else None,
                "reason": b.reason,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            } for b in items]
        })

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        try:
            user_id = int(request.data.get("user_id"))
        except (TypeError, ValueError):
            return Response({"detail": "user_id 필수."}, status=status.HTTP_400_BAD_REQUEST)
        if user_id <= 0:
            return Response({"detail": "user_id 잘못됨."}, status=status.HTTP_400_BAD_REQUEST)
        # 자기 자신 차단 방지
        if request.user.id == user_id:
            return Response({"detail": "본인은 차단할 수 없습니다."}, status=status.HTTP_400_BAD_REQUEST)
        reason = (request.data.get("reason") or "").strip()[:500]
        obj, created = CommunityUserBlock.objects.get_or_create(
            tenant=tenant, user_id=user_id,
            defaults={"blocked_by": request.user, "reason": reason},
        )
        return Response({"id": obj.id, "user_id": obj.user_id, "created": created}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    def delete(self, request, user_id=None):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return Response({"detail": "user_id 잘못됨."}, status=status.HTTP_400_BAD_REQUEST)
        deleted, _ = CommunityUserBlock.objects.filter(tenant=tenant, user_id=uid).delete()
        return Response({"deleted": deleted})


class CommunityStatsView(APIView):
    """학원장 admin 통계 — 최근 N일 카테고리별 글/댓글/좋아요/신고 카운트.

    GET /api/v1/community/admin/stats/?days=30
    """
    permission_classes = [TenantResolvedAndStaff]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        try:
            days = max(1, min(int(request.query_params.get("days") or 30), 365))
        except (TypeError, ValueError):
            days = 30
        since = timezone.now() - timedelta(days=days)

        # 글 카운트 — published 기준, post_type별 + 전체
        posts_by_type = dict(
            PostEntity.objects.filter(tenant=tenant, status="published", created_at__gte=since)
            .values("post_type")
            .annotate(c=Count("id"))
            .values_list("post_type", "c")
        )
        posts_total = sum(posts_by_type.values())

        # 댓글
        replies_total = PostReply.objects.filter(tenant=tenant, created_at__gte=since).count()

        # 좋아요 (글 + 댓글 합산)
        post_likes_total = PostLike.objects.filter(tenant=tenant, created_at__gte=since).count()
        reply_likes_total = PostReplyLike.objects.filter(tenant=tenant, created_at__gte=since).count()

        # 신고 (status별)
        reports_by_status = dict(
            CommunityReport.objects.filter(tenant=tenant, created_at__gte=since)
            .values("status")
            .annotate(c=Count("id"))
            .values_list("status", "c")
        )
        reports_total = sum(reports_by_status.values())

        # top 활성 게시글 (이번 기간 좋아요 + 댓글 합산 상위 5개)
        top_posts = list(
            PostEntity.objects.filter(tenant=tenant, status="published")
            .annotate(
                period_likes=Count("likes", filter=Q(likes__created_at__gte=since), distinct=True),
                period_replies=Count("replies", filter=Q(replies__created_at__gte=since), distinct=True),
            )
            .annotate(score=Count("likes", filter=Q(likes__created_at__gte=since), distinct=True) + Count("replies", filter=Q(replies__created_at__gte=since), distinct=True))
            .order_by("-score", "-created_at")
            .values("id", "title", "post_type", "period_likes", "period_replies")[:5]
        )

        # top 활동 학생 (이번 기간 글 + 댓글 합산 상위 5명) — 활동 점수 시스템 #15
        from apps.domains.students.models import Student
        top_students_qs = (
            Student.objects.filter(tenant=tenant, deleted_at__isnull=True)
            .annotate(
                post_count=Count("post_entities", filter=Q(post_entities__created_at__gte=since), distinct=True),
                reply_count=Count("post_replies", filter=Q(post_replies__created_at__gte=since), distinct=True),
            )
            .annotate(activity_score=F("post_count") + F("reply_count"))
            .filter(activity_score__gt=0)
            .order_by("-activity_score", "name")[:5]
        )
        top_students = [
            {"id": s.id, "name": s.name, "post_count": s.post_count, "reply_count": s.reply_count, "score": s.activity_score}
            for s in top_students_qs
        ]

        # hot keywords (#57) — 글 title+content tokenize → freq top 20
        hot_keywords = self._compute_hot_keywords(tenant, since)

        return Response({
            "days": days,
            "posts": {
                "total": posts_total,
                "by_type": posts_by_type,
            },
            "replies_total": replies_total,
            "likes": {
                "post": post_likes_total,
                "reply": reply_likes_total,
                "total": post_likes_total + reply_likes_total,
            },
            "reports": {
                "total": reports_total,
                "by_status": reports_by_status,
            },
            "top_posts": top_posts,
            "top_students": top_students,
            "hot_keywords": hot_keywords,
        })

    # 한국어 stopword(빈도 매우 높고 의미 약함). 보수적 — over-filter 회피.
    _STOPWORDS = frozenset({
        "그리고", "그래서", "하지만", "근데", "오늘", "내일", "어제", "있다", "없다", "하다", "되다",
        "이다", "있는", "없는", "하는", "되는", "같은", "수업", "강의", "선생님", "학생",
        "the", "and", "for", "with", "this", "that", "have", "will", "your", "from", "are", "was", "were",
    })

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        import re
        # 한국어/영문 2자 이상 토큰. HTML 태그 제거 후.
        text = re.sub(r"<[^>]+>", " ", text or "")
        return [t for t in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text)]

    def _compute_hot_keywords(self, tenant, since):
        from collections import Counter
        from apps.domains.community.models import PostEntity
        # title + content 합쳐 tokenize. 글 100개 한정 — 비용 제어.
        rows = (
            PostEntity.objects.filter(tenant=tenant, status="published", created_at__gte=since)
            .only("title", "content")
            .order_by("-created_at")[:200]
        )
        counter: Counter[str] = Counter()
        for p in rows:
            tokens = self._tokenize((p.title or "") + " " + (p.content or ""))
            for t in tokens:
                low = t.lower()
                if low in self._STOPWORDS:
                    continue
                counter[t] += 1
        top = counter.most_common(20)
        return [{"keyword": k, "count": v} for k, v in top]

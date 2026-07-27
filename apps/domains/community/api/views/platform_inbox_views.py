from __future__ import annotations

import logging
from collections.abc import Mapping

from django.conf import settings
from django.db import transaction
from django.db.models import (
    Case,
    F,
    IntegerField,
    Max,
    Prefetch,
    Q,
    Value,
    When,
)
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import (
    LandingConsultRequest,
    OpsAuditLog,
    PlatformInboxIncidentState,
)
from apps.core.permissions import IsPlatformAdmin
from apps.core.services.platform_inbox import (
    INCIDENT_STATUS_ACTION,
    PROMO_LEAD_SOURCES,
)
from apps.core.services.ops_audit import record_audit
from apps.domains.community.api.serializers import PostReplySerializer
from apps.domains.community.api.support_ticket_contract import serialize_support_ticket
from apps.domains.community.models import (
    PostAttachment,
    PostEntity,
    PostReply,
    platform_support_kind_q,
    platform_support_q,
)
from apps.domains.community.services.html_sanitizer import sanitize_html
from ._common import normalize_idempotency_key

logger = logging.getLogger(__name__)

VALID_INBOX_TYPES = frozenset({"all", "bug", "feedback", "contact"})
VALID_INBOX_STATUSES = frozenset({"all", "open", "resolved"})


def _support_queryset():
    return (
        PostEntity.objects.filter(post_type="board")
        .filter(platform_support_q())
        .select_related("tenant", "created_by")
        .prefetch_related(
            Prefetch(
                "replies",
                queryset=PostReply.objects.select_related("created_by").order_by("created_at"),
            ),
            Prefetch("attachments", queryset=PostAttachment.objects.order_by("created_at")),
        )
        .order_by("-created_at")
    )


def _lead_queryset():
    owner_tenant_id = getattr(settings, "OWNER_TENANT_ID", None)
    if owner_tenant_id is None:
        return LandingConsultRequest.objects.none()
    return (
        LandingConsultRequest.objects.filter(
            tenant_id=owner_tenant_id,
            source__in=PROMO_LEAD_SOURCES,
        )
        .select_related("tenant")
        .order_by("-created_at")
    )


def _serialize_lead(lead) -> dict:
    source_label = "데모 요청" if lead.source == "promo-demo" else "도입 문의"
    return {
        "source": "lead",
        "id": lead.id,
        "tenant_id": lead.tenant_id,
        "tenant_code": lead.tenant.code if lead.tenant else None,
        "tenant_name": lead.tenant.name if lead.tenant else None,
        "title": lead.interest or source_label,
        "subject": lead.interest or source_label,
        "content": lead.message,
        "category_label": lead.interest or None,
        "author_display_name": lead.name,
        "author_role": "prospect",
        "created_at": lead.created_at.isoformat(),
        "status": "resolved" if lead.resolved_at else "open",
        "replies_count": 0,
        "platform_replies_count": 0,
        "replies": [],
        "attachments": [],
        "inquiry_type": "contact",
        "source_label": source_label,
        "content_format": "plain",
        "contact_phone": lead.phone,
        "read_at": lead.read_at.isoformat() if lead.read_at else None,
        "resolved_at": lead.resolved_at.isoformat() if lead.resolved_at else None,
        "admin_memo": lead.admin_memo,
        "context": {
            "source": lead.source,
            "privacy_agreed": lead.privacy_agreed,
            "privacy_policy_version": lead.privacy_policy_version,
        },
    }


def _serialize_incident(
    log,
    state_data: PlatformInboxIncidentState | None,
) -> dict:
    payload = log.payload or {}
    route = str(payload.get("route") or "/unknown")
    incident_status = state_data.status if state_data else "open"
    admin_memo = state_data.admin_memo if state_data else ""
    return {
        "source": "incident",
        "id": log.id,
        "tenant_id": log.target_tenant_id,
        "tenant_code": log.target_tenant.code if log.target_tenant else None,
        "tenant_name": log.target_tenant.name if log.target_tenant else None,
        "title": f"문제 신고 · {route}",
        "subject": f"문제 신고 · {route}",
        "content": str(payload.get("description") or ""),
        "category_label": route,
        "author_display_name": log.actor_username or "사용자",
        "author_role": "member",
        "created_at": log.created_at.isoformat(),
        "status": incident_status,
        "replies_count": 0,
        "platform_replies_count": 0,
        "replies": [],
        "attachments": [],
        "inquiry_type": "bug",
        "source_label": "빠른 문제 신고",
        "content_format": "plain",
        "contact_phone": None,
        "read_at": None,
        "admin_memo": admin_memo,
        "context": {
            "route": route,
            "screen_size": payload.get("screen_size"),
            "sentry_event_id": payload.get("sentry_event_id"),
        },
    }


def _support_open_q():
    return Q(_latest_platform_reply__isnull=True) | Q(
        _latest_requester_reply__gt=F("_latest_platform_reply")
    )


def _filtered_support_queryset(inbox_type: str, inbox_status: str, query: str):
    qs = _support_queryset().annotate(
        _latest_platform_reply=Max(
            "replies__created_at",
            filter=Q(replies__author_role="platform_staff"),
        ),
        _latest_requester_reply=Max(
            "replies__created_at",
            filter=~Q(replies__author_role="platform_staff"),
        ),
    )
    if inbox_type in {"bug", "feedback"}:
        qs = qs.filter(platform_support_kind_q(inbox_type))
    elif inbox_type == "contact":
        return qs.none()
    if inbox_status == "open":
        qs = qs.filter(_support_open_q())
    elif inbox_status == "resolved":
        qs = qs.exclude(_support_open_q())
    if query:
        qs = qs.filter(
            Q(title__icontains=query)
            | Q(content__icontains=query)
            | Q(tenant__code__icontains=query)
            | Q(tenant__name__icontains=query)
            | Q(author_display_name__icontains=query)
        )
    return qs.annotate(
        _queue_rank=Case(
            When(_support_open_q(), then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("_queue_rank", "-created_at")


def _filtered_lead_queryset(inbox_type: str, inbox_status: str, query: str):
    qs = _lead_queryset()
    if inbox_type not in {"all", "contact"}:
        return qs.none()
    if inbox_status == "open":
        qs = qs.filter(resolved_at__isnull=True)
    elif inbox_status == "resolved":
        qs = qs.filter(resolved_at__isnull=False)
    if query:
        qs = qs.filter(
            Q(interest__icontains=query)
            | Q(message__icontains=query)
            | Q(name__icontains=query)
            | Q(phone__icontains=query)
            | Q(tenant__code__icontains=query)
            | Q(tenant__name__icontains=query)
            | Q(source__icontains=query)
        )
    return qs.annotate(
        _queue_rank=Case(
            When(resolved_at__isnull=True, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("_queue_rank", "-created_at")


def _filtered_incident_queryset(
    inbox_type: str,
    inbox_status: str,
    query: str,
):
    if inbox_type not in {"all", "bug"}:
        return OpsAuditLog.objects.none()
    qs = OpsAuditLog.objects.filter(action="user_incident.manual").select_related(
        "target_tenant",
        "inbox_state",
    )
    if inbox_status == "open":
        qs = qs.filter(Q(inbox_state__isnull=True) | Q(inbox_state__status="open"))
    elif inbox_status == "resolved":
        qs = qs.filter(inbox_state__status="resolved")
    if query:
        qs = qs.filter(
            Q(summary__icontains=query)
            | Q(actor_username__icontains=query)
            | Q(target_tenant__code__icontains=query)
            | Q(target_tenant__name__icontains=query)
            | Q(payload__route__icontains=query)
            | Q(payload__description__icontains=query)
        )
    return qs.annotate(
        _queue_rank=Case(
            When(
                Q(inbox_state__isnull=True) | Q(inbox_state__status="open"),
                then=Value(0),
            ),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("_queue_rank", "-created_at")


class PlatformInboxListView(APIView):
    """도입 문의 + 비공개 지원 티켓 + 수동 문제 신고 통합 운영 큐."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        inbox_type = (request.query_params.get("type") or "all").strip().lower()
        inbox_status = (request.query_params.get("status") or "all").strip().lower()
        query = (request.query_params.get("q") or "").strip().lower()[:100]
        if inbox_type not in VALID_INBOX_TYPES:
            return Response(
                {"detail": "type은 all, bug, feedback, contact 중 하나여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if inbox_status not in VALID_INBOX_STATUSES:
            return Response(
                {"detail": "status는 all, open, resolved 중 하나여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(100, max(1, int(request.query_params.get("page_size") or 50)))
        except (TypeError, ValueError):
            return Response(
                {"detail": "page와 page_size는 정수여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.core.services.platform_inbox import platform_inbox_summary

        start = (page - 1) * page_size
        fetch_limit = start + page_size
        support_qs = _filtered_support_queryset(inbox_type, inbox_status, query)
        lead_qs = _filtered_lead_queryset(inbox_type, inbox_status, query)
        incident_qs = _filtered_incident_queryset(inbox_type, inbox_status, query)
        count = support_qs.count() + lead_qs.count() + incident_qs.count()
        candidates: list[dict] = []
        for post in support_qs[:fetch_limit]:
            item = serialize_support_ticket(post)
            item["_sort_at"] = post.created_at
            candidates.append(item)
        for lead in lead_qs[:fetch_limit]:
            item = _serialize_lead(lead)
            item["_sort_at"] = lead.created_at
            candidates.append(item)
        for incident in incident_qs[:fetch_limit]:
            item = _serialize_incident(
                incident,
                getattr(incident, "inbox_state", None),
            )
            item["_sort_at"] = incident.created_at
            candidates.append(item)
        candidates.sort(
            key=lambda item: (
                item["status"] != "open",
                -item["_sort_at"].timestamp(),
            )
        )
        results = candidates[start:fetch_limit]
        for item in results:
            item.pop("_sort_at", None)

        response = Response(
            {
                "results": results,
                "count": count,
                "page": page,
                "page_size": page_size,
                "summary": platform_inbox_summary(),
            }
        )
        response["Cache-Control"] = "private, no-store"
        return response


class PlatformInboxReplyView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, post_id):
        if not isinstance(request.data, Mapping):
            return Response(
                {"detail": "요청 본문은 JSON 객체여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            post = _support_queryset().get(pk=post_id)
        except PostEntity.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        content = str(request.data.get("content") or "").strip()
        try:
            request_key = normalize_idempotency_key(
                request.data.get("idempotency_key")
            )
        except ValueError:
            return Response(
                {"detail": "답변 재시도 키 형식이 올바르지 않습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not content:
            return Response(
                {"detail": "답변 내용을 입력해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(content) > 10_000:
            return Response(
                {"detail": "답변은 10,000자 이내여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        staff = getattr(request.user, "staff", None) or getattr(
            request.user, "staff_profile", None
        )
        author_display_name = (
            getattr(staff, "name", None)
            or getattr(request.user, "username", None)
            or "개발자"
        )
        sanitized_content = sanitize_html(content)
        defaults = {
            "tenant_id": post.tenant_id,
            "content": sanitized_content,
            "created_by": None,
            "author_display_name": str(author_display_name)[:100],
            "author_role": "platform_staff",
        }
        created = True
        if request_key:
            reply, created = PostReply.objects.get_or_create(
                post=post,
                platform_request_key=request_key,
                defaults=defaults,
            )
            if not created and (
                reply.author_role != "platform_staff"
                or reply.content != sanitized_content
            ):
                return Response(
                    {"detail": "같은 답변 재시도 키가 다른 내용에 사용되었습니다."},
                    status=status.HTTP_409_CONFLICT,
                )
        else:
            reply = PostReply.objects.create(
                post=post,
                platform_request_key=None,
                **defaults,
            )
        if created:
            logger.info(
                "Platform inbox reply: post=%s tenant=%s by=%s",
                post.id,
                post.tenant_id,
                request.user.pk,
            )
            record_audit(
                request,
                action="inbox.reply",
                target_tenant=post.tenant,
                summary=f"Inbox reply on support#{post.id}",
                payload={"post_id": post.id, "reply_id": reply.id},
            )
        data = dict(PostReplySerializer(reply).data)
        data["is_platform_reply"] = True
        data["can_delete"] = False
        return Response(
            data,
            status=(
                status.HTTP_201_CREATED
                if created
                else status.HTTP_200_OK
            ),
        )


class PlatformInboxAttachmentDownloadView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request, post_id, att_id):
        try:
            att = PostAttachment.objects.select_related("post").get(
                id=att_id,
                post_id=post_id,
                post__in=_support_queryset(),
            )
        except PostAttachment.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        from apps.domains.community.services.attachment_urls import (
            build_attachment_download_url,
        )

        url = build_attachment_download_url(att, expires_in=3600, force_download=True)
        return Response({"url": url, "original_name": att.original_name})


class PlatformInboxLeadDetailView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def patch(self, request, lead_id):
        if not isinstance(request.data, Mapping):
            return Response(
                {"detail": "요청 본문은 JSON 객체여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            lead = _lead_queryset().get(pk=lead_id)
        except LandingConsultRequest.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        requested_status = request.data.get("status")
        if requested_status is not None and requested_status not in {"open", "resolved"}:
            return Response(
                {"detail": "status는 open 또는 resolved여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        update_fields: list[str] = []
        if requested_status == "resolved":
            lead.resolved_at = lead.resolved_at or timezone.now()
            lead.read_at = lead.read_at or timezone.now()
            update_fields.extend(["resolved_at", "read_at"])
        elif requested_status == "open":
            lead.resolved_at = None
            update_fields.append("resolved_at")
        if "admin_memo" in request.data:
            lead.admin_memo = str(request.data.get("admin_memo") or "").strip()[:2000]
            update_fields.append("admin_memo")
        if not update_fields:
            return Response(
                {"detail": "변경할 상태 또는 메모가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        update_fields.append("updated_at")
        lead.save(update_fields=list(dict.fromkeys(update_fields)))
        record_audit(
            request,
            action="inbox.lead_update",
            target_tenant=lead.tenant,
            summary=f"Promo lead#{lead.id} updated",
            payload={
                "lead_id": lead.id,
                "status": "resolved" if lead.resolved_at else "open",
            },
        )
        return Response(_serialize_lead(lead))


class PlatformInboxIncidentDetailView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def patch(self, request, incident_id):
        if not isinstance(request.data, Mapping):
            return Response(
                {"detail": "요청 본문은 JSON 객체여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            incident = OpsAuditLog.objects.select_related("target_tenant").get(
                pk=incident_id,
                action="user_incident.manual",
            )
        except OpsAuditLog.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        requested_status = str(request.data.get("status") or "").strip().lower()
        if requested_status not in {"open", "resolved"}:
            return Response(
                {"detail": "status는 open 또는 resolved여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        admin_memo = str(request.data.get("admin_memo") or "").strip()[:2000]
        try:
            with transaction.atomic():
                status_log = record_audit(
                    request,
                    action=INCIDENT_STATUS_ACTION,
                    target_tenant=incident.target_tenant,
                    summary=f"Incident#{incident.id} marked {requested_status}",
                    payload={
                        "incident_id": incident.id,
                        "status": requested_status,
                        "admin_memo": admin_memo,
                    },
                )
                if status_log is None:
                    raise RuntimeError("incident status audit failed")
                incident_state, _ = PlatformInboxIncidentState.objects.update_or_create(
                    incident=incident,
                    defaults={
                        "status": requested_status,
                        "admin_memo": admin_memo,
                        "updated_by": request.user,
                    },
                )
        except RuntimeError:
            return Response(
                {"detail": "처리 상태를 저장하지 못했습니다."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(_serialize_incident(incident, incident_state))

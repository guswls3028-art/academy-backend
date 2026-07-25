from __future__ import annotations

import re
from urllib.parse import urlsplit

from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.common.throttles import UserIncidentReportThrottle
from apps.core.permissions import TenantResolvedAndMember
from apps.core.services.ops_audit import record_audit


_NUMERIC_SEGMENT_RE = re.compile(r"(?<=/)\d+(?=/|$)")
_UUID_SEGMENT_RE = re.compile(
    r"(?<=/)[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}(?=/|$)",
    re.IGNORECASE,
)


def sanitize_incident_route(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "/unknown"
    try:
        path = urlsplit(raw).path
    except ValueError:
        path = raw.split("?", 1)[0]
    path = (path or "/unknown")[:200]
    path = _UUID_SEGMENT_RE.sub(":uuid", path)
    return _NUMERIC_SEGMENT_RE.sub(":id", path)


class UserIncidentSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=("manual", "frontend_exception"))
    message = serializers.CharField(max_length=1000, allow_blank=False, trim_whitespace=True)
    route = serializers.CharField(max_length=500, required=False, allow_blank=True)
    error_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    sentry_event_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    screen_size = serializers.RegexField(
        regex=r"^\d{2,5}x\d{2,5}$",
        max_length=16,
        required=False,
        allow_blank=True,
    )

    def validate(self, attrs):
        if attrs["source"] == "frontend_exception" and not attrs.get("error_name"):
            raise serializers.ValidationError(
                {"error_name": "프런트엔드 오류 이름이 필요합니다."}
            )
        return attrs


class UserIncidentReportView(APIView):
    """사용자가 실제로 겪은 문제를 운영 감사 로그에 안전하게 남긴다."""

    permission_classes = [IsAuthenticated, TenantResolvedAndMember]
    throttle_classes = [UserIncidentReportThrottle]

    def post(self, request):
        serializer = UserIncidentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        tenant = request.tenant
        source = data["source"]
        route = sanitize_incident_route(data.get("route"))

        payload = {
            "source": source,
            "route": route,
            "description": data["message"],
        }
        if source == "frontend_exception":
            payload["error_name"] = data["error_name"][:100]
        if data.get("sentry_event_id"):
            payload["sentry_event_id"] = data["sentry_event_id"][:64]
        if data.get("screen_size"):
            payload["screen_size"] = data["screen_size"]

        log = record_audit(
            request,
            action=f"user_incident.{source}",
            summary=f"{source} on {route}",
            target_tenant=tenant,
            payload=payload,
        )
        if log is None:
            return Response(
                {"detail": "문제 접수를 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"incident_id": log.id}, status=status.HTTP_201_CREATED)

"""Only tenant 1's admin97 may inspect local coding-session metadata."""
import logging

from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsPlatformAdmin
from apps.core.services.agent_sessions import load_snapshot

logger = logging.getLogger(__name__)


class IsAgentSessionViewer(BasePermission):
    def has_permission(self, request, view):
        token = getattr(request, "auth", None)
        if token and (token.get("impersonated_by") is not None or token.get("support_preview")):
            return False
        return bool(
            getattr(getattr(request, "tenant", None), "id", None) == 1
            and getattr(request.user, "is_active", False)
            and getattr(request.user, "username", None) in {"admin97", "t1_admin97"}
        )


class DevAgentSessionsView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin, IsAgentSessionViewer]
    http_method_names = ["get", "head", "options"]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def get(self, request):
        try:
            data = load_snapshot()
        except Exception as exc:
            # Upstream errors can include credentials/URLs: log only the class.
            logger.warning("agent_session_snapshot_unavailable class=%s", type(exc).__name__)
            return Response({"detail": "작업 현황을 불러오지 못했습니다. 잠시 후 다시 확인해 주세요."}, status=503, headers={"Cache-Control": "no-store"})
        return Response(data, headers={"Cache-Control": "no-store"})

# PATH: apps/domains/clinic/views/settings_views.py
from django.db import transaction
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.parsing import parse_bool
from apps.core.permissions import TenantResolvedAndStaff
from ..color_utils import get_effective_clinic_colors


# ============================================================
# Clinic Settings (패스카드 색상 등)
# ============================================================
class ClinicSettingsView(APIView):
    """
    GET/PATCH /clinic/settings/
    클리닉 설정 (패스카드 배경 색상 등)
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

        use_daily_random = getattr(tenant, "clinic_use_daily_random", False)
        auto_approve_booking = getattr(tenant, "clinic_auto_approve_booking", False)
        saved = getattr(tenant, "clinic_idcard_colors", None)
        if not saved or not isinstance(saved, list) or len(saved) < 3:
            saved = ["#ef4444", "#3b82f6", "#22c55e"]

        colors = get_effective_clinic_colors(tenant)

        return Response({
            "colors": colors[:3],
            "use_daily_random": use_daily_random,
            "auto_approve_booking": auto_approve_booking,
            "saved_colors": saved[:3],
        })

    def patch(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)
        update_fields = []
        with transaction.atomic():
            if "use_daily_random" in request.data:
                tenant.clinic_use_daily_random = parse_bool(
                    request.data["use_daily_random"], field_name="use_daily_random",
                )
                update_fields.append("clinic_use_daily_random")

            if "auto_approve_booking" in request.data:
                tenant.clinic_auto_approve_booking = parse_bool(
                    request.data["auto_approve_booking"], field_name="auto_approve_booking",
                )
                update_fields.append("clinic_auto_approve_booking")

            colors = request.data.get("colors")
            if colors is not None:
                if not isinstance(colors, list) or len(colors) != 3:
                    return Response(
                        {"detail": "colors는 3개의 색상 코드 배열이어야 합니다. (예: [\"#ef4444\", \"#3b82f6\", \"#22c55e\"])"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                import re
                hex_pattern = re.compile(r"^#[0-9A-Fa-f]{6}$")
                for c in colors:
                    if not isinstance(c, str) or not hex_pattern.match(c):
                        return Response(
                            {"detail": f"잘못된 색상 코드: {c}. #RRGGBB 형식이어야 합니다."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                tenant.clinic_idcard_colors = colors[:3]
                update_fields.append("clinic_idcard_colors")

            if update_fields:
                tenant.save(update_fields=update_fields)

        use_daily_random = getattr(tenant, "clinic_use_daily_random", False)
        auto_approve_booking = getattr(tenant, "clinic_auto_approve_booking", False)
        saved = getattr(tenant, "clinic_idcard_colors", None) or ["#ef4444", "#3b82f6", "#22c55e"]
        return Response({
            "colors": get_effective_clinic_colors(tenant),
            "use_daily_random": use_daily_random,
            "auto_approve_booking": auto_approve_booking,
            "saved_colors": saved[:3],
        })

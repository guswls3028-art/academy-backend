# PATH: apps/domains/clinic/views/settings_views.py
from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.models import Tenant
from apps.core.parsing import parse_bool
from apps.core.permissions import TenantResolvedAndStaff
from apps.core.services.tenant_access import get_authorized_tenant_role
from ..capabilities import clinic_capabilities_for
from ..booking_cutoff import cutoff_settings
from ..color_utils import get_effective_clinic_colors


# ============================================================
# Clinic Settings (패스카드 색상 등)
# ============================================================
class BookingCutoffSettingsSerializer(serializers.Serializer):
    booking_cutoff_mode = serializers.ChoiceField(choices=("none", "previous_day", "before_start"))
    booking_cutoff_time = serializers.TimeField(input_formats=["%H:%M", "%H:%M:%S"])
    booking_cutoff_minutes = serializers.IntegerField(min_value=1, max_value=525600)
    allow_late_requests = serializers.BooleanField()

    def validate_booking_cutoff_time(self, value):
        if value.second or value.microsecond:
            raise ValidationError("마감 시각은 분 단위로 설정해 주세요.")
        return value


class ClinicSettingsUpdateSerializer(BookingCutoffSettingsSerializer):
    """Writable settings. PATCH accepts any subset and preserves omitted values."""

    booking_cutoff_time = serializers.RegexField(r"^(?:[01]?[0-9]|2[0-3]):[0-5]?[0-9](?::0{1,2})?$")
    colors = serializers.ListField(
        child=serializers.RegexField(r"^#[0-9A-Fa-f]{6}$"), min_length=3, max_length=3,
        allow_null=True, help_text="Passcard colors. A null value leaves the saved colors unchanged.",
    )
    use_daily_random = serializers.BooleanField()
    auto_approve_booking = serializers.BooleanField()
    booking_mode = serializers.ChoiceField(choices=("fixed_slot", "time_range"))
    booking_interval_minutes = serializers.ChoiceField(choices=(30, 60))
    booking_max_stay_minutes = serializers.IntegerField(
        min_value=30, help_text="A positive multiple of the effective booking interval.",
    )


class ClinicCapabilitySerializer(serializers.Serializer):
    read = serializers.BooleanField()
    write = serializers.BooleanField()


class ClinicSettingsCapabilitiesSerializer(serializers.Serializer):
    student_operations = ClinicCapabilitySerializer()
    student_contacts = ClinicCapabilitySerializer()
    booking_policy = ClinicCapabilitySerializer()


class ClinicSettingsSerializer(ClinicSettingsUpdateSerializer):
    """Complete response shared by GET and successful PATCH."""

    colors = serializers.ListField(child=serializers.CharField(), min_length=3, max_length=3)
    saved_colors = serializers.ListField(child=serializers.CharField(), min_length=3, max_length=3)
    booking_cutoff_time = serializers.RegexField(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    multi_slot_booking_default = serializers.BooleanField()
    capabilities = ClinicSettingsCapabilitiesSerializer()


_DETAIL = {"type": "object", "required": ["detail"], "properties": {"detail": {"type": "string"}}}
_ACCESS_ERRORS = {
    401: OpenApiResponse(_DETAIL, description="Authentication is required."),
    403: OpenApiResponse(_DETAIL, description="Requires active staff access in the selected tenant. "
                         "Only its owner or admin may change booking policy and cutoff fields."),
}


class ClinicSettingsView(APIView):
    """
    GET/PATCH /clinic/settings/
    클리닉 설정 (패스카드 배경 색상 등)
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @staticmethod
    def _payload(request, tenant):
        saved = getattr(tenant, "clinic_idcard_colors", None)
        if not saved or not isinstance(saved, list) or len(saved) < 3:
            saved = ["#ef4444", "#3b82f6", "#22c55e"]
        return {
            **cutoff_settings(tenant),
            "colors": get_effective_clinic_colors(tenant)[:3],
            "use_daily_random": getattr(tenant, "clinic_use_daily_random", False),
            "auto_approve_booking": getattr(tenant, "clinic_auto_approve_booking", False),
            "multi_slot_booking_default": getattr(
                tenant, "clinic_allow_multi_slot_booking_default", False
            ),
            "booking_mode": getattr(tenant, "clinic_booking_mode", "fixed_slot"),
            "booking_interval_minutes": getattr(
                tenant, "clinic_booking_interval_minutes", 60
            ),
            "booking_max_stay_minutes": getattr(
                tenant, "clinic_booking_max_stay_minutes", 240
            ),
            "capabilities": clinic_capabilities_for(getattr(request, "user", None), tenant),
            "saved_colors": saved[:3],
        }

    @extend_schema(
        description="Read the selected tenant's clinic settings and the caller's capabilities.",
        responses={200: ClinicSettingsSerializer, 400: OpenApiResponse(_DETAIL, description="Missing tenant."),
                   **_ACCESS_ERRORS},
    )
    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

        return Response(self._payload(request, tenant))

    @extend_schema(
        description="Update only supplied settings and return the full current settings. Staff can update "
                    "colors, use_daily_random and auto_approve_booking. The booking mode, interval, maximum "
                    "stay and four cutoff fields require owner or admin access. Cutoff times accept HH:MM "
                    "or HH:MM:00 in Korean local time and are returned as HH:MM.",
        request=ClinicSettingsUpdateSerializer,
        responses={
            200: ClinicSettingsSerializer,
            400: OpenApiResponse(
                {"type": "object", "additionalProperties": {
                    "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                }},
                description="Missing tenant or invalid settings. A detail message or field-specific errors "
                            "describe the rejection. No supplied setting is saved.",
            ),
            **_ACCESS_ERRORS,
        },
    )
    def patch(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)
        policy_fields = {
            "booking_mode",
            "booking_interval_minutes",
            "booking_max_stay_minutes",
        }
        cutoff = BookingCutoffSettingsSerializer(data=request.data, partial=True)
        if (policy_fields | set(cutoff.fields)).intersection(request.data) and get_authorized_tenant_role(
            request.user,
            tenant,
        ) not in {"owner", "admin"}:
            raise PermissionDenied("예약 정책은 대표 또는 관리자만 변경할 수 있습니다.")

        with transaction.atomic():
            tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
            cutoff.is_valid(raise_exception=True)
            update_fields = []
            for key, value in cutoff.validated_data.items():
                field_name = f"clinic_{key}"
                setattr(tenant, field_name, value)
                update_fields.append(field_name)
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

            if policy_fields.intersection(request.data):
                next_mode = request.data.get(
                    "booking_mode", tenant.clinic_booking_mode
                )
                if next_mode not in {"fixed_slot", "time_range"}:
                    raise ValidationError({
                        "booking_mode": "fixed_slot 또는 time_range만 사용할 수 있습니다."
                    })
                try:
                    next_interval = int(request.data.get(
                        "booking_interval_minutes",
                        tenant.clinic_booking_interval_minutes,
                    ))
                    next_max_stay = int(request.data.get(
                        "booking_max_stay_minutes",
                        tenant.clinic_booking_max_stay_minutes,
                    ))
                except (TypeError, ValueError) as exc:
                    raise ValidationError({
                        "booking_policy": "예약 간격과 최대 체류 시간은 숫자여야 합니다."
                    }) from exc
                if next_interval not in {30, 60}:
                    raise ValidationError({
                        "booking_interval_minutes": "예약 간격은 30분 또는 60분이어야 합니다."
                    })
                if next_max_stay < next_interval or next_max_stay % next_interval:
                    raise ValidationError({
                        "booking_max_stay_minutes": "최대 체류 시간은 예약 간격의 양의 배수여야 합니다."
                    })

                if "booking_mode" in request.data:
                    tenant.clinic_booking_mode = next_mode
                    update_fields.append("clinic_booking_mode")
                if "booking_interval_minutes" in request.data:
                    tenant.clinic_booking_interval_minutes = next_interval
                    update_fields.append("clinic_booking_interval_minutes")
                if "booking_max_stay_minutes" in request.data:
                    tenant.clinic_booking_max_stay_minutes = next_max_stay
                    update_fields.append("clinic_booking_max_stay_minutes")

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

        return Response(self._payload(request, tenant))

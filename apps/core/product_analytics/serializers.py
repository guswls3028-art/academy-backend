from __future__ import annotations

import re
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.core.product_analytics.constants import (
    DEVICE_CLASSES,
    EVENT_CTA_CLICK,
    EVENT_CTA_IMPRESSION,
    EVENT_TASK_FAILURE,
    EVENT_TASK_START,
    EVENT_TASK_SUCCESS,
    EVENT_TYPES,
    FAILURE_CATEGORIES,
    MAX_BATCH_EVENTS,
    SCHEMA_VERSION,
    SURFACES,
)

_STABLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
_UUID_IN_PATH_RE = re.compile(
    r"(?i)[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_RAW_NUMERIC_SEGMENT_RE = re.compile(r"/\d+(?:/|$)")


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("객체 형식이어야 합니다.")
        unknown = sorted(set(data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError(
                {key: ["허용되지 않은 필드입니다."] for key in unknown}
            )
        return super().to_internal_value(data)


class ProductUsageEventSerializer(StrictSerializer):
    event_id = serializers.UUIDField()
    event_type = serializers.ChoiceField(choices=EVENT_TYPES)
    occurred_at = serializers.DateTimeField()
    session_id = serializers.UUIDField()
    view_id = serializers.UUIDField()
    interaction_id = serializers.UUIDField(required=False, allow_null=True)
    feature_id = serializers.CharField(max_length=80)
    screen_id = serializers.CharField(max_length=100)
    surface = serializers.ChoiceField(choices=SURFACES)
    route_template = serializers.CharField(max_length=180)
    cta_id = serializers.CharField(
        max_length=80,
        required=False,
        allow_blank=True,
        default="",
    )
    action_id = serializers.CharField(
        max_length=80,
        required=False,
        allow_blank=True,
        default="",
    )
    placement_id = serializers.CharField(
        max_length=80,
        required=False,
        allow_blank=True,
        default="",
    )
    position_index = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=0,
        max_value=32767,
        default=None,
    )
    failure_category = serializers.ChoiceField(
        choices=FAILURE_CATEGORIES,
        required=False,
        allow_blank=True,
        default="",
    )
    device_class = serializers.ChoiceField(choices=DEVICE_CLASSES)
    client_release = serializers.CharField(max_length=64)
    catalog_version = serializers.CharField(max_length=32)
    synthetic = serializers.BooleanField(required=False, default=False)

    def validate_occurred_at(self, value):
        now = timezone.now()
        if value < now - timedelta(hours=24):
            raise serializers.ValidationError("24시간 이전 이벤트는 받을 수 없습니다.")
        if value > now + timedelta(minutes=5):
            raise serializers.ValidationError("서버 시각보다 5분 이후일 수 없습니다.")
        return value

    def _validate_stable_id(self, value: str, field_name: str) -> str:
        if not _STABLE_ID_RE.fullmatch(value):
            raise serializers.ValidationError(
                {field_name: ["소문자 영문, 숫자, 마침표, 하이픈만 허용합니다."]}
            )
        return value

    def validate(self, attrs):
        for field_name in (
            "feature_id",
            "screen_id",
            "cta_id",
            "action_id",
            "placement_id",
        ):
            value = attrs.get(field_name) or ""
            if value:
                self._validate_stable_id(value, field_name)

        route_template = attrs["route_template"]
        if (
            not route_template.startswith("/")
            or "?" in route_template
            or "#" in route_template
            or "://" in route_template
            or _UUID_IN_PATH_RE.search(route_template)
            or _RAW_NUMERIC_SEGMENT_RE.search(route_template)
        ):
            raise serializers.ValidationError(
                {"route_template": ["동적 ID와 쿼리를 제거한 경로 템플릿이어야 합니다."]}
            )

        event_type = attrs["event_type"]
        if event_type in (EVENT_CTA_IMPRESSION, EVENT_CTA_CLICK):
            if not attrs.get("cta_id") or not attrs.get("placement_id"):
                raise serializers.ValidationError(
                    "CTA 이벤트에는 cta_id와 placement_id가 필요합니다."
                )
        if event_type == EVENT_CTA_CLICK and not attrs.get("interaction_id"):
            raise serializers.ValidationError(
                {"interaction_id": ["CTA 클릭에는 interaction_id가 필요합니다."]}
            )
        if event_type in (EVENT_TASK_START, EVENT_TASK_SUCCESS, EVENT_TASK_FAILURE):
            if not attrs.get("interaction_id") or not attrs.get("action_id"):
                raise serializers.ValidationError(
                    "작업 이벤트에는 interaction_id와 action_id가 필요합니다."
                )
        if event_type == EVENT_TASK_FAILURE:
            if not attrs.get("failure_category"):
                raise serializers.ValidationError(
                    {"failure_category": ["작업 실패 분류가 필요합니다."]}
                )
        elif attrs.get("failure_category"):
            raise serializers.ValidationError(
                {"failure_category": ["task_failure에서만 사용할 수 있습니다."]}
            )
        return attrs


class ProductUsageBatchSerializer(StrictSerializer):
    schema_version = serializers.IntegerField()
    events = ProductUsageEventSerializer(
        many=True,
        allow_empty=False,
        max_length=MAX_BATCH_EVENTS,
    )

    def validate_schema_version(self, value):
        if value != SCHEMA_VERSION:
            raise serializers.ValidationError("지원하지 않는 schema_version입니다.")
        return value

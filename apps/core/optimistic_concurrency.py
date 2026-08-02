from __future__ import annotations

from django.utils.dateparse import parse_datetime
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, ValidationError


EXPECTED_UPDATED_AT_HEADER = "X-Expected-Updated-At"


class StaleResourceConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = "stale_resource"

    def __init__(self, *, current_updated_at):
        current_value = serializers.DateTimeField().to_representation(
            current_updated_at
        )
        super().__init__(
            {
                "detail": "다른 사용자가 먼저 설정을 변경했습니다. 최신 값을 불러온 뒤 다시 저장해 주세요.",
                "code": "stale_resource",
                "current_updated_at": current_value,
            }
        )


def assert_expected_updated_at(*, request, instance) -> None:
    expected_value = request.headers.get(EXPECTED_UPDATED_AT_HEADER)
    if not expected_value:
        return

    expected = parse_datetime(expected_value)
    if expected is None:
        raise ValidationError(
            {
                EXPECTED_UPDATED_AT_HEADER: (
                    "올바른 ISO 8601 수정 시각이어야 합니다."
                )
            }
        )

    current = getattr(instance, "updated_at", None)
    if current is None or expected != current:
        raise StaleResourceConflict(current_updated_at=current)

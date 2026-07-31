# PATH: apps/domains/homework/serializers/core.py
"""
Homework Domain Serializers (core)

포함:
- HomeworkPolicySerializer / PatchSerializer

HomeworkScore 관련 serializer는 homework_results.serializers.homework_score 로 이관됨.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.domains.homework.models import HomeworkPolicy
from apps.support.homework.view_dependencies import minimum_live_homework_max_score


class HomeworkPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeworkPolicy
        fields = [
            "id",
            "session",
            "cutline_percent",
            "cutline_mode",
            "cutline_value",
            "round_unit_percent",
            "clinic_enabled",
            "clinic_on_fail",
            "updated_at",
            "created_at",
        ]
        read_only_fields = ["id", "session", "updated_at", "created_at"]


class HomeworkPolicyPatchSerializer(serializers.ModelSerializer):
    """
    PATCH 전용 — 프론트 계약에 맞춰 수정 가능 필드만 허용
    """

    class Meta:
        model = HomeworkPolicy
        fields = [
            "cutline_percent",
            "cutline_mode",
            "cutline_value",
            "round_unit_percent",
            "clinic_enabled",
            "clinic_on_fail",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is None:
            return attrs

        mode = attrs.get("cutline_mode", self.instance.cutline_mode)
        value = attrs.get("cutline_value", self.instance.cutline_value)
        if mode != HomeworkPolicy.CutlineMode.COUNT:
            return attrs

        minimum = minimum_live_homework_max_score(session=self.instance.session)
        if minimum is not None and float(value) > float(minimum[0]):
            raise serializers.ValidationError(
                {
                    "cutline_value": (
                        f"점수 커트라인({float(value):g}점)은 과제 '{minimum[1]}'의 "
                        f"만점({float(minimum[0]):g}점)을 넘을 수 없습니다."
                    )
                }
            )
        return attrs

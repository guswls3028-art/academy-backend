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

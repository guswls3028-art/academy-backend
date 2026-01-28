# PATH: apps/domains/homework/serializers/core.py
"""
Homework Domain Serializers (core)

✅ 포함
- HomeworkPolicySerializer / PatchSerializer
- HomeworkScoreSerializer
- HomeworkQuickPatchSerializer

⚠️ 주의
- 점수 스냅샷 단일 진실은 homework_results.HomeworkScore
- /homework/scores/* 는 프론트 호환을 위해 유지
"""

from __future__ import annotations

from rest_framework import serializers

from apps.domains.homework.models import HomeworkPolicy
from apps.domains.homework_results.models import HomeworkScore


class HomeworkPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeworkPolicy
        fields = [
            "id",
            "session",
            "cutline_percent",
            "round_unit_percent",
            "clinic_enabled",
            "clinic_on_fail",
            "updated_at",
            "created_at",
        ]
        read_only_fields = ["id", "session", "updated_at", "created_at"]


class HomeworkPolicyPatchSerializer(serializers.ModelSerializer):
    """
    ✅ PATCH 전용
    - 프론트 계약에 맞춰 수정 가능 필드만 허용
    """

    class Meta:
        model = HomeworkPolicy
        fields = [
            "cutline_percent",
            "round_unit_percent",
            "clinic_enabled",
            "clinic_on_fail",
        ]


class HomeworkScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeworkScore
        fields = [
            "id",
            "homework",
            "session",
            "enrollment_id",
            "score",
            "max_score",
            "passed",
            "clinic_required",
            "teacher_approved",
            "is_locked",
            "lock_reason",
            "updated_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "homework",
            "session",
            "enrollment_id",
            "passed",
            "clinic_required",
            "is_locked",
            "lock_reason",
            "updated_at",
            "created_at",
        ]


class HomeworkQuickPatchSerializer(serializers.Serializer):
    """
    ✅ 조교 점수 입력 Quick Patch (LOCKED SPEC)

    지원 입력:
    - percent 입력: score=85, max_score 생략 (None)
    - raw 입력: score=18, max_score=20

    해석 규칙 (backend 단일 진실):
    - max_score == None → score를 percent 값으로 간주
    - max_score != None → score/max_score*100 으로 percent 계산
    """

    homework_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()

    score = serializers.FloatField()
    max_score = serializers.FloatField(required=False, allow_null=True)

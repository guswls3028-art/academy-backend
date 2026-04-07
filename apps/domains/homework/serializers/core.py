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
    ✅ PATCH 전용
    - 프론트 계약에 맞춰 수정 가능 필드만 허용
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


class HomeworkScoreSerializer(serializers.ModelSerializer):
    # Backward-compat: expose FK _id value under original key name
    enrollment_id = serializers.IntegerField(read_only=True)

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
    - 미제출: meta_status="NOT_SUBMITTED", score=null
    """

    homework_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()

    score = serializers.FloatField(allow_null=True)
    max_score = serializers.FloatField(required=False, allow_null=True)

    meta_status = serializers.ChoiceField(
        choices=[HomeworkScore.MetaStatus.NOT_SUBMITTED],
        allow_null=True,
        required=False,
    )

    def validate(self, attrs):
        # P1-7: score <= max_score 검증
        score = attrs.get("score")
        max_score = attrs.get("max_score")
        if score is not None and max_score is not None and max_score > 0:
            if score > max_score:
                raise serializers.ValidationError(
                    {"score": f"점수({score})가 만점({max_score})을 초과할 수 없습니다."},
                    code="SCORE_EXCEEDS_MAX",
                )
        if score is not None and score < 0:
            raise serializers.ValidationError(
                {"score": "점수는 0 이상이어야 합니다."},
                code="NEGATIVE_SCORE",
            )
        if max_score is not None and max_score < 0:
            raise serializers.ValidationError(
                {"max_score": "만점은 0 이상이어야 합니다."},
                code="NEGATIVE_MAX_SCORE",
            )
        return attrs

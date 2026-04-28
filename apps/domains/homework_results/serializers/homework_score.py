# PATH: apps/domains/homework_results/serializers/homework_score.py

from __future__ import annotations

from typing import Optional

from rest_framework import serializers

from apps.domains.homework_results.models import HomeworkScore


class _StatusField(serializers.CharField):
    """
    PATCH /homework/scores/{id}/ 에서 status 입력 계약(write-only).
    - 미제출 저장: "NOT_SUBMITTED"
    - 해제: null 또는 "" (요청에 status 키가 '존재'할 때만 반영)
    """

    def to_internal_value(self, data):
        if data is None:
            return None
        s = str(data).strip()
        return s or None


class HomeworkScoreSerializer(serializers.ModelSerializer):
    enrollment_id = serializers.IntegerField(read_only=True)

    # write-only convenience field (DB 컬럼 추가 아님)
    status = _StatusField(required=False, allow_null=True, allow_blank=True, write_only=True)

    class Meta:
        model = HomeworkScore
        fields = [
            "id",
            "enrollment_id",
            "session",
            "homework",
            "score",
            "max_score",
            "teacher_approved",
            "passed",
            "clinic_required",
            "is_locked",
            "lock_reason",
            "updated_by_user_id",
            "meta",
            "created_at",
            "updated_at",
            # input-only
            "status",
        ]

    def validate_status(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if value == HomeworkScore.MetaStatus.NOT_SUBMITTED:
            return value
        raise serializers.ValidationError(
            "status must be NOT_SUBMITTED or null/empty",
            code="INVALID",
        )


class HomeworkQuickPatchSerializer(serializers.Serializer):
    """
    Quick Patch (LOCKED SPEC)

    지원 입력:
    - percent: score=85, max_score 생략 (None)
    - raw/max: score=18, max_score=20
    - 미제출: meta_status="NOT_SUBMITTED", score=null
    """

    homework_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()

    score = serializers.FloatField(allow_null=True, required=False)
    max_score = serializers.FloatField(required=False, allow_null=True)

    meta_status = serializers.ChoiceField(
        choices=[HomeworkScore.MetaStatus.NOT_SUBMITTED],
        allow_null=True,
        required=False,
    )

    def validate(self, attrs):
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

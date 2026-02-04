# PATH: apps/domains/homework/serializers/homework_score.py

from __future__ import annotations

from typing import Any, Optional

from rest_framework import serializers

from apps.domains.homework_results.models import HomeworkScore


class _StatusField(serializers.CharField):
    """
    ✅ status 입력 계약 (기존 API 유지 + 분기 추가)
    - 미제출 저장: "NOT_SUBMITTED"
    - 해제: null 또는 "" (요청에 status 키가 '존재'할 때만 반영)
    """

    def to_internal_value(self, data):
        if data is None:
            return None
        s = str(data).strip()
        return s or None


class HomeworkScoreSerializer(serializers.ModelSerializer):
    """
    ✅ 응답 계약:
    - 프론트는 (score, meta.status)로 상태 구분 가능해야 한다.
    - 기존 meta 응답은 유지되며, status는 meta.status로만 표현한다(필드 추가 X).
    """

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
    Quick Patch (MVP)
    - homework_id + enrollment_id 기반 upsert
    - score 입력 방식 2가지:
      - percent 직접 입력(score=85, max_score=None)
      - raw/max 입력(score=18, max_score=20)

    ✅ 확장:
    - status="NOT_SUBMITTED" 저장/해제 지원 (meta.status)
      - 저장 시: score/max_score는 강제로 None 처리(미제출 ≠ 0점)
      - 해제 시: status 키가 존재하고 null/""이면 meta.status 제거
    """

    homework_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()

    # score는 "미제출" 입력에서 생략 가능하도록 allow_null
    score = serializers.FloatField(required=False, allow_null=True)
    max_score = serializers.FloatField(required=False, allow_null=True)

    # convenience input
    status = _StatusField(required=False, allow_null=True, allow_blank=True)

    def validate(self, attrs: dict) -> dict:
        status = attrs.get("status", serializers.empty)

        # status 키가 있으면 (저장/해제)로 처리 가능
        if status is not serializers.empty:
            if status is None:
                # 해제: score 없이도 허용 (기존 점수 유지)
                return attrs
            if status == HomeworkScore.MetaStatus.NOT_SUBMITTED:
                # 저장: score/max_score는 의미 없으므로 무시 가능
                return attrs
            raise serializers.ValidationError(
                {"status": "status must be NOT_SUBMITTED or null/empty"},
                code="INVALID",
            )

        # status 키가 없으면 기존 규칙: score는 있어야 함
        if "score" not in attrs:
            raise serializers.ValidationError(
                {"score": "score is required when status is not provided"},
                code="INVALID",
            )

        return attrs

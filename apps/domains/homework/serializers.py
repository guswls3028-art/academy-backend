# PATH: apps/domains/homework/serializers.py
# 역할: Policy 조회/패치 + Score 조회/패치 + quick_patch 입력 스키마

"""
Homework Domain Serializers

✅ 메모 (MVP)
- HomeworkPolicy는 session 당 1개 보장
- cutline_percent(%) 는 프론트 Setup에서 수정 가능 (PATCH 허용)
- 점수 방식은 학원마다 다르므로 score/max_score 형태로 저장하고,
  backend에서 percent로 판정해서 passed/clinic_required를 내려준다.

⚠️ Score 스냅샷의 단일 진실은 homework_results 도메인이다.
- 다만 /homework/scores/* 라우팅은 호환을 위해 여기서 유지한다.
"""

from rest_framework import serializers

from .models import HomeworkPolicy

# ✅ 단일 진실: homework score snapshot
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

    session_id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField()

    score = serializers.FloatField()
    max_score = serializers.FloatField(required=False, allow_null=True)

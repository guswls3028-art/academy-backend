from __future__ import annotations

from rest_framework import serializers


class ExamQuestionInitSerializer(serializers.Serializer):
    total_questions = serializers.IntegerField(
        min_value=0, max_value=500, required=False
    )
    default_score = serializers.FloatField(required=False, min_value=0.0)

    # 객관식/주관식 분리: 모두 주어지면 total = choice_count + essay_count 로 생성
    choice_count = serializers.IntegerField(required=False, min_value=0, max_value=500)
    choice_score = serializers.FloatField(required=False, min_value=0.0)
    essay_count = serializers.IntegerField(required=False, min_value=0, max_value=500)
    essay_score = serializers.FloatField(required=False, min_value=0.0)

    def validate(self, attrs):
        has_total = attrs.get("total_questions") is not None
        choice_count = attrs.get("choice_count")
        choice_score = attrs.get("choice_score")
        essay_count = attrs.get("essay_count")
        essay_score = attrs.get("essay_score")
        has_choice_essay = all(
            v is not None for v in (choice_count, choice_score, essay_count, essay_score)
        )

        if has_choice_essay:
            if (choice_count or 0) + (essay_count or 0) == 0:
                raise serializers.ValidationError(
                    "객관식+주관식 문항 수 합이 1 이상이어야 합니다."
                )
            return attrs
        if has_total:
            return attrs
        raise serializers.ValidationError(
            "total_questions 를 보내거나, choice_count/choice_score/essay_count/essay_score 를 모두 보내세요."
        )


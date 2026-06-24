from __future__ import annotations

from rest_framework import serializers


MAX_EXAM_QUESTIONS = 500


class ExamQuestionInitSerializer(serializers.Serializer):
    total_questions = serializers.IntegerField(
        min_value=0, max_value=MAX_EXAM_QUESTIONS, required=False
    )
    default_score = serializers.FloatField(required=False, min_value=0.0)

    # 객관식/주관식 분리: count만 주면 기존 배점은 보존하고,
    # choice_score/essay_score까지 주어진 경우에만 일괄 배점을 갱신한다.
    choice_count = serializers.IntegerField(required=False, min_value=0, max_value=MAX_EXAM_QUESTIONS)
    choice_score = serializers.FloatField(required=False, min_value=0.0)
    essay_count = serializers.IntegerField(required=False, min_value=0, max_value=MAX_EXAM_QUESTIONS)
    essay_score = serializers.FloatField(required=False, min_value=0.0)

    def validate(self, attrs):
        has_total = attrs.get("total_questions") is not None
        choice_count = attrs.get("choice_count")
        choice_score = attrs.get("choice_score")
        essay_count = attrs.get("essay_count")
        essay_score = attrs.get("essay_score")
        has_choice_essay_counts = choice_count is not None and essay_count is not None
        has_any_choice_essay_score = choice_score is not None or essay_score is not None
        has_all_choice_essay_scores = choice_score is not None and essay_score is not None

        if has_any_choice_essay_score and not has_choice_essay_counts:
            raise serializers.ValidationError(
                "choice_score/essay_score 는 choice_count/essay_count 와 함께 보내야 합니다."
            )

        if has_choice_essay_counts:
            if has_any_choice_essay_score and not has_all_choice_essay_scores:
                raise serializers.ValidationError(
                    "choice_score 와 essay_score 는 함께 보내야 합니다."
                )
            total_count = (choice_count or 0) + (essay_count or 0)
            if total_count == 0:
                raise serializers.ValidationError(
                    "객관식+주관식 문항 수 합이 1 이상이어야 합니다."
                )
            if total_count > MAX_EXAM_QUESTIONS:
                raise serializers.ValidationError(
                    f"객관식+주관식 문항 수 합은 최대 {MAX_EXAM_QUESTIONS}문항입니다."
                )
            return attrs
        if has_total:
            return attrs
        raise serializers.ValidationError(
            "total_questions 를 보내거나, choice_count/essay_count 를 보내세요."
        )

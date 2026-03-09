# PATH: apps/domains/results/serializers/session_scores.py
"""
SessionScores Serializer (Score Tab)

✅ 설계 고정(중요)
- 이 Serializer는 "표시용 DTO" 이다.
- 도메인 로직/판정/정책 계산을 하지 않는다.
- View에서 만들어준 dict를 그대로 validate/serialize만 수행한다.

✅ 프론트 계약
- score === null 은 "미산출/미응시/처리중" 의미
- is_locked / lock_reason 은 입력 비활성화 + tooltip 용도
"""

from __future__ import annotations

from rest_framework import serializers


class ScoreBlockSerializer(serializers.Serializer):
    score = serializers.FloatField(allow_null=True)
    max_score = serializers.FloatField(allow_null=True)

    # 객관식/주관식 (시험만). 합산 = score = objective + subjective
    objective_score = serializers.FloatField(allow_null=True, required=False, default=None)
    subjective_score = serializers.FloatField(allow_null=True, required=False, default=None)

    passed = serializers.BooleanField(allow_null=True)
    clinic_required = serializers.BooleanField()

    is_locked = serializers.BooleanField()
    lock_reason = serializers.CharField(allow_null=True, allow_blank=True)


class ExamScoreBlockSerializer(serializers.Serializer):
    exam_id = serializers.IntegerField()
    title = serializers.CharField(allow_blank=True)
    pass_score = serializers.FloatField()

    block = ScoreBlockSerializer()

    # 주관식 점수 입력 모드용 문항별 점수 (ResultItem)
    items = serializers.ListField(
        child=serializers.DictField(),
        allow_empty=True,
        required=False,
        default=list,
    )


class HomeworkScoreBlockSerializer(serializers.Serializer):
    homework_id = serializers.IntegerField()
    title = serializers.CharField(allow_blank=True)

    block = ScoreBlockSerializer()


class SessionScoreRowSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_id = serializers.IntegerField(allow_null=True)
    student_name = serializers.CharField(allow_blank=True)

    exams = ExamScoreBlockSerializer(many=True)
    homeworks = HomeworkScoreBlockSerializer(many=True)

    updated_at = serializers.DateTimeField(allow_null=True)

    # 클리닉 대상이면서 클리닉 예약이 없을 때 이름 셀 노란 배경 표시용
    name_highlight_clinic_no_reservation = serializers.BooleanField(default=False)

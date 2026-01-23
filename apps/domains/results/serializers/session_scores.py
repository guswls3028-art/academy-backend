# PATH: apps/domains/results/serializers/session_scores.py
"""
SessionScores Serializer (Score Tab)

✅ 설계 고정(중요)
- 이 Serializer는 "표시용 DTO" 이다.
- 도메인 로직/판정/정책 계산을 하지 않는다.
- View에서 만들어준 dict를 그대로 validate/serialize만 수행한다.

✅ 프론트 계약
- score === null 은 "미산출/미응시/처리중" 의미 (0과 구분)
- is_locked / lock_reason 은 입력 비활성화 + tooltip 용도
"""

from __future__ import annotations

from rest_framework import serializers


class ScoreBlockSerializer(serializers.Serializer):
    score = serializers.FloatField(allow_null=True)
    max_score = serializers.FloatField(allow_null=True)

    passed = serializers.BooleanField()
    clinic_required = serializers.BooleanField()

    is_locked = serializers.BooleanField()
    lock_reason = serializers.CharField(allow_null=True, allow_blank=True)


class ExamScoreBlockSerializer(serializers.Serializer):
    """
    ✅ Session 1:N Exam 대응
    - exam_id + title + pass_score (meta 없이도 row 자체로 표시 가능)
    - 내부 score block은 ScoreBlockSerializer를 재사용
    """

    exam_id = serializers.IntegerField()
    title = serializers.CharField(allow_blank=True)
    pass_score = serializers.FloatField()

    block = ScoreBlockSerializer()


class SessionScoreRowSerializer(serializers.Serializer):
    """
    ✅ 성적 탭 메인 테이블 Row
    - 학생 1명당 1 Row
    - exams: 시험 요약 리스트(1:N)
    - homework: 과제 요약(세션 단위 스냅샷)
    """

    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField(allow_blank=True)

    exams = ExamScoreBlockSerializer(many=True)
    homework = ScoreBlockSerializer()

    updated_at = serializers.DateTimeField(allow_null=True)

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


class SessionScoreRowSerializer(serializers.Serializer):
    """
    ✅ 성적 탭 메인 테이블 Row (exam_id 포함)
    - Session 1 : N Exam 구조 대응
    - 프론트는 이 Row를 기준으로 우측 패널(결과 상세) 연결
    """

    exam_id = serializers.IntegerField()

    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField(allow_blank=True)

    exam = ScoreBlockSerializer()
    homework = ScoreBlockSerializer()

    updated_at = serializers.DateTimeField(allow_null=True)


class SessionScoresResponseSerializer(serializers.Serializer):
    """
    리스트 응답 wrapper가 필요하면 확장 가능.
    현재 프론트 계약은 "리스트"이므로 many=True로 사용한다.
    """

    # NOTE: 실제로 이 Serializer를 직접 쓰지 않을 수 있지만,
    #       확장 포인트로 남겨둔다.
    items = SessionScoreRowSerializer(many=True)

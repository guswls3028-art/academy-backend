# apps/domains/results/serializers/wrong_note_serializers.py
from __future__ import annotations

from typing import Any, Dict, Optional
from rest_framework import serializers


class WrongNoteItemSerializer(serializers.Serializer):
    """
    오답노트 단일 문항 아이템

    ✅ 의도:
    - ResultFact/ResultItem 구조가 프로젝트마다 조금 달라도
      View에서 dict로 만들어 serialize 가능하게 "단순 Serializer"로 고정
    """

    exam_id = serializers.IntegerField()
    attempt_id = serializers.IntegerField()
    attempt_created_at = serializers.DateTimeField(allow_null=True)

    question_id = serializers.IntegerField()
    question_number = serializers.IntegerField(required=False, allow_null=True)
    answer_type = serializers.CharField(required=False, allow_blank=True)

    # 학생 답 / 정답 / 점수
    student_answer = serializers.CharField(required=False, allow_blank=True)
    correct_answer = serializers.CharField(required=False, allow_blank=True)

    is_correct = serializers.BooleanField()
    score = serializers.FloatField()
    max_score = serializers.FloatField()

    # 원본 메타 (OMR/AI 포함)
    meta = serializers.JSONField(required=False)

    # 옵션: 프론트 UX용 (문제 지문/선지/해설 등은 확장 포인트)
    extra = serializers.JSONField(required=False)


class WrongNoteListResponseSerializer(serializers.Serializer):
    """
    페이지네이션 포함 응답
    """
    count = serializers.IntegerField()
    next = serializers.IntegerField(allow_null=True)   # 다음 offset
    prev = serializers.IntegerField(allow_null=True)   # 이전 offset
    results = WrongNoteItemSerializer(many=True)

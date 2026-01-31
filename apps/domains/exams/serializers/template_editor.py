# PATH: apps/domains/exams/serializers/template_editor.py
from __future__ import annotations

from rest_framework import serializers


class TemplateEditorSummarySerializer(serializers.Serializer):
    """
    템플릿 편집 화면 초기 로딩용 요약 정보

    프론트 보장:
    - 이 응답만으로 템플릿 편집 화면 구성 가능
    """
    exam_id = serializers.IntegerField()
    title = serializers.CharField()
    subject = serializers.CharField()

    sheet_id = serializers.IntegerField()
    total_questions = serializers.IntegerField()

    has_answer_key = serializers.BooleanField()
    is_locked = serializers.BooleanField()  # derived regular 존재 여부

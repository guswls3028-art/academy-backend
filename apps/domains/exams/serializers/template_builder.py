# PATH: apps/domains/exams/serializers/template_builder.py
from rest_framework import serializers


class TemplateBuilderResultSerializer(serializers.Serializer):
    """
    Template Builder 결과 계약

    프론트 보장:
    - sheet_id / answer_key_id는 항상 존재
    """
    exam_id = serializers.IntegerField()
    sheet_id = serializers.IntegerField()
    answer_key_id = serializers.IntegerField()
    total_questions = serializers.IntegerField()

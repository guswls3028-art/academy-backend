# PATH: apps/domains/matchup/serializers.py
from rest_framework import serializers
from .models import MatchupDocument, MatchupProblem


class MatchupDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchupDocument
        fields = [
            "id", "title", "subject", "grade_level",
            "original_name", "size_bytes", "content_type",
            "status", "ai_job_id", "problem_count", "error_message",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class MatchupDocumentUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    subject = serializers.CharField(max_length=100, required=False, allow_blank=True)
    grade_level = serializers.CharField(max_length=50, required=False, allow_blank=True)


class MatchupProblemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchupProblem
        fields = [
            "id", "document_id", "number", "text",
            "image_key", "meta", "created_at",
        ]
        read_only_fields = fields


class SimilarProblemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    document_id = serializers.IntegerField()
    document_title = serializers.CharField()
    number = serializers.IntegerField()
    text = serializers.CharField()
    similarity = serializers.FloatField()
    source_type = serializers.CharField()
    source_lecture_title = serializers.CharField()
    source_session_title = serializers.CharField()
    source_exam_title = serializers.CharField()

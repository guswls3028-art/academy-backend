# PATH: apps/domains/matchup/serializers.py
from rest_framework import serializers
from .models import MatchupDocument, MatchupProblem, MatchupHitReport, MatchupHitReportEntry


class MatchupDocumentSerializer(serializers.ModelSerializer):
    inventory_file_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = MatchupDocument
        fields = [
            "id", "title", "category", "subject", "grade_level",
            "original_name", "size_bytes", "content_type",
            "status", "ai_job_id", "problem_count", "error_message",
            "meta",
            "inventory_file_id",
            "created_at", "updated_at",
        ]
        read_only_fields = fields


class MatchupDocumentUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    category = serializers.CharField(max_length=100, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=100, required=False, allow_blank=True)
    grade_level = serializers.CharField(max_length=50, required=False, allow_blank=True)
    intent = serializers.ChoiceField(choices=["reference", "test"], required=False)


class MatchupProblemSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchupProblem
        fields = [
            "id", "document_id", "number", "text",
            "image_key", "meta", "created_at",
        ]
        read_only_fields = fields


class MatchupHitReportEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchupHitReportEntry
        fields = [
            "id", "exam_problem_id", "selected_problem_ids",
            "comment", "order",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MatchupHitReportSerializer(serializers.ModelSerializer):
    entries = MatchupHitReportEntrySerializer(many=True, read_only=True)
    document_id = serializers.IntegerField(read_only=True)
    document_title = serializers.SerializerMethodField()
    document_category = serializers.SerializerMethodField()

    class Meta:
        model = MatchupHitReport
        fields = [
            "id", "document_id", "document_title", "document_category",
            "title", "summary",
            "status", "submitted_at", "submitted_by_name",
            "entries",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "document_id", "document_title", "document_category",
            "status", "submitted_at", "submitted_by_name",
            "entries", "created_at", "updated_at",
        ]

    def get_document_title(self, obj):
        return obj.document.title if obj.document_id else ""

    def get_document_category(self, obj):
        return obj.document.category if obj.document_id else ""


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

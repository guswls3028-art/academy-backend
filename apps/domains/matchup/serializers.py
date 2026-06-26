# PATH: apps/domains/matchup/serializers.py
from rest_framework import serializers
from .models import MatchupDocument, MatchupProblem, MatchupHitReport, MatchupHitReportEntry


class MatchupDocumentSerializer(serializers.ModelSerializer):
    inventory_file_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = MatchupDocument
        fields = [
            "id", "title", "category", "subject", "grade_level",
            "exam_cycle", "exam_year",
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
    # 2026-05-12 #15 — 학원장이 시험 회차/연도 분류 입력 (랜딩 학교별 grouping)
    exam_cycle = serializers.ChoiceField(
        choices=["", "midterm", "final", "mock", "other"],
        required=False, allow_blank=True,
    )
    exam_year = serializers.IntegerField(required=False, min_value=0, max_value=2100)
    # legacy 2-value (호환 유지)
    intent = serializers.ChoiceField(choices=["reference", "test"], required=False)
    # 7-value SSOT — Phase 1A. 학원장이 잘못 백필된 doc 라벨 즉시 보정 가능.
    source_type = serializers.ChoiceField(
        choices=[
            "student_exam_photo", "school_exam_pdf",
            "commercial_workbook", "academy_workbook",
            "explanation", "answer_key", "other",
        ],
        required=False,
    )


class MatchupProblemSerializer(serializers.ModelSerializer):
    public_image_key = serializers.SerializerMethodField()

    def get_public_image_key(self, obj):
        from .services import get_problem_public_image_key

        return get_problem_public_image_key(obj) or ""

    class Meta:
        model = MatchupProblem
        fields = [
            "id", "document_id", "number", "text",
            "image_key", "public_image_key", "meta", "created_at",
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
    # 강사 정체성 — 보고서의 1순위 메타데이터.
    author_id = serializers.IntegerField(read_only=True, allow_null=True)
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = MatchupHitReport
        fields = [
            "id", "document_id", "document_title", "document_category",
            "author_id", "author_name",
            "title", "summary",
            "status", "submitted_at", "submitted_by_name",
            "entries",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "document_id", "document_title", "document_category",
            "author_id", "author_name",
            "status", "submitted_at", "submitted_by_name",
            "entries", "created_at", "updated_at",
        ]

    def get_document_title(self, obj):
        return obj.document.title if obj.document_id else ""

    def get_document_category(self, obj):
        return obj.document.category if obj.document_id else ""

    def get_author_name(self, obj):
        # 작성 강사명. 본명 → username(prefix 제거) → email 순. legacy는 submitted_by_name fallback.
        if obj.author_id and obj.author is not None:
            from apps.core.models.user import user_display_username
            user = obj.author
            return (
                getattr(user, "name", None)
                or user_display_username(user)
                or getattr(user, "email", "")
                or ""
            )
        return obj.submitted_by_name or ""


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

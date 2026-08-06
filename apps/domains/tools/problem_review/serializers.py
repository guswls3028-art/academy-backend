from __future__ import annotations

from rest_framework import serializers


class ProblemReviewReportSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    status = serializers.CharField(read_only=True)
    title = serializers.CharField(read_only=True, allow_blank=True)
    source_name = serializers.CharField(read_only=True, allow_blank=True)
    source_summary = serializers.JSONField(read_only=True)
    version = serializers.IntegerField(read_only=True)
    last_error = serializers.CharField(read_only=True, allow_blank=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    draft = serializers.JSONField(read_only=True, required=False)
    artifacts = serializers.JSONField(read_only=True, required=False)


class ProblemReviewReportListSerializer(serializers.Serializer):
    reports = ProblemReviewReportSerializer(many=True, read_only=True)


class ProblemReviewReportCreateSerializer(serializers.Serializer):
    external_ai_confirmed = serializers.BooleanField()
    metadata = serializers.JSONField(required=False)
    source_files = serializers.ListField(
        child=serializers.FileField(),
        min_length=1,
        max_length=6,
    )


class ProblemReviewReportPatchSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    title = serializers.CharField(required=False, allow_blank=True, max_length=200)
    draft = serializers.JSONField()


class ProblemReviewExportRequestSerializer(serializers.Serializer):
    output_format = serializers.ChoiceField(choices=("pdf", "pptx"))


class ProblemReviewExportCreateSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    job_id = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    output_format = serializers.ChoiceField(choices=("pdf", "pptx"), read_only=True)
    report_version = serializers.IntegerField(read_only=True)
    source_fingerprint = serializers.CharField(read_only=True)
    filename = serializers.CharField(read_only=True, allow_blank=True)
    download_url = serializers.CharField(read_only=True, required=False)


class ProblemReviewExportStatusSerializer(serializers.Serializer):
    job_id = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    progress = serializers.JSONField(read_only=True, allow_null=True)
    result = serializers.JSONField(read_only=True, allow_null=True)
    error_message = serializers.CharField(read_only=True, allow_null=True)


class ProblemReviewPublishRequestSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)


class ProblemReviewPublishResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    title = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    published_at = serializers.DateTimeField(read_only=True)
    public_url = serializers.CharField(read_only=True)
    pdf_url = serializers.CharField(read_only=True)

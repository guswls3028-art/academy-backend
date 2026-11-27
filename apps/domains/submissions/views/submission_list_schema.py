"""Response projections used by the staff exam and homework submission lists."""
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.domains.submissions.models import Submission, SubmissionMedia
from .homework_file_acceptance_view import HomeworkFileAcceptanceSerializer


# These two status enums are already published by Submission and StudentHomeworkFile.
# Refer to their components so the staff projections keep the same TypeScript types.
@extend_schema_field({"$ref": "#/components/schemas/SubmissionStatusEnum"})
class SubmissionStatusField(serializers.ChoiceField):
    def __init__(self):
        super().__init__(choices=Submission.Status.choices)


@extend_schema_field({"$ref": "#/components/schemas/StudentHomeworkFileStatusEnum"})
class HomeworkFileStatusField(serializers.ChoiceField):
    def __init__(self):
        super().__init__(choices=SubmissionMedia.Status.choices)


class StaffExamSubmissionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField(help_text="제출에 지정된 수강이 없으면 0입니다.")
    student_name = serializers.CharField(allow_blank=True)
    status = SubmissionStatusField()
    archived = serializers.BooleanField()
    source = serializers.ChoiceField(choices=Submission.Source.choices)
    score = serializers.FloatField(allow_null=True)
    created_at = serializers.DateTimeField()
    file_key = serializers.CharField(allow_blank=True)
    has_file = serializers.BooleanField()
    manual_review_required = serializers.BooleanField()
    manual_review_reasons = serializers.ListField(child=serializers.CharField())
    identifier_status = serializers.CharField(allow_null=True)
    answer_stats = serializers.DictField(allow_null=True)
    aligned = serializers.BooleanField(allow_null=True)
    alignment_method = serializers.CharField(allow_null=True)
    sheet_version = serializers.CharField(allow_null=True)


class StaffExamReviewIssuesSerializer(serializers.Serializer):
    items = StaffExamSubmissionSerializer(many=True)
    total = serializers.IntegerField(help_text="커서를 적용하기 전 조건에 맞는 전체 검토 건수입니다.")
    next_cursor = serializers.IntegerField(allow_null=True)


class StaffHomeworkSubmissionFileSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="파일 UUID 또는 기존 단일 파일의 legacy-<제출 ID>입니다.")
    legacy = serializers.BooleanField()
    client_file_id = serializers.UUIDField(allow_null=True)
    upload_batch_id = serializers.UUIDField(allow_null=True)
    position = serializers.IntegerField()
    original_filename = serializers.CharField()
    media_kind = serializers.ChoiceField(choices=SubmissionMedia.Kind.choices)
    mime_type = serializers.CharField()
    file_size = serializers.IntegerField()
    status = HomeworkFileStatusField()
    error_message = serializers.CharField(allow_blank=True)
    upload_started_at = serializers.DateTimeField(allow_null=True)
    uploaded_at = serializers.DateTimeField(allow_null=True)
    failed_at = serializers.DateTimeField(allow_null=True)
    removed_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)


class StaffHomeworkSubmissionSerializer(HomeworkFileAcceptanceSerializer):
    id = serializers.IntegerField()
    enrollment_id = serializers.IntegerField(help_text="제출에 지정된 수강이 없으면 0입니다.")
    session_id = serializers.IntegerField(allow_null=True)
    session_title = serializers.CharField(allow_blank=True)
    homework_lecture_title = serializers.CharField(allow_blank=True)
    student_id = serializers.IntegerField(help_text="연결된 학생이 없으면 0입니다.")
    student_name = serializers.CharField(allow_blank=True)
    student_phone = serializers.CharField(allow_null=True)
    parent_phone = serializers.CharField(allow_null=True)
    status = SubmissionStatusField()
    source = serializers.ChoiceField(choices=Submission.Source.choices)
    file_type = serializers.CharField(allow_null=True)
    file_size = serializers.IntegerField(allow_null=True)
    files = StaffHomeworkSubmissionFileSerializer(many=True)
    media_set_fingerprint = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)
    profile_photo_url = serializers.URLField(allow_null=True)
    name_highlight_clinic_target = serializers.BooleanField()
    teacher_reviewed = serializers.BooleanField()
    teacher_review_source = serializers.ChoiceField(choices=["score", "manual"], allow_null=True)
    teacher_review_note = serializers.CharField(allow_blank=True)
    teacher_reviewed_at = serializers.DateTimeField(allow_null=True)
    teacher_review_updated_at = serializers.DateTimeField(allow_null=True)
    lecture_title = serializers.CharField(required=False, allow_blank=True)
    lecture_color = serializers.CharField(required=False, allow_null=True)
    lecture_chip_label = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class StaffHomeworkSubmissionsPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = StaffHomeworkSubmissionSerializer(many=True)

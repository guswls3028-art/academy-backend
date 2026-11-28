"""OpenAPI responses shared by the staff submission inbox and results dashboard."""

from drf_spectacular.utils import OpenApiResponse
from rest_framework import serializers


class SubmissionDetailErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()


STAFF_SUBMISSION_ERRORS = {
    401: OpenApiResponse(SubmissionDetailErrorSerializer, description="인증이 필요하거나 인증 정보가 유효하지 않습니다."),
    403: OpenApiResponse(SubmissionDetailErrorSerializer, description="현재 학원에서 교직원 권한이 필요합니다."),
}

SUBMISSION_QUERY_ERROR = OpenApiResponse(
    {
        "type": "object",
        "additionalProperties": {
            "oneOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
        },
    },
    description="잘못된 쿼리 필드 또는 detail에 오류 내용을 반환합니다.",
)


class SubmissionSessionChoiceSerializer(serializers.Serializer):
    lecture_id = serializers.IntegerField()
    lecture_title = serializers.CharField(allow_blank=True)
    session_id = serializers.IntegerField()
    session_title = serializers.CharField(allow_blank=True)


class ExamSubmissionSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    target_type = serializers.CharField()
    target_id = serializers.IntegerField()
    target_title = serializers.CharField(allow_blank=True)
    enrollment_id = serializers.IntegerField(allow_null=True)
    student_name = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    created_at = serializers.DateTimeField(allow_null=True)
    lecture_id = serializers.IntegerField(allow_null=True)
    lecture_title = serializers.CharField(allow_blank=True)
    session_id = serializers.IntegerField(allow_null=True)
    session_title = serializers.CharField(required=False, allow_blank=True)
    session_choices = SubmissionSessionChoiceSerializer(many=True, required=False)
    session_origin_recorded = serializers.BooleanField(required=False, help_text="제출에 진입 차시가 기록됐는지 여부입니다.")
    score_lecture_id = serializers.IntegerField(required=False, allow_null=True, help_text="저장된 성적의 원래 수강 강의입니다.")
    session_history_only = serializers.BooleanField(
        required=False, help_text="현재 차시에서 처리할 수 없어 과거 제출로 조회할 때만 true입니다.",
    )


class SubmissionOmrReadinessSerializer(serializers.Serializer):
    can_grade = serializers.BooleanField()
    missing = serializers.ListField(child=serializers.CharField())


class SubmissionExamRecoverySerializer(serializers.Serializer):
    action = serializers.CharField(help_text="시험 설정 보완이 필요할 때 configure_exam입니다.")
    exam_id = serializers.IntegerField()


class PendingExamSubmissionSerializer(ExamSubmissionSummarySerializer):
    enrollment_id = serializers.IntegerField(help_text="연결된 수강이 없으면 0입니다.")
    source = serializers.CharField()
    target_resolved = serializers.BooleanField()
    target_resolved_reason = serializers.CharField(
        allow_null=True, help_text="대상을 찾으면 null, 그 밖에는 target_missing 또는 session_missing입니다.",
    )
    omr_readiness = SubmissionOmrReadinessSerializer(allow_null=True)
    recovery = SubmissionExamRecoverySerializer(allow_null=True)
    is_discarded = serializers.BooleanField()
    discard_reason = serializers.CharField(allow_null=True)
    has_file = serializers.BooleanField()
    file_name = serializers.CharField(allow_blank=True)
    file_type = serializers.CharField(allow_blank=True)
    file_size = serializers.IntegerField(allow_null=True)
    profile_photo_url = serializers.URLField(allow_null=True)


class SubmissionFailureCountsSerializer(serializers.Serializer):
    failed = serializers.IntegerField(min_value=0)
    discarded = serializers.IntegerField(min_value=0)
    real_failed = serializers.IntegerField(min_value=0)


class PendingExamSubmissionPageSerializer(serializers.Serializer):
    results = PendingExamSubmissionSerializer(many=True)
    count = serializers.IntegerField(min_value=0)
    page = serializers.IntegerField(min_value=1)
    page_size = serializers.IntegerField(min_value=1, max_value=200)
    pages = serializers.IntegerField(min_value=1)
    has_next = serializers.BooleanField()
    has_previous = serializers.BooleanField()
    summary = SubmissionFailureCountsSerializer()


class ResultsLandingStatsSerializer(serializers.Serializer):
    active_lectures = serializers.IntegerField(min_value=0)
    active_exams = serializers.IntegerField(min_value=0)
    pending_submissions = serializers.IntegerField(min_value=0)
    done_last_7d = serializers.IntegerField(min_value=0)
    failed_last_24h = serializers.IntegerField(min_value=0)
    pending_top = ExamSubmissionSummarySerializer(many=True, max_length=5)
    recent_done_top = ExamSubmissionSummarySerializer(many=True, max_length=5)

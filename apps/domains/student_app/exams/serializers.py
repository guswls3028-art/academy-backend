# apps/domains/student_app/exams/serializers.py
from rest_framework import serializers


class StudentExamSubmitRequestSerializer(serializers.Serializer):
    answers = serializers.JSONField(help_text="문항 ID별 답안 객체 또는 문항·답안 목록")
    client_request_id = serializers.UUIDField(required=False, allow_null=True)
    expected_attempt_count = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    entry_session_id = serializers.IntegerField(min_value=1, required=False, allow_null=True,
                                               help_text="학생이 시험을 연 차시. 일반 시험 목록에서는 생략")


class StudentExamSubmitReceiptSerializer(serializers.Serializer):
    submission_id = serializers.IntegerField()
    status = serializers.CharField()


class StudentExamSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    grading_mode = serializers.CharField(read_only=True)
    choice_question_count = serializers.IntegerField(read_only=True)
    essay_numbering = serializers.CharField(read_only=True)
    open_at = serializers.DateTimeField(allow_null=True)
    close_at = serializers.DateTimeField(allow_null=True)
    allow_retake = serializers.BooleanField()
    max_attempts = serializers.IntegerField()
    pass_score = serializers.IntegerField()
    description = serializers.CharField(allow_null=True, required=False)
    session_id = serializers.IntegerField(allow_null=True, required=False)
    has_result = serializers.BooleanField(default=False, required=False)
    submission_pending = serializers.BooleanField(default=False, required=False)
    learning_todo_eligible = serializers.BooleanField(default=True, required=False)
    attempt_count = serializers.IntegerField(default=0, required=False)
    student_results_published = serializers.BooleanField(default=True, required=False)

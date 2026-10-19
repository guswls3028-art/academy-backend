"""Explicit API contracts for assessment composition and parent-only feedback."""
from rest_framework import serializers


class AssessmentDetailErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()


class ParentCommentSnapshotSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField(min_value=1)
    text = serializers.CharField(allow_blank=True, max_length=2000)
    version = serializers.IntegerField(min_value=0)
    updated_by = serializers.IntegerField(allow_null=True)
    updated_at = serializers.DateTimeField(allow_null=True)


class ParentCommentRevisionSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=1)
    text = serializers.CharField(allow_blank=True, max_length=2000)
    author_id = serializers.IntegerField(allow_null=True)
    author_name = serializers.CharField()
    created_at = serializers.DateTimeField()


class AdminParentCommentResponseSerializer(ParentCommentSnapshotSerializer):
    history = ParentCommentRevisionSerializer(many=True)


class AdminParentCommentListItemSerializer(AdminParentCommentResponseSerializer):
    student_id = serializers.IntegerField(min_value=1)
    student_name = serializers.CharField()


class AdminParentCommentListResponseSerializer(serializers.Serializer):
    items = AdminParentCommentListItemSerializer(many=True)


class ParentCommentConflictSerializer(AssessmentDetailErrorSerializer):
    current = ParentCommentSnapshotSerializer()


class ParentAssessmentCommentItemSerializer(serializers.Serializer):
    session_id = serializers.IntegerField(min_value=1)
    session_title = serializers.CharField(allow_blank=True)
    lecture_title = serializers.CharField(allow_blank=True)
    assessment_title = serializers.CharField(allow_blank=True, allow_null=True)
    assessment_kind = serializers.ChoiceField(choices=["exam", "homework"])
    assessment_id = serializers.IntegerField(min_value=1)
    text = serializers.CharField(max_length=2000)
    version = serializers.IntegerField(min_value=1)
    updated_at = serializers.DateTimeField()


class ParentAssessmentCommentsResponseSerializer(serializers.Serializer):
    items = ParentAssessmentCommentItemSerializer(many=True)


class ScoreTotalConfigResponseSerializer(serializers.Serializer):
    version = serializers.IntegerField(min_value=0)
    exam_ids = serializers.ListField(child=serializers.IntegerField(min_value=1))
    homework_ids = serializers.ListField(child=serializers.IntegerField(min_value=1))
    exam_order = serializers.ListField(child=serializers.IntegerField(min_value=1))
    homework_order = serializers.ListField(child=serializers.IntegerField(min_value=1))

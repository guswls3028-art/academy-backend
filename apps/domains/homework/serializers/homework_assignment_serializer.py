from rest_framework import serializers


class HomeworkAssignmentRowSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField(allow_blank=True)
    is_selected = serializers.BooleanField()


class HomeworkAssignmentUpdateSerializer(serializers.Serializer):
    enrollment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
        required=True,
    )

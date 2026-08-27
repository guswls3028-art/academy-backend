from rest_framework import serializers
from apps.domains.exams.models import Exam, Sheet

class SheetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sheet
        fields = [
            "id",
            "exam",
            "name",
            "total_questions",
            "file",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "exam", "created_at", "updated_at"]


class SheetCreateSerializer(SheetSerializer):
    """Create keeps the owner input explicit while normal updates cannot reparent."""

    exam = serializers.PrimaryKeyRelatedField(queryset=Exam.objects.all())

    class Meta(SheetSerializer.Meta):
        read_only_fields = ["id", "created_at", "updated_at"]

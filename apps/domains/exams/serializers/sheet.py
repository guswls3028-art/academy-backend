from rest_framework import serializers
from apps.domains.exams.models import Sheet

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

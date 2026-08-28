from django.db.models import Q
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

    exam = serializers.PrimaryKeyRelatedField(
        queryset=Exam.objects.none(),
        error_messages={
            "does_not_exist": "invalid exam id",
            "incorrect_type": "invalid exam id",
        },
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = self.context.get("tenant") or getattr(request, "tenant", None)
        if tenant is not None:
            self.fields["exam"].queryset = Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
                | Q(tenant=tenant)
            ).distinct()

    class Meta(SheetSerializer.Meta):
        read_only_fields = ["id", "created_at", "updated_at"]

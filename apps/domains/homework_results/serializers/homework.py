# PATH: apps/domains/homework_results/serializers/homework.py

from rest_framework import serializers

from apps.domains.homework_results.models import Homework



class HomeworkSerializer(serializers.ModelSerializer):
    class Meta:
        model = Homework
        fields = [
            "id",
            "homework_type",
            "template_homework",
            "session",
            "title",
            "status",
            "meta",
            "display_order",
            "updated_at",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "updated_at",
            "created_at",
        ]

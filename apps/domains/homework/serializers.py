# PATH: apps/domains/homework/serializers.py
from rest_framework import serializers

from apps.domains.homework.models import HomeworkScore


class HomeworkScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeworkScore
        fields = "__all__"

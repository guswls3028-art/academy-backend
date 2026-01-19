# PATH: apps/domains/homework/serializers.py

from rest_framework import serializers
from apps.domains.homework.models import HomeworkScore, HomeworkPolicy


class HomeworkScoreSerializer(serializers.ModelSerializer):
    """
    HomeworkScore Serializer

    - backend 스냅샷 그대로 노출
    - 계산 / 판정 로직 ❌
    """

    class Meta:
        model = HomeworkScore
        fields = "__all__"


class HomeworkPolicySerializer(serializers.ModelSerializer):
    """
    HomeworkPolicy Serializer

    - Session 단위 과제 정책
    """

    class Meta:
        model = HomeworkPolicy
        fields = "__all__"

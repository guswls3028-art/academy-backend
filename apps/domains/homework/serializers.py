# PATH: apps/domains/homework/serializers.py

from apps.domains.homework.models import HomeworkScore, HomeworkPolicy

class HomeworkScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeworkScore
        fields = "__all__"


class HomeworkPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = HomeworkPolicy
        fields = "__all__"

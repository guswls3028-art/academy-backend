from rest_framework import serializers
from .models import Dday


class DdaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Dday
        fields = "__all__"

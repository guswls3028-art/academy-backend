# PATH: apps/domains/lectures/serializers.py

from rest_framework import serializers
from .models import Lecture, Session


class LectureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lecture
        fields = "__all__"
        read_only_fields = ["tenant"]
        ref_name = "Lecture"


class SessionSerializer(serializers.ModelSerializer):
    order = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = Session
        fields = "__all__"
        ref_name = "LectureSession"

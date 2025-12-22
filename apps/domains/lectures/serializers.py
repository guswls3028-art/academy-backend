# domains/lectures/serializers.py

from rest_framework import serializers

from .models import Lecture, Session


# ========================================================
# Lecture
# ========================================================

class LectureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lecture
        fields = "__all__"
        ref_name = "Lecture"


# ========================================================
# Session
# ========================================================

class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = "__all__"
        ref_name = "LectureSession"

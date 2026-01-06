# PATH: apps/domains/lectures/serializers.py

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
    """
    ✅ 프론트 표준 계약:
    - exam: 연결된 시험 ID (nullable)
    - Session 상세에서 examId는 여기서 가져온다
    """
    class Meta:
        model = Session
        fields = "__all__"
        ref_name = "LectureSession"

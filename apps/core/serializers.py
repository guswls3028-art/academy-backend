# apps/core/serializers.py

from rest_framework import serializers
from django.contrib.auth import get_user_model

from apps.core.models import Attendance, Expense

User = get_user_model()


# ------------------------------------
# User Base
# ------------------------------------

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", 
            "username", 
            "name", 
            "phone",
            "is_staff",        # ✅ 추가
            "is_superuser"    # (선택) 있으면 좋음
        ]


# ------------------------------------
# Profile
# ------------------------------------

class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "phone"]


# ------------------------------------
# Attendance
# ------------------------------------

class AttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attendance
        fields = "__all__"
        read_only_fields = ["user", "duration_hours", "amount"]


# ------------------------------------
# Expense
# ------------------------------------

class ExpenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = "__all__"
        read_only_fields = ["user"]

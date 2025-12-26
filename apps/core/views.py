# apps/core/views.py

from datetime import datetime
from django.db.models import Sum

from rest_framework.views import APIView
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema

from apps.core.models import Attendance, Expense
from apps.core.serializers import (
    UserSerializer,
    ProfileSerializer,
    AttendanceSerializer,
    ExpenseSerializer,
)


# --------------------------------------------------
# Auth: /core/me/
# --------------------------------------------------

class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        print("AUTH HEADER:", request.headers.get("Authorization"))
        print("USER:", request.user, request.user.is_authenticated)
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


# --------------------------------------------------
# Profile
# --------------------------------------------------

class ProfileViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["get"])
    def me(self, request):
        serializer = ProfileSerializer(request.user)
        return Response(serializer.data)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["patch"])
    def update_me(self, request):
        serializer = ProfileSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["post"], url_path="change-password")
    def change_password(self, request):
        old_pw = request.data.get("old_password")
        new_pw = request.data.get("new_password")

        if not old_pw or not new_pw:
            return Response({"error": "old_password, new_password 필요"}, status=400)

        if not request.user.check_password(old_pw):
            return Response({"error": "현재 비밀번호가 올바르지 않습니다."}, status=400)

        request.user.set_password(new_pw)
        request.user.save()

        return Response({"message": "비밀번호 변경 완료"})


# --------------------------------------------------
# Attendance
# --------------------------------------------------

class MyAttendanceViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AttendanceSerializer

    def get_queryset(self):
        user = self.request.user
        month = self.request.query_params.get("month")

        qs = Attendance.objects.filter(user=user)

        if month:
            qs = qs.filter(date__startswith=month)

        return qs

    def perform_create(self, serializer):
        user = self.request.user

        start = self.request.data.get("start_time")
        end = self.request.data.get("end_time")

        try:
            start_dt = datetime.strptime(start, "%H:%M")
            end_dt = datetime.strptime(end, "%H:%M")
            duration = (end_dt - start_dt).seconds / 3600
        except Exception:
            duration = 0

        hourly = 15000
        amount = int(duration * hourly)

        serializer.save(
            user=user,
            duration_hours=duration,
            amount=amount,
        )

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        user = request.user
        month = self.request.query_params.get("month")

        qs = Attendance.objects.filter(user=user)

        if month:
            qs = qs.filter(date__startswith=month)

        total_hours = qs.aggregate(Sum("duration_hours"))["duration_hours__sum"] or 0
        total_amount = qs.aggregate(Sum("amount"))["amount__sum"] or 0
        after_tax = int(total_amount * 0.967)

        return Response({
            "total_hours": total_hours,
            "total_amount": total_amount,
            "total_after_tax": after_tax,
        })


# --------------------------------------------------
# Expense
# --------------------------------------------------

class MyExpenseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ExpenseSerializer

    def get_queryset(self):
        user = self.request.user
        month = self.request.query_params.get("month")

        qs = Expense.objects.filter(user=user)

        if month:
            qs = qs.filter(date__startswith=month)

        return qs

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

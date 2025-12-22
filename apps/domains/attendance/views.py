from django.db import transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response

from .models import Attendance
from .serializers import AttendanceSerializer
from .filters import AttendanceFilter
from apps.domains.lectures.models import Session
from apps.domains.enrollment.models import Enrollment


class AttendanceViewSet(ModelViewSet):
    queryset = Attendance.objects.all().select_related(
        "session",
        "enrollment",
        "enrollment__student",
    )
    serializer_class = AttendanceSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = AttendanceFilter
    search_fields = ["enrollment__student__name"]

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        session_id = request.data.get("session")
        student_ids = request.data.get("students", [])

        if not session_id or not isinstance(student_ids, list):
            return Response({"detail": "session, students(list)는 필수입니다"}, status=400)

        session = Session.objects.get(id=session_id)
        created = []

        for sid in student_ids:
            enrollment, _ = Enrollment.objects.get_or_create(
                student_id=sid,
                lecture=session.lecture,
            )
            att, _ = Attendance.objects.get_or_create(
                enrollment=enrollment,
                session=session,
                defaults={"status": "PRESENT"},
            )
            created.append(att)

        return Response(
            AttendanceSerializer(created, many=True).data,
            status=201,
        )

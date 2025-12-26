from django.db import transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status

from .models import Attendance
from .serializers import AttendanceSerializer
from .filters import AttendanceFilter

from apps.domains.lectures.models import Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment


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
            return Response(
                {"detail": "session, students(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = Session.objects.get(id=session_id)
        created = []

        for sid in student_ids:
            # 1️⃣ 강의 수강 등록
            enrollment, _ = Enrollment.objects.get_or_create(
                student_id=sid,
                lecture=session.lecture,
                defaults={"status": "ACTIVE"},
            )

            # 2️⃣ 🔥 세션 접근 권한 (영상/자료/시험의 핵심)
            SessionEnrollment.objects.get_or_create(
                enrollment=enrollment,
                session=session,
            )

            # 3️⃣ 출석 생성
            att, _ = Attendance.objects.get_or_create(
                enrollment=enrollment,
                session=session,
                defaults={"status": "PRESENT"},
            )

            created.append(att)

        return Response(
            AttendanceSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

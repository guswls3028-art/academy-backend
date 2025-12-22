from django.db import transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response

from .models import Enrollment, SessionEnrollment
from .serializers import EnrollmentSerializer, SessionEnrollmentSerializer
from .filters import EnrollmentFilter


class EnrollmentViewSet(ModelViewSet):
    queryset = Enrollment.objects.all().select_related("student", "lecture")
    serializer_class = EnrollmentSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = EnrollmentFilter
    search_fields = ["student__name"]

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        lecture_id = request.data.get("lecture")
        student_ids = request.data.get("students", [])

        if not lecture_id or not isinstance(student_ids, list):
            return Response({"detail": "lecture, students(list)는 필수입니다"}, status=400)

        created = []
        for sid in student_ids:
            obj, _ = Enrollment.objects.get_or_create(
                lecture_id=lecture_id,
                student_id=sid,
                defaults={"status": "ACTIVE"},
            )
            created.append(obj)

        return Response(
            EnrollmentSerializer(created, many=True).data,
            status=201,
        )


class SessionEnrollmentViewSet(ModelViewSet):
    queryset = SessionEnrollment.objects.all().select_related(
        "session",
        "enrollment",
        "enrollment__student",
    )
    serializer_class = SessionEnrollmentSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["session", "enrollment"]
    search_fields = ["enrollment__student__name"]

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        session_id = request.data.get("session")
        enrollment_ids = request.data.get("enrollments", [])

        created = []
        for eid in enrollment_ids:
            obj, _ = SessionEnrollment.objects.get_or_create(
                session_id=session_id,
                enrollment_id=eid,
            )
            created.append(obj)

        return Response(
            SessionEnrollmentSerializer(created, many=True).data,
            status=201,
        )

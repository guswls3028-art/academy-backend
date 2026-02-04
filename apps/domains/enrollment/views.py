# PATH: apps/domains/enrollment/views.py

from django.db import transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError

from .models import Enrollment, SessionEnrollment
from .serializers import EnrollmentSerializer, SessionEnrollmentSerializer
from .filters import EnrollmentFilter
from apps.domains.lectures.models import Session, Lecture
from apps.domains.students.models import Student


class EnrollmentViewSet(ModelViewSet):
    serializer_class = EnrollmentSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_class = EnrollmentFilter
    search_fields = ["student__name"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return (
            Enrollment.objects
            .filter(tenant=tenant)
            .select_related("student", "lecture")
        )

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        tenant = getattr(request, "tenant", None)

        lecture_id = request.data.get("lecture")
        student_ids = request.data.get("students", [])

        if not lecture_id or not isinstance(student_ids, list):
            return Response(
                {"detail": "lecture, students(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ✅ lecture tenant 검증
        lecture = Lecture.objects.filter(
            id=lecture_id,
            tenant=tenant,
        ).first()
        if not lecture:
            raise ValidationError({"detail": "해당 학원의 강의가 아닙니다."})

        created = []
        for sid in student_ids:
            # ✅ student tenant 검증
            if not Student.objects.filter(id=sid, tenant=tenant).exists():
                raise ValidationError(
                    {"detail": f"학생(id={sid})은 현재 학원 소속이 아닙니다."}
                )

            obj, _ = Enrollment.objects.get_or_create(
                tenant=tenant,
                lecture=lecture,
                student_id=sid,
                defaults={"status": "ACTIVE"},
            )
            created.append(obj)

        return Response(
            EnrollmentSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        enrollment = self.get_object()

        SessionEnrollment.objects.filter(
            tenant=enrollment.tenant,
            enrollment=enrollment,
        ).delete()

        enrollment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SessionEnrollmentViewSet(ModelViewSet):
    serializer_class = SessionEnrollmentSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["session", "enrollment"]
    search_fields = ["enrollment__student__name"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        return (
            SessionEnrollment.objects
            .filter(tenant=tenant)
            .select_related(
                "session",
                "enrollment",
                "enrollment__student",
            )
        )

    @transaction.atomic
    @action(detail=False, methods=["post"])
    def bulk_create(self, request):
        tenant = getattr(request, "tenant", None)

        session_id = request.data.get("session")
        enrollment_ids = request.data.get("enrollments", [])

        if not session_id or not isinstance(enrollment_ids, list):
            return Response(
                {"detail": "session, enrollments(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = Session.objects.select_related("lecture").get(id=session_id)

        # ✅ session 소속 lecture tenant 검증
        if session.lecture.tenant_id != tenant.id:
            raise ValidationError({"detail": "다른 학원의 세션입니다."})

        created = []
        for eid in enrollment_ids:
            enrollment = Enrollment.objects.select_related("lecture").get(
                id=eid,
                tenant=tenant,
            )

            if enrollment.lecture_id != session.lecture_id:
                raise ValidationError(
                    {"detail": "다른 강의 수강자는 이 세션에 추가할 수 없습니다."}
                )

            obj, _ = SessionEnrollment.objects.get_or_create(
                tenant=tenant,
                session=session,
                enrollment=enrollment,
            )
            created.append(obj)

        return Response(
            SessionEnrollmentSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

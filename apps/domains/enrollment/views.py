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
from apps.domains.lectures.models import Session


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
            return Response(
                {"detail": "lecture, students(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            status=status.HTTP_201_CREATED,
        )

    # 수강 등록 삭제 시 세션 등록도 함께 삭제
    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        enrollment = self.get_object()

        SessionEnrollment.objects.filter(enrollment=enrollment).delete()
        enrollment.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


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

        if not session_id or not isinstance(enrollment_ids, list):
            return Response(
                {"detail": "session, enrollments(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = Session.objects.select_related("lecture").get(id=session_id)

        created = []
        for eid in enrollment_ids:
            enrollment = Enrollment.objects.select_related("lecture").get(id=eid)

            # 🔥 보호 로직 핵심:
            # 다른 강의 enrollment를 현재 세션에 연결하는 것 차단
            if enrollment.lecture_id != session.lecture_id:
                raise ValidationError(
                    {
                        "detail": (
                            "다른 강의에 등록된 학생은 "
                            "이 세션에 추가할 수 없습니다."
                        )
                    }
                )

            obj, _ = SessionEnrollment.objects.get_or_create(
                session=session,
                enrollment=enrollment,
            )
            created.append(obj)

        return Response(
            SessionEnrollmentSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

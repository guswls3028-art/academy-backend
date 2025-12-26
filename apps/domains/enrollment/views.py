from django.db import transaction
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.response import Response
from rest_framework import status

from .models import Enrollment, SessionEnrollment
from .serializers import EnrollmentSerializer
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
            return Response(
                {"detail": "lecture, students(list)는 필수입니다"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created = []
        for sid in student_ids:
            enrollment, _ = Enrollment.objects.get_or_create(
                lecture_id=lecture_id,
                student_id=sid,
                defaults={"status": "ACTIVE"},
            )

            # 혹시 INACTIVE로 남아있는 경우 방어
            if enrollment.status != "ACTIVE":
                enrollment.status = "ACTIVE"
                enrollment.save(update_fields=["status"])

            created.append(enrollment)

        return Response(
            EnrollmentSerializer(created, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        enrollment = self.get_object()

        # 🔥 세션 접근 권한 정리
        SessionEnrollment.objects.filter(enrollment=enrollment).delete()

        enrollment.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

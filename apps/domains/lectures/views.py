# PATH: apps/domains/lectures/views.py

from django.db.models import Max, Count, Avg, Q
from django.db.models.functions import Coalesce

from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import SearchFilter
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import PermissionDenied, NotFound

from .models import Lecture, Session
from .serializers import LectureSerializer, SessionSerializer

from apps.domains.enrollment.models import Enrollment
from apps.domains.attendance.models import Attendance
from apps.support.video.models import Video


class LectureViewSet(ModelViewSet):
    serializer_class = LectureSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["is_active", "subject"]
    search_fields = ["title", "name", "subject"]

    def get_queryset(self):
        """
        🔐 tenant 단일 진실
        """
        return Lecture.objects.filter(tenant=self.request.tenant)

    def perform_create(self, serializer):
        """
        🔐 Lecture 생성 시 tenant 강제 주입
        """
        serializer.save(tenant=self.request.tenant)

    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, pk=None):
        """
        강의 리포트 조회
        GET /api/v1/lectures/lectures/{id}/report/
        """
        lecture = self.get_object()
        tenant = request.tenant

        # 수강생 수
        enrollments = Enrollment.objects.filter(
            tenant=tenant,
            lecture=lecture,
            status="ACTIVE"
        ).select_related("student")

        # 세션 수
        sessions = Session.objects.filter(lecture=lecture)

        # 비디오 수
        videos = Video.objects.filter(
            session__lecture=lecture
        ).distinct()

        # 출결 통계
        attendances = Attendance.objects.filter(
            tenant=tenant,
            session__lecture=lecture,
            enrollment__in=enrollments
        ).select_related("session", "enrollment")

        attendance_by_status = {}
        # Attendance 모델의 choices 사용
        status_choices = [
            ("PRESENT", "출석"),
            ("LATE", "지각"),
            ("ONLINE", "온라인"),
            ("SUPPLEMENT", "보강"),
            ("EARLY_LEAVE", "조퇴"),
            ("ABSENT", "결석"),
            ("RUNAWAY", "출튀"),
            ("MATERIAL", "자료"),
            ("INACTIVE", "부재"),
            ("SECESSION", "탈퇴"),
        ]
        for status_code, _ in status_choices:
            count = attendances.filter(status=status_code).count()
            if count > 0:
                attendance_by_status[status_code] = count

        # 학생별 리포트 데이터
        students_data = []
        for enrollment in enrollments:
            student = enrollment.student

            # 비디오 진행률 계산
            student_videos = Video.objects.filter(
                session__lecture=lecture
            ).distinct()

            # TODO: 실제 비디오 진행률 계산 로직 필요
            # 현재는 기본값 반환
            completed_videos = 0
            total_videos = student_videos.count()
            avg_progress = 0.0

            # 마지막 출결 상태
            last_attendance = attendances.filter(
                enrollment=enrollment
            ).order_by("-session__date", "-session__order").first()

            students_data.append({
                "enrollment": enrollment.id,
                "student_id": student.id,
                "student_name": student.name,
                "avg_progress": avg_progress,
                "completed_videos": completed_videos,
                "total_videos": total_videos,
                "last_attendance_status": last_attendance.status if last_attendance else None,
            })

        # 요약 통계
        summary = {
            "total_students": enrollments.count(),
            "total_sessions": sessions.count(),
            "total_videos": videos.count(),
            "avg_video_progress": 0.0,  # TODO: 실제 평균 진행률 계산
            "completed_students": 0,  # TODO: 완료 학생 수 계산
        }

        return Response({
            "lecture": {
                "id": lecture.id,
                "title": lecture.title,
                "name": lecture.name,
                "subject": lecture.subject,
            },
            "summary": summary,
            "attendance_by_status": attendance_by_status,
            "students": students_data,
        })


class SessionViewSet(ModelViewSet):
    serializer_class = SessionSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["lecture", "date"]
    search_fields = ["title"]

    def get_queryset(self):
        """
        Session은 lecture를 통해 tenant가 결정됨
        """
        qs = Session.objects.select_related("lecture")
        qs = qs.filter(lecture__tenant=self.request.tenant)

        lecture = self.request.query_params.get("lecture")
        if lecture:
            qs = qs.filter(lecture_id=lecture)

        date = self.request.query_params.get("date")
        if date:
            qs = qs.filter(date=date)

        return qs.order_by("order", "id")

    def perform_create(self, serializer):
        """
        🔐 Session 생성 시 lecture.tenant 검증
        order 미제공 시 해당 강의의 max(order)+1 자동 설정
        """
        lecture = serializer.validated_data["lecture"]
        if lecture.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 강의에는 세션을 추가할 수 없습니다.")

        order = serializer.validated_data.get("order")
        if order is None:
            agg = Session.objects.filter(lecture=lecture).aggregate(Max("order"))
            order = (agg["order__max"] or 0) + 1
        serializer.save(order=order)

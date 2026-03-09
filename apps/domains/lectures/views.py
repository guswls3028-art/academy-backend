# PATH: apps/domains/lectures/views.py

from django.db.models import Max, Count, Avg, Q
from django.db.models.functions import Coalesce

from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import SearchFilter
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import PermissionDenied, NotFound

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from academy.adapters.db.django import repositories_teachers as teacher_repo
from academy.adapters.db.django import repositories_video as video_repo
from .models import Lecture, Session
from .serializers import LectureSerializer, SessionSerializer

from apps.core.models import TenantMembership
from apps.domains.attendance.models import Attendance


class LectureViewSet(ModelViewSet):
    serializer_class = LectureSerializer

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["is_active", "subject"]
    search_fields = ["title", "name", "subject"]

    def get_queryset(self):
        """
        🔐 tenant 단일 진실
        강의 목록(list)에서는 시스템용 "전체공개영상" 강의를 제외하여 강의 관리 화면에 노출되지 않도록 함.
        (해당 강의는 영상 탭/학생 앱에서 public-session API 호출 시 get_or_create 되며, 상세/링크는 retrieve로 접근 가능해야 함)
        """
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            raise PermissionDenied(
                "테넌트 컨텍스트가 없습니다. 로컬에서는 python manage.py ensure_localhost_tenant 실행 후 접속하거나, "
                "X-Tenant-Code 헤더를 설정하세요."
            )
        qs = enroll_repo.lecture_filter_tenant(tenant)
        if self.action == "list":
            qs = qs.exclude(title="전체공개영상")
        return qs

    def perform_create(self, serializer):
        """
        🔐 Lecture 생성 시 tenant 강제 주입
        """
        serializer.save(tenant=self.request.tenant)

    @action(detail=False, methods=["get"], url_path="instructor-options")
    def instructor_options(self, request):
        """
        강의 담당자 선택지: 원장(owner) + 강사(Teacher) 목록.
        원장 = TenantMembership role=owner 인 사용자(테넌트당 전체 권한). 없으면 tenant.owner_name 폴백.
        """
        tenant = request.tenant
        options = []
        seen_owner_names = set()
        # 1) 실제 등록된 원장(owner) — TenantMembership에서 조회
        for m in (
            TenantMembership.objects.filter(
                tenant=tenant,
                role="owner",
                is_active=True,
            )
            .select_related("user")
            .order_by("user__username")
        ):
            name = (getattr(m.user, "name", None) or m.user.username or "").strip()
            if name and name not in seen_owner_names:
                seen_owner_names.add(name)
                options.append({"name": name, "type": "owner"})
        # 2) owner가 한 명도 없을 때만 tenant.owner_name 폴백
        if not seen_owner_names and tenant.owner_name and tenant.owner_name.strip():
            n = tenant.owner_name.strip()
            options.append({"name": n, "type": "owner"})
        # 3) 강사(Teacher) 목록
        for t in teacher_repo.teacher_filter_tenant_active(tenant):
            name = (t.name or "").strip()
            if name and not any(o["name"] == name and o["type"] == "teacher" for o in options):
                options.append({"name": name, "type": "teacher"})
        return Response(options)

    @action(detail=True, methods=["get"], url_path="report")
    def report(self, request, pk=None):
        """
        강의 리포트 조회
        GET /api/v1/lectures/lectures/{id}/report/
        """
        lecture = self.get_object()
        tenant = request.tenant

        # 수강생 수 (일단 삭제 학생 제외)
        enrollments = enroll_repo.enrollment_filter_lecture_active_students(tenant, lecture)

        # 세션 수
        sessions = enroll_repo.get_sessions_by_lecture(lecture)

        # 비디오 수
        videos = video_repo.video_filter_by_lecture(lecture)

        # 출결 통계
        attendances = enroll_repo.get_attendances_for_lecture(
            tenant, lecture, enrollments
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
            student_videos = video_repo.video_filter_by_lecture(lecture)

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
        qs = enroll_repo.session_queryset_select_related_lecture()
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
            agg = enroll_repo.session_aggregate_max_order(lecture)
            order = (agg["max_order"] or 0) + 1
        serializer.save(order=order)

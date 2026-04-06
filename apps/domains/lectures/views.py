# PATH: apps/domains/lectures/views.py

from django.db import transaction
from django.db.models import Max, Count, Avg, Q
from django.db.models.functions import Coalesce

from rest_framework.viewsets import ModelViewSet
from rest_framework.filters import SearchFilter
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status as http_status
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import PermissionDenied, NotFound, ValidationError

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from academy.adapters.db.django import repositories_teachers as teacher_repo
from academy.adapters.db.django import repositories_video as video_repo
from .models import Lecture, Session, Section, SectionAssignment
from .serializers import (
    LectureSerializer,
    SessionSerializer,
    SectionSerializer,
    SectionAssignmentSerializer,
)

from apps.core.models import TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment
from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import TenantResolvedAndStaff


class LectureViewSet(ModelViewSet):
    serializer_class = LectureSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["is_active", "subject"]
    search_fields = ["title", "name", "subject"]

    def get_queryset(self):
        """
        🔐 tenant 단일 진실
        강의 목록(list)에서는 시스템용 강의(is_system=True)를 제외하여 강의 관리 화면에 노출되지 않도록 함.
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
            qs = qs.exclude(is_system=True)
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

        # 출결 통계 — single aggregate query instead of 10 separate .filter().count()
        attendances = enroll_repo.get_attendances_for_lecture(
            tenant, lecture, enrollments
        ).select_related("session", "enrollment")

        # One query: GROUP BY status → {status: count}
        status_counts = attendances.values("status").annotate(cnt=Count("id"))
        attendance_by_status = {
            row["status"]: row["cnt"]
            for row in status_counts
            if row["cnt"] > 0
        }

        # Prefetch all attendances into memory, grouped by enrollment_id.
        # Order by session date/order descending so index [0] = latest.
        all_attendance_rows = list(
            attendances.order_by("-session__date", "-session__order")
            .values_list("enrollment_id", "status")
        )
        latest_attendance_by_enrollment = {}
        for enrollment_id, status in all_attendance_rows:
            # First occurrence per enrollment_id is the latest (due to ordering)
            if enrollment_id not in latest_attendance_by_enrollment:
                latest_attendance_by_enrollment[enrollment_id] = status

        # Video count — same for every student, fetch once outside the loop
        total_videos = videos.count()

        # 학생별 리포트 데이터
        students_data = []
        for enrollment in enrollments:
            student = enrollment.student

            students_data.append({
                "enrollment": enrollment.id,
                "student_id": student.id,
                "student_name": student.name,
                "avg_progress": 0.0,
                "completed_videos": 0,
                "total_videos": total_videos,
                "last_attendance_status": latest_attendance_by_enrollment.get(enrollment.id),
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
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["lecture", "date", "section"]
    search_fields = ["title"]

    def get_queryset(self):
        """
        Session은 lecture를 통해 tenant가 결정됨.
        section 필터 지원 (section_mode=true일 때).
        """
        qs = enroll_repo.session_queryset_select_related_lecture()
        qs = qs.select_related("section")
        qs = qs.filter(lecture__tenant=self.request.tenant)

        lecture = self.request.query_params.get("lecture")
        if lecture:
            qs = qs.filter(lecture_id=lecture)

        date = self.request.query_params.get("date")
        if date:
            qs = qs.filter(date=date)

        section = self.request.query_params.get("section")
        if section:
            qs = qs.filter(section_id=section)

        section_type = self.request.query_params.get("section_type")
        if section_type:
            qs = qs.filter(section__section_type=section_type)

        return qs.order_by("order", "id")

    def perform_create(self, serializer):
        """
        🔐 Session 생성 시 lecture.tenant 검증 + section 일관성 검증
        order 미제공 시 해당 강의(+반)의 max(order)+1 자동 설정
        """
        lecture = serializer.validated_data["lecture"]
        if lecture.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 강의에는 세션을 추가할 수 없습니다.")

        section = serializer.validated_data.get("section")
        if section:
            if section.lecture_id != lecture.id:
                raise ValidationError("반은 해당 강의에 속한 반이어야 합니다.")
            if section.tenant_id != self.request.tenant.id:
                raise PermissionDenied("다른 학원의 반에 세션을 추가할 수 없습니다.")

        order = serializer.validated_data.get("order")
        if order is None:
            if section:
                # section_mode: 반 내 순번
                agg = Session.objects.filter(
                    lecture=lecture, section=section,
                ).aggregate(max_order=Max("order"))
            else:
                agg = enroll_repo.session_aggregate_max_order(lecture)
            order = (agg["max_order"] or 0) + 1
        serializer.save(order=order)

    def perform_update(self, serializer):
        """
        🔐 Session 수정 시 lecture FK 변경 → 테넌트 검증 + section 일관성 검증
        """
        lecture = serializer.validated_data.get("lecture", serializer.instance.lecture)
        if lecture.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 강의로 세션을 이동할 수 없습니다.")

        section = serializer.validated_data.get("section", serializer.instance.section)
        if section:
            if section.lecture_id != lecture.id:
                raise ValidationError("반은 해당 강의에 속한 반이어야 합니다.")
            if section.tenant_id != self.request.tenant.id:
                raise PermissionDenied("다른 학원의 반으로 세션을 이동할 수 없습니다.")

        serializer.save()


class SectionViewSet(ModelViewSet):
    """
    반 (Section) CRUD.
    section_mode=true인 학원에서 강의 내 반 관리.
    """

    serializer_class = SectionSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["lecture", "section_type", "is_active"]
    search_fields = ["label"]

    def get_queryset(self):
        qs = Section.objects.filter(tenant=self.request.tenant)
        qs = qs.select_related("lecture")
        qs = qs.annotate(
            assignment_count=Count(
                "class_assignments",
                filter=Q(class_assignments__enrollment__status="ACTIVE"),
            )
        )
        return qs

    def perform_create(self, serializer):
        lecture = serializer.validated_data["lecture"]
        if lecture.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 강의에 반을 추가할 수 없습니다.")
        serializer.save(tenant=self.request.tenant)

    def perform_update(self, serializer):
        lecture = serializer.validated_data.get("lecture", serializer.instance.lecture)
        if lecture.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 강의로 반을 이동할 수 없습니다.")
        serializer.save()

    @action(detail=False, methods=["post"], url_path="bulk-create-sessions")
    def bulk_create_sessions(self, request):
        """
        반별 차시 일괄 생성.
        같은 order의 차시를 모든 반에 한번에 생성.
        POST { "lecture_id": 1, "title": "1차시", "dates": {"A": "2026-04-09", "B": "2026-04-10"} }
        """
        tenant = request.tenant
        lecture_id = request.data.get("lecture_id")
        title = request.data.get("title", "")
        dates = request.data.get("dates", {})  # {section_label: date_string}

        if not lecture_id or not dates:
            raise ValidationError("lecture_id와 dates는 필수입니다.")

        lecture = Lecture.objects.filter(id=lecture_id, tenant=tenant).first()
        if not lecture:
            raise NotFound("강의를 찾을 수 없습니다.")

        sections = Section.objects.filter(
            tenant=tenant, lecture=lecture, label__in=dates.keys(),
        )
        section_map = {s.label: s for s in sections}

        created = []
        with transaction.atomic():
            for label, date_str in dates.items():
                section = section_map.get(label)
                if not section:
                    continue
                # per-section max order 계산
                agg = Session.objects.filter(
                    lecture=lecture, section=section,
                ).aggregate(max_order=Max("order"))
                next_order = (agg["max_order"] or 0) + 1
                session = Session.objects.create(
                    lecture=lecture,
                    section=section,
                    order=next_order,
                    title=title or f"{next_order}차시",
                    date=date_str,
                )
                created.append(SessionSerializer(session).data)

        return Response(created, status=http_status.HTTP_201_CREATED)


class SectionAssignmentViewSet(ModelViewSet):
    """
    정규편성 관리.
    학생의 반 배정 CRUD + 자동배정 기능.
    """

    serializer_class = SectionAssignmentSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["class_section", "clinic_section", "source", "enrollment__lecture"]

    def get_queryset(self):
        qs = SectionAssignment.objects.filter(tenant=self.request.tenant)
        qs = qs.select_related(
            "enrollment__student",
            "enrollment__lecture",
            "class_section",
            "clinic_section",
        )
        return qs

    def perform_create(self, serializer):
        enrollment = serializer.validated_data["enrollment"]
        if enrollment.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 수강에 편성할 수 없습니다.")
        class_section = serializer.validated_data["class_section"]
        if class_section.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 반에 배정할 수 없습니다.")
        if class_section.lecture_id != enrollment.lecture_id:
            raise ValidationError("수업 반은 수강 중인 강의의 반이어야 합니다.")
        clinic_section = serializer.validated_data.get("clinic_section")
        if clinic_section:
            if clinic_section.tenant_id != self.request.tenant.id:
                raise PermissionDenied("다른 학원의 클리닉 반에 배정할 수 없습니다.")
            if clinic_section.lecture_id != enrollment.lecture_id:
                raise ValidationError("클리닉 반은 수강 중인 강의의 반이어야 합니다.")
        serializer.save(tenant=self.request.tenant)

    def perform_update(self, serializer):
        enrollment = serializer.validated_data.get("enrollment", serializer.instance.enrollment)
        if enrollment.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 수강에 편성할 수 없습니다.")
        class_section = serializer.validated_data.get("class_section", serializer.instance.class_section)
        if class_section.tenant_id != self.request.tenant.id:
            raise PermissionDenied("다른 학원의 반에 배정할 수 없습니다.")
        if class_section.lecture_id != enrollment.lecture_id:
            raise ValidationError("수업 반은 수강 중인 강의의 반이어야 합니다.")
        clinic_section = serializer.validated_data.get("clinic_section", serializer.instance.clinic_section)
        if clinic_section:
            if clinic_section.tenant_id != self.request.tenant.id:
                raise PermissionDenied("다른 학원의 클리닉 반에 배정할 수 없습니다.")
            if clinic_section.lecture_id != enrollment.lecture_id:
                raise ValidationError("클리닉 반은 수강 중인 강의의 반이어야 합니다.")
        serializer.save()

    @action(detail=False, methods=["post"], url_path="auto-assign")
    def auto_assign(self, request):
        """
        미편성 학생 자동배정.
        POST { "lecture_id": 1, "section_type": "CLASS" }
        각 반의 현재 인원과 max_capacity를 고려하여 균등 배분.
        """
        tenant = request.tenant
        lecture_id = request.data.get("lecture_id")
        section_type = request.data.get("section_type", "CLASS")

        if not lecture_id:
            raise ValidationError("lecture_id는 필수입니다.")

        lecture = Lecture.objects.filter(id=lecture_id, tenant=tenant).first()
        if not lecture:
            raise NotFound("강의를 찾을 수 없습니다.")

        sections = list(
            Section.objects.filter(
                tenant=tenant, lecture=lecture,
                section_type=section_type, is_active=True,
            ).order_by("label")
        )
        if not sections:
            raise ValidationError(f"{section_type} 타입의 활성 반이 없습니다.")

        # 이미 편성된 enrollment IDs (MANUAL 배정 포함 — 자동배정으로 덮어쓰지 않음)
        if section_type == "CLASS":
            assigned_ids = set(
                SectionAssignment.objects.filter(
                    tenant=tenant, enrollment__lecture=lecture,
                ).values_list("enrollment_id", flat=True)
            )
            # MANUAL 배정은 별도로 추적하여 update_or_create에서 보호
            manual_ids = set(
                SectionAssignment.objects.filter(
                    tenant=tenant, enrollment__lecture=lecture,
                    source="MANUAL",
                ).values_list("enrollment_id", flat=True)
            )
        else:
            assigned_ids = set(
                SectionAssignment.objects.filter(
                    tenant=tenant, enrollment__lecture=lecture,
                ).exclude(clinic_section__isnull=True)
                .values_list("enrollment_id", flat=True)
            )
            # CLINIC 자동배정도 MANUAL 보호
            manual_ids = set(
                SectionAssignment.objects.filter(
                    tenant=tenant, enrollment__lecture=lecture,
                    source="MANUAL",
                ).exclude(clinic_section__isnull=True)
                .values_list("enrollment_id", flat=True)
            )

        # 미편성 활성 수강생 (MANUAL 배정된 학생도 제외 — 자동배정으로 덮어쓰지 않음)
        excluded_ids = assigned_ids | manual_ids
        unassigned = Enrollment.objects.filter(
            tenant=tenant, lecture=lecture, status="ACTIVE",
        ).exclude(id__in=excluded_ids)

        unassigned_list = list(unassigned)
        if not unassigned_list:
            return Response({"message": "미편성 학생이 없습니다.", "assigned": 0})

        # 현재 반별 인원수 → 균등 배분
        if section_type == "CLASS":
            counts = {s.id: SectionAssignment.objects.filter(
                tenant=tenant, class_section=s,
            ).count() for s in sections}
        else:
            counts = {s.id: SectionAssignment.objects.filter(
                tenant=tenant, clinic_section=s,
            ).count() for s in sections}

        assigned_count = 0
        skipped_count = 0
        with transaction.atomic():
            for enrollment in unassigned_list:
                # 가장 인원 적은 반 중 ���용 가능한 반 선택
                available = [
                    s for s in sections
                    if s.max_capacity is None or counts.get(s.id, 0) < s.max_capacity
                ]
                if not available:
                    skipped_count += len(unassigned_list) - assigned_count
                    break

                target = min(available, key=lambda s: counts.get(s.id, 0))

                if section_type == "CLASS":
                    SectionAssignment.objects.update_or_create(
                        tenant=tenant,
                        enrollment=enrollment,
                        defaults={
                            "class_section": target,
                            "source": "AUTO",
                        },
                    )
                else:
                    # clinic: 기존 assignment에 clinic_section 업데이트
                    assignment = SectionAssignment.objects.filter(
                        tenant=tenant, enrollment=enrollment,
                    ).first()
                    if assignment:
                        assignment.clinic_section = target
                        assignment.source = "AUTO"
                        assignment.save(update_fields=["clinic_section", "source", "updated_at"])
                    else:
                        # class 편성이 없으면 CLINIC 자동배정 불가 → skip
                        skipped_count += 1
                        continue

                counts[target.id] = counts.get(target.id, 0) + 1
                assigned_count += 1

        msg = f"{assigned_count}명 자동 배정 완료."
        if skipped_count:
            msg += f" {skipped_count}명은 수용 ���과 또는 수업 반 미편성으로 건너뜀."
        return Response({
            "message": msg,
            "assigned": assigned_count,
            "skipped": skipped_count,
        })

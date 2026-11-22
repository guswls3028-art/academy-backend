import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.dawon_preview_identity import matches_original
from apps.core.models import TenantMembership
from apps.domains.students import dawon_schema as schema
from apps.domains.students.models import Student


class DemoRoleInput(serializers.Serializer):
    role = serializers.ChoiceField(choices=["admin", "teacher", "student", "guardian"])


class DawonDemoSessionThrottle(AnonRateThrottle):
    # Keep the configured anon rate and client identity, with a login-only counter.
    cache_format = "throttle_dawon_demo_session_%(ident)s"


class DawonDemoSessionView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [DawonDemoSessionThrottle]

    @extend_schema(request=DemoRoleInput, responses={200: schema.token_pair})
    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) or not tenant or (tenant.pk, tenant.code) != (719, "dawon"):
            raise NotFound()

        intent = DemoRoleInput(data=request.data)
        intent.is_valid(raise_exception=True)
        role = intent.validated_data["role"]
        if role == "admin":
            user = get_user_model().objects.filter(
                tenant=tenant, username="t719_dawon-demo-admin", is_active=True,
                is_staff=False, is_superuser=False,
            ).first()
            if user and (user.has_usable_password() or TenantMembership.objects.filter(
                user=user, is_active=True,
            ).exclude(tenant=tenant, role="admin").exists()):
                raise NotFound()
            expected = "admin"
        elif role == "teacher":
            user = get_user_model().objects.filter(tenant=tenant, username="t719_dawon-demo-teacher", is_active=True).first()
            expected = "teacher"
        else:
            sample = Student.objects.filter(
                tenant=tenant, pk=2, name__in=("다원 수업 검수 학생 2", "김학생 2"), deleted_at__isnull=True, is_managed=True,
            ).select_related("user", "parent__user").first()
            if sample and sample.name == "김학생 2" and not matches_original(sample):
                raise NotFound()
            if role == "guardian":
                parent = sample.parent if sample else None
                user = parent.user if parent and parent.tenant_id == tenant.pk else None
                if user:
                    from apps.domains.parents.services import parent_account_username

                    if (
                        not re.fullmatch(r"0100000\d{4}", parent.phone)
                        or user.username != parent_account_username(tenant=tenant, parent_phone=parent.phone)
                        or user.tenant_id != tenant.pk or user.is_staff or user.is_superuser
                    ):
                        raise NotFound()
                expected = "parent"
                if user and Student.objects.filter(tenant=tenant, parent=parent, deleted_at__isnull=True).exclude(pk=sample.pk).exists():
                    raise NotFound()
            else:
                user = sample.user if sample else None
                expected = "student"
        if not user or not user.is_active or not TenantMembership.objects.filter(
            tenant=tenant, user=user, role=expected, is_active=True,
        ).exists():
            raise NotFound("체험 계정을 확인할 수 없습니다.")
        if role == "student":
            from apps.domains.clinic.contracts import ensure_demo_clinic_sessions
            ensure_demo_clinic_sessions(tenant)

        end = timezone.now() + timedelta(hours=2)
        jwt = RefreshToken.for_user(user)
        jwt["tenant_id"] = tenant.pk
        jwt["token_version"] = user.token_version
        jwt["mcp"] = False
        jwt["dawon_demo_role"] = role
        jwt["dawon_demo_until"] = int(end.timestamp())
        jwt.set_exp(lifetime=timedelta(hours=2))
        response = Response({"access": str(jwt.access_token), "refresh": str(jwt)})
        response["Cache-Control"] = "no-store"
        return response

class DawonDemoLearningThrottle(AnonRateThrottle):
    """Calendar browsing must not consume passwordless login capacity."""

    scope = "dawon_demo_learning"
    rate = "240/minute"


class DawonDemoLearningView(APIView):
    """Read-only projection of the designated public sample's enrolled lessons."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [DawonDemoLearningThrottle]

    @extend_schema(responses={200: schema.learning})
    def get(self, request):
        from collections import defaultdict
        from django.db.models import F
        from apps.domains.attendance.models import Attendance
        from apps.domains.homework_results.models import Homework
        from apps.support.results.student_result_dependencies import active_enrollments_for_student
        from apps.support.student_app.results_summary import build_student_grades_summary
        from apps.domains.video.models import Video
        from apps.domains.video.services.access_resolver import AccessMode, get_effective_access_mode
        from apps.support.student_app.session_dependencies import get_active_student_session_ids, get_student_lecture_sessions

        tenant = getattr(request, "tenant", None)
        if not getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) or not tenant or (tenant.pk, tenant.code) != (719, "dawon"):
            raise NotFound()
        sample = Student.objects.filter(
            tenant=tenant, pk=2, name__in=("다원 수업 검수 학생 2", "김학생 2"), deleted_at__isnull=True, is_managed=True,
        ).first()
        if sample is None or (sample.name == "김학생 2" and not matches_original(sample)):
            raise NotFound()
        enrollments = {
            row.lecture_id: row for row in active_enrollments_for_student(tenant=tenant, student=sample)
        }
        enrolled_ids = [row.pk for row in enrollments.values()]
        sessions = get_student_lecture_sessions(
            session_ids=get_active_student_session_ids(student=sample, tenant=tenant),
            tenant=tenant, student=sample, hidden_before=sample.schedule_hidden_before,
            hidden_session_ids=set(sample.schedule_hidden_ids or []),
        )
        sessions = [s for s in sessions if s.lecture_id in enrollments]
        ids = [s.pk for s in sessions]
        attendances = {
            a.session_id: a.get_status_display() for a in Attendance.objects.filter(
                tenant=tenant, session_id__in=ids, enrollment_id__in=enrolled_ids,
                enrollment__lecture_id=F("session__lecture_id"),
            ).only("session_id", "status")
        }
        grades = build_student_grades_summary(tenant=tenant, student=sample, include_grade_board_details=False)
        allowed_pairs = {(s.pk, enrollments[s.lecture_id].pk) for s in sessions}
        homework = defaultdict(list)
        exams = defaultdict(list)
        assigned = [g for g in grades["homeworks"] if (g["session_id"], g["enrollment_id"]) in allowed_pairs]
        metadata = {
            (pk, session_id): meta if isinstance(meta, dict) else {}
            for pk, session_id, meta in Homework.objects.filter(
                tenant=tenant, session_id__in=ids, pk__in={g["homework_id"] for g in assigned},
            ).values_list("pk", "session_id", "meta")
        }
        for grade in assigned:
            session_id, pk = grade["session_id"], grade["homework_id"]
            meta = metadata.get((pk, session_id))
            if meta is None:
                continue
            homework[session_id].append({
                "id": pk, "title": grade["title"], "state": grade["submission_state"],
                **{field: meta[field] if isinstance(meta.get(field), str) else None
                   for field in ("student_instructions", "due_date", "submission_guide")},
            })
        for grade in grades["exams"]:
            if (grade["session_id"], grade["enrollment_id"]) in allowed_pairs:
                available = grade["total_score"] is not None and not grade.get("is_provisional") and not grade.get("grading_status")
                if available:
                    attempt_status = "채점 완료"
                elif grade.get("submission_pending") or grade.get("grading_status") or grade.get("is_provisional"):
                    attempt_status = "채점 중"
                elif grade.get("meta_status") == "NOT_SUBMITTED":
                    attempt_status = "미응시"
                else:
                    attempt_status = "결과 대기"
                exams[grade["session_id"]].append({
                    "id": grade["exam_id"], "title": grade["title"], "score": grade["total_score"], "max": grade["max_score"],
                    "result_available": available, "attempt_status": attempt_status,
                })
        videos = defaultdict(list)
        for video in Video.objects.filter(tenant=tenant, session_id__in=ids, status="READY").select_related("session"):
            if get_effective_access_mode(video=video, enrollment=enrollments[video.session.lecture_id]) != AccessMode.BLOCKED:
                videos[video.session_id].append({"id": video.pk, "title": video.title})
        from apps.domains.students.services.dawon_demo_learner import learner_topics, session_materials

        materials = session_materials(tenant=tenant, session_ids=ids)
        response = Response({"sessions": [{
            "id": s.pk, "lecture_id": s.lecture_id, "lecture": s.lecture.title, "title": s.title or s.display_label,
            "order": s.order, "session_label": s.display_label,
            "date": s.effective_date, "time": getattr(s, "schedule_start_time", None),
            "attendance": attendances.get(s.pk), "videos": [item["title"] for item in videos[s.pk]],
            "video_items": videos[s.pk], "materials": materials.get(s.pk, []),
            "exams": exams[s.pk], "homework": homework[s.pk],
        } for s in sessions]})
        response.data.update(learner_topics(tenant=tenant, student=sample))
        response["Cache-Control"] = "no-store"
        return response

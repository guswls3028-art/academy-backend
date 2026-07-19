from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.progress.models import (
    ClinicLink,
    LectureProgress,
    ProgressPolicy,
    RiskLog,
    SessionProgress,
)
from apps.domains.progress.serializers import (
    ClinicLinkSerializer,
    LectureProgressSerializer,
    ProgressPolicySerializer,
    RiskLogSerializer,
    SessionProgressSerializer,
)
from apps.domains.progress.views import (
    ClinicLinkViewSet,
    LectureProgressViewSet,
    ProgressPolicyViewSet,
    RiskLogViewSet,
    SessionProgressViewSet,
)
User = get_user_model()
Enrollment = django_apps.get_model("enrollment", "Enrollment")
Lecture = django_apps.get_model("lectures", "Lecture")
Session = django_apps.get_model("lectures", "Session")
Student = django_apps.get_model("students", "Student")


class ProgressViewSetTenantIsolationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.mine = self._make_tenant_data("progress-mine")
        self.foreign = self._make_tenant_data("progress-foreign")
        self.teacher = User.objects.create_user(
            username="progress-teacher",
            password="test1234",
            tenant=self.mine["tenant"],
        )
        TenantMembership.objects.create(
            tenant=self.mine["tenant"],
            user=self.teacher,
            role="teacher",
        )

    @staticmethod
    def _make_tenant_data(code):
        tenant = Tenant.objects.create(code=code, name=code, is_active=True)
        student_user = User.objects.create_user(
            username=f"{code}-student",
            password="test1234",
            tenant=tenant,
        )
        TenantMembership.objects.create(
            tenant=tenant,
            user=student_user,
            role="student",
        )
        student = Student.objects.create(
            tenant=tenant,
            user=student_user,
            ps_number=f"PS-{code}",
            omr_code=f"{tenant.id:08d}",
            name=code,
            parent_phone="01012345678",
        )
        lecture = Lecture.objects.create(
            tenant=tenant,
            title=code,
            name=code,
            subject="MATH",
        )
        session = Session.objects.create(lecture=lecture, order=1, title="1차시")
        enrollment = Enrollment.objects.create(
            tenant=tenant,
            student=student,
            lecture=lecture,
            status="ACTIVE",
        )
        return {
            "tenant": tenant,
            "student_user": student_user,
            "lecture": lecture,
            "session": session,
            "enrollment": enrollment,
        }

    def _request(self, method, path, data=None, *, user=None):
        request = getattr(self.factory, method)(path, data or {}, format="json")
        request.tenant = self.mine["tenant"]
        force_authenticate(request, user=user or self.teacher)
        return request

    def test_policy_post_and_patch_are_tenant_scoped(self):
        mine_second_lecture = Lecture.objects.create(
            tenant=self.mine["tenant"],
            title="mine-second",
            name="mine-second",
            subject="MATH",
        )
        create_view = ProgressPolicyViewSet.as_view({"post": "create"})

        response = create_view(
            self._request(
                "post",
                "/api/v1/progress/policies/",
                {"lecture": self.mine["lecture"].id},
            )
        )
        self.assertEqual(response.status_code, 201, response.data)
        policy = ProgressPolicy.objects.get()

        cross_create = create_view(
            self._request(
                "post",
                "/api/v1/progress/policies/",
                {"lecture": self.foreign["lecture"].id},
            )
        )
        self.assertEqual(cross_create.status_code, 400, cross_create.data)

        update_view = ProgressPolicyViewSet.as_view({"patch": "partial_update"})
        cross_patch = update_view(
            self._request(
                "patch",
                f"/api/v1/progress/policies/{policy.id}/",
                {"lecture": self.foreign["lecture"].id},
            ),
            pk=policy.id,
        )
        self.assertEqual(cross_patch.status_code, 400, cross_patch.data)

        same_tenant_patch = update_view(
            self._request(
                "patch",
                f"/api/v1/progress/policies/{policy.id}/",
                {"lecture": mine_second_lecture.id},
            ),
            pk=policy.id,
        )
        self.assertEqual(same_tenant_patch.status_code, 200, same_tenant_patch.data)

    def test_risk_log_post_and_patch_are_tenant_scoped(self):
        create_view = RiskLogViewSet.as_view({"post": "create"})
        valid_payload = {
            "enrollment": self.mine["enrollment"].id,
            "session": self.mine["session"].id,
            "risk_level": RiskLog.RiskLevel.WARNING,
            "rule": RiskLog.Rule.OTHER,
        }

        response = create_view(
            self._request("post", "/api/v1/progress/risk-logs/", valid_payload)
        )
        self.assertEqual(response.status_code, 201, response.data)
        risk_log = RiskLog.objects.get()

        for field_name, foreign_id in (
            ("enrollment", self.foreign["enrollment"].id),
            ("session", self.foreign["session"].id),
        ):
            payload = dict(valid_payload)
            payload[field_name] = foreign_id
            with self.subTest(post_field=field_name):
                cross_create = create_view(
                    self._request("post", "/api/v1/progress/risk-logs/", payload)
                )
                self.assertEqual(cross_create.status_code, 400, cross_create.data)

        update_view = RiskLogViewSet.as_view({"patch": "partial_update"})
        for field_name, foreign_id in (
            ("enrollment", self.foreign["enrollment"].id),
            ("session", self.foreign["session"].id),
        ):
            with self.subTest(patch_field=field_name):
                cross_patch = update_view(
                    self._request(
                        "patch",
                        f"/api/v1/progress/risk-logs/{risk_log.id}/",
                        {field_name: foreign_id},
                    ),
                    pk=risk_log.id,
                )
                self.assertEqual(cross_patch.status_code, 400, cross_patch.data)

    def test_student_cannot_write_progress_models(self):
        response = ProgressPolicyViewSet.as_view({"post": "create"})(
            self._request(
                "post",
                "/api/v1/progress/policies/",
                {"lecture": self.mine["lecture"].id},
                user=self.mine["student_user"],
            )
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProgressPolicy.objects.exists())

    def test_student_cannot_list_student_specific_progress_models(self):
        cases = (
            (SessionProgressViewSet, "/api/v1/progress/session-progress/"),
            (LectureProgressViewSet, "/api/v1/progress/lecture-progress/"),
            (ClinicLinkViewSet, "/api/v1/progress/clinic-links/"),
            (RiskLogViewSet, "/api/v1/progress/risk-logs/"),
        )

        for viewset, path in cases:
            view = viewset.as_view({"get": "list"})
            with self.subTest(viewset=viewset.__name__):
                student_response = view(
                    self._request(
                        "get",
                        path,
                        user=self.mine["student_user"],
                    )
                )
                self.assertEqual(student_response.status_code, 403)

                teacher_response = view(self._request("get", path))
                self.assertEqual(teacher_response.status_code, 200, teacher_response.data)

    def test_student_can_still_list_non_personal_progress_policy(self):
        response = ProgressPolicyViewSet.as_view({"get": "list"})(
            self._request(
                "get",
                "/api/v1/progress/policies/",
                user=self.mine["student_user"],
            )
        )

        self.assertEqual(response.status_code, 200, response.data)

    def test_clinic_link_patch_cannot_change_tenant(self):
        clinic_link = ClinicLink.objects.create(
            tenant=self.mine["tenant"],
            enrollment=self.mine["enrollment"],
            session=self.mine["session"],
            reason=ClinicLink.Reason.TEACHER_RECOMMEND,
        )
        request = self._request(
            "patch",
            f"/api/v1/progress/clinic-links/{clinic_link.id}/",
            {"tenant": self.foreign["tenant"].id, "memo": "updated"},
        )

        response = ClinicLinkViewSet.as_view({"patch": "partial_update"})(
            request,
            pk=clinic_link.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        clinic_link.refresh_from_db()
        self.assertEqual(clinic_link.tenant_id, self.mine["tenant"].id)
        self.assertEqual(clinic_link.memo, "updated")
        serializer = ClinicLinkSerializer(context={"request": request})
        self.assertNotIn("tenant", serializer.fields)

    def test_every_sibling_writable_relation_queryset_is_tenant_scoped(self):
        request = self._request("post", "/", {})
        cases = (
            (ProgressPolicySerializer, "lecture", self.mine["lecture"].id, self.foreign["lecture"].id),
            (SessionProgressSerializer, "session", self.mine["session"].id, self.foreign["session"].id),
            (LectureProgressSerializer, "lecture", self.mine["lecture"].id, self.foreign["lecture"].id),
            (LectureProgressSerializer, "last_session", self.mine["session"].id, self.foreign["session"].id),
            (ClinicLinkSerializer, "session", self.mine["session"].id, self.foreign["session"].id),
            (RiskLogSerializer, "enrollment", self.mine["enrollment"].id, self.foreign["enrollment"].id),
            (RiskLogSerializer, "session", self.mine["session"].id, self.foreign["session"].id),
        )

        for serializer_class, field_name, mine_id, foreign_id in cases:
            serializer = serializer_class(context={"request": request})
            queryset = serializer.fields[field_name].queryset
            with self.subTest(serializer=serializer_class.__name__, field=field_name):
                self.assertTrue(queryset.filter(id=mine_id).exists())
                self.assertFalse(queryset.filter(id=foreign_id).exists())

    def test_sibling_patch_endpoints_reject_cross_tenant_relations(self):
        session_progress = SessionProgress.objects.create(
            enrollment=self.mine["enrollment"],
            session=self.mine["session"],
        )
        lecture_progress = LectureProgress.objects.create(
            enrollment=self.mine["enrollment"],
            lecture=self.mine["lecture"],
            last_session=self.mine["session"],
        )
        clinic_link = ClinicLink.objects.create(
            tenant=self.mine["tenant"],
            enrollment=self.mine["enrollment"],
            session=self.mine["session"],
            reason=ClinicLink.Reason.TEACHER_RECOMMEND,
        )
        cases = (
            (
                SessionProgressViewSet,
                session_progress.id,
                {"session": self.foreign["session"].id},
            ),
            (
                LectureProgressViewSet,
                lecture_progress.id,
                {"lecture": self.foreign["lecture"].id},
            ),
            (
                LectureProgressViewSet,
                lecture_progress.id,
                {"last_session": self.foreign["session"].id},
            ),
            (
                ClinicLinkViewSet,
                clinic_link.id,
                {"session": self.foreign["session"].id},
            ),
        )

        for viewset, object_id, payload in cases:
            request = self._request("patch", f"/progress/{object_id}/", payload)
            with self.subTest(viewset=viewset.__name__, payload=payload):
                response = viewset.as_view({"patch": "partial_update"})(
                    request,
                    pk=object_id,
                )
                self.assertEqual(response.status_code, 400, response.data)

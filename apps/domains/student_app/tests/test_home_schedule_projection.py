"""Home schedule projection: preserve visible history semantics without loading it."""
import hashlib
import json
from datetime import date, time, timedelta
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import TenantMembership
from apps.domains.student_app.tests.test_session_tenant_isolation import (
    _create_lecture, _create_student, _create_tenant, _create_user,
)


class HomeScheduleProjectionTests(TestCase):
    today = date(2026, 10, 21)

    def setUp(self):
        self.day = patch("django.utils.timezone.localdate", return_value=self.today)
        self.day.start()
        self.addCleanup(self.day.stop)
        self.tenant = _create_tenant("home-schedule")
        self.user = _create_user(self.tenant, "home-student")
        self.student = _create_student(self.tenant, self.user, "A")
        self.parent_user = _create_user(self.tenant, "home-parent")
        self.parent = apps.get_model("parents", "Parent").objects.create(
            tenant=self.tenant, user=self.parent_user, phone="01022223333", name="Parent",
        )
        self.student.parent = self.parent
        self.student.save(update_fields=["parent"])
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.parent_user, role="parent")
        self.lecture = _create_lecture(self.tenant, "Home class")
        self.enrollment = apps.get_model("enrollment", "Enrollment").objects.create(
            tenant=self.tenant, student=self.student, lecture=self.lecture, status="ACTIVE",
        )
        self.order = 0

    def client_for(self, user=None, child=None):
        user = user or self.user
        token = AccessToken.for_user(user)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = user.token_version or 0
        headers = {"HTTP_HOST": "api.hakwonplus.com", "HTTP_X_TENANT_CODE": self.tenant.code}
        if child is not None:
            headers["HTTP_X_STUDENT_ID"] = str(child.pk)
        client = APIClient(**headers)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def session(self, day, *, configured=None, start=None, assigned=True):
        self.order += 1
        session = apps.get_model("lectures", "Session").objects.create(
            lecture=self.lecture, date=day, order=self.order, title=f"Class {self.order}",
        )
        membership = apps.get_model("enrollment", "SessionEnrollment").objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=session,
        )
        if configured is not None:
            schedule = apps.get_model("lectures", "SessionSchedule").objects.create(
                session=session, date=configured, start_time=start, label="Assigned",
            )
            if assigned:
                apps.get_model("lectures", "SessionScheduleMembership").objects.create(
                    session_enrollment=membership, schedule=schedule,
                )
        return session

    def clinic(self, day, *, start=time(14), booking=None, status="booked"):
        session = apps.get_model("clinic", "Session").objects.create(
            tenant=self.tenant, date=day, start_time=start, duration_minutes=180,
            max_participants=10, title="Clinic", booking_mode="time_range",
        )
        values = {}
        if booking:
            values = {"booking_start_time": booking[0], "booking_end_time": booking[1]}
        return apps.get_model("clinic", "SessionParticipant").objects.create(
            tenant=self.tenant, session=session, student=self.student, status=status, **values,
        )

    def get(self, client=None, *, scope="home"):
        path = "/api/v1/student/sessions/me/" + (f"?scope={scope}" if scope is not None else "")
        response = (client or self.client_for()).get(path)
        self.assertEqual(response.status_code, 200)
        return response

    def test_scope_preserves_28_day_boundary_all_future_and_exact_assignment_for_both_roles(self):
        cutoff = self.today - timedelta(days=27)
        old = self.session(cutoff - timedelta(days=1))
        first = self.session(cutoff)
        self.session(self.today)
        self.session(self.today + timedelta(days=1))
        self.session(self.today + timedelta(days=365))
        assigned = self.session(cutoff - timedelta(days=90), configured=self.today + timedelta(days=2), start=time(9, 5))
        self.session(self.today + timedelta(days=10), configured=cutoff - timedelta(days=1))
        unassigned = self.session(self.today + timedelta(days=20), configured=self.today + timedelta(days=3), assigned=False)
        for role, client in (("student", self.client_for()), ("parent", self.client_for(self.parent_user, self.student))):
            with self.subTest(role=role):
                full = self.get(client, scope=None)
                home = self.get(client)
                expected = [row for row in full.data if row["date"] and row["date"] >= cutoff.isoformat()]
                self.assertEqual(home.data, {"items": expected, "has_any_sessions": bool(full.data)})
                self.assertIn(first.pk, [row["id"] for row in expected])
                self.assertNotIn(old.pk, [row["id"] for row in expected])
                self.assertNotIn(unassigned.pk, [row["id"] for row in expected])
                self.assertEqual(next(row for row in expected if row["id"] == assigned.pk)["start_time"], "09:05:00")
        with patch("django.utils.timezone.localdate", return_value=self.today + timedelta(days=1)):
            next_day = self.get().data["items"]
            self.assertNotIn(first.pk, [row["id"] for row in next_day])
            self.assertTrue(any(row["date"] == (self.today + timedelta(days=365)).isoformat() for row in next_day))

    def test_overnight_bookings_hidden_ids_and_cutoff_match_full_visible_schedule(self):
        cutoff = self.today - timedelta(days=27)
        overnight = self.clinic(cutoff - timedelta(days=1), start=time(23), booking=(time(1), time(2)))
        self.clinic(cutoff - timedelta(days=1))
        self.clinic(cutoff, status="pending")
        hidden = self.clinic(self.today + timedelta(days=1), status="attended")
        self.student.schedule_hidden_ids = [-hidden.pk]
        self.student.save(update_fields=["schedule_hidden_ids"])
        for client in (self.client_for(), self.client_for(self.parent_user, self.student)):
            full = self.get(client, scope=None).data
            home = self.get(client).data
            self.assertEqual(home["items"], [row for row in full if row["date"] >= cutoff.isoformat()])
            self.assertEqual(next(row for row in home["items"] if row["id"] == -overnight.pk)["date"], cutoff.isoformat())
            self.assertNotIn(-hidden.pk, [row["id"] for row in home["items"]])
        self.student.schedule_hidden_before = cutoff
        self.student.save(update_fields=["schedule_hidden_before"])
        self.assertEqual(self.get().data, {"items": [], "has_any_sessions": False})

    def test_old_only_hidden_only_and_undated_preserve_empty_state_metadata(self):
        old = self.session(self.today - timedelta(days=1000))
        for client in (self.client_for(), self.client_for(self.parent_user, self.student)):
            self.assertEqual(self.get(client).data, {"items": [], "has_any_sessions": True})
        self.student.schedule_hidden_ids = [old.pk]
        self.student.save(update_fields=["schedule_hidden_ids"])
        self.assertEqual(self.get().data, {"items": [], "has_any_sessions": False})
        self.student.schedule_hidden_ids = []
        self.student.schedule_hidden_before = self.today
        self.student.save(update_fields=["schedule_hidden_ids", "schedule_hidden_before"])
        self.assertEqual(self.get().data, {"items": [], "has_any_sessions": False})
        apps.get_model("lectures", "Session").objects.filter(pk=old.pk).update(date=None)
        self.student.schedule_hidden_before = None
        self.student.save(update_fields=["schedule_hidden_before"])
        self.assertEqual(self.get().data, {"items": [], "has_any_sessions": True})
        self.student.schedule_hidden_before = self.today
        self.student.save(update_fields=["schedule_hidden_before"])
        self.assertEqual(self.get().data["has_any_sessions"], bool(self.get(scope=None).data))

    def test_old_only_clinic_metadata_uses_booking_day_and_visible_scope(self):
        old = self.clinic(self.today - timedelta(days=200))
        self.assertEqual(self.get().data, {"items": [], "has_any_sessions": True})
        self.student.schedule_hidden_ids = [-old.pk]
        self.student.save(update_fields=["schedule_hidden_ids"])
        self.assertEqual(self.get().data, {"items": [], "has_any_sessions": False})
        self.student.schedule_hidden_ids = []
        self.student.schedule_hidden_before = self.today - timedelta(days=100)
        self.student.save(update_fields=["schedule_hidden_ids", "schedule_hidden_before"])
        self.assertEqual(self.get().data, {"items": [], "has_any_sessions": False})

    def test_unknown_scope_and_unselected_or_foreign_parent_fail_closed(self):
        self.session(self.today)
        self.assertEqual(self.client_for().get("/api/v1/student/sessions/me/?scope=other").status_code, 400)
        self.assertEqual(self.client_for(self.parent_user).get("/api/v1/student/sessions/me/?scope=home").status_code, 403)
        other = _create_student(self.tenant, _create_user(self.tenant, "not-owned"), "Other")
        self.assertEqual(self.client_for(self.parent_user, other).get("/api/v1/student/sessions/me/?scope=home").status_code, 403)
        foreign = _create_tenant("home-foreign")
        foreign_student = _create_student(foreign, _create_user(foreign, "foreign"), "Foreign")
        self.assertEqual(self.client_for(self.parent_user, foreign_student).get("/api/v1/student/sessions/me/?scope=home").status_code, 403)

    def test_selected_child_and_inconsistent_roster_remain_scoped(self):
        own = self.session(self.today)
        other_user = _create_user(self.tenant, "home-sibling")
        sibling = _create_student(self.tenant, other_user, "Sibling")
        sibling.parent = self.parent
        sibling.save(update_fields=["parent"])
        sibling_enrollment = apps.get_model("enrollment", "Enrollment").objects.create(
            tenant=self.tenant, student=sibling, lecture=self.lecture, status="ACTIVE",
        )
        sibling_session = apps.get_model("lectures", "Session").objects.create(
            lecture=self.lecture, date=self.today, order=2, title="Sibling class",
        )
        Membership = apps.get_model("enrollment", "SessionEnrollment")
        Membership.objects.create(tenant=self.tenant, enrollment=sibling_enrollment, session=sibling_session)
        foreign_tenant = _create_tenant("home-roster-foreign")
        foreign_lecture = _create_lecture(foreign_tenant, "Foreign")
        foreign_session = apps.get_model("lectures", "Session").objects.create(
            lecture=foreign_lecture, date=self.today, order=1,
        )
        Membership.objects.create(tenant=self.tenant, enrollment=self.enrollment, session=foreign_session)
        local_other_lecture = _create_lecture(self.tenant, "Unmatched lecture")
        mismatched = apps.get_model("lectures", "Session").objects.create(
            lecture=local_other_lecture, date=self.today, order=1,
        )
        Membership.objects.create(tenant=self.tenant, enrollment=self.enrollment, session=mismatched)
        for child, expected in ((self.student, own.pk), (sibling, sibling_session.pk)):
            home = self.get(self.client_for(self.parent_user, child)).data
            self.assertEqual([row["id"] for row in home["items"]], [expected])
        student_home = self.get(self.client_for(self.user, sibling)).data
        self.assertEqual([row["id"] for row in student_home["items"]], [own.pk])
        sibling.schedule_hidden_ids = [sibling_session.pk]
        sibling.save(update_fields=["schedule_hidden_ids"])
        self.assertEqual(self.get(self.client_for(self.parent_user, sibling)).data,
            {"items": [], "has_any_sessions": False})

    def test_history_scale_payload_parity_and_query_bound(self):
        Session = apps.get_model("lectures", "Session")
        Membership = apps.get_model("enrollment", "SessionEnrollment")
        self.session(self.today)
        self.session(self.today + timedelta(days=1))
        for history_count in (1, 1000):
            present = Session.objects.filter(date__lt=self.today).count()
            added = Session.objects.bulk_create([
                Session(lecture=self.lecture, title="Old class", date=self.today - timedelta(days=100 + index),
                    order=3 + present + index, regular_order=3 + present + index)
                for index in range(history_count - present)
            ])
            Membership.objects.bulk_create([
                Membership(tenant=self.tenant, session=row, enrollment=self.enrollment) for row in added
            ])
            for role, client in (("student", self.client_for()), ("parent", self.client_for(self.parent_user, self.student))):
                with CaptureQueriesContext(connection) as full_queries:
                    full = self.get(client, scope=None)
                with CaptureQueriesContext(connection) as home_queries:
                    home = self.get(client)
                expected = [row for row in full.data if row["date"] and row["date"] >= (self.today - timedelta(days=27)).isoformat()]
                self.assertEqual(home.data["items"], expected)
                self.assertTrue(home.data["has_any_sessions"])
                self.assertEqual(len(full.data), history_count + 2)
                self.assertEqual(len(home.data["items"]), 2)
                self.assertEqual(len(home_queries), len(full_queries) - 1)
                if history_count == 1000:
                    self.assertLess(len(home.content), len(full.content) // 100)
                print(json.dumps({"history": history_count, "role": role,
                    "fullRows": len(full.data), "homeRows": len(home.data["items"]),
                    "fullBytes": len(full.content), "homeBytes": len(home.content),
                    "fullQueries": len(full_queries), "homeQueries": len(home_queries),
                    "itemsSha256": hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()}))

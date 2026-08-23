import threading
from unittest.mock import patch

from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import Tenant
from apps.domains.clinic.models import (
    SessionParticipantPlanItem,
)
from apps.domains.clinic.services.lifecycle import replace_participant_clinic_plan
from apps.domains.clinic.tests import ClinicAPITestMixin
from apps.domains.enrollment.models import Enrollment
from apps.domains.progress.models import ClinicLink
from apps.domains.progress.services.clinic_resolution_service import (
    ClinicResolutionService,
)


class ClinicParticipantPlanAPITest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("clinic_participant_plan", student_count=2)
        self.tenant = self.data["tenant"]
        self.admin = self.data["admin_user"]
        self.student = self.data["students"][0]
        self.enrollment = self.data["enrollments"][0]
        self.participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            enrollment=self.enrollment,
            status="booked",
        )
        self.link_a = self.make_clinic_link(
            self.enrollment,
            self.data["lec_session"],
            source_type="exam",
            source_id=101,
        )
        self.link_b = self.make_clinic_link(
            self.enrollment,
            self.data["lec_session"],
            source_type="homework",
            source_id=102,
        )
        self.link_c = self.make_clinic_link(
            self.enrollment,
            self.data["lec_session"],
            source_type="exam",
            source_id=203,
        )
        self.client.force_authenticate(user=self.admin)

    @property
    def endpoint(self):
        return f"/api/v1/clinic/participants/{self.participant.id}/planned-clinic-links/"

    def _put(self, ids):
        return self.client.put(
            self.endpoint,
            {"planned_clinic_link_ids": ids},
            format="json",
            **self._headers(self.tenant),
        )

    def _get(self):
        return self.client.get(
            f"/api/v1/clinic/participants/{self.participant.id}/",
            **self._headers(self.tenant),
        )

    def test_staff_replace_persists_sorted_session_scoped_plan(self):
        response = self._put([self.link_b.id, self.link_a.id])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["planned_clinic_link_ids"],
            sorted([self.link_a.id, self.link_b.id]),
        )
        self.assertEqual(
            self._get().data["planned_clinic_link_ids"],
            sorted([self.link_a.id, self.link_b.id]),
        )
        self.assertEqual(
            SessionParticipantPlanItem.objects.filter(
                participant=self.participant,
                removed_at__isnull=True,
            ).count(),
            2,
        )

    def test_duplicate_input_fails_closed_without_changing_existing_plan(self):
        self.assertEqual(self._put([self.link_a.id]).status_code, 200)

        duplicate = self._put([self.link_b.id, self.link_b.id])

        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertEqual(self._get().data["planned_clinic_link_ids"], [self.link_a.id])

    def test_invalid_resolved_student_tenant_enrollment_and_session_targets_fail_closed(self):
        self.assertEqual(self._put([self.link_a.id]).status_code, 200)

        resolved = self.make_clinic_link(
            self.enrollment,
            self.data["lec_session"],
            source_type="exam",
            source_id=103,
        )
        resolved.resolved_at = timezone.now()
        resolved.resolution_type = ClinicLink.ResolutionType.WAIVED
        resolved.save(update_fields=["resolved_at", "resolution_type"])

        other_student = self.make_clinic_link(
            self.data["enrollments"][1],
            self.data["lec_session"],
            source_type="exam",
            source_id=104,
        )

        inactive_lecture = self.make_lecture(self.tenant, title="비활성 수강 강의")
        inactive_lecture_session = self.make_lecture_session(inactive_lecture, order=3)
        inactive_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=inactive_lecture,
            status="INACTIVE",
        )
        inactive = self.make_clinic_link(
            inactive_enrollment,
            inactive_lecture_session,
            source_type="exam",
            source_id=105,
        )

        other_lecture = self.make_lecture(self.tenant, title="계획 범위 밖 강의")
        other_lecture_session = self.make_lecture_session(other_lecture, order=2)
        other_enrollment = self.make_enrollment(
            self.tenant,
            self.student,
            other_lecture,
        )
        outside_session_scope = self.make_clinic_link(
            other_enrollment,
            other_lecture_session,
            source_type="exam",
            source_id=106,
        )
        self.data["clinic_session"].target_lectures.add(self.data["lecture"])

        foreign = self.setup_api_tenant("clinic_plan_foreign", student_count=1)
        foreign_link = self.make_clinic_link(
            foreign["enrollments"][0],
            foreign["lec_session"],
            source_type="exam",
            source_id=107,
        )

        for invalid_link in (
            resolved,
            other_student,
            inactive,
            outside_session_scope,
            foreign_link,
        ):
            with self.subTest(link_id=invalid_link.id):
                response = self._put([self.link_b.id, invalid_link.id])
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(
                    self._get().data["planned_clinic_link_ids"],
                    [self.link_a.id],
                )

    @patch(
        "apps.domains.clinic.views.participant_views._send_clinic_notification",
        return_value={"requested": 1, "failed": 0, "send_to": "parent"},
    )
    def test_attendance_absence_checkout_and_completion_do_not_change_plan(self, _notify):
        self.assertEqual(self._put([self.link_a.id, self.link_b.id]).status_code, 200)

        absent = self.client.patch(
            f"/api/v1/clinic/participants/{self.participant.id}/set_status/",
            {"status": "no_show", "send_to": "parent"},
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(absent.status_code, 200, absent.data)
        self.assertEqual(absent.data["planned_clinic_link_ids"], sorted([self.link_a.id, self.link_b.id]))

        arrived = self.client.patch(
            f"/api/v1/clinic/participants/{self.participant.id}/set_status/",
            {"status": "attended", "is_late": True, "send_to": "parent"},
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(arrived.status_code, 200, arrived.data)

        completed = self.client.post(
            f"/api/v1/clinic/participants/{self.participant.id}/complete/",
            {"send_to": "parent"},
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(completed.status_code, 200, completed.data)

        checked_out = self.client.post(
            f"/api/v1/clinic/participants/{self.participant.id}/checkout/",
            {"send_to": "parent"},
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(checked_out.status_code, 200, checked_out.data)
        self.assertEqual(
            checked_out.data["planned_clinic_link_ids"],
            sorted([self.link_a.id, self.link_b.id]),
        )
        self.assertFalse(
            SessionParticipantPlanItem.objects.filter(
                participant=self.participant,
                removed_at__isnull=False,
            ).exists()
        )

    def test_resolve_and_carry_over_hide_stale_plan_but_keep_audit_rows(self):
        self.assertEqual(
            self._put([self.link_a.id, self.link_b.id, self.link_c.id]).status_code,
            200,
        )

        ClinicResolutionService.resolve_manually(
            clinic_link_id=self.link_c.id,
            user_id=self.admin.id,
            memo="오늘 범위 직접 해소",
        )
        self.assertEqual(
            self._get().data["planned_clinic_link_ids"],
            sorted([self.link_a.id, self.link_b.id]),
        )

        ClinicResolutionService.waive(
            clinic_link_id=self.link_a.id,
            user_id=self.admin.id,
            memo="오늘 범위에서 면제",
        )
        self.assertEqual(self._get().data["planned_clinic_link_ids"], [self.link_b.id])

        ClinicResolutionService.carry_over(clinic_link_id=self.link_b.id)
        self.assertEqual(self._get().data["planned_clinic_link_ids"], [])

        audit_rows = SessionParticipantPlanItem.objects.filter(
            participant=self.participant,
        ).order_by("clinic_link_id")
        self.assertEqual(audit_rows.count(), 3)
        self.assertTrue(all(row.removed_at for row in audit_rows))
        self.assertEqual(
            {row.removal_reason for row in audit_rows},
            {
                "clinic_link_resolved:MANUAL_OVERRIDE",
                "clinic_link_resolved:WAIVED",
                "clinic_link_resolved:CARRIED_OVER",
            },
        )

    def test_student_cannot_replace_today_plan(self):
        self.client.force_authenticate(user=self.student.user)

        response = self._put([self.link_a.id])

        self.assertEqual(response.status_code, 403, response.data)


class ClinicParticipantPlanPostgresConcurrencyTest(TransactionTestCase, ClinicAPITestMixin):
    reset_sequences = True

    def setUp(self):
        self.data = self.setup_api_tenant("clinic_plan_pg", student_count=1)
        self.participant = self.make_participant(
            self.data["tenant"],
            self.data["clinic_session"],
            self.data["students"][0],
            enrollment=self.data["enrollments"][0],
            status="booked",
        )
        self.links = [
            self.make_clinic_link(
                self.data["enrollments"][0],
                self.data["lec_session"],
                source_type="exam",
                source_id=201 + index,
            )
            for index in range(2)
        ]

    def test_concurrent_replace_serializes_without_duplicate_active_items(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL row-lock contract")

        barrier = threading.Barrier(2)
        errors = []

        def replace(link_id):
            close_old_connections()
            try:
                tenant = Tenant.objects.get(id=self.data["tenant"].id)
                actor = type(self.data["admin_user"]).objects.get(id=self.data["admin_user"].id)
                barrier.wait(timeout=10)
                replace_participant_clinic_plan(
                    tenant=tenant,
                    participant_id=self.participant.id,
                    clinic_link_ids=[link_id],
                    actor=actor,
                )
            except Exception as exc:  # pragma: no cover - surfaced by assertion
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [threading.Thread(target=replace, args=(link.id,)) for link in self.links]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        active_ids = list(
            SessionParticipantPlanItem.objects.filter(
                participant=self.participant,
                removed_at__isnull=True,
            ).values_list("clinic_link_id", flat=True)
        )
        self.assertEqual(len(active_ids), 1)
        self.assertIn(active_ids[0], [link.id for link in self.links])
        self.assertEqual(
            SessionParticipantPlanItem.objects.filter(participant=self.participant).count(),
            2,
        )

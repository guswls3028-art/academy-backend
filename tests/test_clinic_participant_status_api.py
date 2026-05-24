from rest_framework.test import APITestCase

from apps.core.models import TenantMembership
from apps.domains.clinic.tests import ClinicAPITestMixin


class ParticipantPendingStatusTransitionAPITest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("pending_status_api", student_count=2)
        self.tenant = self.data["tenant"]
        self.admin = self.data["admin_user"]
        self.student = self.data["students"][0]
        self.student.user.tenant = self.tenant
        self.student.user.save(update_fields=["tenant"])

    def test_staff_can_approve_pending_booking(self):
        self.client.force_authenticate(user=self.admin)
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="pending",
        )

        resp = self.client.patch(
            f"/api/v1/clinic/participants/{participant.id}/set_status/",
            {"status": "booked"},
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        participant.refresh_from_db()
        self.assertEqual(participant.status, "booked")
        self.assertEqual(participant.status_changed_by_id, self.admin.id)

    def test_staff_cannot_mark_pending_attended_without_approval(self):
        self.client.force_authenticate(user=self.admin)
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="pending",
        )

        resp = self.client.patch(
            f"/api/v1/clinic/participants/{participant.id}/set_status/",
            {"status": "attended"},
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(resp.status_code, 400, resp.data)
        participant.refresh_from_db()
        self.assertEqual(participant.status, "pending")

    def test_non_staff_member_cannot_use_staff_status_transition(self):
        member = self.make_user("non_staff_pending_status_api")
        TenantMembership.objects.create(
            user=member,
            tenant=self.tenant,
            role="student",
            is_active=True,
        )
        self.client.force_authenticate(user=member)
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="pending",
        )

        resp = self.client.patch(
            f"/api/v1/clinic/participants/{participant.id}/set_status/",
            {"status": "booked"},
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(resp.status_code, 403, resp.data)
        participant.refresh_from_db()
        self.assertEqual(participant.status, "pending")

    def test_student_can_still_cancel_own_pending_booking(self):
        self.client.force_authenticate(user=self.student.user)
        participant = self.make_participant(
            self.tenant,
            self.data["clinic_session"],
            self.student,
            status="pending",
        )

        resp = self.client.patch(
            f"/api/v1/clinic/participants/{participant.id}/set_status/",
            {"status": "cancelled"},
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        participant.refresh_from_db()
        self.assertEqual(participant.status, "cancelled")
        self.assertEqual(participant.status_changed_by_id, self.student.user_id)

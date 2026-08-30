import datetime

from rest_framework.test import APITestCase

from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.tests import ClinicAPITestMixin


class ClinicMultiSlotBookingAPITest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("clinic_multi_slot", student_count=2)
        self.tenant = self.data["tenant"]
        self.student = self.data["students"][0]
        self.tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        self.first_session = self.make_clinic_session(
            self.tenant,
            date=self.tomorrow,
            start_time=datetime.time(17, 0),
            location="클리닉 1실",
            max_participants=10,
        )
        self.second_session = self.make_clinic_session(
            self.tenant,
            date=self.tomorrow,
            start_time=datetime.time(18, 0),
            location="클리닉 1실",
            max_participants=10,
        )

    def _headers(self):
        return super()._headers(self.tenant)

    def test_student_books_two_time_slots_atomically(self):
        self.client.force_authenticate(user=self.student.user)

        response = self.client.post(
            "/api/v1/clinic/participants/bulk-create/",
            {
                "session_ids": [self.first_session.id, self.second_session.id],
                "student_request_memo": "17시부터 19시까지 참여",
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            [participant["session"] for participant in response.data["participants"]],
            [self.first_session.id, self.second_session.id],
        )
        participants = SessionParticipant.objects.filter(
            tenant=self.tenant,
            student=self.student,
            session_id__in=[self.first_session.id, self.second_session.id],
        ).order_by("session__start_time")
        self.assertEqual(participants.count(), 2)
        self.assertEqual(
            {participant.status for participant in participants},
            {SessionParticipant.Status.PENDING},
        )
        self.assertEqual(
            {participant.source for participant in participants},
            {SessionParticipant.Source.STUDENT_REQUEST},
        )
        self.assertEqual(
            {participant.student_request_memo for participant in participants},
            {"17시부터 19시까지 참여"},
        )

    def test_student_bulk_booking_rolls_back_every_slot_when_one_is_full(self):
        self.make_participant(
            self.tenant,
            self.second_session,
            self.data["students"][1],
            status=SessionParticipant.Status.BOOKED,
        )
        self.second_session.max_participants = 1
        self.second_session.save(update_fields=["max_participants"])
        self.client.force_authenticate(user=self.student.user)

        response = self.client.post(
            "/api/v1/clinic/participants/bulk-create/",
            {"session_ids": [self.first_session.id, self.second_session.id]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertFalse(
            SessionParticipant.objects.filter(
                tenant=self.tenant,
                student=self.student,
                session_id__in=[self.first_session.id, self.second_session.id],
            ).exists()
        )

    def test_student_bulk_booking_rejects_sessions_from_different_dates(self):
        another_date_session = self.make_clinic_session(
            self.tenant,
            date=self.tomorrow + datetime.timedelta(days=1),
            start_time=datetime.time(17, 0),
            location="클리닉 1실",
            max_participants=10,
        )
        self.client.force_authenticate(user=self.student.user)

        response = self.client.post(
            "/api/v1/clinic/participants/bulk-create/",
            {
                "session_ids": [self.first_session.id, another_date_session.id],
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(
            SessionParticipant.objects.filter(
                tenant=self.tenant,
                student=self.student,
            ).exists()
        )

    def test_staff_adds_multiple_students_to_multiple_slots_atomically(self):
        self.client.force_authenticate(user=self.data["admin_user"])
        student_ids = [student.id for student in self.data["students"]]

        response = self.client.post(
            "/api/v1/clinic/participants/bulk-create/",
            {
                "session_ids": [self.first_session.id, self.second_session.id],
                "student_ids": student_ids,
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["count"], 4)
        participants = SessionParticipant.objects.filter(
            tenant=self.tenant,
            session_id__in=[self.first_session.id, self.second_session.id],
            student_id__in=student_ids,
        )
        self.assertEqual(participants.count(), 4)
        self.assertEqual(
            {participant.status for participant in participants},
            {SessionParticipant.Status.BOOKED},
        )
        self.assertEqual(
            {participant.source for participant in participants},
            {SessionParticipant.Source.MANUAL},
        )

    def test_student_cannot_supply_bulk_student_ids(self):
        self.client.force_authenticate(user=self.student.user)

        response = self.client.post(
            "/api/v1/clinic/participants/bulk-create/",
            {
                "session_ids": [self.first_session.id],
                "student_ids": [self.data["students"][1].id],
            },
            format="json",
            **self._headers(),
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(
            SessionParticipant.objects.filter(
                tenant=self.tenant,
                session=self.first_session,
            ).exists()
        )

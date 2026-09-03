import datetime

from rest_framework.test import APITestCase

from apps.domains.clinic.models import SessionParticipant
from apps.domains.clinic.tests import ClinicAPITestMixin


class ClinicTimeRequestAPITest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("clinic_time_request", student_count=1)
        self.tenant = self.data["tenant"]
        self.admin = self.data["admin_user"]
        self.student = self.data["students"][0]
        self.session = self.data["clinic_session"]
        self.session.date = datetime.date.today() + datetime.timedelta(days=1)
        self.session.start_time = datetime.time(17, 0)
        self.session.duration_minutes = 300
        self.session.allow_time_preference = True
        self.session.save(
            update_fields=[
                "date",
                "start_time",
                "duration_minutes",
                "allow_time_preference",
                "updated_at",
            ]
        )

    def _student_post(self, payload):
        self.client.force_authenticate(user=self.student.user)
        return self.client.post(
            "/api/v1/clinic/participants/",
            payload,
            format="json",
            **self._headers(self.tenant),
        )

    def test_student_can_save_advisory_time_range_inside_session(self):
        response = self._student_post(
            {
                "session": self.session.id,
                "student_request_memo": "7시에 국어 학원이 있어요.",
                "preferred_start_time": "19:00",
                "preferred_end_time": "21:00",
            }
        )

        self.assertEqual(response.status_code, 201, response.data)
        participant = SessionParticipant.objects.get(id=response.data["id"])
        self.assertEqual(participant.preferred_start_time, datetime.time(19, 0))
        self.assertEqual(participant.preferred_end_time, datetime.time(21, 0))
        self.assertEqual(participant.student_request_memo, "7시에 국어 학원이 있어요.")
        self.assertEqual(participant.memo, "")
        self.assertEqual(response.data["session_start_time"], datetime.time(17, 0))

    def test_time_range_requires_both_values_and_session_bounds(self):
        missing_end = self._student_post(
            {
                "session": self.session.id,
                "preferred_start_time": "19:00",
            }
        )
        outside = self._student_post(
            {
                "session": self.session.id,
                "preferred_start_time": "16:30",
                "preferred_end_time": "21:00",
            }
        )

        self.assertEqual(missing_end.status_code, 400, missing_end.data)
        self.assertEqual(outside.status_code, 400, outside.data)
        self.assertFalse(
            SessionParticipant.objects.filter(
                tenant=self.tenant,
                student=self.student,
            ).exists()
        )

    def test_time_range_is_rejected_when_session_does_not_accept_it(self):
        self.session.allow_time_preference = False
        self.session.save(update_fields=["allow_time_preference", "updated_at"])

        response = self._student_post(
            {
                "session": self.session.id,
                "preferred_start_time": "19:00",
                "preferred_end_time": "21:00",
            }
        )

        self.assertEqual(response.status_code, 400, response.data)

    def test_generic_staff_patch_is_not_exposed(self):
        participant = self.make_participant(
            self.tenant,
            self.session,
            self.student,
            status="booked",
        )
        self.client.force_authenticate(user=self.admin)

        payloads = [
            {"preferred_start_time": "19:00"},
            {
                "preferred_start_time": "16:30",
                "preferred_end_time": "21:00",
            },
            {
                "preferred_start_time": "19:00",
                "preferred_end_time": "21:00",
            },
            {"student_request_memo": "일반 PATCH로 요청 출처를 바꾸면 안 됨"},
        ]
        for index, payload in enumerate(payloads):
            if index == 2:
                self.session.allow_time_preference = False
                self.session.save(
                    update_fields=["allow_time_preference", "updated_at"]
                )

            response = self.client.patch(
                f"/api/v1/clinic/participants/{participant.id}/",
                payload,
                format="json",
                **self._headers(self.tenant),
            )

            self.assertEqual(
                response.status_code,
                405,
                response.content.decode("utf-8", errors="replace"),
            )
            participant.refresh_from_db()
            self.assertIsNone(participant.preferred_start_time)
            self.assertIsNone(participant.preferred_end_time)
            self.assertEqual(participant.student_request_memo, "")

    def test_staff_status_note_does_not_overwrite_student_request(self):
        participant = self.make_participant(
            self.tenant,
            self.session,
            self.student,
            status="pending",
            source="student_request",
        )
        participant.student_request_memo = "8시까지 끝내 주세요."
        participant.save(update_fields=["student_request_memo", "updated_at"])
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            f"/api/v1/clinic/participants/{participant.id}/set_status/",
            {"status": "booked", "memo": "영상 시청 꼭 확인"},
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(response.status_code, 200, response.data)
        participant.refresh_from_db()
        self.assertEqual(participant.student_request_memo, "8시까지 끝내 주세요.")
        self.assertEqual(participant.staff_memo, "영상 시청 꼭 확인")

    def test_staff_memo_is_staff_only_and_can_be_updated_independently(self):
        participant = self.make_participant(
            self.tenant,
            self.session,
            self.student,
            status="booked",
        )
        participant.memo = "과거 교직원 내부 메모"
        participant.student_request_memo = "8시까지 끝내 주세요."
        participant.save(
            update_fields=["memo", "student_request_memo", "updated_at"]
        )
        self.client.force_authenticate(user=self.admin)

        updated = self.client.patch(
            f"/api/v1/clinic/participants/{participant.id}/staff-memo/",
            {"staff_memo": "클리닉 조교에게 시험지 A 전달"},
            format="json",
            **self._headers(self.tenant),
        )

        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["staff_memo"], "클리닉 조교에게 시험지 A 전달")

        self.client.force_authenticate(user=self.student.user)
        student_view = self.client.get(
            f"/api/v1/clinic/participants/{participant.id}/",
            **self._headers(self.tenant),
        )

        self.assertEqual(student_view.status_code, 200, student_view.data)
        self.assertNotIn("staff_memo", student_view.data)
        self.assertNotIn("memo", student_view.data)
        self.assertEqual(
            student_view.data["student_request_memo"],
            "8시까지 끝내 주세요.",
        )

        denied = self.client.patch(
            f"/api/v1/clinic/participants/{participant.id}/staff-memo/",
            {"staff_memo": "학생이 바꾸면 안 됨"},
            format="json",
            **self._headers(self.tenant),
        )
        self.assertEqual(denied.status_code, 403, denied.data)
        participant.refresh_from_db()
        self.assertEqual(participant.staff_memo, "클리닉 조교에게 시험지 A 전달")

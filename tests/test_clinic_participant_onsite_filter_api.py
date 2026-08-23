import datetime

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.domains.clinic.models import SessionParticipantPlanItem
from apps.domains.clinic.tests import ClinicAPITestMixin


class ClinicParticipantOnsiteFilterAPITest(APITestCase, ClinicAPITestMixin):
    def setUp(self):
        self.data = self.setup_api_tenant("clinic_onsite", student_count=1)
        self.other = self.setup_api_tenant("clinic_onsite_other", student_count=1)
        self.tenant = self.data["tenant"]
        self.student = self.data["students"][0]
        self.enrollment = self.data["enrollments"][0]
        self.admin = self.data["admin_user"]
        self.onsite_date = datetime.date(2030, 1, 2)
        self.client.force_authenticate(user=self.admin)

    def _get(self, query):
        return self.client.get(
            f"/api/v1/clinic/participants/?{query}",
            **self._headers(self.tenant),
        )

    def _checked_in_at(self, hour, minute=0):
        return timezone.make_aware(
            datetime.datetime.combine(
                self.onsite_date,
                datetime.time(hour, minute),
            )
        )

    def _attended(self, session, *, student=None, checked_in_at=None, **updates):
        participant = self.make_participant(
            self.tenant,
            session,
            student or self.student,
            enrollment=self.enrollment if student in (None, self.student) else None,
            status="attended",
        )
        participant.checked_in_at = checked_in_at
        for field, value in updates.items():
            setattr(participant, field, value)
        participant.save(
            update_fields=["checked_in_at", *updates.keys(), "updated_at"]
        )
        return participant

    def test_exact_onsite_filter_is_ordered_before_existing_pagination(self):
        early_session = self.make_clinic_session(
            self.tenant,
            date=self.onsite_date,
            start_time=datetime.time(14, 0),
            location="onsite-early",
            max_participants=30,
        )
        late_session = self.make_clinic_session(
            self.tenant,
            date=self.onsite_date,
            start_time=datetime.time(15, 0),
            location="onsite-late",
            max_participants=30,
        )
        checked_in_at = self._checked_in_at(9)

        matching = []
        for index in range(21):
            session = late_session if index % 2 == 0 else early_session
            matching.append(
                self._attended(
                    session,
                    checked_in_at=checked_in_at,
                )
            )
        matching.append(
            self._attended(
                late_session,
                checked_in_at=self._checked_in_at(8, 59),
            )
        )
        matching.append(
            self._attended(
                early_session,
                checked_in_at=self._checked_in_at(9, 1),
            )
        )

        planned_participant = matching[3]
        planned_participant.is_late = True
        planned_participant.completed_at = self._checked_in_at(10)
        planned_participant.save(
            update_fields=["is_late", "completed_at", "updated_at"]
        )
        clinic_link = self.make_clinic_link(
            self.enrollment,
            self.data["lec_session"],
            source_type="exam",
            source_id=901,
        )
        SessionParticipantPlanItem.objects.create(
            participant=planned_participant,
            clinic_link=clinic_link,
            selected_by=self.admin,
        )

        expected_ids = [
            participant.id
            for participant in sorted(
                matching,
                key=lambda participant: (
                    participant.checked_in_at,
                    participant.session.start_time,
                    participant.id,
                ),
            )
        ]
        query = f"onsite_date={self.onsite_date.isoformat()}&ordering=-created_at"

        first_page = self._get(query)
        second_page = self._get(f"{query}&page=2")

        self.assertEqual(first_page.status_code, 200, first_page.data)
        self.assertEqual(second_page.status_code, 200, second_page.data)
        self.assertEqual(first_page.data["count"], 23)
        self.assertEqual(second_page.data["count"], 23)
        self.assertEqual(
            [row["id"] for row in first_page.data["results"]],
            expected_ids[:20],
        )
        self.assertEqual(
            [row["id"] for row in second_page.data["results"]],
            expected_ids[20:],
        )
        projected = next(
            row
            for row in first_page.data["results"] + second_page.data["results"]
            if row["id"] == planned_participant.id
        )
        self.assertTrue(projected["is_late"])
        self.assertIsNotNone(projected["completed_at"])
        self.assertEqual(projected["planned_clinic_link_ids"], [clinic_link.id])

    def test_foreign_and_corrupt_rows_are_excluded(self):
        target_session = self.make_clinic_session(
            self.tenant,
            date=self.onsite_date,
            start_time=datetime.time(14, 0),
            location="onsite-target",
            max_participants=20,
        )
        valid = self._attended(
            target_session,
            checked_in_at=self._checked_in_at(9),
        )

        wrong_date_session = self.make_clinic_session(
            self.tenant,
            date=self.onsite_date + datetime.timedelta(days=1),
            start_time=datetime.time(14, 0),
            location="onsite-wrong-date",
            max_participants=20,
        )
        self._attended(
            wrong_date_session,
            checked_in_at=self._checked_in_at(9),
        )
        booked = self.make_participant(
            self.tenant,
            target_session,
            self.student,
            enrollment=self.enrollment,
            status="booked",
        )
        booked.checked_in_at = self._checked_in_at(9)
        booked.save(update_fields=["checked_in_at", "updated_at"])
        self._attended(target_session, checked_in_at=None)
        self._attended(
            target_session,
            checked_in_at=self._checked_in_at(9),
            checked_out_at=self._checked_in_at(10),
        )
        self._attended(None, checked_in_at=self._checked_in_at(9))

        foreign_session = self.make_clinic_session(
            self.other["tenant"],
            date=self.onsite_date,
            start_time=datetime.time(14, 0),
            location="onsite-foreign",
            max_participants=20,
        )
        self._attended(foreign_session, checked_in_at=self._checked_in_at(9))
        self._attended(
            target_session,
            student=self.other["students"][0],
            checked_in_at=self._checked_in_at(9),
        )
        foreign_participant = self.make_participant(
            self.other["tenant"],
            foreign_session,
            self.other["students"][0],
            enrollment=self.other["enrollments"][0],
            status="attended",
        )
        foreign_participant.checked_in_at = self._checked_in_at(9)
        foreign_participant.save(update_fields=["checked_in_at", "updated_at"])

        response = self._get(f"onsite_date={self.onsite_date.isoformat()}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual([row["id"] for row in response.data["results"]], [valid.id])

    def test_invalid_onsite_date_fails_closed(self):
        for value in (
            "",
            "2030-02-30",
            "02-01-2030",
            "01/02/2030",
            "2030-1-2",
            "not-a-date",
        ):
            with self.subTest(value=value):
                response = self._get(f"onsite_date={value}")
                self.assertEqual(response.status_code, 400, response.data)

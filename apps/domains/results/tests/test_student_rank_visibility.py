from django.test import SimpleTestCase

from apps.domains.results.services.student_rank_visibility import (
    student_rank_visibility_payload,
)


class StudentRankVisibilityTests(SimpleTestCase):
    def setUp(self):
        self.rank_info = {
            "rank": 2,
            "percentile": 20.0,
            "cohort_size": 10,
            "cohort_avg": 72.5,
        }

    def test_rank_can_be_hidden_while_attempt_count_remains_visible(self):
        payload = student_rank_visibility_payload(
            self.rank_info,
            rank_published=False,
            cohort_size_published=True,
        )

        self.assertEqual(payload, {
            "rank": None,
            "percentile": None,
            "cohort_size": 10,
            "cohort_avg": None,
        })

    def test_rank_can_be_shown_without_exposing_attempt_count(self):
        payload = student_rank_visibility_payload(
            self.rank_info,
            rank_published=True,
            cohort_size_published=False,
        )

        self.assertEqual(payload, {
            "rank": 2,
            "percentile": None,
            "cohort_size": None,
            "cohort_avg": 72.5,
        })

    def test_single_respondent_does_not_receive_a_meaningless_first_place(self):
        payload = student_rank_visibility_payload(
            {**self.rank_info, "rank": 1, "cohort_size": 1},
            rank_published=True,
            cohort_size_published=True,
        )

        self.assertEqual(payload, {
            "rank": None,
            "percentile": None,
            "cohort_size": 1,
            "cohort_avg": None,
        })

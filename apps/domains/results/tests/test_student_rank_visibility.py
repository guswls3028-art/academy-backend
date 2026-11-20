from django.test import SimpleTestCase

from apps.domains.results.services.student_rank_visibility import (
    student_rank_visibility_payload,
)


class StudentRankVisibilityTests(SimpleTestCase):
    def test_benchmarks_use_highest_ceil_tenth_of_valid_scores(self):
        from apps.domains.results.utils.ranking import _competition_rank

        rows = [{"enrollment_id": i, "total_score": i} for i in range(11)]
        rows.append({"enrollment_id": 11, "total_score": None})
        row = _competition_rank(rows)[0]
        self.assertEqual(row["cohort_avg"], 5)
        self.assertEqual(row["cohort_top_decile_avg"], 9.5)
        self.assertEqual(row["cohort_highest"], 10)

    def setUp(self):
        self.rank_info = {
            "rank": 2,
            "percentile": 20.0,
            "cohort_size": 10,
            "cohort_avg": 72.5,
            "cohort_highest": 95,
            "cohort_top_decile_avg": 95,
        }

    def test_rank_can_be_hidden_while_attempt_count_remains_visible(self):
        payload = student_rank_visibility_payload(
            self.rank_info,
            include_score_benchmarks=True,
            rank_published=False,
            cohort_size_published=True,
        )

        self.assertEqual(payload, {
            "rank": None,
            "percentile": None,
            "cohort_size": 10,
            "cohort_avg": None,
            "cohort_highest": None,
            "cohort_top_decile_avg": None,
        })

    def test_rank_can_be_shown_without_exposing_attempt_count(self):
        payload = student_rank_visibility_payload(
            self.rank_info,
            include_score_benchmarks=True,
            rank_published=True,
            cohort_size_published=False,
        )

        self.assertEqual(payload, {
            "rank": 2,
            "percentile": None,
            "cohort_size": None,
            "cohort_avg": 72.5,
            "cohort_highest": 95,
            "cohort_top_decile_avg": 95,
        })

    def test_single_respondent_does_not_receive_a_meaningless_first_place(self):
        payload = student_rank_visibility_payload(
            {**self.rank_info, "rank": 1, "cohort_size": 1},
            include_score_benchmarks=True,
            rank_published=True,
            cohort_size_published=True,
        )

        self.assertEqual(payload, {
            "rank": None,
            "percentile": None,
            "cohort_size": 1,
            "cohort_avg": None,
            "cohort_highest": None,
            "cohort_top_decile_avg": None,
        })

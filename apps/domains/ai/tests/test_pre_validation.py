from django.test import SimpleTestCase

from apps.domains.ai.services.pre_validation import validate_input_for_basic


class SinglePlanPreValidationTests(SimpleTestCase):
    def test_legacy_tier_name_does_not_block_omr_photo(self):
        result = validate_input_for_basic(
            tier="standard",
            job_type="omr_grading",
            payload={
                "content_type": "image/jpeg",
                "file_size_mb": 5,
            },
        )

        self.assertEqual(result, (True, None, None))

    def test_input_quality_limits_still_apply_to_every_tier(self):
        ok, _message, rejection_code = validate_input_for_basic(
            tier="all",
            job_type="omr_grading",
            payload={
                "content_type": "image/jpeg",
                "file_size_mb": 51,
            },
        )

        self.assertFalse(ok)
        self.assertEqual(rejection_code, "FILE_TOO_LARGE")

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.submissions.models import OMRDetectedAnswer, Submission
from apps.domains.submissions.omr_pipeline.services.facts import record_recognition_fact
from apps.support.omr.exam_structure import empty_exam_structure


User = get_user_model()


class OMRFactFKMappingTests(TestCase):
    def test_recognition_fact_without_question_map_keeps_question_number_out_of_fk(self):
        tenant = Tenant.objects.create(code="omr-fk-map", name="OMR FK Map")
        user = User.objects.create_user(
            username="omr-fk-map-user",
            password="pass1234!",
            tenant=tenant,
        )
        submission = Submission.objects.create(
            tenant=tenant,
            user=user,
            target_type=Submission.TargetType.EXAM,
            target_id=999_001,
            source=Submission.Source.OMR_SCAN,
            payload={},
        )

        run = record_recognition_fact(
            submission=submission,
            job_id="legacy-no-map",
            status="DONE",
            error=None,
            worker_result={
                "version": "legacy",
                "answers": [
                    {
                        "question_id": 1,
                        "detected": ["1"],
                        "status": "ok",
                    }
                ],
            },
            exam_structure=empty_exam_structure(),
        )

        fact = OMRDetectedAnswer.objects.get(submission=submission, recognition_run=run)
        self.assertEqual(fact.question_number, 1)
        self.assertIsNone(fact.exam_question_id)

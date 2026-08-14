from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.exams.models import Exam
from apps.domains.results.models import ExamResult
from apps.domains.submissions.models import Submission


User = get_user_model()


class CheckIntegrityCommandTests(TestCase):
    def test_manual_override_audit_scans_rows_after_the_old_sample_limit(self):
        tenant = Tenant.objects.create(name="Integrity", code="integrity", is_active=True)
        user = User.objects.create(
            tenant=tenant,
            username="integrity_admin",
            is_active=True,
            is_staff=True,
        )
        exam = Exam.objects.create(
            tenant=tenant,
            title="Integrity exam",
            exam_type=Exam.ExamType.REGULAR,
            max_attempts=1,
            pass_score=0,
            max_score=100,
        )
        Submission.objects.bulk_create(
            [
                Submission(
                    tenant=tenant,
                    user=user,
                    target_type=Submission.TargetType.EXAM,
                    target_id=exam.id,
                    source=Submission.Source.ONLINE,
                    status=Submission.Status.DONE,
                )
                for _ in range(201)
            ]
        )
        submissions = list(Submission.objects.order_by("id"))
        ExamResult.objects.bulk_create(
            [
                ExamResult(
                    submission=submission,
                    exam=exam,
                    manual_overrides=(
                        {"1": {"earned": 1, "max_score": 1}}
                        if index < 200
                        else {"1": {"earned": 1}}
                    ),
                )
                for index, submission in enumerate(submissions)
            ]
        )

        output = StringIO()
        call_command("check_integrity", stdout=output)

        self.assertIn(
            "[9] ExamResult manual_overrides missing max_score: 1",
            output.getvalue(),
        )

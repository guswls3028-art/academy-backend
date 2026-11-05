from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.core.models import Tenant

from apps.domains.surveys.models import Survey, SurveyRecipient, SurveyResponse
from apps.domains.surveys.services import SurveyConflict, save_response

Student = apps.get_model("students", "Student")

@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locks are required")
class SurveyCapacityConcurrencyTests(TransactionTestCase):
    def test_only_one_of_two_simultaneous_students_gets_last_place(self):
        tenant = Tenant.objects.create(code="survey-concurrency", name="설문 정원 검증", is_active=True)
        students = []
        for number in [1, 2]:
            user = get_user_model().objects.create_user(tenant=tenant, username=f"survey-pg-{number}", password="test")
            students.append(Student.objects.create(tenant=tenant, user=user, name=f"학생 {number}", ps_number=f"SURVEY-PG-{number}", omr_code=f"7800000{number}"))
        survey = Survey.objects.create(tenant=tenant, title="마지막 한 자리", status="open", published_at=timezone.now(), response_limit=1, questions=[{"id": "attend", "label": "참여", "type": "text", "required": True, "options": []}])
        SurveyRecipient.objects.bulk_create([SurveyRecipient(tenant=tenant, survey=survey, student=student) for student in students])
        barrier = Barrier(2)

        def submit(student):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '8s'")
                    save_response(tenant=tenant, student=student, user=student.user, survey_id=survey.id, values={"answers": {"attend": "신청합니다"}, "attachment_ids": [], "score_submission_ids": [], "expected_version": 0})
                return "saved"
            except SurveyConflict:
                return "full"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, students))
        self.assertCountEqual(results, ["saved", "full"])
        self.assertEqual(SurveyResponse.objects.filter(tenant=tenant, survey=survey).count(), 1)

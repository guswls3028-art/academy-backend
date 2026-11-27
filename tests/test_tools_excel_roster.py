"""Exercise Excel session restoration in a fresh process with the Tools worker app registry."""
import os
import subprocess
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch


class ToolsExcelRosterTests(unittest.TestCase):
    def test_worker_processes_workbooks_without_the_web_framework(self):
        # The API suite has already imported DRF and initialized its own set of Django models.
        subprocess.run(
            [sys.executable, "-B", "-m", "tests.test_tools_excel_roster"],
            cwd=Path(__file__).resolve().parents[1], check=True, timeout=120,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.worker",
                 "WORKER_TYPE": "TOOLS", "AWS_EC2_METADATA_DISABLED": "true"},
        )


def verify_workbooks():
    import django
    from django.conf import settings

    sys.modules["rest_framework"] = None
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
    django.setup()
    from django.apps import apps
    from django.core.management import call_command
    from django.utils import timezone
    from openpyxl import Workbook

    from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository, job_create
    from academy.application.use_cases.tools.worker_dispatcher import handle_tools_job
    from apps.core.models import Tenant, User
    from apps.domains.ai.models import AIResultModel
    from apps.domains.attendance.models import Attendance
    from apps.domains.enrollment.models import Enrollment, SessionEnrollment
    from apps.domains.fees.models import FeePayment, FeeTemplate, StudentFee, StudentInvoice
    from apps.domains.lectures.models import Lecture, Session
    from apps.domains.students.models import Student
    from apps.shared.contracts.ai_job import AIJob
    from apps.support.enrollment.import_dependencies import EnrollmentImportError, restore_withdrawn_session_for_import

    settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
    call_command("migrate", run_syncdb=True, verbosity=0)
    tenant = Tenant.objects.create(code="qa-tools-roster", name="QA")
    student = Student.objects.create(tenant=tenant, user=User.objects.create(tenant=tenant, username="qa-roster"),
                                     name="QA 학생", ps_number="QA-01", omr_code="12345678", parent_phone="01000000000")
    lecture = Lecture.objects.create(tenant=tenant, title="QA 강의", name="QA")
    enrollment = Enrollment.objects.create(tenant=tenant, lecture=lecture, student=student, status="INACTIVE")
    lessons = [Session.objects.create(lecture=lecture, order=i, title=f"QA {i}") for i in (1, 2, 3)]
    marks = [Attendance.objects.create(tenant=tenant, enrollment=enrollment, session=s, status="SECESSION",
                                       memo="교사 기록") for s in lessons]
    template = FeeTemplate.objects.create(tenant=tenant, lecture=lecture, name="QA 수강료", amount=9000)
    fee = StudentFee.objects.create(tenant=tenant, student=student, enrollment=enrollment, fee_template=template,
                                    adjusted_amount=7500, is_active=False)
    roster = SessionEnrollment.objects.filter(tenant=tenant, enrollment=enrollment)
    workbook = Workbook()
    workbook.active.append(["학생번호", "이름", "학부모전화번호"])
    workbook.active.append([student.ps_number, student.name, student.parent_phone])

    def dispatch(label, session=None):
        key = f"excel/{tenant.pk}/{label}.xlsx"
        payload = {"tenant_id": tenant.pk, "lecture_id": lecture.pk, "file_key": key}
        if session is not None:
            payload["session_id"] = session.pk
        job = job_create(job_id=label, job_type="excel_parsing", tenant_id=str(tenant.pk), payload=payload)
        now = timezone.now()
        assert DjangoAIJobRepository().mark_running(job.job_id, "qa-tools", now + timedelta(minutes=5), now)
        job.refresh_from_db()
        storage = Mock()
        storage.download_to_path.side_effect = lambda bucket, filename, path: workbook.save(path)
        with patch("academy.application.use_cases.ai.pipelines.excel_handler.R2ObjectStorageAdapter", return_value=storage), \
             patch("academy.application.use_cases.ai.pipelines.excel_handler._record_progress"):
            result = handle_tools_job(AIJob(id=job.job_id, type="excel_parsing", tenant_id=str(tenant.pk),
                                            claim_locked_at=job.locked_at, payload=payload))
        storage.delete_object.assert_called_once()
        job.refresh_from_db()
        return job, result

    def check_state(statuses, active):
        for mark, status in zip(marks, statuses):
            mark.refresh_from_db()
            assert (mark.status, mark.memo) == (status, "교사 기록")
        fee.refresh_from_db()
        assert (fee.is_active, fee.adjusted_amount, fee.enrollment_id) == (active, 7500, enrollment.pk)
        assert StudentFee.objects.count() == 1 and not StudentInvoice.objects.exists() and not FeePayment.objects.exists()

    job, result = dispatch("implicit-first")
    assert job.status == result.status == "DONE" and result.result["withdrawn_session_preserved_count"] == 1
    assert not roster.exists()
    check_state(["SECESSION"] * 3, False)

    foreign = Tenant.objects.create(code="qa-tools-other", name="QA other")
    with unittest.TestCase().assertRaisesRegex(EnrollmentImportError, "다른 학원"):
        restore_withdrawn_session_for_import(tenant=foreign, session=lessons[2], enrollment=enrollment)
    assert not roster.exists()

    # Fail after the roster and fee write, before atomic job completion. Both must roll back.
    with unittest.TestCase().assertLogs("academy.application.use_cases.ai.pipelines.excel_handler", level="ERROR"), \
         patch("apps.support.attendance.learning_todo_eligibility.reconcile_learning_todo_targets",
               side_effect=RuntimeError("synthetic rollback")) as reconcile:
        job, result = dispatch("rollback-third", lessons[2])
    reconcile.assert_called_once()
    assert result.status == "FAILED" and not AIResultModel.objects.filter(job=job).exists()
    assert not roster.exists()
    check_state(["SECESSION"] * 3, False)

    memberships = []
    for label in ("explicit-third", "repeat-third"):
        job, result = dispatch(label, lessons[2])
        assert job.status == result.status == "DONE" and result.result["session_enrolled_count"] == 1
        member = roster.get()
        assert member.session_id == lessons[2].pk
        memberships.append(member.pk)
        check_state(["SECESSION", "SECESSION", "UNSET"], True)
    assert memberships[0] == memberships[1] and AIResultModel.objects.count() == 3


if __name__ == "__main__":
    with patch("socket.socket.connect", side_effect=AssertionError("unexpected external connection")) as connections:
        verify_workbooks()
        connections.assert_not_called()

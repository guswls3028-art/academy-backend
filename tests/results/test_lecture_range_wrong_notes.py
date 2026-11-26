import io
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

import fitz
from PIL import Image
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.callbacks import _handle_wrong_note_pdf_result
from apps.domains.results.models import Result, WrongNoteBatch, WrongNotePDF
from apps.domains.results.services.wrong_note_pdf_worker import handle_wrong_note_pdf_generation_job
from apps.shared.contracts.ai_job import AIJob


class LectureRangePrintTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="qa-print-range", name="QA")
        self.admin = get_user_model().objects.create_user(username="range-admin", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="teacher")
        self.lecture = apps.get_model("lectures", "Lecture").objects.create(
            tenant=self.tenant, name="Course", title="Course", subject="SCIENCE",
        )
        Session = apps.get_model("lectures", "Session")
        self.sessions = [Session.objects.create(lecture=self.lecture, order=i, title=f"S{i}") for i in range(1, 5)]
        self.students = []
        for i, name in enumerate(["Alpha", "Bravo"]):
            user = get_user_model().objects.create_user(username=f"range-student-{i}", tenant=self.tenant)
            student = apps.get_model("students", "Student").objects.create(
                tenant=self.tenant, user=user, name=name, ps_number=f"P00{i}",
                omr_code=f"9800000{i}", parent_phone="01000000000",
            )
            enrollment = apps.get_model("enrollment", "Enrollment").objects.create(
                lecture=self.lecture, tenant=self.tenant, student=student,
            )
            self.students.append(enrollment)
            apps.get_model("enrollment", "SessionEnrollment").objects.create(
                session=self.sessions[1], enrollment=enrollment, tenant=self.tenant,
            )
        self.images = {}
        self.exam = self.make_exam("Midterm", ["EX-ONE", "EX-TWO"])
        self.exam.sessions.add(*self.sessions)
        outside = self.make_exam("Outside", ["Z"])
        outside.sessions.add(self.sessions[0], self.sessions[3])
        workbook_exam = self.make_exam("Worksheet master", ["HW-ONE", "HW-TWO"])
        self.hw = apps.get_model("homework_results", "Homework").objects.create(
            tenant=self.tenant, session=self.sessions[2], title="Worksheet", source_exam=workbook_exam,
        )
        for enrollment in self.students:
            apps.get_model("homework", "HomeworkAssignment").objects.create(
                tenant=self.tenant, enrollment=enrollment, homework=self.hw, session=self.sessions[2],
            )
        self.range = {"lecture_id": self.lecture.pk, "from_session_order": 2, "to_session_order": 3}
        self.payload = {**self.range, "students": [
            {"enrollment_id": self.students[0].pk, "sources": [
                {"type": "exam", "id": self.exam.pk, "numbers": [1]},
                {"type": "homework", "id": self.hw.pk, "numbers": [1]},
            ]},
            {"enrollment_id": self.students[1].pk, "sources": [
                {"type": "exam", "id": self.exam.pk, "numbers": [2]},
            ]},
        ]}
        self.queue = []
        self.objects = {}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        def save(*, fileobj, key, **kwargs):
            self.objects[key] = fileobj.read()
        def copy(*, key, destination, max_bytes):
            data = self.objects[key]
            self.assertLessEqual(len(data), max_bytes)
            return destination.write(data)
        service = "apps.domains.results.services."
        self.publisher = self.stack.enter_context(patch(service + "wrong_note_batch_service.publish_wrong_note_pdf_ai_job",
                                                       side_effect=lambda job: self.queue.append(job) or True))
        self.stack.enter_context(patch(service + "wrong_note_pdf_service.get_object_bytes_r2_storage",
                                       side_effect=lambda *, key, **kwargs: self.images.get(key)))
        self.stack.enter_context(patch(service + "wrong_note_pdf_service.upload_fileobj_to_r2_storage", side_effect=save))
        self.stack.enter_context(patch(service + "wrong_note_pdf_service.delete_object_r2_storage"))
        self.stack.enter_context(patch(service + "wrong_note_batch_archive.upload_fileobj_to_r2_storage", side_effect=save))
        self.stack.enter_context(patch(service + "wrong_note_batch_archive.copy_storage_object_to_file", side_effect=copy))
        self.stack.enter_context(patch("apps.domains.results.views.wrong_note_batch_view.generate_presigned_get_url_storage",
                                       side_effect=lambda *, key, **kwargs: key))
        self.stack.enter_context(patch("apps.domains.results.throttles.WrongNotePDFCreateThrottle.allow_request", return_value=True))

    def make_exam(self, title, answers):
        exam = apps.get_model("exams", "Exam").objects.create(
            tenant=self.tenant, title=title, exam_type="regular", segmentation_status="ready",
        )
        sheet = apps.get_model("exams", "Sheet").objects.create(exam=exam, total_questions=len(answers))
        key = {}
        for number, answer in enumerate(answers, 1):
            image_key = f"tenants/{self.tenant.pk}/exams/{exam.pk}/{number}.png"
            image = Image.new("RGB", (110, 60), color=(200, 80 * number, 50))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            self.images[image_key] = buffer.getvalue()
            q = apps.get_model("exams", "ExamQuestion").objects.create(
                sheet=sheet, number=number, image_key=image_key,
            )
            key[str(q.pk)] = answer
        apps.get_model("exams", "AnswerKey").objects.create(exam=exam, answers=key)
        return exam

    def request(self, method, suffix, data=None, *, user=None, tenant=None):
        path = "/api/v1/results/wrong-notes/" + suffix
        handler = resolve(path)
        if method == "get":
            req = self.factory.get(path, data or {})
        else:
            req = self.factory.post(path, data or {}, format="json")
        req.tenant = tenant or self.tenant
        force_authenticate(req, user=user or self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            return handler.func(req, **handler.kwargs)

    def consume(self):
        model = self.queue.pop(0)
        job = AIJob(id=model.job_id, type=model.job_type, tenant_id=model.tenant_id,
                    source_domain=model.source_domain, source_id=model.source_id, payload=model.payload)
        result = handle_wrong_note_pdf_generation_job(job)
        with self.captureOnCommitCallbacks(execute=True):
            _handle_wrong_note_pdf_result(job_id=job.id, source_id=job.source_id,
                                         status=result.status, error=result.error, result_payload=result.result)
        return result.result["outcome"]

    def test_range_entry_to_saved_unique_student_pdfs_and_zip(self):
        response = self.request("get", "range/", self.range)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([(s["title"], s["session_order"]) for s in response.data["sources"]],
                         [("Midterm", 2), ("Worksheet", 3)])
        first = self.request("post", "batches/", self.payload)
        self.assertEqual(first.status_code, 202, first.data)
        batch_id = first.data["id"]
        same = self.request("post", "batches/", self.payload)
        self.assertEqual(same.data["id"], batch_id)
        self.assertEqual(len(self.queue), 1)
        path = f"batches/{batch_id}/"
        self.assertEqual(self.request("get", path + "download/").status_code, 409)
        self.assertEqual(self.consume(), "DONE")
        self.assertEqual(self.consume(), "DONE")
        self.assertEqual(self.request("get", path).data["status"], "DONE")
        key = self.request("get", path + "download/").data["url"]
        with zipfile.ZipFile(io.BytesIO(self.objects[key])) as archive:
            self.assertEqual(len(archive.namelist()), 2)
            for name in archive.namelist():
                with fitz.open(stream=archive.read(name), filetype="pdf") as doc:
                    text = "\n".join(page.get_text() for page in doc)
                    self.assertIn("2~3차시", text)
                    self.assertNotIn("주차별", text)
                    self.assertTrue(any(page.get_images() for page in doc))
                if name.startswith("Alpha_"):
                    self.assertIn("EX-ONE", text)
                    self.assertIn("HW-ONE", text)
                    self.assertNotIn("EX-TWO", text)
                    self.assertNotIn("Bravo", text)
                else:
                    self.assertTrue(name.startswith("Bravo_"))
                    self.assertIn("EX-TWO", text)
                    self.assertNotIn("HW-ONE", text)
                    self.assertNotIn("Alpha", text)
        self.assertFalse(Result.objects.exists())
        self.assertFalse(apps.get_model("homework_results", "HomeworkScore").objects.exists())

    def test_partial_failure_keeps_first_file_and_retries_only_second(self):
        batch_id = self.request("post", "batches/", self.payload).data["id"]
        self.assertEqual(self.consume(), "DONE")
        path = f"batches/{batch_id}/"
        first_doc = WrongNotePDF.objects.get(batch_id=batch_id, status="DONE")
        saved = self.objects[first_doc.file_path]
        missing = f"tenants/{self.tenant.pk}/exams/{self.exam.pk}/2.png"
        image = self.images.pop(missing)
        self.assertEqual(self.consume(), "FAILED")
        self.assertEqual(self.request("get", path).data["status"], "FAILED")
        self.images[missing] = image
        self.assertEqual(self.request("post", path + "retry/").status_code, 202)
        self.request("post", path + "retry/")
        self.assertEqual(len(self.queue), 1)
        self.assertEqual(self.consume(), "DONE")
        self.assertEqual(self.request("get", path).data["status"], "DONE")
        self.assertEqual(WrongNotePDF.objects.filter(enrollment=self.students[0]).count(), 1)
        self.assertEqual(self.objects[first_doc.file_path], saved)

    def test_rejects_range_assignment_role_tenant_and_unapproved_question(self):
        apps.get_model("exams", "ExamEnrollment").objects.create(exam=self.exam, enrollment=self.students[0])
        self.assertEqual(self.request("post", "batches/", self.payload).status_code, 400)
        self.payload["students"] = self.payload["students"][:1]
        self.payload["students"][0]["sources"][0]["numbers"] = [99]
        self.assertEqual(self.request("post", "batches/", self.payload).status_code, 400)
        self.assertFalse(WrongNoteBatch.objects.exists())
        self.payload["students"][0]["sources"][0]["numbers"] = [1]
        self.assertEqual(self.request("post", "batches/", self.payload, user=self.students[0].student.user).status_code, 403)
        foreign = Tenant.objects.create(code="qa-range-other", name="Other")
        self.assertEqual(self.request("get", "range/", self.range, tenant=foreign).status_code, 403)
        self.payload.update(from_session_order=3, to_session_order=2)
        self.assertEqual(self.request("post", "batches/", self.payload).status_code, 400)
        self.payload.update(self.range)
        self.exam.segmentation_status = "review_required"
        self.exam.save(update_fields=["segmentation_status"])
        self.assertEqual(self.request("post", "batches/", self.payload).status_code, 400)

    def test_single_student_receives_pdf_and_worker_rechecks_approved_key(self):
        self.payload["students"] = self.payload["students"][:1]
        batch_id = self.request("post", "batches/", self.payload).data["id"]
        answer_key = self.exam.answer_key
        answer_key.answers = {k: "REVISED" for k in answer_key.answers}
        answer_key.save()
        self.assertEqual(self.consume(), "FAILED")
        new = self.request("post", "batches/", self.payload).data["id"]
        self.assertNotEqual(new, batch_id)
        self.assertEqual(self.consume(), "DONE")
        response = self.request("get", f"batches/{new}/download/")
        self.assertTrue(response.data["filename"].endswith(".pdf"))
        self.assertTrue(self.objects[response.data["url"]].startswith(b"%PDF"))

    def test_publish_failure_is_visible_recoverable_and_counted_without_student_data(self):
        from apps.core.management.commands.check_dev_alerts import rule_wrong_note_batch_failures

        self.publisher.side_effect = None
        self.publisher.return_value = False
        batch_id = self.request("post", "batches/", self.payload).data["id"]
        path = f"batches/{batch_id}/"
        self.assertEqual(self.request("get", path).data["status"], "FAILED")
        self.assertIsNone(rule_wrong_note_batch_failures())
        for _ in range(2):
            self.request("post", path + "retry/")
        report = rule_wrong_note_batch_failures()
        self.assertEqual(report["total"], 3)
        self.assertTrue(all(set(row) == {"id", "batch_id"} for row in report["rows"]))
        self.publisher.side_effect = lambda job: self.queue.append(job) or True
        self.request("post", path + "retry/")
        self.assertEqual(self.consume(), "DONE")
        self.assertEqual(self.consume(), "DONE")
        foreign = Tenant.objects.create(code="qa-print-isolation", name="Other")
        other_admin = get_user_model().objects.create_user(username="other-print-admin", tenant=foreign)
        TenantMembership.ensure_active(tenant=foreign, user=other_admin, role="admin")
        for suffix in (path, path + "download/", "range/"):
            response = self.request("get", suffix, self.range, tenant=foreign, user=other_admin)
            self.assertEqual(response.status_code, 404)
        self.assertEqual(self.request("post", path + "retry/", tenant=foreign, user=other_admin).status_code, 404)

"""Integration coverage for student-reported scores across inventory and results."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import TenantMembership
from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.inventory.models import InventoryFile, InventoryFolder
from apps.domains.inventory.views import (
    FileDeleteView,
    FileUploadView,
    FolderDeleteView,
    InventoryListView,
)
from apps.domains.results.models import StudentReportedScore
from apps.domains.students.models import Student
from apps.domains.results.views.admin_student_performance_view import (
    AdminStudentPerformanceView,
)
from apps.domains.results.views.admin_student_reported_score_view import (
    AdminStudentReportedScoreReviewView,
)


User = get_user_model()


class StudentReportedScoreTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.data = self.setup_full_tenant("reported-score", student_count=1)
        self.tenant = self.data["tenant"]
        self.student = self.data["students"][0]
        self.student_user = self.student.user
        self.admin = User.objects.create_user(
            username="reported_score_admin",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")

    def _student_upload(
        self,
        *,
        source="school_exam",
        extra=None,
        filename="score.jpg",
        content=b"\xff\xd8\xff\xe0score-image",
    ):
        data = {
            "scope": "student",
            "student_ps": self.student.ps_number,
            "score_submission": "true",
            "score_source": source,
            "academic_year": "2026",
            "subject": "수학",
            "score": "88",
            "max_score": "100",
            "exam_date": "2026-04-28",
            "semester": "1",
            "exam_round": "first",
            "description": "학교 성적표 제출",
            "file": SimpleUploadedFile(
                filename,
                content,
                content_type="image/jpeg",
            ),
        }
        if source != "school_exam":
            data.pop("semester")
            data.pop("exam_round")
            data["exam_month"] = "6"
            data["grade_scale"] = "nine"
            data["grade_rank"] = "2"
            data["percentile"] = "91.5"
        if extra:
            data.update(extra)
        request = self.factory.post("/storage/inventory/upload/", data=data, format="multipart")
        request.tenant = self.tenant
        with (
            patch(
                "apps.domains.inventory.views.JWTAuthentication.authenticate",
                return_value=(self.student_user, None),
            ),
            patch(
                "apps.domains.inventory.views.Program.ensure_for_tenant",
                return_value=SimpleNamespace(plan="pro"),
            ),
            patch("apps.domains.inventory.views.upload_fileobj_to_r2_storage") as upload_r2,
        ):
            response = FileUploadView.as_view()(request)
        return response, upload_r2

    def _review(self, score_id, action="verify", *, tenant=None, user=None):
        request = self.factory.patch(
            f"/results/admin/reported-scores/{score_id}/review/",
            data={"action": action},
            format="json",
        )
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.admin)
        return AdminStudentReportedScoreReviewView.as_view()(request, score_id=score_id)

    def _console(self):
        request = self.factory.get("/results/admin/student-performance/", {"days": "all"})
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return AdminStudentPerformanceView.as_view()(request)

    def test_student_upload_creates_pending_score_with_evidence_and_inventory_projection(self):
        response, upload_r2 = self._student_upload()

        self.assertEqual(response.status_code, 200, response.content)
        payload = json.loads(response.content)
        self.assertEqual(payload["scoreSubmission"]["status"], "pending")
        self.assertEqual(payload["scoreSubmission"]["label"], "2026년 1학기 1차 지필평가(중간)")
        upload_r2.assert_called_once()

        row = StudentReportedScore.objects.get()
        self.assertEqual(row.tenant, self.tenant)
        self.assertEqual(row.student, self.student)
        self.assertEqual(row.evidence_file.scope, "student")

        list_request = self.factory.get(
            "/storage/inventory/",
            {"scope": "student", "student_ps": self.student.ps_number},
        )
        list_request.tenant = self.tenant
        with patch(
            "apps.domains.inventory.views.JWTAuthentication.authenticate",
            return_value=(self.student_user, None),
        ):
            listed = InventoryListView.as_view()(list_request)
        self.assertEqual(json.loads(listed.content)["files"][0]["scoreSubmission"]["status"], "pending")

    def test_invalid_student_metadata_is_rejected_before_r2_side_effect(self):
        response, upload_r2 = self._student_upload(extra={"score": "120"})

        self.assertEqual(response.status_code, 400)
        upload_r2.assert_not_called()
        self.assertFalse(InventoryFile.objects.exists())
        self.assertFalse(StudentReportedScore.objects.exists())

    def test_masqueraded_evidence_file_is_rejected_before_r2_side_effect(self):
        response, upload_r2 = self._student_upload(content=b"not-a-real-jpeg")

        self.assertEqual(response.status_code, 400)
        self.assertIn("파일 내용", json.loads(response.content)["detail"])
        upload_r2.assert_not_called()

    def test_kice_mock_month_is_limited_to_official_sixth_and_ninth_months(self):
        response, upload_r2 = self._student_upload(
            source="kice_mock",
            extra={"exam_month": "7"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("6월 또는 9월", json.loads(response.content)["detail"])
        upload_r2.assert_not_called()

    def test_pending_score_enters_review_queue_and_verified_score_enters_statistics(self):
        upload, _upload_r2 = self._student_upload()
        score_id = json.loads(upload.content)["scoreSubmission"]["id"]

        pending_console = self._console()
        self.assertEqual(pending_console.status_code, 200, pending_console.data)
        self.assertEqual(pending_console.data["summary"]["pending_reported_score_count"], 1)
        self.assertEqual(
            pending_console.data["students"][0]["source_summaries"]["school"]["scored_count"],
            0,
        )

        reviewed = self._review(score_id)
        self.assertEqual(reviewed.status_code, 200, reviewed.data)
        self.assertEqual(reviewed.data["status"], "verified")

        verified_console = self._console()
        student = verified_console.data["students"][0]
        self.assertEqual(verified_console.data["summary"]["pending_reported_score_count"], 0)
        self.assertEqual(student["source_summaries"]["school"]["scored_count"], 1)
        self.assertEqual(student["source_summaries"]["school"]["latest_score_pct"], 88.0)
        self.assertEqual(
            student["subject_summaries"]["school"]["수학"]["latest_score_pct"],
            88.0,
        )
        self.assertEqual(verified_console.data["filter_options"]["reported_subjects"], ["수학"])

    def test_five_grade_and_school_achievement_metrics_are_preserved(self):
        upload, _ = self._student_upload(extra={
            "grade_scale": "five",
            "grade_rank": "4",
            "achievement_level": "B",
            "subject_average": "71.25",
            "standard_deviation": "12.4",
            "cohort_size": "198",
        })

        self.assertEqual(upload.status_code, 200, upload.content)
        score = json.loads(upload.content)["scoreSubmission"]
        self.assertEqual(score["grade_scale"], "five")
        self.assertEqual(score["grade_rank"], 4)
        self.assertEqual(score["achievement_level"], "B")
        self.assertEqual(score["subject_average"], 71.25)
        self.assertEqual(score["standard_deviation"], 12.4)
        self.assertEqual(score["cohort_size"], 198)

    def test_five_grade_scale_rejects_rank_above_five(self):
        upload, upload_r2 = self._student_upload(extra={
            "grade_scale": "five",
            "grade_rank": "6",
        })

        self.assertEqual(upload.status_code, 400)
        upload_r2.assert_not_called()

    def test_grade_rank_requires_explicit_grade_scale(self):
        upload, upload_r2 = self._student_upload(extra={"grade_rank": "2"})

        self.assertEqual(upload.status_code, 400)
        self.assertIn("5등급제 또는 9등급제", json.loads(upload.content)["detail"])
        upload_r2.assert_not_called()

    def test_verifying_corrected_submission_replaces_previous_verified_score(self):
        first, _ = self._student_upload(filename="first.jpg")
        first_id = json.loads(first.content)["scoreSubmission"]["id"]
        self.assertEqual(self._review(first_id).status_code, 200)

        second, _ = self._student_upload(extra={"score": "93"}, filename="second.jpg")
        second_id = json.loads(second.content)["scoreSubmission"]["id"]
        self.assertEqual(self._review(second_id).status_code, 200)

        self.assertEqual(StudentReportedScore.objects.get(id=first_id).status, "rejected")
        self.assertEqual(StudentReportedScore.objects.get(id=second_id).status, "verified")
        self.assertEqual(StudentReportedScore.objects.filter(status="verified").count(), 1)

    def test_verified_trends_are_separated_by_subject(self):
        math_upload, _ = self._student_upload(filename="math.jpg")
        self.assertEqual(
            self._review(json.loads(math_upload.content)["scoreSubmission"]["id"]).status_code,
            200,
        )
        english_upload, _ = self._student_upload(
            filename="english.jpg",
            extra={"subject": "영어", "score": "50"},
        )
        self.assertEqual(
            self._review(json.loads(english_upload.content)["scoreSubmission"]["id"]).status_code,
            200,
        )

        response = self._console()
        summaries = response.data["students"][0]["subject_summaries"]["school"]
        self.assertEqual(summaries["수학"]["latest_score_pct"], 88.0)
        self.assertEqual(summaries["영어"]["latest_score_pct"], 50.0)
        self.assertIsNone(summaries["수학"]["change_pct_points"])
        self.assertIsNone(summaries["영어"]["change_pct_points"])

    def test_review_locks_student_as_shared_concurrency_key(self):
        upload, _ = self._student_upload()
        score_id = json.loads(upload.content)["scoreSubmission"]["id"]

        with patch(
            "apps.support.results.student_reported_scores.Student.objects.select_for_update",
            wraps=Student.objects.select_for_update,
        ) as lock_student:
            response = self._review(score_id)

        self.assertEqual(response.status_code, 200)
        lock_student.assert_called_once_with()

    def test_linked_evidence_cannot_be_deleted_and_foreign_tenant_cannot_review(self):
        upload, _ = self._student_upload()
        payload = json.loads(upload.content)
        inv_file_id = int(payload["id"])
        score_id = payload["scoreSubmission"]["id"]

        delete_request = self.factory.delete(
            f"/storage/inventory/files/{inv_file_id}/?scope=student&student_ps={self.student.ps_number}"
        )
        delete_request.tenant = self.tenant
        with patch(
            "apps.domains.inventory.views.JWTAuthentication.authenticate",
            return_value=(self.student_user, None),
        ):
            deleted = FileDeleteView.as_view()(delete_request, file_id=inv_file_id)
        self.assertEqual(deleted.status_code, 409)
        self.assertTrue(InventoryFile.objects.filter(id=inv_file_id).exists())

        other = self.setup_full_tenant("reported-score-other", student_count=1)
        other_admin = User.objects.create_user(
            username="reported_score_other_admin",
            password="test1234",
            tenant=other["tenant"],
        )
        TenantMembership.ensure_active(
            tenant=other["tenant"],
            user=other_admin,
            role="admin",
        )
        foreign_review = self._review(
            score_id,
            tenant=other["tenant"],
            user=other_admin,
        )
        self.assertEqual(foreign_review.status_code, 404)

    def test_recursive_folder_delete_cannot_remove_linked_evidence_or_r2_object(self):
        upload, _ = self._student_upload()
        inv_file = InventoryFile.objects.get(id=int(json.loads(upload.content)["id"]))
        folder = InventoryFolder.objects.create(
            tenant=self.tenant,
            scope="student",
            student_ps=self.student.ps_number,
            name="성적표",
        )
        inv_file.folder = folder
        inv_file.save(update_fields=["folder", "updated_at"])

        request = self.factory.delete(
            f"/storage/inventory/folders/{folder.id}/"
            f"?scope=student&student_ps={self.student.ps_number}&recursive=true"
        )
        request.tenant = self.tenant
        with (
            patch(
                "apps.domains.inventory.views.JWTAuthentication.authenticate",
                return_value=(self.student_user, None),
            ),
            patch(
                "apps.domains.inventory.services.delete_object_r2_storage"
            ) as delete_r2,
        ):
            response = FolderDeleteView.as_view()(request, folder_id=folder.id)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)["code"], "reported_score_evidence_protected")
        delete_r2.assert_not_called()
        self.assertTrue(InventoryFile.objects.filter(id=inv_file.id).exists())
        self.assertTrue(InventoryFolder.objects.filter(id=folder.id).exists())

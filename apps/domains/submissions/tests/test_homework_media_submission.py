from __future__ import annotations

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models.query import QuerySet
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.homework.models import HomeworkAssignment
from apps.domains.homework_results.models import Homework, HomeworkScore
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission, SubmissionMedia
from apps.domains.submissions.views.homework_submission_media_view import (
    HomeworkSubmissionMediaCollectionView,
    HomeworkSubmissionMediaDetailView,
    HomeworkSubmissionMediaPreviewView,
)
from apps.domains.submissions.views.homework_submissions_list_view import (
    HomeworkSubmissionsListView,
)
from apps.support.results.admin_student_grades_dependencies import (
    submitted_homework_keys_for_grades,
)


User = get_user_model()


def _jpeg(name: str = "page.jpg", *, body: bytes = b"page") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name,
        b"\xff\xd8\xff\xe0" + body,
        content_type="image/jpeg",
    )


def _mp4(name: str = "proof.mp4") -> SimpleUploadedFile:
    return SimpleUploadedFile(
        name,
        b"\x00\x00\x00\x18ftypmp42" + b"video-proof",
        content_type="video/mp4",
    )


class HomeworkSubmissionMediaTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="homework-media",
            name="Homework Media",
            is_active=True,
        )
        self.student_user = User.objects.create_user(
            username="homework-media-student",
            password="pw1234",
            tenant=self.tenant,
            name="학생",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.student_user,
            role="student",
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            ps_number="HWM001",
            omr_code="31415926",
            name="학생",
            phone="01011112222",
            parent_phone="01033334444",
        )
        self.teacher = User.objects.create_user(
            username="homework-media-teacher",
            password="pw1234",
            tenant=self.tenant,
            name="선생님",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.teacher,
            role="teacher",
        )
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="수학",
            name="수학",
            subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture,
            order=1,
            title="1회",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        self.homework = Homework.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="풀이 인증",
            meta={"default_max_score": 10},
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.homework,
            session=self.session,
            enrollment=self.enrollment,
        )

    def _request(self, method: str, path: str, *, user=None, data=None):
        if method == "get":
            request = self.factory.get(path, data=data)
        elif method == "post":
            request = self.factory.post(path, data=data, format="multipart")
        elif method == "delete":
            request = self.factory.delete(path, data=data, format="json")
        else:
            raise AssertionError(f"unsupported method: {method}")
        request.tenant = self.tenant
        force_authenticate(request, user=user or self.student_user)
        return request

    def _post(self, *, file, client_file_id=None, batch_id=None, position=0):
        path = f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/"
        request = self._request(
            "post",
            path,
            data={
                "enrollment_id": self.enrollment.id,
                "client_file_id": client_file_id or str(uuid.uuid4()),
                "upload_batch_id": batch_id or str(uuid.uuid4()),
                "position": position,
                "file": file,
            },
        )
        return HomeworkSubmissionMediaCollectionView.as_view()(
            request,
            homework_id=self.homework.id,
        )

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_multiple_images_and_video_persist_with_identity_and_order(
        self,
        upload_fileobj_to_r2,
    ):
        batch_id = str(uuid.uuid4())
        first = self._post(file=_jpeg("01.jpg"), batch_id=batch_id, position=0)
        second = self._post(file=_mp4("02.mp4"), batch_id=batch_id, position=1)

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertNotEqual(first.data["id"], second.data["id"])
        self.assertEqual(upload_fileobj_to_r2.call_count, 2)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(SubmissionMedia.objects.count(), 2)

        request = self._request(
            "get",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/",
            data={"enrollment_id": self.enrollment.id},
        )
        response = HomeworkSubmissionMediaCollectionView.as_view()(
            request,
            homework_id=self.homework.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["position"] for item in response.data["files"]], [0, 1])
        self.assertEqual(
            [item["media_kind"] for item in response.data["files"]],
            ["image", "video"],
        )
        self.assertEqual(response.data["limits"]["max_files"], 20)
        self.assertEqual(response.data["limits"]["max_file_size_bytes"], 100 * 1024 * 1024)
        self.assertEqual(response.data["limits"]["max_total_size_bytes"], 500 * 1024 * 1024)

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_same_client_file_retry_is_idempotent(
        self,
        upload_fileobj_to_r2,
    ):
        client_file_id = str(uuid.uuid4())
        first = self._post(file=_jpeg(body=b"same"), client_file_id=client_file_id)
        second = self._post(file=_jpeg(body=b"same"), client_file_id=client_file_id)

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertTrue(second.data["deduplicated"])
        upload_fileobj_to_r2.assert_called_once()

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_same_successful_fingerprint_with_new_client_id_is_not_duplicated(
        self,
        upload_fileobj_to_r2,
    ):
        first = self._post(file=_jpeg(body=b"same-fingerprint"), position=0)
        second = self._post(file=_jpeg(body=b"same-fingerprint"), position=1)

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertTrue(second.data["deduplicated"])
        self.assertEqual(SubmissionMedia.objects.count(), 1)
        upload_fileobj_to_r2.assert_called_once()

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_failed_file_retry_reuses_object_identity_and_keeps_successful_file(
        self,
        upload_fileobj_to_r2,
    ):
        upload_fileobj_to_r2.side_effect = [None, RuntimeError("storage unavailable"), None]
        successful = self._post(file=_jpeg("success.jpg", body=b"success"), position=0)
        client_file_id = str(uuid.uuid4())
        failed = self._post(
            file=_jpeg("retry.jpg", body=b"retry"),
            client_file_id=client_file_id,
            position=1,
        )

        self.assertEqual(successful.status_code, 201, successful.data)
        self.assertEqual(failed.status_code, 503, failed.data)
        failed_media = SubmissionMedia.objects.get(client_upload_id=client_file_id)
        failed_object_key = failed_media.object_key
        self.assertEqual(failed_media.status, SubmissionMedia.Status.FAILED)
        self.assertEqual(
            SubmissionMedia.objects.get(id=successful.data["id"]).status,
            SubmissionMedia.Status.UPLOADED,
        )

        retried = self._post(
            file=_jpeg("retry.jpg", body=b"retry"),
            client_file_id=client_file_id,
            position=1,
        )

        self.assertEqual(retried.status_code, 201, retried.data)
        failed_media.refresh_from_db()
        self.assertEqual(failed_media.status, SubmissionMedia.Status.UPLOADED)
        self.assertEqual(failed_media.object_key, failed_object_key)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(SubmissionMedia.objects.count(), 2)

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_object_success_with_db_finalize_failure_is_recoverable_by_same_retry(
        self,
        upload_fileobj_to_r2,
    ):
        client_file_id = str(uuid.uuid4())
        original_update = QuerySet.update

        def fail_uploaded_update(queryset, **kwargs):
            if kwargs.get("status") == SubmissionMedia.Status.UPLOADED:
                raise RuntimeError("database finalize unavailable")
            return original_update(queryset, **kwargs)

        with patch.object(QuerySet, "update", new=fail_uploaded_update):
            failed = self._post(
                file=_jpeg("db-retry.jpg", body=b"db-retry"),
                client_file_id=client_file_id,
            )

        self.assertEqual(failed.status_code, 503, failed.data)
        media = SubmissionMedia.objects.get(client_upload_id=client_file_id)
        object_key = media.object_key
        self.assertEqual(media.status, SubmissionMedia.Status.FAILED)
        self.assertEqual(media.error_message, "파일 저장 확인 실패")

        retried = self._post(
            file=_jpeg("db-retry.jpg", body=b"db-retry"),
            client_file_id=client_file_id,
        )

        self.assertEqual(retried.status_code, 201, retried.data)
        media.refresh_from_db()
        self.assertEqual(media.object_key, object_key)
        self.assertEqual(media.status, SubmissionMedia.Status.UPLOADED)
        self.assertEqual(upload_fileobj_to_r2.call_count, 2)

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_rejects_signature_mismatch_and_twenty_first_active_file(
        self,
        upload_fileobj_to_r2,
    ):
        invalid = self._post(
            file=SimpleUploadedFile("fake.jpg", b"not-an-image", content_type="image/jpeg")
        )
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(Submission.objects.count(), 0)

        for position in range(20):
            response = self._post(
                file=_jpeg(f"{position}.jpg", body=f"page-{position}".encode()),
                position=position,
            )
            self.assertEqual(response.status_code, 201, response.data)
        overflow = self._post(
            file=_jpeg("overflow.jpg", body=b"overflow"),
            position=19,
        )

        self.assertEqual(overflow.status_code, 400, overflow.data)
        self.assertEqual(overflow.data["code"], "HOMEWORK_MEDIA_LIMIT")

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_cross_enrollment_upload_is_denied(
        self,
        upload_fileobj_to_r2,
    ):
        other_user = User.objects.create_user(
            username="homework-media-other",
            password="pw1234",
            tenant=self.tenant,
        )
        other_student = Student.objects.create(
            tenant=self.tenant,
            user=other_user,
            ps_number="HWM002",
            omr_code="27182818",
            name="다른 학생",
            phone="01055556666",
            parent_phone="01077778888",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=other_student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        path = f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/"
        request = self._request(
            "post",
            path,
            data={
                "enrollment_id": other_enrollment.id,
                "client_file_id": str(uuid.uuid4()),
                "upload_batch_id": str(uuid.uuid4()),
                "position": 0,
                "file": _jpeg(),
            },
        )
        response = HomeworkSubmissionMediaCollectionView.as_view()(
            request,
            homework_id=self.homework.id,
        )

        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(Submission.objects.count(), 0)

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_student_remove_is_soft_and_restores_not_submitted_projection(
        self,
        upload_fileobj_to_r2,
    ):
        created = self._post(file=_jpeg())
        submission_id = created.data["id"]

        request = self._request(
            "delete",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/{submission_id}/",
            data={"enrollment_id": self.enrollment.id},
        )
        response = HomeworkSubmissionMediaDetailView.as_view()(
            request,
            homework_id=self.homework.id,
            media_id=str(submission_id),
        )

        self.assertEqual(response.status_code, 204, getattr(response, "data", None))
        media = SubmissionMedia.objects.get(id=submission_id)
        self.assertIsNotNone(media.removed_at)
        self.assertEqual(media.removed_by_id, self.student_user.id)
        self.assertTrue(media.object_key)
        self.assertEqual(
            submitted_homework_keys_for_grades(
                tenant=self.tenant,
                enrollment_ids=[self.enrollment.id],
                homework_ids=[self.homework.id],
            ),
            set(),
        )

    @patch(
        "apps.domains.submissions.views.homework_submission_media_view.generate_presigned_get_url",
        return_value="https://preview.invalid/legacy",
    )
    def test_existing_single_file_submission_is_projected_and_previewable(self, presign):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=self.homework.id,
            source=Submission.Source.HOMEWORK_IMAGE,
            file_key="tenants/1/ai/submissions/legacy/page.jpg",
            file_type="image/jpeg",
            file_size=2345,
            status=Submission.Status.SUBMITTED,
            meta={"original_filename": "기존 풀이.jpg"},
        )
        request = self._request(
            "get",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/",
            data={"enrollment_id": self.enrollment.id},
        )
        response = HomeworkSubmissionMediaCollectionView.as_view()(
            request,
            homework_id=self.homework.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["files"]), 1)
        self.assertEqual(response.data["files"][0]["id"], f"legacy-{submission.id}")
        self.assertEqual(response.data["files"][0]["status"], SubmissionMedia.Status.UPLOADED)
        self.assertEqual(response.data["files"][0]["original_filename"], "기존 풀이.jpg")
        self.assertEqual(SubmissionMedia.objects.count(), 0)

        preview_request = self._request(
            "get",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/legacy-{submission.id}/preview/",
            user=self.teacher,
        )
        preview = HomeworkSubmissionMediaPreviewView.as_view()(
            preview_request,
            homework_id=self.homework.id,
            media_id=f"legacy-{submission.id}",
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["url"], "https://preview.invalid/legacy")
        presign.assert_called_once_with(key=submission.file_key, expires_in=600)

    @patch(
        "apps.domains.submissions.views.homework_submission_media_view.generate_presigned_get_url"
    )
    def test_removed_legacy_file_is_not_presigned_for_teacher(self, presign):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=self.homework.id,
            source=Submission.Source.HOMEWORK_IMAGE,
            file_key="tenants/1/ai/submissions/legacy/removed.jpg",
            file_type="image/jpeg",
            file_size=2345,
            status=Submission.Status.SUBMITTED,
            meta={"homework_media_legacy_removed_at": "2026-08-23T00:00:00Z"},
        )
        request = self._request(
            "get",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/legacy-{submission.id}/preview/",
            user=self.teacher,
        )

        response = HomeworkSubmissionMediaPreviewView.as_view()(
            request,
            homework_id=self.homework.id,
            media_id=f"legacy-{submission.id}",
        )

        self.assertEqual(response.status_code, 404, response.data)
        presign.assert_not_called()

    @patch(
        "apps.domains.submissions.views.homework_submission_media_view.generate_presigned_get_url"
    )
    def test_student_preview_requires_current_active_enrollment(self, presign):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=self.homework.id,
            source=Submission.Source.HOMEWORK_MEDIA,
            status=Submission.Status.SUBMITTED,
        )
        media = SubmissionMedia.objects.create(
            tenant=self.tenant,
            submission=submission,
            status=SubmissionMedia.Status.UPLOADED,
            object_key="tenants/1/ai/submissions/1/inactive.jpg",
            fingerprint="d" * 64,
            media_kind=SubmissionMedia.Kind.IMAGE,
            mime_type="image/jpeg",
            size=4567,
            original_filename="지난 풀이.jpg",
            client_upload_id=uuid.uuid4(),
            upload_batch_id=uuid.uuid4(),
            position=0,
        )
        self.enrollment.status = "INACTIVE"
        self.enrollment.save(update_fields=["status", "updated_at"])
        request = self._request(
            "get",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/{media.id}/preview/",
        )

        response = HomeworkSubmissionMediaPreviewView.as_view()(
            request,
            homework_id=self.homework.id,
            media_id=str(media.id),
        )

        self.assertEqual(response.status_code, 403, response.data)
        presign.assert_not_called()

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_student_remove_is_locked_after_teacher_score(
        self,
        upload_fileobj_to_r2,
    ):
        created = self._post(file=_jpeg())
        HomeworkScore.objects.create(
            enrollment=self.enrollment,
            session=self.session,
            homework=self.homework,
            score=10,
            max_score=10,
            passed=True,
            attempt_index=1,
        )
        request = self._request(
            "delete",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/{created.data['id']}/",
            data={"enrollment_id": self.enrollment.id},
        )
        response = HomeworkSubmissionMediaDetailView.as_view()(
            request,
            homework_id=self.homework.id,
            media_id=str(created.data["id"]),
        )

        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["code"], "HOMEWORK_MEDIA_REVIEWED")

    @patch("apps.domains.submissions.services.homework_media.upload_fileobj_to_r2")
    def test_student_cannot_remove_another_students_file(
        self,
        upload_fileobj_to_r2,
    ):
        created = self._post(file=_jpeg())
        other_user = User.objects.create_user(
            username="homework-media-delete-other",
            password="pw1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=other_user,
            role="student",
        )
        other_student = Student.objects.create(
            tenant=self.tenant,
            user=other_user,
            ps_number="HWM003",
            omr_code="16180339",
            name="삭제 시도 학생",
            phone="01088889999",
            parent_phone="01099990000",
        )
        other_enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=other_student,
            lecture=self.lecture,
            status="ACTIVE",
        )
        HomeworkAssignment.objects.create(
            tenant=self.tenant,
            homework=self.homework,
            session=self.session,
            enrollment=other_enrollment,
        )
        request = self._request(
            "delete",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/{created.data['id']}/",
            user=other_user,
            data={"enrollment_id": other_enrollment.id},
        )

        response = HomeworkSubmissionMediaDetailView.as_view()(
            request,
            homework_id=self.homework.id,
            media_id=str(created.data["id"]),
        )

        self.assertEqual(response.status_code, 404, response.data)
        self.assertIsNone(SubmissionMedia.objects.get(id=created.data["id"]).removed_at)

    @patch(
        "apps.domains.submissions.views.homework_submission_media_view.generate_presigned_get_url",
        return_value="https://preview.invalid/file",
    )
    def test_teacher_gets_per_file_status_error_and_authorized_preview(self, presign):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=self.homework.id,
            source=Submission.Source.HOMEWORK_MEDIA,
            status=Submission.Status.SUBMITTED,
        )
        failed_media = SubmissionMedia.objects.create(
            tenant=self.tenant,
            submission=submission,
            status=SubmissionMedia.Status.FAILED,
            object_key="tenants/1/ai/submissions/1/video.mp4",
            fingerprint="a" * 64,
            media_kind=SubmissionMedia.Kind.VIDEO,
            mime_type="video/mp4",
            size=1234,
            original_filename="풀이 영상.mp4",
            client_upload_id=uuid.uuid4(),
            upload_batch_id=uuid.uuid4(),
            position=2,
            error_message="analysis failed",
        )
        media = SubmissionMedia.objects.create(
            tenant=self.tenant,
            submission=submission,
            status=SubmissionMedia.Status.UPLOADED,
            object_key="tenants/1/ai/submissions/1/image.jpg",
            fingerprint="b" * 64,
            media_kind=SubmissionMedia.Kind.IMAGE,
            mime_type="image/jpeg",
            size=4567,
            original_filename="풀이 사진.jpg",
            client_upload_id=uuid.uuid4(),
            upload_batch_id=uuid.uuid4(),
            position=3,
        )
        list_request = self._request(
            "get",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/",
            user=self.teacher,
        )
        listed = HomeworkSubmissionsListView.as_view()(
            list_request,
            homework_id=self.homework.id,
        )

        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data[0]["files"][0]["original_filename"], "풀이 영상.mp4")
        self.assertEqual(listed.data[0]["files"][0]["media_kind"], "video")
        self.assertEqual(listed.data[0]["files"][0]["position"], 2)
        self.assertEqual(listed.data[0]["files"][0]["error_message"], "analysis failed")
        self.assertNotIn("object_key", listed.data[0]["files"][0])
        self.assertNotIn("file_key", listed.data[0])

        preview_request = self._request(
            "get",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/{media.id}/preview/",
            user=self.teacher,
        )
        preview = HomeworkSubmissionMediaPreviewView.as_view()(
            preview_request,
            homework_id=self.homework.id,
            media_id=str(media.id),
        )

        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["url"], "https://preview.invalid/file")
        self.assertEqual(preview.data["media_kind"], "image")
        presign.assert_called_once_with(key=media.object_key, expires_in=600)

        failed_request = self._request(
            "get",
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/{failed_media.id}/preview/",
            user=self.teacher,
        )
        failed_preview = HomeworkSubmissionMediaPreviewView.as_view()(
            failed_request,
            homework_id=self.homework.id,
            media_id=str(failed_media.id),
        )
        self.assertEqual(failed_preview.status_code, 409, failed_preview.data)

    @patch(
        "apps.domains.submissions.views.homework_submission_media_view.generate_presigned_get_url",
        return_value="https://preview.invalid/file",
    )
    def test_teacher_cannot_preview_another_tenant_file(self, presign):
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            enrollment=self.enrollment,
            target_type=Submission.TargetType.HOMEWORK,
            target_id=self.homework.id,
            source=Submission.Source.HOMEWORK_MEDIA,
            status=Submission.Status.SUBMITTED,
        )
        media = SubmissionMedia.objects.create(
            tenant=self.tenant,
            submission=submission,
            status=SubmissionMedia.Status.UPLOADED,
            object_key="tenants/1/ai/submissions/1/image.jpg",
            fingerprint="c" * 64,
            media_kind=SubmissionMedia.Kind.IMAGE,
            mime_type="image/jpeg",
            size=4567,
            original_filename="풀이 사진.jpg",
            client_upload_id=uuid.uuid4(),
            upload_batch_id=uuid.uuid4(),
            position=0,
        )
        other_tenant = Tenant.objects.create(
            code="homework-media-other-tenant",
            name="Other Tenant",
            is_active=True,
        )
        other_teacher = User.objects.create_user(
            username="homework-media-other-teacher",
            password="pw1234",
            tenant=other_tenant,
        )
        TenantMembership.ensure_active(
            tenant=other_tenant,
            user=other_teacher,
            role="teacher",
        )
        request = self.factory.get(
            f"/api/v1/submissions/submissions/homework/{self.homework.id}/media/{media.id}/preview/"
        )
        request.tenant = other_tenant
        force_authenticate(request, user=other_teacher)

        response = HomeworkSubmissionMediaPreviewView.as_view()(
            request,
            homework_id=self.homework.id,
            media_id=str(media.id),
        )

        self.assertEqual(response.status_code, 404, response.data)
        presign.assert_not_called()

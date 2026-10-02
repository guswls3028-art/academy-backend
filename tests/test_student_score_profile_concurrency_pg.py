from __future__ import annotations

import io
import threading
import time
import unittest
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase
from PIL import Image
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.inventory.views import FileUploadView
from apps.domains.parents.services import ensure_parent_account_for_student
from apps.domains.students.services import update_student_profile
from apps.domains.students.services.account_notice import lock_account_notice_users
from apps.domains.students.services.lifecycle import restore_student, soft_delete_student
from apps.support.students.namespace_lock import STUDENT_PS_NAMESPACE_LOCK_VERSION


User = get_user_model()
Student = apps.get_model("students", "Student")
InventoryFile = apps.get_model("inventory", "InventoryFile")
StudentReportedScore = apps.get_model("results", "StudentReportedScore")


class TestStudentScoreProfileConcurrencyPostgres(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL is required for score/profile lock races.")
        super().setUpClass()

    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="score-parent-profile-race", name="Score Parent Profile Race", is_active=True,
        )
        self.parent_a = ensure_parent_account_for_student(
            tenant=self.tenant, parent_phone="01081001001",
            student_name="성적 학생", initial_password="Parent-A-1234",
        ).parent
        self.student_user = User.objects.create_user(
            username="01081002002", phone="01081002002", name="성적 학생",
            tenant=self.tenant, password="Student-1234",
        )
        self.student = Student.objects.create(
            tenant=self.tenant, user=self.student_user, parent=self.parent_a,
            name="성적 학생", ps_number="01081002002", phone="01081002002",
            parent_phone=self.parent_a.phone, omr_code="81002002",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant, user=self.student_user, role="student",
        )
        self.parent_b_password = "Parent-B-1234"
        self.parent_b = ensure_parent_account_for_student(
            tenant=self.tenant, parent_phone="01081003003",
            student_name=self.student.name, initial_password=self.parent_b_password,
        ).parent
        self.assertLess(self.parent_a.user_id, self.student.user_id)
        self.password_hashes = dict(User.objects.filter(
            pk__in=(self.parent_a.user_id, self.student.user_id, self.parent_b.user_id),
        ).values_list("pk", "password"))
        token = AccessToken.for_user(self.parent_a.user)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = self.parent_a.user.token_version
        self.parent_token = str(token)
        image = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(image, format="JPEG")
        self.jpeg = image.getvalue()
        self.put_keys = []
        self.deleted_keys = []
        self.storage_objects = set()
        put = patch("apps.domains.inventory.views.upload_fileobj_to_r2_storage", side_effect=self._put)
        delete = patch("apps.infrastructure.storage.r2.delete_object_r2_storage", side_effect=self._delete)
        put.start()
        self.addCleanup(put.stop)
        delete.start()
        self.addCleanup(delete.stop)

    def _put(self, *args, key, **kwargs):
        self.put_keys.append(key)
        self.storage_objects.add(key)

    def _delete(self, *, key, **kwargs):
        self.deleted_keys.append(key)
        self.storage_objects.discard(key)

    def _thread(self, name, work, errors, pids, observer=None):
        def run():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[name] = cursor.fetchone()[0]
                if observer is None:
                    work()
                else:
                    with connection.execute_wrapper(observer):
                        work()
            except BaseException as exc:
                errors.append((name, exc))
            finally:
                connection.close()

        return threading.Thread(target=run, name=name)

    def _finish(self, threads, *releases):
        for event in releases:
            event.set()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=15)
        for thread in threads:
            self.assertFalse(thread.is_alive(), f"{thread.name} did not finish")

    def _wait_for_lock(self, pids, name):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pid = pids.get(name)
            if pid is not None:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [pid],
                    )
                    row = cursor.fetchone()
                if row and row[0] == "Lock":
                    return
            time.sleep(0.05)
        self.fail(f"{name} did not reach a real PostgreSQL Lock wait")

    def _namespace_sql(self, sql, params):
        prefix = f"{STUDENT_PS_NAMESPACE_LOCK_VERSION}:{self.tenant.pk}:"
        return "pg_advisory_xact_lock" in str(sql) and any(
            str(value).startswith(prefix) for value in (params or ())
        )

    def _student_save_hook(self, entered, release=None):
        original = Student.save

        def save(student, *args, **kwargs):
            if (
                threading.current_thread().name == "profile"
                and student.pk == self.student.pk and not entered.is_set()
            ):
                entered.set()
                if release is not None and not release.wait(timeout=10):
                    raise TimeoutError("profile save release timed out")
            return original(student, *args, **kwargs)

        return save

    def _upload(self, responses):
        request = APIRequestFactory().post(
            "/storage/inventory/upload/",
            data={
                "scope": "student", "student_ps": self.student.ps_number,
                "score_submission": "true", "score_source": "school_exam",
                "academic_year": "2026", "semester": "1", "exam_round": "first",
                "exam_date": "2026-10-02", "subject": "math", "score": "88", "max_score": "100",
                "file": SimpleUploadedFile("score.jpg", self.jpeg, content_type="image/jpeg"),
            },
            format="multipart",
            HTTP_AUTHORIZATION=f"Bearer {self.parent_token}",
            HTTP_X_STUDENT_ID=str(self.student.pk),
        )
        request.tenant = self.tenant
        responses.append(FileUploadView.as_view()(request))

    def _profile(self, results):
        with transaction.atomic():
            result = update_student_profile(
                student=Student.objects.get(pk=self.student.pk), tenant=self.tenant,
                data={
                    "parent_phone": self.parent_b.phone,
                    "parent_initial_password": "Do-not-replace-existing-1234",
                },
            )
            lock_account_notice_users(result.student)
            Student.objects.select_for_update().get(pk=result.student.pk)
        results.append(result)

    def _assert_profile_changed(self, results):
        self.assertEqual(len(results), 1)
        self.assertIn("parent_phone", results[0].changed_fields)
        self.assertTrue(results[0].parent_relinked)
        self.assertFalse(results[0].parent_credentials_initialized)
        self.student.refresh_from_db()
        self.assertEqual(self.student.parent_id, self.parent_b.pk)
        self.assertEqual(self.student.parent_phone, self.parent_b.phone)
        self.assertEqual(self.student.user_id, self.student_user.pk)
        self.assertEqual(dict(User.objects.filter(
            pk__in=self.password_hashes,
        ).values_list("pk", "password")), self.password_hashes)
        self.parent_b.user.refresh_from_db()
        self.assertTrue(self.parent_b.user.check_password(self.parent_b_password))

    def _assert_uploaded(self, responses):
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].status_code, 200, responses[0].content)
        score = StudentReportedScore.objects.get(student_id=self.student.pk, tenant=self.tenant)
        score.refresh_from_db()
        self.assertEqual(score.score, 88)
        self.assertEqual(score.submitted_by_id, self.parent_a.user_id)
        evidence = InventoryFile.objects.get(pk=score.evidence_file_id, tenant=self.tenant)
        evidence.refresh_from_db()
        self.assertEqual(evidence.student_ps, self.student.ps_number)
        self.assertEqual(self.put_keys, [evidence.r2_key])
        self.assertEqual(self.storage_objects, {evidence.r2_key})
        self.assertEqual(self.deleted_keys, [])

    def test_upload_first_blocks_parent_phone_update_before_student_save(self):
        namespace_held, release_upload, save_entered = (threading.Event() for _ in range(3))
        errors, responses, results, pids = [], [], [], {}

        def observe_namespace(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if self._namespace_sql(sql, params) and not namespace_held.is_set():
                namespace_held.set()
                if not release_upload.wait(timeout=10):
                    raise TimeoutError("upload namespace release timed out")
            return result

        upload = self._thread("upload", lambda: self._upload(responses), errors, pids, observe_namespace)
        profile = self._thread("profile", lambda: self._profile(results), errors, pids)
        with patch.object(Student, "save", new=self._student_save_hook(save_entered)):
            try:
                upload.start()
                self.assertTrue(namespace_held.wait(timeout=5))
                profile.start()
                self._wait_for_lock(pids, "profile")
                self.assertFalse(save_entered.is_set(), "Profile crossed the uploading User reference fence")
            finally:
                self._finish((upload, profile), release_upload)
        self.assertEqual(errors, [])
        self.assertTrue(save_entered.is_set())
        self._assert_uploaded(responses)
        self._assert_profile_changed(results)

    def test_parent_ensure_first_blocks_profile_before_student_save(self):
        parent_held, release_ensure, save_entered = (threading.Event() for _ in range(3))
        errors, ensured, results, pids = [], [], [], {}
        Parent = apps.get_model("parents", "Parent")

        def observe_parent(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if Parent._meta.db_table in str(sql) and "FOR UPDATE" in str(sql).upper() and not parent_held.is_set():
                parent_held.set()
                if not release_ensure.wait(timeout=10):
                    raise TimeoutError("parent ensure release timed out")
            return result

        def ensure():
            with transaction.atomic():
                result = ensure_parent_account_for_student(
                    tenant=self.tenant, parent_phone=self.parent_b.phone,
                    student_name=self.student.name, initial_password="Do-not-replace-existing-1234",
                )
                self.assertTrue(result.password_for_notice)
                ensured.append(result.parent.pk)

        creation = self._thread("ensure", ensure, errors, pids, observe_parent)
        profile = self._thread("profile", lambda: self._profile(results), errors, pids)
        with patch.object(Student, "save", new=self._student_save_hook(save_entered)):
            try:
                creation.start()
                self.assertTrue(parent_held.wait(timeout=5))
                profile.start()
                self._wait_for_lock(pids, "profile")
                self.assertFalse(save_entered.is_set(), "Profile saved before the existing parent account fence")
            finally:
                self._finish((creation, profile), release_ensure)
        self.assertEqual(errors, [])
        self.assertEqual(ensured, [self.parent_b.pk])
        self._assert_profile_changed(results)

    def test_restore_and_sorted_account_notice_recover_incomplete_parent_without_deadlock(self):
        original_ps = self.student.ps_number
        notice_student = Student.objects.select_related("parent__user").get(pk=self.student.pk)
        soft_delete_student(self.student, tenant=self.tenant)
        self.student.refresh_from_db()
        self.parent_a.user.set_unusable_password()
        self.parent_a.user.save(update_fields=["password"])
        student_held, release_restore = threading.Event(), threading.Event()
        errors, restored, notice_done, pids = [], [], [], {}

        def observe_student(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if Student._meta.db_table in str(sql) and "FOR UPDATE" in str(sql).upper() and not student_held.is_set():
                student_held.set()
                if not release_restore.wait(timeout=10):
                    raise TimeoutError("restore account release timed out")
            return result

        def restore():
            restored.append(restore_student(
                Student.objects.get(pk=self.student.pk), tenant=self.tenant,
                parent_initial_password="Restored-Parent-1234", parent_initial_password_mode="fixed",
            ))

        def notice():
            with transaction.atomic():
                # This notice resolved its family before soft delete disconnected Parent.
                lock_account_notice_users(notice_student)
                Student.objects.select_for_update().get(pk=notice_student.pk)
            notice_done.append(True)

        recovery = self._thread("restore", restore, errors, pids, observe_student)
        sender = self._thread("notice", notice, errors, pids)
        try:
            recovery.start()
            self.assertTrue(student_held.wait(timeout=5))
            sender.start()
            self._wait_for_lock(pids, "notice")
        finally:
            self._finish((recovery, sender), release_restore)
        self.assertEqual(errors, [])
        self.assertEqual(len(restored), 1)
        self.assertEqual(notice_done, [True])
        self.student.refresh_from_db()
        self.student_user.refresh_from_db()
        self.parent_a.user.refresh_from_db()
        self.assertIsNone(self.student.deleted_at)
        self.assertEqual(self.student.ps_number, original_ps)
        self.assertTrue(self.student_user.is_active)
        self.assertTrue(self.parent_a.user.check_password("Restored-Parent-1234"))
        self.assertTrue(TenantMembership.objects.get(tenant=self.tenant, user=self.student_user).is_active)
        self.assertEqual(self.put_keys, [])
        self.assertEqual(self.storage_objects, set())

    def test_profile_first_rejects_stale_parent_upload_and_cleans_exact_put(self):
        save_entered, release_profile, namespace_requested = (threading.Event() for _ in range(3))
        errors, responses, results, pids = [], [], [], {}

        def observe_namespace(execute, sql, params, many, context):
            if self._namespace_sql(sql, params):
                namespace_requested.set()
            return execute(sql, params, many, context)

        profile = self._thread("profile", lambda: self._profile(results), errors, pids)
        upload = self._thread("upload", lambda: self._upload(responses), errors, pids, observe_namespace)
        with patch.object(Student, "save", new=self._student_save_hook(save_entered, release_profile)):
            try:
                profile.start()
                self.assertTrue(save_entered.wait(timeout=5))
                upload.start()
                self._wait_for_lock(pids, "upload")
                self.assertEqual(len(self.put_keys), 1)
                self.assertFalse(namespace_requested.is_set(), "Upload crossed the profile reference fence")
            finally:
                self._finish((profile, upload), release_profile)
        self.assertEqual(errors, [])
        self._assert_profile_changed(results)
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].status_code, 409, responses[0].content)
        self.assertFalse(StudentReportedScore.objects.filter(student_id=self.student.pk).exists())
        self.assertFalse(InventoryFile.objects.filter(tenant=self.tenant).exists())
        self.assertEqual(self.deleted_keys, self.put_keys)
        self.assertEqual(self.storage_objects, set())

    def test_partial_user_gate_and_sorted_account_notice_complete_without_deadlock(self):
        user_held, release_upload = threading.Event(), threading.Event()
        errors, responses, notice_done, pids, first_user_ids = [], [], [], {}, []

        def observe_first_user(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if (
                User._meta.db_table in str(sql) and "FOR KEY SHARE" in str(sql).upper()
                and not user_held.is_set()
            ):
                first_user_ids.append(int(params[0]))
                user_held.set()
                if not release_upload.wait(timeout=10):
                    raise TimeoutError("first User reference release timed out")
            return result

        def notice():
            with transaction.atomic():
                student = Student.objects.select_related("parent__user").get(pk=self.student.pk)
                lock_account_notice_users(student)
                Student.objects.select_for_update().get(pk=student.pk)
            notice_done.append(True)

        upload = self._thread("upload", lambda: self._upload(responses), errors, pids, observe_first_user)
        sender = self._thread("notice", notice, errors, pids)
        try:
            upload.start()
            self.assertTrue(user_held.wait(timeout=5))
            sender.start()
            self._wait_for_lock(pids, "notice")
        finally:
            self._finish((upload, sender), release_upload)
        self.assertEqual(errors, [])
        self.assertEqual(first_user_ids, [self.parent_a.user_id])
        self.assertEqual(notice_done, [True])
        self._assert_uploaded(responses)

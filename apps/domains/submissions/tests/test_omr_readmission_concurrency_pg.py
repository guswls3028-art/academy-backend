"""Real PostgreSQL row locks and the partial unique claim arbitrate readmission."""
import threading
import time
import unittest

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from apps.domains.submissions.models import OmrUploadBatchItem, Submission
from apps.domains.submissions.tests.test_omr_readmission import OmrReadmissionFixture


class OmrReadmissionConcurrencyTests(OmrReadmissionFixture, TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL required for row-lock and unique-conflict evidence")
        super().setUpClass()

    def _wait_for_blocked_backend(self, backend_pid):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid = %s AND NOT granted)", [backend_pid])
                if cursor.fetchone()[0]:
                    return
            time.sleep(0.02)
        self.fail("Competing admission never waited on the held database row lock")

    def _start_upload(self, batch_id, *, name, connected, backend_ids, outcomes, errors):
        def run():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_ids[name] = cursor.fetchone()[0]
                connected.set()
                item, response = self.upload(batch_id)
                outcomes.append((item, response.data))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()
        worker = threading.Thread(target=run, name=name)
        worker.start()
        return worker

    def test_two_new_batches_after_discard_have_one_winner_and_one_duplicate(self):
        archived, _ = self.upload()
        self.discard(archived.submission)
        old_hash = archived.content_sha256
        original = Submission.objects.filter(pk=archived.submission_id).values().get()
        batches = [self.initialize(), self.initialize()]
        storage_started, release_storage = threading.Event(), threading.Event()
        connected = [threading.Event(), threading.Event()]
        backend_ids, outcomes, errors = {}, [], []

        def hold_first_upload(*args, **kwargs):
            if threading.current_thread().name == "readmission-first":
                storage_started.set()
                if not release_storage.wait(15):
                    raise AssertionError("Readmission test did not release its first upload")

        self.storage.side_effect = hold_first_upload
        workers = []
        try:
            workers.append(self._start_upload(batches[0], name="readmission-first", connected=connected[0],
                                             backend_ids=backend_ids, outcomes=outcomes, errors=errors))
            self.assertTrue(storage_started.wait(10), errors)
            workers.append(self._start_upload(batches[1], name="readmission-second", connected=connected[1],
                                             backend_ids=backend_ids, outcomes=outcomes, errors=errors))
            self.assertTrue(connected[1].wait(10), errors)
            self._wait_for_blocked_backend(backend_ids["readmission-second"])
        finally:
            release_storage.set()
            for worker in workers:
                worker.join(15)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertCountEqual([data["created_count"] for _, data in outcomes], [1, 0])
        self.assertEqual(sum(data["counts"]["duplicate"] for _, data in outcomes), 1)
        active = OmrUploadBatchItem.objects.get(
            tenant=self.tenant, exam_id=self.exam.id, content_sha256=old_hash, admission_status="received",
        )
        duplicate = next(item for item, data in outcomes if data["counts"]["duplicate"])
        self.assertEqual(duplicate.duplicate_of_submission_id, active.submission_id)
        archived.refresh_from_db()
        self.assertEqual(archived.content_sha256, old_hash)
        self.assertEqual(archived.failure_code, "submission_archived")
        self.assertEqual(Submission.objects.filter(pk=archived.submission_id).values().get(), original)
        self.assertEqual(Submission.objects.filter(tenant=self.tenant).count(), 2)
        self.assertEqual(self.dispatch.call_count, 2)
        # The blocked statement's snapshot cannot see the winner inserted later.
        # Its partial-unique conflict must compensate only its new object, then
        # resolve the committed active winner in the IntegrityError fallback.
        self.assertEqual(self.cleanup.call_count, 1)
        self.assertNotEqual(self.cleanup.call_args.kwargs["key"], original["file_key"])

    def _race_with_discard(self, *, commit):
        original, _ = self.upload()
        old_hash = original.content_sha256
        batch_id = self.initialize()
        held, release, connected = threading.Event(), threading.Event(), threading.Event()
        backend_ids, outcomes, errors = {}, [], []

        def discard():
            close_old_connections()
            try:
                with transaction.atomic():
                    self.discard(Submission.objects.get(pk=original.submission_id))
                    held.set()
                    if not release.wait(15):
                        raise AssertionError("Discard transaction was not released")
                    if not commit:
                        transaction.set_rollback(True)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        discard_worker = threading.Thread(target=discard)
        workers = [discard_worker]
        discard_worker.start()
        try:
            self.assertTrue(held.wait(10), errors)
            workers.append(self._start_upload(batch_id, name="readmission-after-discard", connected=connected,
                                             backend_ids=backend_ids, outcomes=outcomes, errors=errors))
            self.assertTrue(connected.wait(10), errors)
            self._wait_for_blocked_backend(backend_ids["readmission-after-discard"])
        finally:
            release.set()
            for worker in workers:
                worker.join(15)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 1)
        admitted, data = outcomes[0]
        original.refresh_from_db()
        self.assertEqual(original.content_sha256, old_hash)
        self.assertEqual(data["created_count"], 1 if commit else 0)
        if commit:
            self.assertEqual(original.failure_code, "submission_archived")
            self.assertNotEqual(admitted.submission_id, original.submission_id)
        else:
            self.assertEqual(original.admission_status, "received")
            self.assertEqual(admitted.duplicate_of_submission_id, original.submission_id)
            self.assertEqual(original.submission.status, Submission.Status.SUBMITTED)

    def test_committed_discard_is_seen_after_waiting_for_submission_lock(self):
        self._race_with_discard(commit=True)

    def test_rolled_back_discard_keeps_the_original_duplicate_owner(self):
        self._race_with_discard(commit=False)

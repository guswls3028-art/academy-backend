"""Observe PostgreSQL lock contention, including rollback then successful retry."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from django.db import close_old_connections, connection

from apps.domains.results.models import ExamAttempt, Result, ResultFact
from apps.domains.results.tests.test_omr_reassignment import correct_student, scored_paper  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("rollback_first", [False, True])
def test_concurrent_reassignment_waits_for_commit_or_rollback(scored_paper, monkeypatch, rollback_first):
    if connection.vendor != "postgresql":
        pytest.skip("Actual PostgreSQL row locks required")
    from apps.domains.results.services import sync_result_from_submission

    tenant, exam, first, second, submission = scored_paper
    first_written = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    backend_pids = {}
    real_correct = sync_result_from_submission.correct_scored_omr_attribution

    def controlled_correct(**kwargs):
        former = real_correct(**kwargs)
        if threading.current_thread().name.endswith("_0") and former is not None:
            first_written.set()
            assert release_first.wait(15), "test must release the held transaction"
            if rollback_first:
                raise RuntimeError("owned rollback regression")
        return former

    monkeypatch.setattr(sync_result_from_submission, "correct_scored_omr_attribution", controlled_correct)

    def request_correction(label):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '12s'")
                cursor.execute("SELECT pg_backend_pid()")
                backend_pids[label] = cursor.fetchone()[0]
            if label == "second":
                second_started.set()
            return correct_student(tenant, submission, second).status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="omr_correction") as executor:
        first_call = executor.submit(request_correction, "first")
        try:
            assert first_written.wait(10)
            second_call = executor.submit(request_correction, "second")
            assert second_started.wait(5)
            locked = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [backend_pids["second"]])
                    row = cursor.fetchone()
                if row and row[0] == "Lock":
                    locked = True
                    break
                time.sleep(.025)
            assert locked, "second HTTP action must actually wait on PostgreSQL"
            assert not second_call.done()
        finally:
            release_first.set()
        assert first_call.result(timeout=15) == (500 if rollback_first else 200)
        assert second_call.result(timeout=15) == 200

    result = Result.objects.get(target_type="exam", target_id=exam.id)
    assert result.enrollment_id == result.attempt.enrollment_id == second.id
    assert result.total_score == 100
    assert not Result.objects.filter(enrollment=first).exists()
    assert ExamAttempt.objects.filter(submission_id=submission.id).count() == 1
    assert ResultFact.objects.filter(source="omr_reassignment").count() == 1

"""PostgreSQL regression coverage for score-edit session locking."""

from __future__ import annotations

import threading
import time
import unittest
import uuid

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from apps.core.models import Tenant
from apps.domains.results.models import ScoreEditDraft
from apps.support.results.progress_read_dependencies import (
    lock_score_edit_scope_for_session,
)


pytestmark = pytest.mark.django_db(transaction=True)
User = get_user_model()
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")


class ScoreEditLockConcurrencyPGTests(TransactionTestCase):
    """The edit lease serializes writers without deadlocking deferred FKs."""

    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest(
                "PostgreSQL row-level locking is required for this regression test."
            )
        from django.apps import apps as django_apps

        cls.available_apps = [
            app_config.name for app_config in django_apps.get_app_configs()
        ]
        super().setUpClass()

    def test_waiting_editor_does_not_deadlock_first_editor_commit(self):
        suffix = uuid.uuid4().hex[:8]
        tenant = Tenant.objects.create(
            name=f"Score Lock {suffix}",
            code=f"score_lock_{suffix}",
            is_active=True,
        )
        editor = User.objects.create(
            tenant=tenant,
            username=f"score-lock-{suffix}",
            is_active=True,
        )
        lecture = Lecture.objects.create(
            tenant=tenant,
            title="Score Lock Lecture",
            name="Score Lock Lecture",
            subject="MATH",
        )
        session = Session.objects.create(
            lecture=lecture,
            order=1,
            title="Session 1",
        )

        first_locked = threading.Event()
        second_attempting = threading.Event()
        errors: list[str] = []

        def first_editor() -> None:
            close_old_connections()
            try:
                with transaction.atomic():
                    lock_score_edit_scope_for_session(
                        session_id=session.id,
                        tenant=tenant,
                    )
                    ScoreEditDraft.objects.create(
                        session_id=session.id,
                        tenant_id=tenant.id,
                        editor_user_id=editor.id,
                        payload={"client_id": "first", "changes": []},
                    )
                    first_locked.set()
                    if not second_attempting.wait(timeout=5):
                        raise AssertionError("second editor did not attempt the lock")
                    # Give PostgreSQL time to enqueue the competing row lock
                    # before this transaction runs deferred FK checks at commit.
                    time.sleep(0.2)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"first: {exc!r}")
            finally:
                close_old_connections()

        def second_editor() -> None:
            close_old_connections()
            try:
                if not first_locked.wait(timeout=5):
                    raise AssertionError("first editor did not acquire the lock")
                with transaction.atomic():
                    second_attempting.set()
                    lock_score_edit_scope_for_session(
                        session_id=session.id,
                        tenant=tenant,
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"second: {exc!r}")
            finally:
                close_old_connections()

        first = threading.Thread(target=first_editor)
        second = threading.Thread(target=second_editor)
        first.start()
        second.start()
        first.join(timeout=10)
        second.join(timeout=10)

        self.assertFalse(first.is_alive(), "first editor thread did not finish")
        self.assertFalse(second.is_alive(), "second editor thread did not finish")
        self.assertEqual(errors, [])
        self.assertTrue(
            ScoreEditDraft.objects.filter(
                session=session,
                tenant=tenant,
                editor_user=editor,
            ).exists()
        )

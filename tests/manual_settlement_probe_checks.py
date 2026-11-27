"""Opt-in failure-boundary checks for the isolated billing probe."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from apps.billing import qa_image_settlement
from apps.billing.models import Invoice
from apps.billing.qa_settlement import SETTLEMENT_SETTINGS, SettlementFixtureMixin
from apps.core.models import Tenant


PROBE = Path(__file__).resolve().parents[1] / "scripts" / "verify_isolated_billing.py"


class PreflightTests(TestCase):
    def run_child(self, args, cwd, extra_env=None, record=None):
        env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "LANG"}}
        return subprocess.run([sys.executable, "-I", "-B", *args], input=json.dumps(record or {}) + "\n", text=True,
                              cwd=cwd, env={**env, **(extra_env or {})}, capture_output=True, timeout=30)

    def test_inherited_application_environment_is_rejected_before_fixture(self):
        with TemporaryDirectory() as root:
            child = self.run_child([str(PROBE)], root, {"ACADEMY_RUNTIME_ENV": "development"})
            self.assertNotEqual(child.returncode, 0)
            self.assertIn("Refusing an inherited application environment", child.stderr)
            self.assertNotIn("BILLING_PROBE_FIXTURE_READY", child.stdout)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_process_guards_refuse_disk_db_writes_and_tcp_listener(self):
        code = f"""import runpy,socket,sqlite3
runpy.run_path({str(PROBE)!r})['protect_child']()
for action in (lambda: sqlite3.connect('unowned.sqlite3'), lambda: open('unowned.txt','w'),
               lambda: socket.socket().bind(('127.0.0.1',0))):
    try:
        action()
    except PermissionError:
        continue
    raise RuntimeError('an unexpected operation was accepted')
"""
        with TemporaryDirectory() as root:
            child = self.run_child(["-c", code], root)
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_invalid_image_correlation_stops_before_setup(self):
        record = {"nonce": "a"*32, "frontend_version": "b"*40, "pinned_backend_revision": "c"*40,
                  "archive_sha256": "d"*64, "dist_fingerprint": "e"*64, "runtime_assets_sha256": "f"*64,
                  "image": {"digest": "unknown"}}
        with TemporaryDirectory() as root:
            child = self.run_child([str(PROBE)], root, record=record)
            self.assertNotEqual(child.returncode, 0)
            self.assertNotIn("BILLING_PROBE_FIXTURE_READY", child.stdout)
            self.assertEqual(list(Path(root).iterdir()), [])


@override_settings(**SETTLEMENT_SETTINGS)
class FixtureCleanupTests(SettlementFixtureMixin, TransactionTestCase):
    def test_existing_local_identity_is_never_adopted(self):
        existing = Tenant.objects.create(pk=9999, code="9999", name="unrelated synthetic tenant")
        reached = []
        with self.assertRaises(AssertionError):
            self.exercise_settlement(lambda *_: reached.append(True))
        self.assertFalse(reached)
        existing.refresh_from_db()
        self.assertEqual(existing.name, "unrelated synthetic tenant")
        self.assertFalse(get_user_model().objects.filter(tenant=existing).exists())

    def test_failure_after_login_and_first_invoice_removes_only_this_fixture(self):
        user_ids, token_ids = [], []

        def fail_after_invoice(platform, schools):
            qa_image_settlement.ImageSettlementProbe.login(self, platform)
            owner = qa_image_settlement.ImageSettlementProbe.login(self, schools[0])
            self.assertEqual(owner.post("/api/v1/billing/bank-transfer/activate/").status_code, 200)
            user_ids.extend(get_user_model().objects.filter(
                tenant__code__in=[platform["code"], *(s["code"] for s in schools)]).values_list("pk", flat=True))
            token_ids.extend(OutstandingToken.objects.filter(user_id__in=user_ids).values_list("pk", flat=True))
            self.assertTrue(user_ids and token_ids)
            self.assertEqual(Invoice.objects.count(), 1)
            raise RuntimeError("simulated client failure")

        with self.assertRaisesRegex(RuntimeError, "simulated client failure"):
            self.exercise_settlement(fail_after_invoice)
        self.assertFalse(Tenant.objects.filter(pk=9999).exists())
        self.assertFalse(get_user_model().objects.filter(pk__in=user_ids).exists())
        self.assertFalse(OutstandingToken.objects.filter(pk__in=token_ids).exists())
        self.assertFalse(Invoice.objects.exists())

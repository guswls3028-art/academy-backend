"""Opt-in browser acceptance on the candidate API with a disposable Django test DB."""

import json
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.test import LiveServerTestCase, override_settings

from apps.billing.qa_settlement import SETTLEMENT_SETTINGS, SettlementFixtureMixin


def load_tests(loader, tests, pattern):
    # Django discovers explicit test modules before creating its test databases.
    if (settings.SETTINGS_MODULE != "apps.api.config.settings.test" or
            connection.vendor != "sqlite" or not connection.is_in_memory_db()):
        raise RuntimeError("Select the in-memory Academy test settings for billing browser QA.")
    return tests


@override_settings(**SETTLEMENT_SETTINGS)
class BillingBrowserJourney(SettlementFixtureMixin, LiveServerTestCase):
    host = "127.0.0.1"

    def test_browser_settlement(self):
        self.exercise_settlement(self.run_browser)
        print("BILLING_BROWSER_PASS schools=2 payments=2 external_issues=0 remaining_tenants_users_and_billing=0")

    def run_browser(self, platform, schools):
        frontend = Path(os.environ["BILLING_QA_FRONTEND"]).resolve(strict=True)
        entry = frontend / "scripts" / "run-manual-settlement-browser.mjs"
        self.assertTrue(entry.is_file())
        boundary = {"api": self.live_server_url, "operator": platform, "schools": schools}
        allowed = {"path", "systemroot", "windir", "temp", "tmp", "tmpdir", "home", "userprofile",
                   "localappdata", "appdata", "programfiles", "programfiles(x86)", "comspec",
                   "billing_qa_dist", "billing_qa_revision", "billing_qa_channel", "playwright_browsers_path"}
        child_env = {k: v for k, v in os.environ.items() if k.lower() in allowed}
        subprocess.run(["node", str(entry)], cwd=frontend, input=json.dumps(boundary), text=True,
                       env=child_env, check=True, timeout=240)

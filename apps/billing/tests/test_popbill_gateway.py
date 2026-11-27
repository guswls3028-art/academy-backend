"""Exercise actual SDK serialization and document lookup without making network calls."""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from popbill import CashbillService, PopbillException, TaxinvoiceService
from apps.billing.popbill_gateway import exchange


class GatewayTests(TestCase):
    def test_tax_and_cash_use_one_provider_key_on_repeat(self):
        for kind, cls, absent, approval in (
            ("CASH", CashbillService, -14000003, "TB0000001"),
            ("BUSINESS_TAX", TaxinvoiceService, -11000005, "2026111910000000ab000001"),
        ):
            with self.subTest(kind=kind):
                key = "test-subscription-1"
                key_field = "mgtKey" if kind == "CASH" else "invoicerMgtKey"
                request = dict(
                    kind=kind,
                    key=key,
                    corp="1234567890",
                    link_id="fixture",
                    secret_key="fixture",
                    mode="test",
                    document={key_field: key},
                )
                info = SimpleNamespace(itemKey="fixture", stateCode=304, confirmNum=approval, ntsconfirmNum=approval)
                answers = [PopbillException(absent, "Fixture"), info, info, info]
                with (
                    patch.object(cls, "_httpget", side_effect=answers),
                    patch.object(
                        cls,
                        "_httppost",
                        return_value=SimpleNamespace(tradeDT=None),
                    ) as issue,
                ):
                    self.assertEqual(exchange(request), {"state": 304, "number": approval})
                    self.assertEqual(exchange({**request, "document": None})["number"], approval)
                    self.assertEqual(issue.call_count, 1)
                    self.assertEqual(json.loads(issue.call_args.args[1])[key_field], key)
                    self.assertEqual(issue.call_args.args[-1], "ISSUE")

    def test_worker_can_start_without_initializing_django(self):
        run = subprocess.run(
            [sys.executable, "-m", "apps.billing.popbill_gateway"],
            input=b"{}",
            capture_output=True,
            timeout=8,
            check=True,
        )
        self.assertEqual(json.loads(run.stdout), {"error": "transport"})
        self.assertEqual(run.stderr, b"")

    def test_lookup_error_never_starts_an_issue(self):
        request = dict(
            kind="CASH",
            key="test",
            corp="1234567890",
            link_id="fixture",
            secret_key="fixture",
            mode="test",
            document={"mgtKey": "test"},
        )
        with (
            patch.object(CashbillService, "_httpget", side_effect=PopbillException(-1, "Fixture")),
            patch.object(CashbillService, "_httppost") as issue,
        ):
            with self.assertRaises(PopbillException):
                exchange(request)
            issue.assert_not_called()

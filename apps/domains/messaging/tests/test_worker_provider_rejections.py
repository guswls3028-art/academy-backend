from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.domains.messaging.solapi_mock import MockSolapiMessageService
from apps.worker.messaging_worker.sqs_main import (
    _classify_definitely_rejected_provider_exception,
    _send_failure_disposition,
    send_one_alimtalk,
)
from apps.worker.messaging_worker.solapi_receipts import receipt_reference


class MockProviderReceiptTests(SimpleTestCase):
    def test_account_notice_logs_only_generated_receipt_and_count(self):
        service = MockSolapiMessageService()
        notice = {"to": "synthetic-recipient", "variables": {"password": "fixture-password"}}
        for messages, count in ((notice, 1), ([notice, notice], 2)):
            with self.subTest(count=count), self.assertLogs("apps.domains.messaging.solapi_mock", "INFO") as captured:
                response = service.send(messages, request_config={"private": "fixture-configuration"})
            group = response.group_info
            self.assertRegex(group.group_id, r"^mock-[0-9a-f]{12}$")
            self.assertEqual(group.count.registered_success, count)
            self.assertEqual([record.getMessage() for record in captured.records],
                             [f"[MockSolapi] 모의 접수 group_id={group.group_id} count={count}"])


class WorkerProviderRejectionTests(SimpleTestCase):
    def test_solapi_single_recipient_admission_and_lookup_reference(self):
        from solapi import SolapiMessageService

        destination = "synthetic-recipient"
        money = dict(requested=0, replacement=0, refund=0, sum=0)
        cases = (
            # successes, failures, group ID, failed message ID, expected disposition and lookup
            (0, 1, "group", "failed-message", "terminal", ("failed-message", "message")),
            (0, 1, "", "", "terminal", ("", "none")),
            (1, 1, "mixed-group", "failed-message", "ambiguous", ("mixed-group", "group")),
            (1, 1, "", "failed-message", "ambiguous", ("", "none")),
            (1, 0, "group", "", "accepted", ("group", "group")),
            (1, 0, "", "", "accepted", ("", "none")),
            (0, 0, "unknown-group", "", "ambiguous", ("unknown-group", "group")),
        )
        client = SolapiMessageService("synthetic", "synthetic")
        for accepted, failed, group_id, message_id, state, lookup in cases:
            with self.subTest(accepted=accepted, failed=failed, lookup=lookup):
                count = dict(total=accepted + failed, registered_success=accepted, registered_failed=failed,
                             sent_total=0, sent_success=0, sent_pending=0, sent_replacement=0, refund=0)
                group = dict(count=count, count_for_charge={}, balance=money, point=money, app={},
                             log=[], status="COMPLETE", allow_duplicates=False, is_refunded=False,
                             account_id="fixture", master_account_id=None, api_version="4", group_id=group_id,
                             price={}, date_created=None, date_updated=None)
                row = {"to": destination, "from": None, "type": "ATA", "country": "82",
                       "statusMessage": "fixture", "messageId": message_id, "statusCode": "3100", "accountId": "fixture"}
                reply = {"group_info": group, "failed_message_list": [row] if failed else []}
                with (patch("solapi.services.message_service.default_fetcher", return_value=reply) as transport,
                      patch("apps.worker.messaging_worker.sqs_main._get_solapi_client", return_value=client),
                      patch("apps.domains.messaging.policy.check_recipient_allowed", return_value=True)):
                    result = send_one_alimtalk(None, destination, "fixture", "channel", "template")
                transport.assert_called_once()
                self.assertTrue(result["provider_called"])
                actual = "accepted" if result["status"] == "ok" else _send_failure_disposition(
                    result["reason"], provider_send_started=True,
                    definitely_not_accepted=result.get("definitely_not_accepted", False),
                    provider_retryable=result.get("provider_retryable"))
                self.assertEqual((actual, receipt_reference(result)), (state, lookup))

    @patch("apps.domains.messaging.provider_delivery.get_solapi_client")
    def test_message_and_partial_group_queries_do_not_use_another_recipient(self, client):
        from apps.domains.messaging.provider_delivery import get_provider_delivery_status
        from apps.domains.messaging.security import build_recipient_fingerprint

        current = SimpleNamespace(to="01000000001", status_code="3058")
        other = SimpleNamespace(to="01000000002", status_code="4000")
        rows = SimpleNamespace(message_list={"target": current, "other": other}, next_key=None)
        client.return_value.get_messages.return_value = rows
        log = SimpleNamespace(provider_message_id="target", provider_reference_kind="message")
        self.assertEqual(get_provider_delivery_status(log)["status_code"], "3058")
        query = client.return_value.get_messages.call_args.args[0]
        self.assertEqual((query.message_id, query.group_id), ("target", None))
        log.status, log.failure_reason = "failed", "alimtalk_failed_or_rejected"
        current.status_code = "4000"
        self.assertEqual(get_provider_delivery_status(log)["status"], "unavailable")
        log.status, current.status_code = "ambiguous", "3058"
        del rows.message_list["target"]
        self.assertEqual(get_provider_delivery_status(log)["status"], "unavailable")

        log.provider_reference_kind = "group"
        log.failure_reason = "provider_partial_acceptance"
        log.recipient_fingerprint = build_recipient_fingerprint(current.to)
        self.assertEqual(get_provider_delivery_status(log)["status"], "unavailable")
        rows.message_list["target"] = current
        self.assertEqual(get_provider_delivery_status(log)["status"], "failed")
        rows.next_key = "next"
        self.assertEqual(get_provider_delivery_status(log)["status"], "unavailable")
        log.provider_message_id = ""
        client.return_value.get_messages.reset_mock()
        self.assertEqual(get_provider_delivery_status(log)["status"], "unavailable")
        client.return_value.get_messages.assert_not_called()

    def test_quota_exceeded_is_terminal_not_ambiguous(self):
        exc = RuntimeError("QuotaExceeded")
        reason = _classify_definitely_rejected_provider_exception(exc)

        self.assertEqual(reason, "provider_quota_exceeded")
        self.assertEqual(
            _send_failure_disposition(
                reason,
                provider_send_started=True,
                definitely_not_accepted=True,
                provider_retryable=False,
            ),
            "terminal",
        )

    def test_not_enough_balance_is_terminal_not_ambiguous(self):
        class SolapiError(Exception):
            pass

        reason = _classify_definitely_rejected_provider_exception(
            SolapiError("NotEnoughBalance")
        )

        self.assertEqual(reason, "provider_not_enough_balance")

    def test_unknown_exception_after_provider_boundary_remains_ambiguous(self):
        reason = _classify_definitely_rejected_provider_exception(
            TimeoutError("connection lost")
        )

        self.assertEqual(reason, "")
        self.assertEqual(
            _send_failure_disposition(
                "connection lost",
                provider_send_started=True,
            ),
            "ambiguous",
        )

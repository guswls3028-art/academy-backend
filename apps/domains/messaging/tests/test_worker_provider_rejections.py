from django.test import SimpleTestCase

from apps.worker.messaging_worker.sqs_main import (
    _classify_definitely_rejected_provider_exception,
    _send_failure_disposition,
)


class WorkerProviderRejectionTests(SimpleTestCase):
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

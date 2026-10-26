"""Queue latency and authoritative execution contracts, without external services."""
from unittest.mock import MagicMock, Mock, patch

import pytest

from academy.framework.workers import ai_sqs_worker
from academy.application.use_cases.ai.process_ai_job_from_sqs import PreparedJob, prepare_ai_job
from academy.domain.ai.entities import AIJob, AIJobStatus
from apps.shared.contracts.ai_result import AIResult


def test_claim_executes_the_committed_payload_and_identity_not_a_stale_envelope():
    saved = AIJob(
        job_id="saved", job_type="matchup_analysis", status=AIJobStatus.PENDING,
        payload={"tenant_id": "1", "document_id": 7}, tenant_id="1",
        source_domain="matchup", source_id="7", tier="premium",
    )
    uow = MagicMock()
    uow.ai_jobs.get_for_update.return_value = saved
    uow.ai_jobs.mark_running.return_value = True
    prepared = prepare_ai_job(
        uow, job_id="saved", receipt_handle="receipt", tenant_id="1",
        job_type="ppt_generation", tier="lite", source_domain="tools", source_id="99",
        payload={"tenant_id": "1", "document_id": 99},
    )
    assert prepared is not None
    assert prepared.job_type == saved.job_type
    assert prepared.payload == saved.payload
    assert (prepared.source_domain, prepared.source_id, prepared.tier) == ("matchup", "7", "premium")


def test_inconsistent_stored_tenant_payload_never_reaches_inference():
    saved = AIJob(
        job_id="saved", job_type="ppt_generation", status=AIJobStatus.PENDING,
        payload={"tenant_id": "2"}, tenant_id="1",
    )
    uow = MagicMock()
    uow.ai_jobs.get_for_update.return_value = saved
    assert prepare_ai_job(
        uow, job_id="saved", receipt_handle="receipt", tenant_id="1",
        job_type=saved.job_type, tier="basic", payload={"tenant_id": "1"},
    ) is None
    uow.ai_jobs.mark_running.assert_not_called()


@pytest.mark.parametrize("preferred,other,draw", [("basic", "lite", 1), ("lite", "basic", 4)])
def test_busy_other_queue_is_received_without_waiting_on_empty_preferred(preferred, other, draw):
    queue = Mock()
    queue.queue_name.side_effect = lambda tier: tier
    message = {"job_id": "ready"}
    queue.receive.side_effect = lambda *, tier, wait_time_seconds: message if tier == other else None
    with patch.dict("os.environ", {"AI_WORKER_PREMIUM_ONLY": "0"}), patch.object(
        ai_sqs_worker.random, "randint", return_value=draw
    ):
        assert ai_sqs_worker._weighted_poll(queue) == (message, other)
    assert [(c.kwargs["tier"], c.kwargs["wait_time_seconds"]) for c in queue.receive.call_args_list] == [
        (preferred, 0), (other, 0)
    ]


def test_empty_queues_still_long_poll_instead_of_spinning():
    queue = Mock()
    queue.queue_name.side_effect = lambda tier: tier
    queue.receive.return_value = None
    with patch.dict("os.environ", {"AI_WORKER_PREMIUM_ONLY": "0"}), patch.object(
        ai_sqs_worker.random, "randint", return_value=1
    ):
        assert ai_sqs_worker._weighted_poll(queue) == (None, "basic")
    assert [c.kwargs["wait_time_seconds"] for c in queue.receive.call_args_list] == [0, 0, 20]


def test_tiers_on_one_physical_queue_do_not_add_redundant_probes():
    from academy.adapters.queue.sqs.ai_queue import SQSAIQueueAdapter

    queue = SQSAIQueueAdapter()
    queue._impl = Mock()
    queue._impl._get_queue_name.return_value = "academy-v1-ai-queue"
    queue._impl.receive_message.return_value = None
    with patch.dict("os.environ", {"AI_WORKER_PREMIUM_ONLY": "0"}), patch.object(
        ai_sqs_worker.random, "randint", return_value=1
    ):
        assert ai_sqs_worker._weighted_poll(queue) == (None, "basic")
    queue._impl.receive_message.assert_called_once_with(tier="basic", wait_time_seconds=20)


@pytest.mark.parametrize("tools_only,premium,tier", [(True, "0", "tools"), (False, "1", "premium")])
def test_dedicated_worker_keeps_its_single_queue(tools_only, premium, tier):
    queue = Mock()
    queue.receive.return_value = None
    with patch.dict("os.environ", {"AI_WORKER_PREMIUM_ONLY": premium}):
        assert ai_sqs_worker._weighted_poll(queue, tools_only=tools_only) == (None, tier)
    queue.receive.assert_called_once_with(tier=tier, wait_time_seconds=20)


@pytest.mark.parametrize("success", [True, False])
def test_tools_worker_acks_delivery_queue_and_uses_stored_execution_tier(monkeypatch, success):
    prepared = PreparedJob("job", "ppt_generation", "premium", {}, "receipt", tenant_id="1")
    message = {"job_id": "job", "job_type": "old-envelope-type", "tier": "basic", "receipt_handle": "receipt"}
    queue = Mock()

    def delete(*args):
        ai_sqs_worker._shutdown = True
        return True

    queue.delete.side_effect = delete
    monkeypatch.setattr(ai_sqs_worker, "_shutdown", False)
    monkeypatch.setattr(ai_sqs_worker, "_current_receipt_handle", None)
    monkeypatch.setattr(ai_sqs_worker.signal, "signal", Mock())
    monkeypatch.setattr(ai_sqs_worker, "close_old_connections", Mock())
    monkeypatch.setattr(ai_sqs_worker, "_release_db_connections", Mock())
    monkeypatch.setattr("apps.shared.utils.heartbeat.beat", Mock())
    monkeypatch.setattr(ai_sqs_worker, "_weighted_poll", lambda *a, **k: (message, "tools"))
    monkeypatch.setattr(ai_sqs_worker, "prepare_ai_job", lambda *a, **k: prepared)
    extender = Mock()
    monkeypatch.setattr(ai_sqs_worker, "SQSVisibilityExtender", lambda *a: extender)
    inference = Mock(return_value=AIResult.done("job", {}) if success else AIResult.failed("job", "failed"))
    monkeypatch.setattr(ai_sqs_worker, "_run_inference", inference)
    monkeypatch.setattr(ai_sqs_worker, "complete_ai_job", Mock(return_value=True))
    failed = Mock(return_value=True)
    monkeypatch.setattr(ai_sqs_worker, "fail_ai_job", failed)
    monkeypatch.setattr(ai_sqs_worker, "_dispatch_terminal_callback_from_message", Mock(return_value=True))
    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, worker_kind="tools", supported_job_types={"ppt_generation"}) == 0
    inference.assert_called_once()
    queue.delete.assert_called_once_with("receipt", "tools")
    assert extender.start.call_args.kwargs["tier"] == "tools"
    if success:
        failed.assert_not_called()
    else:
        assert failed.call_args.args[3] == "premium"

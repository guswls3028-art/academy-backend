from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import MagicMock

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "v1"
    / "reconcile_common_alimtalk_sender.py"
)
SPEC = importlib.util.spec_from_file_location("reconcile_common_alimtalk_sender", SCRIPT_PATH)
assert SPEC and SPEC.loader
reconcile_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reconcile_module
SPEC.loader.exec_module(reconcile_module)


API_SENDER = "0212345678"
ACTIVE_SENDER = "0211112222"
API_KEY = "provider-api-key-secret"
API_SECRET = "provider-api-secret-secret"


def _environment(settings_module: str, sender: str, size: int) -> dict[str, str]:
    value = {
        "DJANGO_SETTINGS_MODULE": settings_module,
        "SOLAPI_API_KEY": API_KEY,
        "SOLAPI_API_SECRET": API_SECRET,
        "SOLAPI_SENDER": sender,
    }
    value.update({f"SAFE_KEY_{index}": str(index) for index in range(size - len(value))})
    return value


class FakeRuntime:
    def __init__(self) -> None:
        self.api = _environment("apps.api.config.settings.prod", API_SENDER, 73)
        self.worker = _environment("apps.api.config.settings.worker", API_SENDER, 59)
        self.api_raw = reconcile_module._encode_environment(self.api, wrapping="plain")
        self.worker_raw = reconcile_module._encode_environment(
            self.worker,
            wrapping="base64",
        )
        self.api_version = 10
        self.worker_version = 20
        self.api_key_id = "alias/aws/ssm"
        self.worker_key_id = "alias/aws/ssm"
        self.active = [ACTIVE_SENDER]
        self.queues = {"visible": 0, "inflight": 0, "delayed": 0}
        self.puts: list[str] = []
        self.refreshes: list[str] = []
        self.lock_acquired = False
        self.lock_released = False
        self.lock_owned = False
        self.lock_assertions = 0
        self.lock_renews = 0
        self.fail_put_number: int | None = None
        self.commit_then_fail_put_number: int | None = None
        self.interleave_put_number: int | None = None
        self.concurrent_api_write = False
        self.drift_api_kms_after_put = False
        self.interleave_after_health = False
        self.fail_refresh = False
        self.health_checked = False

    def read_snapshot(self):
        return reconcile_module.EnvironmentSnapshot(
            api_raw=self.api_raw,
            worker_raw=self.worker_raw,
            api_version=self.api_version,
            worker_version=self.worker_version,
            api_key_id=self.api_key_id,
            worker_key_id=self.worker_key_id,
            api=dict(self.api),
            worker=dict(self.worker),
        )

    def active_senders(self, api_key: str, api_secret: str):
        assert api_key == API_KEY
        assert api_secret == API_SECRET
        return list(self.active)

    def queue_counts(self):
        return dict(self.queues)

    def acquire_lock(self, _owner: str):
        self.lock_acquired = True
        self.lock_owned = True

    def assert_lock_owned(self, _owner: str):
        self.lock_assertions += 1
        if not self.lock_owned:
            raise reconcile_module.ReconcileError(
                "shared_production_lock_ownership_lost"
            )

    def renew_lock(self, _owner: str):
        self.lock_renews += 1
        if not self.lock_owned:
            raise reconcile_module.ReconcileError("shared_production_lock_renew_failed")

    def release_lock(self, _owner: str):
        if not self.lock_owned:
            raise reconcile_module.ReconcileError(
                "shared_production_lock_release_failed"
            )
        self.lock_released = True
        self.lock_owned = False

    def put_environment(self, name: str, value: str, *, key_id: str):
        expected_key_id = (
            self.api_key_id
            if name == reconcile_module.API_ENV_PARAMETER
            else self.worker_key_id
        )
        assert key_id == expected_key_id
        self.puts.append(name)
        if self.fail_put_number == len(self.puts):
            raise reconcile_module.ReconcileError("runtime_environment_write_failed")
        if name == reconcile_module.API_ENV_PARAMETER:
            if self.interleave_put_number == len(self.puts):
                self.api["INTERLEAVED_SAFE_KEY"] = "external"
                self.api_raw = reconcile_module._encode_environment(
                    self.api,
                    wrapping="plain",
                )
                self.api_version += 1
            if self.concurrent_api_write:
                self.api_version += 1
            self.api_raw = value
            self.api = json.loads(value)
            self.api_version += 1
            if self.drift_api_kms_after_put:
                self.api_key_id = "alias/unexpected"
            if self.commit_then_fail_put_number == len(self.puts):
                raise reconcile_module.ReconcileError(
                    "runtime_environment_write_failed"
                )
            return self.api_version
        assert name == reconcile_module.WORKER_ENV_PARAMETER
        if self.interleave_put_number == len(self.puts):
            self.worker["INTERLEAVED_SAFE_KEY"] = "external"
            self.worker_raw = reconcile_module._encode_environment(
                self.worker,
                wrapping="base64",
            )
            self.worker_version += 1
        self.worker_raw = value
        self.worker = json.loads(base64.b64decode(value).decode("utf-8"))
        self.worker_version += 1
        if self.commit_then_fail_put_number == len(self.puts):
            raise reconcile_module.ReconcileError("runtime_environment_write_failed")
        return self.worker_version

    def refresh_service(self, asg_name: str):
        self.refreshes.append(asg_name)
        if self.fail_refresh:
            raise reconcile_module.ReconcileError("runtime_refresh_failed")

    def runtime_sender_digests(self, *, asg_name, container_name, digest_key):
        assert asg_name in {
            reconcile_module.API_ASG,
            reconcile_module.MESSAGING_ASG,
        }
        assert container_name in {
            reconcile_module.API_CONTAINER,
            reconcile_module.MESSAGING_CONTAINER,
        }
        return [
            hmac.new(
                digest_key,
                ACTIVE_SENDER.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        ]

    def assert_public_health(self):
        self.health_checked = True
        if self.interleave_after_health:
            self.api["INTERLEAVED_SAFE_KEY"] = "external"
            self.api_raw = reconcile_module._encode_environment(
                self.api,
                wrapping="plain",
            )
            self.api_version += 1


def _run(runtime: FakeRuntime, *, apply: bool):
    output: list[str] = []
    source_checks: list[bool] = []
    reconcile_module.reconcile(
        runtime,
        apply=apply,
        source_check=lambda: source_checks.append(True),
        output=output.append,
    )
    return output, source_checks


def test_dry_run_is_strictly_read_only():
    runtime = FakeRuntime()

    output, source_checks = _run(runtime, apply=False)

    assert runtime.puts == []
    assert runtime.refreshes == []
    assert runtime.lock_acquired is False
    assert runtime.lock_released is False
    assert source_checks == []
    assert output[-1] == "DRY_RUN_COMPLETE writes=0 refreshes=0"


def test_api_worker_provider_drift_fails_closed():
    runtime = FakeRuntime()
    runtime.worker["SOLAPI_API_KEY"] = "different"

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="api_worker_provider_config_drift",
    ):
        _run(runtime, apply=False)

    assert runtime.puts == []
    assert runtime.refreshes == []


@pytest.mark.parametrize("active", [[], [ACTIVE_SENDER, "0213334444"]])
def test_provider_sender_must_be_exact_singleton(active):
    runtime = FakeRuntime()
    runtime.active = active

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="provider_active_sender_must_be_singleton",
    ):
        _run(runtime, apply=False)

    assert runtime.puts == []


def test_nonempty_messaging_queue_blocks_before_lock():
    runtime = FakeRuntime()
    runtime.queues["inflight"] = 1

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="messaging_queue_not_empty",
    ):
        _run(runtime, apply=True)

    assert runtime.lock_acquired is False
    assert runtime.puts == []


def test_apply_updates_only_two_sender_keys_and_refreshes_only_two_asgs():
    runtime = FakeRuntime()
    before_api = dict(runtime.api)
    before_worker = dict(runtime.worker)

    output, source_checks = _run(runtime, apply=True)

    expected_api = dict(before_api, SOLAPI_SENDER=ACTIVE_SENDER)
    expected_worker = dict(before_worker, SOLAPI_SENDER=ACTIVE_SENDER)
    assert runtime.api == expected_api
    assert runtime.worker == expected_worker
    assert runtime.puts == [
        reconcile_module.API_ENV_PARAMETER,
        reconcile_module.WORKER_ENV_PARAMETER,
    ]
    assert runtime.refreshes == [
        reconcile_module.MESSAGING_ASG,
        reconcile_module.API_ASG,
    ]
    assert runtime.lock_acquired is True
    assert runtime.lock_released is True
    assert runtime.lock_assertions == 1
    assert runtime.lock_renews >= 6
    assert runtime.health_checked is True
    assert source_checks == [True, True]
    assert output[-1].startswith("COMMON_ALIMTALK_SENDER_RECONCILED ")


def test_second_ssm_write_failure_rolls_back_before_any_refresh():
    runtime = FakeRuntime()
    original_api_raw = runtime.api_raw
    original_worker_raw = runtime.worker_raw
    runtime.fail_put_number = 2

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="runtime_environment_write_failed",
    ):
        _run(runtime, apply=True)

    assert runtime.api_raw == original_api_raw
    assert runtime.worker_raw == original_worker_raw
    assert runtime.refreshes == []
    assert runtime.lock_released is True
    assert runtime.puts == [
        reconcile_module.API_ENV_PARAMETER,
        reconcile_module.WORKER_ENV_PARAMETER,
        reconcile_module.API_ENV_PARAMETER,
    ]


def test_refresh_failure_keeps_target_ssm_and_retains_lock():
    runtime = FakeRuntime()
    runtime.fail_refresh = True

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="forward_convergence_required_lock_retained",
    ):
        _run(runtime, apply=True)

    assert runtime.api["SOLAPI_SENDER"] == ACTIVE_SENDER
    assert runtime.worker["SOLAPI_SENDER"] == ACTIVE_SENDER
    assert runtime.lock_acquired is True
    assert runtime.lock_released is False


@pytest.mark.parametrize("put_number", [1, 2])
def test_commit_then_timeout_rolls_back_every_possibly_written_parameter(put_number):
    runtime = FakeRuntime()
    original_api_raw = runtime.api_raw
    original_worker_raw = runtime.worker_raw
    runtime.commit_then_fail_put_number = put_number

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="runtime_environment_write_failed",
    ):
        _run(runtime, apply=True)

    assert runtime.api_raw == original_api_raw
    assert runtime.worker_raw == original_worker_raw
    assert runtime.refreshes == []
    assert runtime.lock_released is True


def test_concurrent_ssm_writer_is_detected_and_lock_is_retained():
    runtime = FakeRuntime()
    runtime.concurrent_api_write = True

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="concurrent_runtime_writer_detected_lock_retained",
    ):
        _run(runtime, apply=True)

    assert runtime.refreshes == []
    assert runtime.lock_released is False


def test_kms_key_drift_is_detected_and_lock_is_retained():
    runtime = FakeRuntime()
    runtime.drift_api_kms_after_put = True

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="concurrent_runtime_writer_detected_lock_retained",
    ):
        _run(runtime, apply=True)

    assert runtime.refreshes == []
    assert runtime.lock_released is False


def test_rollback_interleaving_writer_is_detected_and_lock_is_retained():
    runtime = FakeRuntime()
    runtime.commit_then_fail_put_number = 2
    runtime.interleave_put_number = 3

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="runtime_environment_rollback_failed_lock_retained",
    ):
        _run(runtime, apply=True)

    assert runtime.refreshes == []
    assert runtime.lock_released is False


def test_post_refresh_ssm_drift_is_detected_and_lock_is_retained():
    runtime = FakeRuntime()
    runtime.interleave_after_health = True

    with pytest.raises(
        reconcile_module.ReconcileError,
        match="forward_convergence_required_lock_retained",
    ):
        _run(runtime, apply=True)

    assert runtime.refreshes == [
        reconcile_module.MESSAGING_ASG,
        reconcile_module.API_ASG,
    ]
    assert runtime.lock_released is False


def test_operator_output_never_contains_sender_or_provider_credentials():
    runtime = FakeRuntime()

    output, _ = _run(runtime, apply=True)
    rendered = "\n".join(output)

    for secret in (API_SENDER, ACTIVE_SENDER, API_KEY, API_SECRET):
        assert secret not in rendered


def test_active_sender_response_accepts_current_provider_shapes_only():
    assert reconcile_module._extract_active_senders([ACTIVE_SENDER]) == [ACTIVE_SENDER]
    assert reconcile_module._extract_active_senders(
        [{"phoneNumber": ACTIVE_SENDER, "status": "ACTIVE"}]
    ) == [ACTIVE_SENDER]
    with pytest.raises(
        reconcile_module.ReconcileError,
        match="provider_active_sender_shape_drift",
    ):
        reconcile_module._extract_active_senders({"list": [ACTIVE_SENDER]})


def test_legacy_worker_diagnostic_no_longer_prints_raw_sender():
    diagnostic = (
        SCRIPT_PATH.parent / "check-workers-sender-queue.ps1"
    ).read_text(encoding="utf-8")

    assert "SOLAPI_SENDER=[" not in diagnostic
    assert "SOLAPI_COMMON_CONFIG_PRESENT=" in diagnostic
    assert "SOLAPI_API_WORKER_EQUAL=" in diagnostic


def test_boto_put_preserves_exact_kms_key_id_and_refuses_cache_drift():
    runtime = object.__new__(reconcile_module.AwsRuntime)
    runtime._parameter_key_ids = {
        reconcile_module.API_ENV_PARAMETER: "alias/academy-runtime"
    }
    runtime.ssm = MagicMock()
    runtime.ssm.put_parameter.return_value = {"Version": 42}

    version = runtime.put_environment(
        reconcile_module.API_ENV_PARAMETER,
        "{}",
        key_id="alias/academy-runtime",
    )

    assert version == 42
    runtime.ssm.put_parameter.assert_called_once_with(
        Name=reconcile_module.API_ENV_PARAMETER,
        Type="SecureString",
        Value="{}",
        Overwrite=True,
        KeyId="alias/academy-runtime",
    )
    with pytest.raises(
        reconcile_module.ReconcileError,
        match="runtime_parameter_kms_key_drift",
    ):
        runtime.put_environment(
            reconcile_module.API_ENV_PARAMETER,
            "{}",
            key_id="alias/unexpected",
        )

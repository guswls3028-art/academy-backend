from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "v1" / "reconcile_common_alimtalk_owner_tenant.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location(
    "reconcile_common_alimtalk_owner_tenant",
    SCRIPT_PATH,
)
assert SPEC and SPEC.loader
owner_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = owner_module
SPEC.loader.exec_module(owner_module)


API_KEY = "provider-api-key-secret"
API_SECRET = "provider-api-secret-secret"
SENDER = "0212345678"


def _environment(settings_module: str, size: int, *, explicit: bool) -> dict[str, str]:
    value = {
        "DJANGO_SETTINGS_MODULE": settings_module,
        "SOLAPI_API_KEY": API_KEY,
        "SOLAPI_API_SECRET": API_SECRET,
        "SOLAPI_SENDER": SENDER,
    }
    if explicit:
        value["OWNER_TENANT_ID"] = owner_module.EXPECTED_OWNER_TENANT_ID
    value.update({f"SAFE_KEY_{index}": str(index) for index in range(size - len(value))})
    return value


class FakeRuntime:
    def __init__(self, *, api_explicit: bool = False, worker_explicit: bool = False) -> None:
        self.api = _environment(
            "apps.api.config.settings.prod",
            73,
            explicit=api_explicit,
        )
        self.worker = _environment(
            "apps.api.config.settings.worker",
            59,
            explicit=worker_explicit,
        )
        self.api_version = 20
        self.worker_version = 30
        self.api_key_id = "alias/academy-api-env"
        self.worker_key_id = "alias/academy-worker-env"
        self.queue_state = {"visible": 0, "inflight": 0, "delayed": 0}
        self.puts: list[str] = []
        self.refreshes: list[str] = []
        self.lock_acquired = False
        self.lock_released = False
        self.lock_assertions = 0
        self.lock_renews = 0
        self.health_checked = False
        self.runtime_readbacks: list[tuple[str, str]] = []
        self.fail_put_for: str | None = None
        self.commit_then_fail_for: str | None = None
        self.fail_rollback_for: str | None = None
        self.kms_drift_after_put_for: str | None = None
        self.returned_version_offset = 0
        self.runtime_mismatch = False
        self.fail_refresh = False
        self.fail_health = False
        self.queue_after_refresh = False
        self.runtime_stale_until_refresh = False
        self.lose_lock_after_first_put = False

    def _api_raw(self) -> str:
        return owner_module.sender_boundary._encode_environment(
            self.api,
            wrapping="plain",
        )

    def _worker_raw(self) -> str:
        return owner_module.sender_boundary._encode_environment(
            self.worker,
            wrapping="base64",
        )

    def read_snapshot(self):
        return owner_module.sender_boundary.EnvironmentSnapshot(
            api_raw=self._api_raw(),
            worker_raw=self._worker_raw(),
            api_version=self.api_version,
            worker_version=self.worker_version,
            api_key_id=self.api_key_id,
            worker_key_id=self.worker_key_id,
            api=dict(self.api),
            worker=dict(self.worker),
        )

    def queue_counts(self) -> dict[str, int]:
        if self.queue_after_refresh and self.refreshes:
            return {"visible": 1, "inflight": 0, "delayed": 0}
        return dict(self.queue_state)

    def acquire_lock(self, owner: str) -> None:
        assert owner
        self.lock_acquired = True

    def renew_lock(self, owner: str) -> None:
        assert self.lock_acquired and owner
        if self.lose_lock_after_first_put and self.puts:
            raise owner_module.ReconcileError("shared_production_lock_renew_failed")
        self.lock_renews += 1

    def assert_lock_owned(self, owner: str) -> None:
        assert self.lock_acquired and owner
        if self.lose_lock_after_first_put and self.puts:
            raise owner_module.ReconcileError("shared_production_lock_ownership_lost")
        self.lock_assertions += 1

    def release_lock(self, owner: str) -> None:
        assert self.lock_acquired and owner
        self.lock_released = True

    def put_environment(self, name: str, value: str, *, key_id: str) -> int:
        self.puts.append(name)
        if self.fail_put_for == name:
            raise owner_module.ReconcileError("runtime_environment_write_failed")
        decoded = (
            json.loads(value)
            if name == owner_module.API_ENV_PARAMETER
            else json.loads(base64.b64decode(value, validate=True).decode("utf-8"))
        )
        if self.fail_rollback_for == name and "OWNER_TENANT_ID" not in decoded:
            raise owner_module.ReconcileError("runtime_environment_write_failed")
        if name == owner_module.API_ENV_PARAMETER:
            assert key_id == self.api_key_id
            self.api = decoded
            self.api_version += 1
            version = self.api_version
        else:
            assert name == owner_module.WORKER_ENV_PARAMETER
            assert key_id == self.worker_key_id
            self.worker = decoded
            self.worker_version += 1
            version = self.worker_version
        if self.kms_drift_after_put_for == name:
            if name == owner_module.API_ENV_PARAMETER:
                self.api_key_id = "alias/unexpected-api-key"
            else:
                self.worker_key_id = "alias/unexpected-worker-key"
        if self.commit_then_fail_for == name:
            self.commit_then_fail_for = None
            raise owner_module.ReconcileError("runtime_environment_write_failed")
        return version + self.returned_version_offset

    def refresh_service(self, asg_name: str) -> None:
        self.refreshes.append(asg_name)
        if self.fail_refresh:
            raise owner_module.ReconcileError("runtime_refresh_failed")

    def runtime_owner_digests(
        self,
        *,
        asg_name: str,
        container_name: str,
        digest_key: bytes,
    ) -> list[str]:
        self.runtime_readbacks.append((asg_name, container_name))
        stale = self.runtime_mismatch or (
            self.runtime_stale_until_refresh and not self.refreshes
        )
        value = "unexpected" if stale else owner_module.EXPECTED_OWNER_TENANT_ID
        return [hmac.new(digest_key, value.encode(), hashlib.sha256).hexdigest()]

    def assert_public_health(self) -> None:
        self.health_checked = True
        if self.fail_health:
            raise owner_module.ReconcileError("api_public_health_failed")


def _run(runtime: FakeRuntime, *, apply: bool):
    output: list[str] = []
    source_checks: list[bool] = []
    owner_module.reconcile(
        runtime,
        apply=apply,
        source_check=lambda: source_checks.append(True),
        output=output.append,
    )
    return output, source_checks


def test_dry_run_reports_both_explicitness_flags_without_mutation_or_secret_output():
    runtime = FakeRuntime()

    output, source_checks = _run(runtime, apply=False)

    diagnostic = "\n".join(output)
    assert "api_explicit=false" in diagnostic
    assert "worker_explicit=false" in diagnostic
    assert "owner_equal=true" in diagnostic
    assert "owner_expected=true" in diagnostic
    assert "writes=0 refreshes=0" in diagnostic
    assert API_KEY not in diagnostic
    assert API_SECRET not in diagnostic
    assert SENDER not in diagnostic
    assert "OWNER_TENANT_ID=1" not in diagnostic
    assert source_checks == []
    assert runtime.puts == []
    assert runtime.refreshes == []
    assert runtime.lock_acquired is False


@pytest.mark.parametrize("value", ["2", "01", "", None, 1, True])
def test_explicit_owner_value_must_be_exact_string_one(value):
    runtime = FakeRuntime()
    runtime.api["OWNER_TENANT_ID"] = value

    with pytest.raises(
        owner_module.ReconcileError,
        match="api_owner_tenant_value_drift",
    ):
        _run(runtime, apply=True)

    assert runtime.lock_acquired is False
    assert runtime.puts == []


def test_apply_adds_only_owner_key_and_refreshes_messaging_then_api():
    runtime = FakeRuntime()
    before_api = dict(runtime.api)
    before_worker = dict(runtime.worker)

    output, source_checks = _run(runtime, apply=True)

    assert runtime.api == dict(
        before_api,
        OWNER_TENANT_ID=owner_module.EXPECTED_OWNER_TENANT_ID,
    )
    assert runtime.worker == dict(
        before_worker,
        OWNER_TENANT_ID=owner_module.EXPECTED_OWNER_TENANT_ID,
    )
    assert runtime.puts == [
        owner_module.WORKER_ENV_PARAMETER,
        owner_module.API_ENV_PARAMETER,
    ]
    assert runtime.refreshes == [owner_module.MESSAGING_ASG, owner_module.API_ASG]
    assert runtime.runtime_readbacks == [
        (owner_module.MESSAGING_ASG, owner_module.MESSAGING_CONTAINER),
        (owner_module.API_ASG, owner_module.API_CONTAINER),
    ]
    assert runtime.health_checked is True
    assert runtime.api_version == 21
    assert runtime.worker_version == 31
    assert runtime.api_key_id == "alias/academy-api-env"
    assert runtime.worker_key_id == "alias/academy-worker-env"
    assert runtime.lock_released is True
    assert runtime.lock_assertions == 1
    assert runtime.lock_renews >= 6
    assert source_checks == [True, True]
    assert output[-1].startswith("COMMON_ALIMTALK_OWNER_RECONCILED ")


def test_apply_updates_only_missing_side_but_refreshes_both_runtimes():
    runtime = FakeRuntime(api_explicit=True)

    _run(runtime, apply=True)

    assert runtime.puts == [owner_module.WORKER_ENV_PARAMETER]
    assert runtime.refreshes == [owner_module.MESSAGING_ASG, owner_module.API_ASG]
    assert runtime.api["OWNER_TENANT_ID"] == owner_module.EXPECTED_OWNER_TENANT_ID
    assert runtime.worker["OWNER_TENANT_ID"] == owner_module.EXPECTED_OWNER_TENANT_ID


def test_apply_is_noop_when_both_documents_are_already_explicit():
    runtime = FakeRuntime(api_explicit=True, worker_explicit=True)

    output, _ = _run(runtime, apply=True)

    assert runtime.puts == []
    assert runtime.refreshes == []
    assert runtime.runtime_readbacks == [
        (owner_module.MESSAGING_ASG, owner_module.MESSAGING_CONTAINER),
        (owner_module.API_ASG, owner_module.API_CONTAINER),
    ]
    assert runtime.health_checked is True
    assert runtime.lock_released is True
    assert output[-1].startswith("COMMON_ALIMTALK_OWNER_ALREADY_EXPLICIT ")
    assert "writes=0 refreshes=0 queues=0" in output[-1]


def test_apply_refreshes_stale_runtimes_when_ssm_is_already_explicit():
    runtime = FakeRuntime(api_explicit=True, worker_explicit=True)
    runtime.runtime_stale_until_refresh = True

    output, _ = _run(runtime, apply=True)

    assert runtime.puts == []
    assert runtime.refreshes == [owner_module.MESSAGING_ASG, owner_module.API_ASG]
    assert runtime.runtime_readbacks == [
        (owner_module.MESSAGING_ASG, owner_module.MESSAGING_CONTAINER),
        (owner_module.API_ASG, owner_module.API_CONTAINER),
        (owner_module.MESSAGING_ASG, owner_module.MESSAGING_CONTAINER),
        (owner_module.API_ASG, owner_module.API_CONTAINER),
    ]
    assert runtime.health_checked is True
    assert runtime.lock_released is True
    assert output[-1].startswith("COMMON_ALIMTALK_OWNER_RECONCILED ")


def test_second_write_failure_rolls_back_first_write_before_lock_release():
    runtime = FakeRuntime()
    before_worker_raw = runtime._worker_raw()
    runtime.fail_put_for = owner_module.API_ENV_PARAMETER

    with pytest.raises(
        owner_module.ReconcileError,
        match="runtime_environment_write_failed",
    ):
        _run(runtime, apply=True)

    assert runtime._worker_raw() == before_worker_raw
    assert runtime.puts == [
        owner_module.WORKER_ENV_PARAMETER,
        owner_module.API_ENV_PARAMETER,
        owner_module.WORKER_ENV_PARAMETER,
    ]
    assert runtime.refreshes == []
    assert runtime.lock_released is True


def test_second_write_commit_then_timeout_rolls_back_both_exact_documents():
    runtime = FakeRuntime()
    before_api_raw = runtime._api_raw()
    before_worker_raw = runtime._worker_raw()
    runtime.commit_then_fail_for = owner_module.API_ENV_PARAMETER

    with pytest.raises(
        owner_module.ReconcileError,
        match="runtime_environment_write_failed",
    ):
        _run(runtime, apply=True)

    assert runtime._api_raw() == before_api_raw
    assert runtime._worker_raw() == before_worker_raw
    assert runtime.puts == [
        owner_module.WORKER_ENV_PARAMETER,
        owner_module.API_ENV_PARAMETER,
        owner_module.API_ENV_PARAMETER,
        owner_module.WORKER_ENV_PARAMETER,
    ]
    assert runtime.refreshes == []
    assert runtime.lock_released is True


def test_rollback_failure_retains_lock_for_forward_recovery():
    runtime = FakeRuntime()
    runtime.fail_put_for = owner_module.API_ENV_PARAMETER
    runtime.fail_rollback_for = owner_module.WORKER_ENV_PARAMETER

    with pytest.raises(
        owner_module.ReconcileError,
        match="runtime_environment_rollback_failed_lock_retained",
    ):
        _run(runtime, apply=True)

    assert runtime.lock_released is False
    assert runtime.refreshes == []


def test_concurrent_version_evidence_retains_lock_without_refresh():
    runtime = FakeRuntime()
    runtime.returned_version_offset = 1

    with pytest.raises(
        owner_module.ReconcileError,
        match="concurrent_runtime_writer_detected_lock_retained",
    ):
        _run(runtime, apply=True)

    assert runtime.lock_released is False
    assert runtime.refreshes == []


def test_kms_metadata_drift_retains_lock_without_refresh():
    runtime = FakeRuntime()
    runtime.kms_drift_after_put_for = owner_module.WORKER_ENV_PARAMETER

    with pytest.raises(
        owner_module.ReconcileError,
        match="concurrent_runtime_writer_detected_lock_retained",
    ):
        _run(runtime, apply=True)

    assert runtime.lock_released is False
    assert runtime.refreshes == []


def test_lock_loss_after_first_write_never_performs_unowned_rollback_or_release():
    runtime = FakeRuntime()
    runtime.lose_lock_after_first_put = True

    with pytest.raises(
        owner_module.ReconcileError,
        match="runtime_lock_lost_after_write_lock_retained",
    ):
        _run(runtime, apply=True)

    assert runtime.puts == [owner_module.WORKER_ENV_PARAMETER]
    assert runtime.refreshes == []
    assert runtime.lock_released is False


@pytest.mark.parametrize(
    ("attribute", "reason"),
    [
        ("fail_refresh", "forward_convergence_required_lock_retained"),
        ("runtime_mismatch", "forward_convergence_required_lock_retained"),
        ("fail_health", "forward_convergence_required_lock_retained"),
        ("queue_after_refresh", "forward_convergence_required_lock_retained"),
    ],
)
def test_post_write_or_refresh_failure_retains_lock(attribute: str, reason: str):
    runtime = FakeRuntime()
    setattr(runtime, attribute, True)

    with pytest.raises(owner_module.ReconcileError, match=reason):
        _run(runtime, apply=True)

    assert runtime.lock_released is False


def test_nonempty_queue_fails_before_lock_and_write():
    runtime = FakeRuntime()
    runtime.queue_state["visible"] = 1

    with pytest.raises(owner_module.ReconcileError, match="messaging_queue_not_empty"):
        _run(runtime, apply=True)

    assert runtime.lock_acquired is False
    assert runtime.puts == []


def test_secret_free_diagnostic_checks_api_worker_owner_without_printing_values():
    diagnostic = (ROOT / "scripts" / "v1" / "check-workers-sender-queue.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "OWNER_API_CONFIGURED=" in diagnostic
    assert "OWNER_WORKER_CONFIGURED=" in diagnostic
    assert "OWNER_API_WORKER_EQUAL=" in diagnostic
    assert "OWNER_TENANT_EXPECTED=" in diagnostic
    assert "OWNER_TENANT_ID=$" not in diagnostic
    assert "Write-Host $apiOwner" not in diagnostic
    assert "Write-Host $workerOwner" not in diagnostic


@pytest.mark.parametrize(
    ("api_owner", "worker_owner", "expected"),
    [
        ("1", "1", ("true", "true", "true", "true")),
        (1, 1, ("false", "false", "false", "false")),
        ("1", 1, ("true", "false", "false", "false")),
        (None, "1", ("false", "true", "false", "false")),
        ("2", "2", ("false", "false", "false", "false")),
    ],
)
def test_diagnostic_preserves_json_owner_type_and_requires_exact_string_one(
    api_owner,
    worker_owner,
    expected,
):
    api = {
        "SOLAPI_API_KEY": "fake-key",
        "SOLAPI_API_SECRET": "fake-secret",
        "SOLAPI_SENDER": "0212345678",
        "MESSAGING_SQS_QUEUE_NAME": "academy-v1-messaging-queue",
        "OWNER_TENANT_ID": api_owner,
    }
    worker = dict(api, OWNER_TENANT_ID=worker_owner)
    api_json = json.dumps(api, separators=(",", ":"))
    worker_b64 = base64.b64encode(
        json.dumps(worker, separators=(",", ":")).encode()
    ).decode()
    script = ROOT / "scripts" / "v1" / "check-workers-sender-queue.ps1"
    command = f"""
    $global:OwnerDiagnosticReadIndex = 0
    function global:aws {{
        $global:LASTEXITCODE = 0
        if ($global:OwnerDiagnosticReadIndex -eq 0) {{
            [void]($global:OwnerDiagnosticReadIndex++)
            return '{api_json}'
        }}
        return '{worker_b64}'
    }}
    & '{script.as_posix()}'
    """
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if line.startswith("OWNER_")
    )
    assert (
        lines["OWNER_API_CONFIGURED"],
        lines["OWNER_WORKER_CONFIGURED"],
        lines["OWNER_API_WORKER_EQUAL"],
        lines["OWNER_TENANT_EXPECTED"],
    ) == expected

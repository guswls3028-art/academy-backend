#!/usr/bin/env python3
"""Make the fixed common Alimtalk owner tenant explicit in both runtimes.

The command is read-only unless ``--apply`` is supplied. It never accepts or
prints an owner value, provider credential, sender number, or decrypted SSM
document. The established runtime boundary defaults an absent owner to tenant
1; this tool only makes that exact existing value explicit.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import re
import secrets
import sys
import time
from typing import Any, Callable

import reconcile_common_alimtalk_sender as sender_boundary


API_ASG = sender_boundary.API_ASG
API_CONTAINER = sender_boundary.API_CONTAINER
API_ENV_PARAMETER = "/academy/api/env"
MESSAGING_ASG = sender_boundary.MESSAGING_ASG
MESSAGING_CONTAINER = sender_boundary.MESSAGING_CONTAINER
WORKER_ENV_PARAMETER = "/academy/workers/env"
ConcurrentWriteError = sender_boundary.ConcurrentWriteError
EnvironmentSnapshot = sender_boundary.EnvironmentSnapshot
ReconcileError = sender_boundary.ReconcileError

OWNER_KEY = "OWNER_TENANT_ID"
EXPECTED_OWNER_TENANT_ID = "1"


class AwsRuntime(sender_boundary.AwsRuntime):
    def runtime_owner_digests(
        self,
        *,
        asg_name: str,
        container_name: str,
        digest_key: bytes,
    ) -> list[str]:
        instances = self._in_service_instances(asg_name)
        if not instances:
            raise ReconcileError("runtime_asg_not_in_service")
        key_hex = digest_key.hex()
        python = (
            "import hashlib,hmac,json,sys;"
            "items=json.load(sys.stdin);"
            "env=dict(item.split('=',1) for item in items if '=' in item);"
            "value=env.get('OWNER_TENANT_ID','');"
            f"print('missing' if not value else hmac.new(bytes.fromhex('{key_hex}'),value.encode(),hashlib.sha256).hexdigest())"
        )
        command = (
            f"docker inspect {container_name} --format '{{{{json .Config.Env}}}}' "
            f'| python3 -c "{python}"'
        )
        digests: list[str] = []
        for instance_id in instances:
            try:
                command_id = self.ssm.send_command(
                    InstanceIds=[instance_id],
                    DocumentName="AWS-RunShellScript",
                    Parameters={"commands": [command]},
                )["Command"]["CommandId"]
            except Exception as exc:
                raise ReconcileError("runtime_owner_readback_failed") from exc
            for _ in range(40):
                try:
                    result = self.ssm.get_command_invocation(
                        CommandId=command_id,
                        InstanceId=instance_id,
                    )
                except Exception:
                    time.sleep(3)
                    continue
                status = result.get("Status")
                if status == "Success":
                    value = str(result.get("StandardOutputContent") or "").strip()
                    if value == "missing":
                        raise ReconcileError("runtime_owner_not_explicit")
                    if not re.fullmatch(r"[0-9a-f]{64}", value):
                        raise ReconcileError("runtime_owner_readback_failed")
                    digests.append(value)
                    break
                if status in {"Failed", "Cancelled", "TimedOut"}:
                    raise ReconcileError("runtime_owner_readback_failed")
                time.sleep(3)
            else:
                raise ReconcileError("runtime_owner_readback_timeout")
        return digests


def _owner_is_explicit(environment: dict[str, Any], *, label: str) -> bool:
    if OWNER_KEY not in environment:
        return False
    value = environment[OWNER_KEY]
    if type(value) is not str or value != EXPECTED_OWNER_TENANT_ID:
        raise ReconcileError(f"{label}_owner_tenant_value_drift")
    return True


def _assert_only_owner_changed(
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    expected = dict(before)
    expected[OWNER_KEY] = EXPECTED_OWNER_TENANT_ID
    if after != expected or set(after) != set(before) | {OWNER_KEY}:
        raise ReconcileError("runtime_environment_unexpected_change")


def _assert_parameter_metadata_unchanged(
    before: EnvironmentSnapshot,
    after: EnvironmentSnapshot,
) -> None:
    if before.api_key_id != after.api_key_id:
        raise ConcurrentWriteError("api_parameter_kms_or_writer_drift")
    if before.worker_key_id != after.worker_key_id:
        raise ConcurrentWriteError("worker_parameter_kms_or_writer_drift")


def _assert_worker_write_committed_exactly(
    before: EnvironmentSnapshot,
    after: EnvironmentSnapshot,
    *,
    returned_version: int,
) -> None:
    _assert_parameter_metadata_unchanged(before, after)
    if returned_version != before.worker_version + 1:
        raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")
    if after.worker_version != returned_version:
        raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")
    if after.api_version != before.api_version or after.api_raw != before.api_raw:
        raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
    _assert_only_owner_changed(before.worker, after.worker)


def _assert_api_write_committed_exactly(
    before: EnvironmentSnapshot,
    after_worker: EnvironmentSnapshot,
    after_api: EnvironmentSnapshot,
    *,
    returned_version: int,
) -> None:
    _assert_parameter_metadata_unchanged(before, after_api)
    if returned_version != before.api_version + 1:
        raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
    if after_api.api_version != returned_version:
        raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
    if (
        after_api.worker_version != after_worker.worker_version
        or after_api.worker_raw != after_worker.worker_raw
    ):
        raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")
    _assert_only_owner_changed(before.api, after_api.api)


def _validated_state(
    runtime: AwsRuntime,
) -> tuple[EnvironmentSnapshot, bool, bool]:
    snapshot = runtime.read_snapshot()
    sender_boundary._assert_shared_provider_config(snapshot)
    api_explicit = _owner_is_explicit(snapshot.api, label="api")
    worker_explicit = _owner_is_explicit(snapshot.worker, label="worker")
    if any(runtime.queue_counts().values()):
        raise ReconcileError("messaging_queue_not_empty")
    return snapshot, api_explicit, worker_explicit


def _runtime_owner_evidence(
    runtime: AwsRuntime,
    *,
    digest_key: bytes,
) -> tuple[bool, list[str], list[str]]:
    expected_digest = hmac.new(
        digest_key,
        EXPECTED_OWNER_TENANT_ID.encode(),
        hashlib.sha256,
    ).hexdigest()
    try:
        messaging_digests = runtime.runtime_owner_digests(
            asg_name=MESSAGING_ASG,
            container_name=MESSAGING_CONTAINER,
            digest_key=digest_key,
        )
        api_digests = runtime.runtime_owner_digests(
            asg_name=API_ASG,
            container_name=API_CONTAINER,
            digest_key=digest_key,
        )
    except ReconcileError as exc:
        if str(exc) == "runtime_owner_not_explicit":
            return False, [], []
        raise
    current = all(
        value == expected_digest for value in messaging_digests + api_digests
    )
    return current, messaging_digests, api_digests


def _rollback_pre_refresh(
    runtime: AwsRuntime,
    *,
    owner: str,
    before: EnvironmentSnapshot,
    api_attempted: bool,
    worker_attempted: bool,
) -> None:
    runtime.assert_lock_owned(owner)
    current = runtime.read_snapshot()
    _assert_parameter_metadata_unchanged(before, current)
    expected_api = dict(before.api, OWNER_TENANT_ID=EXPECTED_OWNER_TENANT_ID)
    expected_worker = dict(before.worker, OWNER_TENANT_ID=EXPECTED_OWNER_TENANT_ID)

    if api_attempted:
        api_is_original = (
            current.api_version == before.api_version
            and current.api_raw == before.api_raw
        )
        api_is_our_write = (
            current.api_version == before.api_version + 1
            and current.api == expected_api
        )
        if not api_is_original and not api_is_our_write:
            raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
    elif current.api_version != before.api_version or current.api_raw != before.api_raw:
        raise ConcurrentWriteError("api_parameter_concurrent_write_detected")

    if worker_attempted:
        worker_is_original = (
            current.worker_version == before.worker_version
            and current.worker_raw == before.worker_raw
        )
        worker_is_our_write = (
            current.worker_version == before.worker_version + 1
            and current.worker == expected_worker
        )
        if not worker_is_original and not worker_is_our_write:
            raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")
    elif (
        current.worker_version != before.worker_version
        or current.worker_raw != before.worker_raw
    ):
        raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")

    if api_attempted and current.api_raw != before.api_raw:
        runtime.assert_lock_owned(owner)
        base_version = current.api_version
        returned_version: int | None = None
        try:
            returned_version = runtime.put_environment(
                API_ENV_PARAMETER,
                before.api_raw,
                key_id=before.api_key_id,
            )
        except Exception:
            pass
        after_api = runtime.read_snapshot()
        _assert_parameter_metadata_unchanged(before, after_api)
        if returned_version is not None and returned_version != base_version + 1:
            raise ConcurrentWriteError("api_rollback_concurrent_write_detected")
        if after_api.api_version != base_version + 1:
            raise ConcurrentWriteError("api_rollback_concurrent_write_detected")
        if after_api.api_raw != before.api_raw:
            raise ReconcileError("api_environment_rollback_mismatch")
        if (
            after_api.worker_version != current.worker_version
            or after_api.worker_raw != current.worker_raw
        ):
            raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")
        current = after_api

    if worker_attempted and current.worker_raw != before.worker_raw:
        runtime.assert_lock_owned(owner)
        base_version = current.worker_version
        returned_version = None
        try:
            returned_version = runtime.put_environment(
                WORKER_ENV_PARAMETER,
                before.worker_raw,
                key_id=before.worker_key_id,
            )
        except Exception:
            pass
        after_worker = runtime.read_snapshot()
        _assert_parameter_metadata_unchanged(before, after_worker)
        if returned_version is not None and returned_version != base_version + 1:
            raise ConcurrentWriteError("worker_rollback_concurrent_write_detected")
        if after_worker.worker_version != base_version + 1:
            raise ConcurrentWriteError("worker_rollback_concurrent_write_detected")
        if after_worker.worker_raw != before.worker_raw:
            raise ReconcileError("worker_environment_rollback_mismatch")
        if (
            after_worker.api_version != current.api_version
            or after_worker.api_raw != current.api_raw
        ):
            raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
        current = after_worker

    _assert_parameter_metadata_unchanged(before, current)
    if current.api_raw != before.api_raw:
        raise ReconcileError("api_environment_rollback_mismatch")
    if current.worker_raw != before.worker_raw:
        raise ReconcileError("worker_environment_rollback_mismatch")
    runtime.assert_lock_owned(owner)


def reconcile(
    runtime: AwsRuntime,
    *,
    apply: bool,
    source_check: Callable[[], None] = sender_boundary._assert_source_freshness,
    output: Callable[[str], None] = print,
) -> None:
    snapshot, api_explicit, worker_explicit = _validated_state(runtime)
    output(
        "COMMON_ALIMTALK_OWNER_PLAN "
        f"api_explicit={str(api_explicit).lower()} "
        f"worker_explicit={str(worker_explicit).lower()} "
        "owner_equal=true owner_expected=true queues=0"
    )
    if not apply:
        output("DRY_RUN_COMPLETE writes=0 refreshes=0")
        return

    source_check()
    owner = f"manual:alimtalk-owner-explicit:{int(time.time())}:{secrets.token_hex(4)}"
    runtime.acquire_lock(owner)
    lock_releasable = True
    lock_released = False
    refresh_started = False
    api_attempted = False
    worker_attempted = False
    success_message: str | None = None
    try:
        runtime.renew_lock(owner)
        source_check()
        snapshot, api_explicit, worker_explicit = _validated_state(runtime)
        new_api = dict(snapshot.api)
        new_worker = dict(snapshot.worker)
        new_api[OWNER_KEY] = EXPECTED_OWNER_TENANT_ID
        new_worker[OWNER_KEY] = EXPECTED_OWNER_TENANT_ID
        _assert_only_owner_changed(snapshot.api, new_api)
        _assert_only_owner_changed(snapshot.worker, new_worker)

        if api_explicit and worker_explicit:
            runtime.renew_lock(owner)
            digest_key = secrets.token_bytes(32)
            runtime_current, messaging_digests, api_digests = _runtime_owner_evidence(
                runtime,
                digest_key=digest_key,
            )
            if runtime_current:
                runtime.assert_public_health()
                if any(runtime.queue_counts().values()):
                    raise ReconcileError("messaging_queue_not_empty_after_readback")
                final_snapshot = runtime.read_snapshot()
                _assert_parameter_metadata_unchanged(snapshot, final_snapshot)
                if (
                    final_snapshot.api_version != snapshot.api_version
                    or final_snapshot.api_raw != snapshot.api_raw
                    or final_snapshot.worker_version != snapshot.worker_version
                    or final_snapshot.worker_raw != snapshot.worker_raw
                ):
                    raise ConcurrentWriteError("runtime_parameter_concurrent_write_detected")
                runtime.assert_lock_owned(owner)
                success_message = (
                    "COMMON_ALIMTALK_OWNER_ALREADY_EXPLICIT "
                    f"api_runtimes={len(api_digests)} "
                    f"messaging_runtimes={len(messaging_digests)} "
                    "writes=0 refreshes=0 queues=0"
                )
                runtime.release_lock(owner)
                lock_released = True
                output(success_message)
                return

        after_worker = snapshot
        if not worker_explicit:
            runtime.renew_lock(owner)
            worker_attempted = True
            worker_version = runtime.put_environment(
                WORKER_ENV_PARAMETER,
                sender_boundary._encode_environment(new_worker, wrapping="base64"),
                key_id=snapshot.worker_key_id,
            )
            after_worker = runtime.read_snapshot()
            _assert_worker_write_committed_exactly(
                snapshot,
                after_worker,
                returned_version=worker_version,
            )

        persisted = after_worker
        if not api_explicit:
            runtime.renew_lock(owner)
            api_attempted = True
            api_version = runtime.put_environment(
                API_ENV_PARAMETER,
                sender_boundary._encode_environment(new_api, wrapping="plain"),
                key_id=snapshot.api_key_id,
            )
            persisted = runtime.read_snapshot()
            _assert_api_write_committed_exactly(
                snapshot,
                after_worker,
                persisted,
                returned_version=api_version,
            )

        refresh_started = True
        runtime.renew_lock(owner)
        runtime.refresh_service(MESSAGING_ASG)
        runtime.renew_lock(owner)
        runtime.refresh_service(API_ASG)
        runtime.renew_lock(owner)

        digest_key = secrets.token_bytes(32)
        runtime_current, messaging_digests, api_digests = _runtime_owner_evidence(
            runtime,
            digest_key=digest_key,
        )
        if not runtime_current:
            raise ReconcileError("runtime_owner_readback_mismatch")
        runtime.assert_public_health()
        if any(runtime.queue_counts().values()):
            raise ReconcileError("messaging_queue_not_empty_after_refresh")

        runtime.renew_lock(owner)
        final_snapshot = runtime.read_snapshot()
        _assert_parameter_metadata_unchanged(persisted, final_snapshot)
        if (
            final_snapshot.api_version != persisted.api_version
            or final_snapshot.api_raw != persisted.api_raw
        ):
            raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
        if (
            final_snapshot.worker_version != persisted.worker_version
            or final_snapshot.worker_raw != persisted.worker_raw
        ):
            raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")
        _owner_is_explicit(final_snapshot.api, label="api")
        _owner_is_explicit(final_snapshot.worker, label="worker")
        runtime.assert_lock_owned(owner)
        success_message = (
            "COMMON_ALIMTALK_OWNER_RECONCILED "
            f"api_runtimes={len(api_digests)} "
            f"messaging_runtimes={len(messaging_digests)} queues=0"
        )
    except Exception as exc:
        if refresh_started:
            lock_releasable = False
            output(f"LOCK_RETAINED owner={owner}")
            raise ReconcileError("forward_convergence_required_lock_retained") from exc
        if isinstance(exc, ConcurrentWriteError):
            lock_releasable = False
            output(f"LOCK_RETAINED owner={owner}")
            raise ReconcileError("concurrent_runtime_writer_detected_lock_retained") from exc
        if api_attempted or worker_attempted:
            try:
                runtime.assert_lock_owned(owner)
            except Exception as lock_exc:
                lock_releasable = False
                output(f"LOCK_RETAINED owner={owner}")
                raise ReconcileError("runtime_lock_lost_after_write_lock_retained") from lock_exc
        try:
            _rollback_pre_refresh(
                runtime,
                owner=owner,
                before=snapshot,
                api_attempted=api_attempted,
                worker_attempted=worker_attempted,
            )
        except Exception as rollback_exc:
            lock_releasable = False
            output(f"LOCK_RETAINED owner={owner}")
            raise ReconcileError("runtime_environment_rollback_failed_lock_retained") from rollback_exc
        if isinstance(exc, ReconcileError):
            raise
        raise ReconcileError("owner_tenant_reconcile_failed") from exc
    finally:
        if lock_releasable and not lock_released:
            runtime.release_lock(owner)
    if success_message is not None:
        output(success_message)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Make the fixed owner tenant explicit and refresh API + Messaging.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        reconcile(AwsRuntime(), apply=args.apply)
    except ReconcileError as exc:
        print(f"COMMON_ALIMTALK_OWNER_ABORTED reason={exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

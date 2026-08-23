#!/usr/bin/env python3
"""Reconcile the common Solapi sender across provider, SSM, and runtimes.

The command is read-only unless ``--apply`` is supplied.  It never accepts or
prints a sender number, provider credential, or decrypted SSM document.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import boto3
import requests


REGION = "ap-northeast-2"
API_ENV_PARAMETER = "/academy/api/env"
WORKER_ENV_PARAMETER = "/academy/workers/env"
LOCK_TABLE = "academy-v1-video-job-lock"
LOCK_KEY = "__deployment_control_v2__"
API_ASG = "academy-v1-api-asg"
MESSAGING_ASG = "academy-v1-messaging-worker-asg"
API_CONTAINER = "academy-api"
MESSAGING_CONTAINER = "academy-messaging-worker"
MESSAGING_QUEUE = "academy-v1-messaging-queue"
MESSAGING_DLQ = "academy-v1-messaging-queue-dlq"
ACTIVE_SENDERS_URL = "https://api.solapi.com/senderid/v1/numbers/active"
LOCK_TTL_SECONDS = 10_800
REQUIRED_PROVIDER_KEYS = (
    "SOLAPI_API_KEY",
    "SOLAPI_API_SECRET",
    "SOLAPI_SENDER",
)


class ReconcileError(RuntimeError):
    """Controlled, secret-free operator failure."""


class ConcurrentWriteError(ReconcileError):
    """Another SSM writer crossed the reconciliation boundary."""


@dataclass(frozen=True)
class EnvironmentSnapshot:
    api_raw: str
    worker_raw: str
    api_version: int
    worker_version: int
    api_key_id: str
    worker_key_id: str
    api: dict[str, Any]
    worker: dict[str, Any]


def _normalize_sender(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _decode_environment(
    raw: str,
    *,
    wrapping: str,
    minimum_keys: int,
    expected_settings: str,
) -> dict[str, Any]:
    try:
        decoded = (
            base64.b64decode(raw, validate=True).decode("utf-8")
            if wrapping == "base64"
            else raw
        )
        value = json.loads(decoded)
    except Exception as exc:
        raise ReconcileError("runtime_environment_decode_failed") from exc
    if not isinstance(value, dict) or len(value) < minimum_keys:
        raise ReconcileError("runtime_environment_shape_drift")
    if value.get("DJANGO_SETTINGS_MODULE") != expected_settings:
        raise ReconcileError("runtime_settings_module_drift")
    return value


def _encode_environment(value: dict[str, Any], *, wrapping: str) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if wrapping == "base64":
        return base64.b64encode(encoded.encode("utf-8")).decode("ascii")
    return encoded


def _extract_active_senders(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        raise ReconcileError("provider_active_sender_shape_drift")
    senders: list[str] = []
    for item in payload:
        if isinstance(item, str):
            candidate = item
        elif isinstance(item, dict):
            status = str(item.get("status") or "ACTIVE").upper()
            if status != "ACTIVE":
                continue
            candidate = item.get("phoneNumber")
        else:
            raise ReconcileError("provider_active_sender_item_drift")
        normalized = _normalize_sender(candidate)
        if not 8 <= len(normalized) <= 12:
            raise ReconcileError("provider_active_sender_value_drift")
        senders.append(normalized)
    return list(dict.fromkeys(senders))


def _create_solapi_auth_header(api_key: str, api_secret: str) -> str:
    date_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    salt = secrets.token_hex(16)
    signature = hmac.new(
        api_secret.encode("utf-8"),
        f"{date_time}{salt}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        "HMAC-SHA256 "
        f"apiKey={api_key}, date={date_time}, salt={salt}, signature={signature}"
    )


def _assert_provider_alignment(
    snapshot: EnvironmentSnapshot,
    active_senders: list[str],
) -> str:
    _assert_shared_provider_config(snapshot)
    if len(active_senders) != 1:
        raise ReconcileError("provider_active_sender_must_be_singleton")
    return active_senders[0]


def _assert_shared_provider_config(snapshot: EnvironmentSnapshot) -> None:
    for key in REQUIRED_PROVIDER_KEYS:
        api_value = str(snapshot.api.get(key) or "")
        worker_value = str(snapshot.worker.get(key) or "")
        if not api_value or not worker_value:
            raise ReconcileError("common_provider_credentials_unavailable")
        if not hmac.compare_digest(api_value, worker_value):
            raise ReconcileError("api_worker_provider_config_drift")


def _assert_only_sender_changed(
    before: dict[str, Any],
    after: dict[str, Any],
    target_sender: str,
) -> None:
    expected = dict(before)
    expected["SOLAPI_SENDER"] = target_sender
    if after != expected or set(after) != set(before):
        raise ReconcileError("runtime_environment_unexpected_change")


def _assert_parameter_metadata_unchanged(
    before: EnvironmentSnapshot,
    after: EnvironmentSnapshot,
) -> None:
    if before.api_key_id != after.api_key_id:
        raise ConcurrentWriteError("api_parameter_kms_or_writer_drift")
    if before.worker_key_id != after.worker_key_id:
        raise ConcurrentWriteError("worker_parameter_kms_or_writer_drift")


def _assert_api_write_committed_exactly(
    before: EnvironmentSnapshot,
    after: EnvironmentSnapshot,
    *,
    returned_version: int,
    target: str,
) -> None:
    _assert_parameter_metadata_unchanged(before, after)
    if returned_version != before.api_version + 1:
        raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
    if after.api_version != returned_version:
        raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
    if after.worker_version != before.worker_version or after.worker_raw != before.worker_raw:
        raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")
    _assert_only_sender_changed(before.api, after.api, target)


def _assert_worker_write_committed_exactly(
    before: EnvironmentSnapshot,
    after_api: EnvironmentSnapshot,
    after_worker: EnvironmentSnapshot,
    *,
    returned_version: int,
    target: str,
) -> None:
    _assert_parameter_metadata_unchanged(before, after_worker)
    if after_worker.api_version != after_api.api_version:
        raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
    if after_worker.api_raw != after_api.api_raw:
        raise ConcurrentWriteError("api_parameter_concurrent_write_detected")
    if returned_version != before.worker_version + 1:
        raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")
    if after_worker.worker_version != returned_version:
        raise ConcurrentWriteError("worker_parameter_concurrent_write_detected")
    _assert_only_sender_changed(before.worker, after_worker.worker, target)


class AwsRuntime:
    def __init__(self, *, region: str = REGION) -> None:
        session = boto3.Session(region_name=region)
        self.region = region
        self.ssm = session.client("ssm")
        self.sqs = session.client("sqs")
        self.ddb = session.client("dynamodb")
        self.asg = session.client("autoscaling")
        self._parameter_key_ids: dict[str, str] = {}

    def read_snapshot(self) -> EnvironmentSnapshot:
        try:
            metadata: dict[str, dict[str, Any]] = {}
            for name in (API_ENV_PARAMETER, WORKER_ENV_PARAMETER):
                descriptions = self.ssm.describe_parameters(
                    ParameterFilters=[
                        {"Key": "Name", "Option": "Equals", "Values": [name]}
                    ]
                ).get("Parameters", [])
                if len(descriptions) != 1 or descriptions[0].get("Type") != "SecureString":
                    raise ReconcileError("runtime_parameter_metadata_drift")
                key_id = str(descriptions[0].get("KeyId") or "")
                if not key_id:
                    raise ReconcileError("runtime_parameter_kms_key_unavailable")
                metadata[name] = descriptions[0]
                self._parameter_key_ids[name] = key_id
            api_result = self.ssm.get_parameter(
                Name=API_ENV_PARAMETER,
                WithDecryption=True,
            )["Parameter"]
            worker_result = self.ssm.get_parameter(
                Name=WORKER_ENV_PARAMETER,
                WithDecryption=True,
            )["Parameter"]
        except ReconcileError:
            raise
        except Exception as exc:
            raise ReconcileError("runtime_environment_read_failed") from exc
        api_raw = str(api_result.get("Value") or "")
        worker_raw = str(worker_result.get("Value") or "")
        return EnvironmentSnapshot(
            api_raw=api_raw,
            worker_raw=worker_raw,
            api_version=int(api_result.get("Version") or 0),
            worker_version=int(worker_result.get("Version") or 0),
            api_key_id=str(metadata[API_ENV_PARAMETER]["KeyId"]),
            worker_key_id=str(metadata[WORKER_ENV_PARAMETER]["KeyId"]),
            api=_decode_environment(
                api_raw,
                wrapping="plain",
                minimum_keys=70,
                expected_settings="apps.api.config.settings.prod",
            ),
            worker=_decode_environment(
                worker_raw,
                wrapping="base64",
                minimum_keys=55,
                expected_settings="apps.api.config.settings.worker",
            ),
        )

    def active_senders(self, api_key: str, api_secret: str) -> list[str]:
        try:
            response = requests.get(
                ACTIVE_SENDERS_URL,
                headers={
                    "Authorization": _create_solapi_auth_header(api_key, api_secret),
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ReconcileError("provider_active_sender_read_failed") from exc
        if response.status_code != 200:
            raise ReconcileError("provider_active_sender_read_failed")
        try:
            payload = response.json()
        except Exception as exc:
            raise ReconcileError("provider_active_sender_shape_drift") from exc
        return _extract_active_senders(payload)

    def queue_counts(self) -> dict[str, int]:
        totals = {"visible": 0, "inflight": 0, "delayed": 0}
        attributes = (
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        )
        for queue_name in (MESSAGING_QUEUE, MESSAGING_DLQ):
            try:
                url = self.sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
                values = self.sqs.get_queue_attributes(
                    QueueUrl=url,
                    AttributeNames=list(attributes),
                )["Attributes"]
            except Exception as exc:
                raise ReconcileError("messaging_queue_read_failed") from exc
            totals["visible"] += int(values.get(attributes[0], "0"))
            totals["inflight"] += int(values.get(attributes[1], "0"))
            totals["delayed"] += int(values.get(attributes[2], "0"))
        return totals

    def acquire_lock(self, owner: str) -> None:
        now = int(time.time())
        try:
            self.ddb.put_item(
                TableName=LOCK_TABLE,
                Item={
                    "videoId": {"S": LOCK_KEY},
                    "owner": {"S": owner},
                    "ttl": {"N": str(now + LOCK_TTL_SECONDS)},
                    "acquiredAt": {"N": str(now)},
                },
                ConditionExpression="attribute_not_exists(videoId) OR #ttl < :now",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={":now": {"N": str(now)}},
            )
        except Exception as exc:
            raise ReconcileError("shared_production_lock_unavailable") from exc

    def assert_lock_owned(self, owner: str) -> None:
        try:
            item = self.ddb.get_item(
                TableName=LOCK_TABLE,
                Key={"videoId": {"S": LOCK_KEY}},
                ConsistentRead=True,
            ).get("Item", {})
        except Exception as exc:
            raise ReconcileError("shared_production_lock_read_failed") from exc
        actual = str(item.get("owner", {}).get("S") or "")
        expires = int(item.get("ttl", {}).get("N") or 0)
        if actual != owner or expires <= int(time.time()):
            raise ReconcileError("shared_production_lock_ownership_lost")

    def renew_lock(self, owner: str) -> None:
        now = int(time.time())
        try:
            self.ddb.update_item(
                TableName=LOCK_TABLE,
                Key={"videoId": {"S": LOCK_KEY}},
                UpdateExpression="SET #ttl = :expires",
                ConditionExpression="#owner = :owner AND #ttl >= :now",
                ExpressionAttributeNames={"#owner": "owner", "#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":owner": {"S": owner},
                    ":now": {"N": str(now)},
                    ":expires": {"N": str(now + LOCK_TTL_SECONDS)},
                },
            )
        except Exception as exc:
            raise ReconcileError("shared_production_lock_renew_failed") from exc

    def release_lock(self, owner: str) -> None:
        now = int(time.time())
        try:
            self.ddb.delete_item(
                TableName=LOCK_TABLE,
                Key={"videoId": {"S": LOCK_KEY}},
                ConditionExpression="#owner = :owner AND #ttl >= :now",
                ExpressionAttributeNames={"#owner": "owner", "#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":owner": {"S": owner},
                    ":now": {"N": str(now)},
                },
            )
        except Exception as exc:
            raise ReconcileError("shared_production_lock_release_failed") from exc

    def put_environment(self, name: str, value: str, *, key_id: str) -> int:
        arguments: dict[str, Any] = {
            "Name": name,
            "Type": "SecureString",
            "Value": value,
            "Overwrite": True,
        }
        if not key_id or key_id != self._parameter_key_ids.get(name):
            raise ReconcileError("runtime_parameter_kms_key_drift")
        arguments["KeyId"] = key_id
        try:
            result = self.ssm.put_parameter(**arguments)
        except Exception as exc:
            raise ReconcileError("runtime_environment_write_failed") from exc
        return int(result.get("Version") or 0)

    def refresh_service(self, asg_name: str) -> None:
        instances = self._in_service_instances(asg_name)
        if not instances:
            raise ReconcileError("runtime_asg_not_in_service")
        try:
            self.asg.set_instance_protection(
                AutoScalingGroupName=asg_name,
                InstanceIds=instances,
                ProtectedFromScaleIn=False,
            )
            refresh_id = self.asg.start_instance_refresh(
                AutoScalingGroupName=asg_name,
                Preferences={
                    "MinHealthyPercentage": 100,
                    "MaxHealthyPercentage": 200,
                    "InstanceWarmup": 120,
                },
            )["InstanceRefreshId"]
        except Exception as exc:
            raise ReconcileError("runtime_refresh_start_failed") from exc
        for _ in range(40):
            try:
                result = self.asg.describe_instance_refreshes(
                    AutoScalingGroupName=asg_name,
                    InstanceRefreshIds=[refresh_id],
                )["InstanceRefreshes"][0]
            except Exception as exc:
                raise ReconcileError("runtime_refresh_read_failed") from exc
            status = result.get("Status")
            if status == "Successful":
                return
            if status in {
                "Failed",
                "Cancelled",
                "RollbackFailed",
                "RollbackSuccessful",
            }:
                raise ReconcileError("runtime_refresh_failed")
            time.sleep(30)
        raise ReconcileError("runtime_refresh_timeout")

    def runtime_sender_digests(
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
            "value=env.get('SOLAPI_SENDER','');"
            "assert value;"
            f"print(hmac.new(bytes.fromhex('{key_hex}'),value.encode(),hashlib.sha256).hexdigest())"
        )
        command = (
            f"docker inspect {container_name} --format '{{{{json .Config.Env}}}}' "
            f"| python3 -c \"{python}\""
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
                raise ReconcileError("runtime_sender_readback_failed") from exc
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
                    if not re.fullmatch(r"[0-9a-f]{64}", value):
                        raise ReconcileError("runtime_sender_readback_failed")
                    digests.append(value)
                    break
                if status in {"Failed", "Cancelled", "TimedOut"}:
                    raise ReconcileError("runtime_sender_readback_failed")
                time.sleep(3)
            else:
                raise ReconcileError("runtime_sender_readback_timeout")
        return digests

    def assert_public_health(self) -> None:
        for path, expected in (("healthz", "ok"), ("health", "healthy")):
            try:
                response = requests.get(
                    f"https://api.hakwonplus.com/{path}",
                    timeout=15,
                )
                payload = response.json()
            except Exception as exc:
                raise ReconcileError("api_public_health_failed") from exc
            if response.status_code != 200 or payload.get("status") != expected:
                raise ReconcileError("api_public_health_failed")

    def _in_service_instances(self, asg_name: str) -> list[str]:
        try:
            groups = self.asg.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            )["AutoScalingGroups"]
        except Exception as exc:
            raise ReconcileError("runtime_asg_read_failed") from exc
        if len(groups) != 1:
            raise ReconcileError("runtime_asg_read_failed")
        group = groups[0]
        desired = int(group.get("DesiredCapacity") or 0)
        instances = [
            str(item["InstanceId"])
            for item in group.get("Instances", [])
            if item.get("LifecycleState") == "InService"
            and item.get("HealthStatus") == "Healthy"
        ]
        if desired < 1 or len(instances) != desired:
            raise ReconcileError("runtime_asg_not_in_service")
        return instances


def _assert_source_freshness() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "v1" / "assert-production-source-freshness.ps1"
    result = subprocess.run(
        ["pwsh", str(script), "-RepoRoot", str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ReconcileError("production_source_freshness_failed")


def _validated_state(runtime: AwsRuntime) -> tuple[EnvironmentSnapshot, str]:
    snapshot = runtime.read_snapshot()
    _assert_shared_provider_config(snapshot)
    active = runtime.active_senders(
        str(snapshot.api.get("SOLAPI_API_KEY") or ""),
        str(snapshot.api.get("SOLAPI_API_SECRET") or ""),
    )
    target = _assert_provider_alignment(snapshot, active)
    if any(runtime.queue_counts().values()):
        raise ReconcileError("messaging_queue_not_empty")
    return snapshot, target


def _rollback_pre_refresh(
    runtime: AwsRuntime,
    *,
    before: EnvironmentSnapshot,
    target: str,
    api_attempted: bool,
    worker_attempted: bool,
) -> None:
    current = runtime.read_snapshot()
    _assert_parameter_metadata_unchanged(before, current)
    expected_api = dict(before.api, SOLAPI_SENDER=target)
    expected_worker = dict(before.worker, SOLAPI_SENDER=target)

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

    if worker_attempted and current.worker != before.worker:
        base_version = current.worker_version
        returned_version: int | None = None
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

    if api_attempted and current.api != before.api:
        base_version = current.api_version
        returned_version = None
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

    _assert_parameter_metadata_unchanged(before, current)
    if current.api_raw != before.api_raw:
        raise ReconcileError("api_environment_rollback_mismatch")
    if current.worker_raw != before.worker_raw:
        raise ReconcileError("worker_environment_rollback_mismatch")


def reconcile(
    runtime: AwsRuntime,
    *,
    apply: bool,
    source_check: Callable[[], None] = _assert_source_freshness,
    output: Callable[[str], None] = print,
) -> None:
    snapshot, target = _validated_state(runtime)
    aligned = _normalize_sender(snapshot.api["SOLAPI_SENDER"]) == target
    output(
        "COMMON_ALIMTALK_SENDER_PLAN "
        f"api_keys={len(snapshot.api)} worker_keys={len(snapshot.worker)} "
        f"configured_active={str(aligned).lower()} queues=0"
    )
    if not apply:
        output("DRY_RUN_COMPLETE writes=0 refreshes=0")
        return

    source_check()
    owner = f"manual:solapi-sender-reconcile:{int(time.time())}:{secrets.token_hex(4)}"
    runtime.acquire_lock(owner)
    lock_releasable = True
    refresh_started = False
    api_attempted = False
    worker_attempted = False
    success_message: str | None = None
    try:
        runtime.renew_lock(owner)
        source_check()
        snapshot, target = _validated_state(runtime)
        new_api = dict(snapshot.api)
        new_worker = dict(snapshot.worker)
        new_api["SOLAPI_SENDER"] = target
        new_worker["SOLAPI_SENDER"] = target
        _assert_only_sender_changed(snapshot.api, new_api, target)
        _assert_only_sender_changed(snapshot.worker, new_worker, target)

        after_api = snapshot
        if snapshot.api != new_api:
            runtime.renew_lock(owner)
            api_attempted = True
            api_version = runtime.put_environment(
                API_ENV_PARAMETER,
                _encode_environment(new_api, wrapping="plain"),
                key_id=snapshot.api_key_id,
            )
            after_api = runtime.read_snapshot()
            _assert_api_write_committed_exactly(
                snapshot,
                after_api,
                returned_version=api_version,
                target=target,
            )

        persisted = after_api
        if snapshot.worker != new_worker:
            runtime.renew_lock(owner)
            worker_attempted = True
            worker_version = runtime.put_environment(
                WORKER_ENV_PARAMETER,
                _encode_environment(new_worker, wrapping="base64"),
                key_id=snapshot.worker_key_id,
            )
            persisted = runtime.read_snapshot()
            _assert_worker_write_committed_exactly(
                snapshot,
                after_api,
                persisted,
                returned_version=worker_version,
                target=target,
            )

        refresh_started = True
        runtime.renew_lock(owner)
        runtime.refresh_service(MESSAGING_ASG)
        runtime.renew_lock(owner)
        runtime.refresh_service(API_ASG)
        runtime.renew_lock(owner)

        digest_key = secrets.token_bytes(32)
        expected_digest = hmac.new(
            digest_key,
            target.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        messaging_digests = runtime.runtime_sender_digests(
            asg_name=MESSAGING_ASG,
            container_name=MESSAGING_CONTAINER,
            digest_key=digest_key,
        )
        api_digests = runtime.runtime_sender_digests(
            asg_name=API_ASG,
            container_name=API_CONTAINER,
            digest_key=digest_key,
        )
        if any(value != expected_digest for value in messaging_digests + api_digests):
            raise ReconcileError("runtime_sender_readback_mismatch")

        active = runtime.active_senders(
            str(persisted.api["SOLAPI_API_KEY"]),
            str(persisted.api["SOLAPI_API_SECRET"]),
        )
        if active != [target]:
            raise ReconcileError("provider_active_sender_changed")
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
        runtime.assert_lock_owned(owner)
        success_message = (
            "COMMON_ALIMTALK_SENDER_RECONCILED "
            f"api_runtimes={len(api_digests)} messaging_runtimes={len(messaging_digests)} "
            "queues=0"
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
        try:
            _rollback_pre_refresh(
                runtime,
                before=snapshot,
                target=target,
                api_attempted=api_attempted,
                worker_attempted=worker_attempted,
            )
        except Exception as rollback_exc:
            lock_releasable = False
            output(f"LOCK_RETAINED owner={owner}")
            raise ReconcileError("runtime_environment_rollback_failed_lock_retained") from rollback_exc
        if isinstance(exc, ReconcileError):
            raise
        raise ReconcileError("sender_reconcile_failed") from exc
    finally:
        if lock_releasable:
            runtime.release_lock(owner)
    if success_message is not None:
        output(success_message)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mutate the two runtime parameters and refresh API + Messaging.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        reconcile(AwsRuntime(), apply=args.apply)
    except ReconcileError as exc:
        print(f"COMMON_ALIMTALK_SENDER_ABORTED reason={exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

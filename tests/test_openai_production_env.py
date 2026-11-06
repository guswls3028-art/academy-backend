"""Offline SSM/Dynamo fault boundaries for the approved production step."""
import base64
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/v1"
sys.path.insert(0, str(SCRIPTS))
import publish_openai_production_env as env

OWNER = "ci-deploy:123:1"
SHA = "a" * 40
RELEASE = f"sha-{SHA}-run-123-1"
SECRET = "synthetic-candidate-private-key"


class Dynamo:
    def __init__(self):
        self.item = {"owner": {"S": OWNER}, "ttl": {"N": "11800"}}
        self.lose_save_response = False

    def __call__(self, op, *args):
        if op == "get-item":
            assert "--consistent-read" in args
            return {"Item": copy.deepcopy(self.item)}
        values = json.loads(args[args.index("--expression-attribute-values") + 1])
        condition = args[args.index("--condition-expression") + 1]
        if op == "put-item":
            assert "attribute_not_exists(#env) AND attribute_not_exists(#envTtl)" in condition
            if "runtimeEnvTransition" in self.item or "runtimeEnvTransitionTtl" in self.item:
                raise RuntimeError("ConditionalCheckFailedException")
            if self.item and int(self.item["ttl"]["N"]) >= int(values[":now"]["N"]):
                raise RuntimeError("ConditionalCheckFailedException")
            self.item = json.loads(args[args.index("--item") + 1])
            return {}
        assert "#owner = :owner" in condition
        if self.item.get("owner") != values[":owner"]:
            raise RuntimeError("ConditionalCheckFailedException")
        if "#ttl >= :now" in condition and int(self.item["ttl"]["N"]) < int(values[":now"]["N"]):
            raise RuntimeError("ConditionalCheckFailedException")
        if "attribute_not_exists(#window)" in condition and ("accountWindow" in self.item or "accountWindowTtl" in self.item):
            raise RuntimeError("ConditionalCheckFailedException")
        if "attribute_not_exists(#env)" in condition and ("runtimeEnvTransition" in self.item or "runtimeEnvTransitionTtl" in self.item):
            raise RuntimeError("ConditionalCheckFailedException")
        if ":previous" in values:
            assert "#env = :previous" in condition and "#envTtl = :pinned" in condition
            if self.item.get("runtimeEnvTransition") != values[":previous"] or self.item.get("runtimeEnvTransitionTtl") != values[":pinned"]:
                raise RuntimeError("ConditionalCheckFailedException")
        if op == "delete-item":
            assert "attribute_not_exists(#env)" in condition and "attribute_not_exists(#envTtl)" in condition
            self.item = {}
        elif ":state" in values:
            self.item.update(runtimeEnvTransition=values[":state"], runtimeEnvTransitionTtl=values[":pinned"], ttl=values[":pinned"])
        elif ":previous" in values:
            self.item.pop("runtimeEnvTransition")
            self.item.pop("runtimeEnvTransitionTtl")
            self.item["ttl"] = values[":expires"]
        else:
            self.item["ttl"] = values[":expires"]
        if self.lose_save_response:
            self.lose_save_response = False
            raise TimeoutError("durable intent response lost")
        return {}


class Rejected(Exception):
    response = {"Error": {"Code": "AccessDeniedException"}}


class SSM:
    def __init__(self):
        api = {"DJANGO_SETTINGS_MODULE": "apps.api.config.settings.prod", "OPENAI_API_KEY": "legacy-must-stay",
               "EMBEDDING_OPENAI_API_KEY": "embedding-legacy-must-stay", "GEMINI_API_KEY": "gemini-must-stay",
               "DB_PASSWORD": "synthetic-db-private-value", "CUSTOM": "keep"}
        workers = {**api, "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.worker"}
        self.history = {
            env.API: {86: json.dumps(api)}, env.WORKERS: {139: base64.b64encode(json.dumps(workers).encode()).decode()},
            env.SOURCE: {4: SECRET},
            env.DEVELOPMENT: {7: json.dumps({env.KEY: SECRET, "ACADEMY_RUNTIME_ENV": "development",
                "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.worker", "ACADEMY_DEVELOPMENT_RELEASE_ID": RELEASE})},
        }
        self.meta = {name: {"Type": "SecureString", "Tier": "Advanced", "KeyId": "alias/test"} for name in env.TARGETS}
        self.calls = []
        self.fault = None

    def value(self, name, version=None):
        version = version or max(self.history[name])
        return self.history[name][version], version

    def metadata(self, name):
        return copy.deepcopy(self.meta[name])

    def put(self, name, value, metadata):
        assert metadata == self.meta[name]
        self.calls.append((name, value))
        if self.fault:
            self.fault(self, name, value)
        version = max(self.history[name]) + 1
        self.history[name][version] = value
        return version


@pytest.fixture
def boundary(monkeypatch):
    dynamo, ssm = Dynamo(), SSM()
    monkeypatch.setattr(env.lock, "_aws", dynamo)
    monkeypatch.setattr(env.lock.time, "time", lambda: 1000)
    return dynamo, ssm, env.Publisher(ssm, OWNER, SHA, 7, RELEASE)


def test_publish_preserves_legacy_keys_and_unrelated_values_then_is_idempotent(boundary):
    dynamo, ssm, publisher = boundary
    originals = {name: env.decode(ssm.value(name)[0], name == env.WORKERS) for name in env.TARGETS}
    result = publisher.publish()
    assert result == {"status": "published", "versions": {env.API: 87, env.WORKERS: 140},
                      "originalVersions": {env.API: 86, env.WORKERS: 139}, "developmentVersion": 7, "sourceVersion": 4}
    assert [name for name, _ in ssm.calls] == list(env.TARGETS)
    for name in env.TARGETS:
        actual = env.decode(ssm.value(name)[0], name == env.WORKERS)
        assert actual == {**originals[name], env.KEY: SECRET, "EMBEDDING_BACKEND": "local"}
    assert "runtimeEnvTransition" not in dynamo.item
    repeated = publisher.publish()
    assert repeated["versions"] == result["versions"]
    assert repeated["originalVersions"] == result["versions"]
    assert len(ssm.calls) == 2
    env.lock.release(env.lock.DEFAULT_TABLE, OWNER)
    assert dynamo.item == {}


@pytest.mark.parametrize("changed", ["release", "key", "legacy", "oversize"])
def test_preflight_fails_before_any_write(boundary, changed):
    dynamo, ssm, publisher = boundary
    dev = json.loads(ssm.history[env.DEVELOPMENT][7])
    if changed == "release": dev["ACADEMY_DEVELOPMENT_RELEASE_ID"] = "another-candidate"
    if changed == "key": dev[env.KEY] = "different-key"
    if changed == "legacy": dev["OPENAI_API_KEY"] = "must-be-empty"
    if changed == "oversize":
        ssm.history[env.API][86] = json.dumps({"large": "x" * 8192})
    ssm.history[env.DEVELOPMENT][7] = json.dumps(dev)
    with pytest.raises(env.TransitionError): publisher.publish()
    assert ssm.calls == []
    assert "runtimeEnvTransition" not in dynamo.item


def test_known_second_rejection_restores_original_raw_and_unpins(boundary):
    dynamo, ssm, publisher = boundary
    originals = {name: ssm.value(name)[0] for name in env.TARGETS}
    def reject_worker(store, name, value):
        if name == env.WORKERS: raise Rejected("private response must not escape")
    ssm.fault = reject_worker
    with pytest.raises(env.TransitionError, match="NOT_PUBLISHED"): publisher.publish()
    assert [name for name, _ in ssm.calls] == [env.API, env.WORKERS, env.API]
    assert all(ssm.value(name)[0] == originals[name] for name in env.TARGETS)
    assert ssm.value(env.API)[1] == 88
    assert "runtimeEnvTransition" not in dynamo.item


def test_lost_response_after_commit_is_reconciled_without_retry(boundary):
    dynamo, ssm, publisher = boundary
    def lose(store, name, value):
        if len(store.calls) == 2:
            store.history[name][max(store.history[name]) + 1] = value
            raise TimeoutError("private provider body")
    ssm.fault = lose
    with pytest.raises(env.TransitionError, match="NOT_PUBLISHED"): publisher.publish()
    assert [name for name, _ in ssm.calls] == [env.API, env.WORKERS, env.WORKERS, env.API]
    assert "runtimeEnvTransition" not in dynamo.item


def test_unknown_not_yet_visible_commit_retains_pin_across_renew_and_cleanup(boundary, monkeypatch):
    dynamo, ssm, publisher = boundary
    ssm.fault = lambda *args: (_ for _ in ()).throw(TimeoutError("unconfirmed"))
    with pytest.raises(env.TransitionError, match="RECOVERY_REQUIRED"): publisher.publish()
    assert len(ssm.calls) == 1
    pin = json.loads(dynamo.item["runtimeEnvTransition"]["S"])
    assert SECRET not in json.dumps(pin) and "synthetic-db-private-value" not in json.dumps(pin)
    assert "accountWindow" not in dynamo.item
    monkeypatch.setattr(env.lock.time, "time", lambda: 90000)
    env.lock.renew(env.lock.DEFAULT_TABLE, OWNER, 10800)
    assert dynamo.item["ttl"] == {"N": str(env.lock.ENV_PIN_TTL)}
    with pytest.raises(RuntimeError): env.lock.release(env.lock.DEFAULT_TABLE, OWNER)
    with pytest.raises(RuntimeError): env.lock.acquire(env.lock.DEFAULT_TABLE, "another-run", 10800)
    # Root recovery may proceed only after the exact pending write is visible.
    ssm.fault = None
    ssm.history[env.API][87] = publisher.candidates[env.API]
    recovered = env.Publisher(ssm, OWNER, SHA, 7, RELEASE).recover()
    assert recovered == {"status": "restored"}
    assert "runtimeEnvTransition" not in dynamo.item


@pytest.mark.parametrize("fault", ["lockloss", "drift", "compensation", "readback"])
def test_unknown_boundaries_never_blindly_overwrite_or_unlock(boundary, fault):
    dynamo, ssm, publisher = boundary
    def fail(store, name, value):
        if len(store.calls) == 2:
            if fault == "lockloss": dynamo.item["owner"] = {"S": "another-owner"}
            if fault == "drift": store.history[env.API][88] = '{"foreign":"preserve"}'
            if fault == "readback": store.history[env.WORKERS][140] = '{"foreign":"preserve"}'
            raise Rejected("synthetic-private-body")
        if len(store.calls) == 3 and fault == "compensation": raise TimeoutError("unknown restoration")
    ssm.fault = fail
    with pytest.raises(env.TransitionError, match="RECOVERY_REQUIRED"): publisher.publish()
    assert len(ssm.calls) == (3 if fault == "compensation" else 2)
    assert "runtimeEnvTransition" in dynamo.item
    with pytest.raises(RuntimeError): env.lock.release(env.lock.DEFAULT_TABLE, OWNER)
    if fault == "drift": assert ssm.value(env.API)[0] == '{"foreign":"preserve"}'


def test_cancel_after_intent_does_not_allow_unchecked_release(boundary):
    dynamo, ssm, publisher = boundary
    dynamo.lose_save_response = True
    with pytest.raises(env.TransitionError): publisher.publish()
    assert ssm.calls == []
    assert "runtimeEnvTransition" in dynamo.item
    with pytest.raises(RuntimeError): env.lock.release(env.lock.DEFAULT_TABLE, OWNER)
    assert env.Publisher(ssm, OWNER, SHA, 7, RELEASE).recover() == {"status": "restored"}


def test_workflow_is_synchronous_after_approval_and_verified_development():
    jobs = yaml.safe_load((ROOT / ".github/workflows/v1-build-and-push-latest.yml").read_text(encoding="utf-8"))["jobs"]
    job = jobs["run-migrations"]
    assert job["environment"] == "production"
    assert {"verify-api-development", "verify-api-preprod"} <= set(job["needs"])
    steps = job["steps"]
    locate = lambda text: next(i for i, step in enumerate(steps) if text in json.dumps(step))
    assert locate("setup-python@") < locate("constraints.txt boto3 botocore") < locate("publish_openai_production_env.py")
    assert locate("release_freshness.py") < locate("Preserve the exact cutover plan") < locate("publish_openai_production_env.py")
    assert locate("publish_openai_production_env.py") < locate("Pause only account") < locate("Run Django migrate")
    step = steps[locate("publish_openai_production_env.py")]
    assert "continue-on-error" not in step
    assert "needs.verify-api-development.outputs.workers_parameter_version" in step["env"]["VERIFIED_DEVELOPMENT_VERSION"]
    assert 'test "$VERIFIED_DEVELOPMENT_RELEASE" = "$RELEASE_IMAGE_TAG"' in step["run"]
    for name in ("deploy-api", "deploy-ai", "deploy-tools"):
        assert "run-migrations" in jobs[name]["needs"]
        assert "needs.run-migrations.result == 'success'" in jobs[name]["if"]


@pytest.mark.parametrize("missing", ["runtimeEnvTransition", "runtimeEnvTransitionTtl"])
def test_orphan_env_pin_is_not_stolen_even_if_lease_appears_expired(boundary, missing):
    dynamo, ssm, publisher = boundary
    publisher.prepare()
    dynamo.item.pop(missing)
    dynamo.item["ttl"] = {"N": "999"}
    with pytest.raises(RuntimeError): env.lock.renew(env.lock.DEFAULT_TABLE, OWNER, 10800)
    with pytest.raises(RuntimeError): env.lock.release(env.lock.DEFAULT_TABLE, OWNER)
    with pytest.raises(RuntimeError): env.lock.acquire(env.lock.DEFAULT_TABLE, "another-run", 10800)
    assert not ssm.calls


def test_openai_managed_policy_has_only_needed_new_permissions():
    raw = (SCRIPTS / "templates/iam/policy_gha_openai_runtime.json").read_text()
    policy = json.loads(raw.replace("__REGION__", env.REGION).replace("__ACCOUNT_ID__", env.ACCOUNT))
    statements = {row["Sid"]: row for row in policy["Statement"]}
    assert set(statements) == {"DevelopmentOpenAiKeyRead", "OpenAiWorkersEnvPublish", "OpenAiEnvMetadataRead"}
    assert statements["DevelopmentOpenAiKeyRead"]["Action"] == "ssm:GetParameter"
    assert statements["DevelopmentOpenAiKeyRead"]["Resource"] == f"arn:aws:ssm:{env.REGION}:{env.ACCOUNT}:parameter{env.SOURCE}"
    assert statements["OpenAiWorkersEnvPublish"]["Action"] == "ssm:PutParameter"
    assert statements["OpenAiWorkersEnvPublish"]["Resource"] == f"arn:aws:ssm:{env.REGION}:{env.ACCOUNT}:parameter{env.WORKERS}"
    assert statements["OpenAiEnvMetadataRead"] == {"Sid": "OpenAiEnvMetadataRead", "Effect": "Allow",
        "Action": "ssm:DescribeParameters", "Resource": "*", "Condition": {"StringEquals": {"aws:RequestedRegion": env.REGION}}}
    assert len(json.dumps(policy, separators=(",", ":"))) <= 6144
    inline = json.loads((ROOT / "infra/worker_asg/iam_policy_gha_ecr_build.json").read_text())
    assert len(json.dumps(inline, separators=(",", ":"))) <= 10240
    source = (SCRIPTS / "resources/iam.ps1").read_text(encoding="utf-8-sig")
    function = source.split("function Ensure-GitHubActionsDeployIAM", 1)[1]
    assert function.index('-PolicyName "academy-gha-openai-runtime"') < function.index('"put-role-policy"')
    assert '"arn:aws:iam::$($script:AccountId):policy/academy-gha-openai-runtime"' in function


def test_sdk_uses_exact_names_metadata_and_reviewed_secure_put(capsys):
    store = env.Store.__new__(env.Store)
    store.ssm = MagicMock()
    store.ssm.get_parameter.return_value = {"Parameter": {
        "Name": env.API, "Type": "SecureString", "Version": 87, "Value": SECRET}}
    assert store.value(env.API, 87) == (SECRET, 87)
    store.ssm.get_parameter.assert_called_once_with(Name=env.API + ":87", WithDecryption=True)
    meta = {"Type": "SecureString", "Tier": "Advanced", "KeyId": "alias/preserve"}
    store.ssm.describe_parameters.return_value = {"Parameters": [{"Name": env.API, **meta}]}
    assert store.metadata(env.API) == meta
    store.ssm.describe_parameters.assert_called_once_with(ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [env.API]}])
    store.ssm.put_parameter.return_value = {"Version": 88, "Tier": "Advanced", "Value": SECRET}
    assert store.put(env.API, SECRET, meta) == 88
    store.ssm.put_parameter.assert_called_once_with(Name=env.API, Value=SECRET, Overwrite=True, **meta)
    captured = capsys.readouterr()
    assert not (captured.out or captured.err)


def test_cli_cannot_echo_private_sdk_output_or_exception(monkeypatch, capsys):
    def fail():
        print(SECRET)
        print(SECRET, file=sys.stderr)
        raise RuntimeError(SECRET)
    monkeypatch.setattr(env, "Store", lambda: None)
    monkeypatch.setattr(env, "Publisher", lambda *args: SimpleNamespace(recover=fail))
    monkeypatch.setattr(sys, "argv", ["publisher", "--recover", "--owner", OWNER,
        "--source", SHA, "--release", RELEASE, "--development-version", "7"])
    assert env.main() == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert "OPENAI_ENV_TRANSITION_FAILED" in captured.err


def test_env_pin_and_account_window_are_distinct_and_exclusive(boundary):
    dynamo, ssm, publisher = boundary
    publisher.prepare()
    before = copy.deepcopy(dynamo.item)
    with pytest.raises(RuntimeError):
        env.lock.save_window(env.lock.DEFAULT_TABLE, OWNER, {"phase": "planned"})
    assert dynamo.item == before
    assert "accountWindow" not in dynamo.item
    assert not ssm.calls


def test_duplicate_environment_keys_cannot_be_silently_rewritten(boundary):
    dynamo, ssm, publisher = boundary
    ssm.history[env.API][86] = '{"keep":"first","keep":"second"}'
    with pytest.raises(env.TransitionError): publisher.publish()
    assert not ssm.calls and "runtimeEnvTransition" not in dynamo.item


def test_transient_latest_read_lag_retries_reads_without_repeating_put(boundary, monkeypatch):
    dynamo, ssm, publisher = boundary
    original_read = ssm.value
    stale_reads = []
    def delayed(name, version=None):
        if name == env.API and version is None and max(ssm.history[name]) == 87 and len(stale_reads) < 2:
            stale_reads.append(name)
            return original_read(name, 86)
        return original_read(name, version)
    monkeypatch.setattr(ssm, "value", delayed)
    monkeypatch.setattr(env.time, "sleep", lambda seconds: None)
    assert publisher.publish()["status"] == "published"
    assert len(stale_reads) == 2 and len(ssm.calls) == 2
    assert "runtimeEnvTransition" not in dynamo.item

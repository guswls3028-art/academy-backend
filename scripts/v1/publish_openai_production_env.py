"""Approved-job OpenAI env pair publication; durable intent contains no payloads.

Two SSM writes are not atomic. A dedicated pin on the existing deployment lock
survives cancellation and ambiguous responses until exact publication/restoration.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import io
import json
import logging
import os
from pathlib import Path
import re
import time

import deployment_lock as lock
from account_write_cutover import ci_binding
from ssm_secure_put import put_secure_parameter

API = "/academy/api/env"
WORKERS = "/academy/workers/env"
TARGETS = (API, WORKERS)
DEVELOPMENT = "/academy/workers/development/env"
SOURCE = "/academy/providers/development/openai-api-key"
KEY = "ACADEMY_OPENAI_API_KEY"
ACCOUNT = "809466760795"
REGION = "ap-northeast-2"
REJECTED = {"AccessDeniedException", "ValidationException", "ParameterLimitExceeded",
            "ParameterMaxVersionLimitExceeded", "ParameterPatternMismatchException", "InvalidKeyId"}


class TransitionError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise TransitionError(code)


def decode(raw, workers=False):
    if workers:
        raw = base64.b64decode(raw, validate=True).decode("utf-8")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate-env-key")
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    require(isinstance(value, dict), "env-object-required")
    return value


def candidate(raw, key, workers=False):
    value = decode(raw, workers)
    value[KEY] = key
    value["EMBEDDING_BACKEND"] = "local"
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return base64.b64encode(encoded.encode()).decode() if workers else encoded


class Store:
    def __init__(self):
        import boto3
        from botocore.config import Config
        self.session = boto3.Session(region_name=REGION)
        config = Config(retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=60)
        identity = self.session.client("sts", config=config).get_caller_identity()
        require(identity.get("Account") == ACCOUNT, "account-mismatch")
        self.ssm = self.session.client("ssm", config=config)

    def value(self, name, version=None):
        result = self.ssm.get_parameter(Name=f"{name}:{version}" if version else name, WithDecryption=True)["Parameter"]
        require(result.get("Name") == name and result.get("Type") == "SecureString", "parameter-identity")
        require(type(result.get("Version")) is int and result["Version"] > 0, "parameter-version")
        require(isinstance(result.get("Value"), str) and bool(result["Value"]), "parameter-value")
        require(version is None or result["Version"] == version, "parameter-version-mismatch")
        return result["Value"], result["Version"]

    def metadata(self, name):
        result = self.ssm.describe_parameters(ParameterFilters=[{"Key": "Name", "Option": "Equals", "Values": [name]}])
        rows = result.get("Parameters", [])
        require(len(rows) == 1 and rows[0].get("Name") == name and not result.get("NextToken"), "metadata-identity")
        row = rows[0]
        require(row.get("Type") == "SecureString" and row.get("Tier") in {"Standard", "Advanced"}
                and bool(row.get("KeyId")) and row.get("DataType", "text") == "text", "metadata-contract")
        return {key: row[key] for key in ("Type", "Tier", "KeyId")}

    def put(self, name, value, metadata):
        # Reuse the reviewed SDK primitive with this bounded, no-retry client.
        class Session:
            def client(inner, service):
                return self.ssm
        return put_secure_parameter({"region": REGION, "request": {
            "Name": name, "Value": value, "Overwrite": True, **metadata,
        }}, session_factory=lambda **kwargs: Session())["Version"]


class Publisher:
    def __init__(self, store, owner, source, development_version, release, table=lock.DEFAULT_TABLE):
        self.store, self.owner, self.source = store, owner, source
        self.development_version, self.release, self.table = development_version, release, table
        self.state = None
        self.originals, self.candidates = {}, {}

    def check(self):
        # The consistent load checks the same owner/expiry as assert_owned.
        require(lock.load_env_transition(self.table, self.owner) == self.state, "transition-state-drift")

    def save(self, state):
        lock.save_env_transition(self.table, self.owner, state, expected=self.state)
        self.state = copy.deepcopy(state)
        self.check()

    def key(self, source_version=None):
        dev_raw, _ = self.store.value(DEVELOPMENT, self.development_version)
        dev = decode(dev_raw)
        require(dev.get("ACADEMY_DEVELOPMENT_RELEASE_ID") == self.release
                and dev.get("ACADEMY_RUNTIME_ENV") == "development"
                and dev.get("DJANGO_SETTINGS_MODULE") == "apps.api.config.settings.worker", "development-binding")
        key, version = self.store.value(SOURCE, source_version)
        key = key.strip()
        require(bool(key) and not any(c in key for c in "\r\n\0") and dev.get(KEY) == key,
                "development-key-mismatch")
        require(not dev.get("OPENAI_API_KEY") and not dev.get("EMBEDDING_OPENAI_API_KEY"), "development-legacy-key")
        return key, version

    def verify(self, name, raw, version):
        # Retry only reads. A newer/changed value is drift, not propagation lag.
        for attempt in range(4):
            self.check()
            try:
                selected = self.store.value(name, version)
                latest = self.store.value(name)
                metadata = self.store.metadata(name)
            except Exception:
                if attempt == 3:
                    raise TransitionError("parameter-readback-unconfirmed") from None
            else:
                require(selected == (raw, version) and latest[1] <= version, "parameter-drift")
                require(metadata == self.state["metadata"][name], "metadata-drift")
                if latest[1] == version:
                    require(latest == (raw, version), "parameter-drift")
                    return
            if attempt < 3:
                time.sleep(0.5)
        raise TransitionError("parameter-readback-unconfirmed")

    def prepare(self):
        self.check()
        key, source_version = self.key()
        versions, metadata = {}, {}
        for name in TARGETS:
            raw, version = self.store.value(name)
            meta = self.store.metadata(name)
            updated = candidate(raw, key, name == WORKERS)
            require(len(updated.encode()) <= (8192 if meta["Tier"] == "Advanced" else 4096), "env-size")
            # An exact semantic match needs no new SSM version or formatting churn.
            self.originals[name] = raw
            self.candidates[name] = raw if decode(raw, name == WORKERS) == decode(updated, name == WORKERS) else updated
            versions[name], metadata[name] = version, meta
        state = {"schema": 1, "owner": self.owner, "source": self.source, "release": self.release,
                 "developmentVersion": self.development_version, "sourceVersion": source_version,
                 "originalVersions": versions, "metadata": metadata, "writes": {}, "phase": "prepared"}
        # Pin before any SSM write. A killed runner cannot lose its mutation intent.
        self.save(state)
        for name in TARGETS:
            self.verify(name, self.originals[name], versions[name])

    def write(self, name, value, previous, kind):
        self.verify(name, previous[0], previous[1])
        state = copy.deepcopy(self.state)
        intent = {"kind": kind, "before": previous[1], "after": previous[1] + 1, "outcome": "pending"}
        state["writes"][name] = intent
        self.save(state)
        self.check()
        try:
            version = self.store.put(name, value, self.state["metadata"][name])
        except Exception as error:
            # Only explicit service rejections prove that this attempt did not write.
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code in REJECTED:
                state = copy.deepcopy(self.state)
                state["writes"][name]["outcome"] = "rejected"
                self.save(state)
            raise TransitionError("parameter-write-unconfirmed") from None
        require(version == intent["after"], "unexpected-write-version")
        self.verify(name, value, version)
        state = copy.deepcopy(self.state)
        state["writes"][name]["outcome"] = "verified"
        self.save(state)
        return version

    def clear(self, phase):
        state = copy.deepcopy(self.state)
        state["phase"] = phase
        self.save(state)
        lock.clear_env_transition(self.table, self.owner, expected=self.state)
        self.state = None
        self.check()

    def restore(self):
        """Unknown commit + unchanged latest is unresolved, never a no-write proof."""
        self.check()
        for name in reversed(TARGETS):
            write = self.state["writes"].get(name)
            original_version = self.state["originalVersions"][name]
            original = self.originals[name]
            current = self.store.value(name)
            if write is None:
                self.verify(name, original, original_version)
                continue
            if write["outcome"] == "rejected":
                expected = original if write["kind"] == "publish" else self.candidates[name]
                self.verify(name, expected, write["before"])
                if write["kind"] == "publish":
                    continue
                raise TransitionError("restoration-rejected")
            expected = self.candidates[name] if write["kind"] == "publish" else original
            require(current == (expected, write["after"]), "unresolved-write-or-drift")
            self.verify(name, expected, write["after"])
            if write["kind"] == "publish":
                self.write(name, original, current, "restore")
        for name in TARGETS:
            write = self.state["writes"].get(name)
            version = write["after"] if write and write["kind"] == "restore" else self.state["originalVersions"][name]
            self.verify(name, self.originals[name], version)
        self.clear("restored")

    def publish(self):
        try:
            self.prepare()
            versions = dict(self.state["originalVersions"])
            for name in TARGETS:
                if self.candidates[name] != self.originals[name]:
                    versions[name] = self.write(name, self.candidates[name], (self.originals[name], versions[name]), "publish")
            for name in TARGETS:
                self.verify(name, self.candidates[name], versions[name])
            receipt = {"status": "published", "versions": versions,
                       "originalVersions": self.state["originalVersions"],
                       "developmentVersion": self.development_version, "sourceVersion": self.state["sourceVersion"]}
            self.clear("published")
            return receipt
        except Exception:
            # Loss of durable-state ownership stops compensation before its first write.
            if self.state is not None:
                try:
                    self.restore()
                except Exception:
                    raise TransitionError("OPENAI_ENV_RECOVERY_REQUIRED") from None
            raise TransitionError("OPENAI_ENV_NOT_PUBLISHED") from None

    def recover(self):
        self.state = lock.load_env_transition(self.table, self.owner)
        require(self.state is not None and self.state.get("schema") == 1
                and self.state.get("owner") == self.owner and self.state.get("source") == self.source
                and self.state.get("developmentVersion") == self.development_version
                and self.state.get("release") == self.release, "recovery-binding")
        key, _ = self.key(self.state["sourceVersion"])
        for name in TARGETS:
            raw, _ = self.store.value(name, self.state["originalVersions"][name])
            self.originals[name] = raw
            self.candidates[name] = candidate(raw, key, name == WORKERS)
        self.restore()
        return {"status": "restored"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--development-version", required=True, type=int)
    parser.add_argument("--candidate")
    parser.add_argument("--recover", action="store_true", help="Exact pinned attempt only; owning manual recovery gates apply")
    args = parser.parse_args()
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            require(bool(re.fullmatch(r"[0-9a-f]{40}", args.source)) and args.development_version > 0, "binding-format")
            require(bool(re.fullmatch(r"ci-deploy:[1-9][0-9]*:[1-9][0-9]*", args.owner)), "owner-format")
            run, attempt = args.owner.split(":")[1:]
            require(args.release == f"sha-{args.source}-run-{run}-{attempt}", "release-binding")
            if not args.recover:
                ci_binding(args.owner, args.source)
                require(os.environ.get("GITHUB_REF") == "refs/heads/main"
                        and os.environ.get("GITHUB_REPOSITORY") == "guswls3028-art/academy-backend", "ci-main-required")
                manifest = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
                require(manifest.get("gitSha") == args.source and manifest.get("status") == "candidate"
                        and manifest.get("complete") is False, "candidate-binding")
            publisher = Publisher(Store(), args.owner, args.source, args.development_version, args.release)
            receipt = publisher.recover() if args.recover else publisher.publish()
        print(json.dumps({"source": args.source, "release": args.release, **receipt}, sort_keys=True))
        return 0
    except Exception:
        # Raw provider/SSM errors and payloads never cross the CLI boundary.
        print("OPENAI_ENV_TRANSITION_FAILED; inspect the owned runtimeEnvTransition pin", file=__import__("sys").stderr)
        return 1
    finally:
        logging.disable(previous_logging)


if __name__ == "__main__":
    raise SystemExit(main())

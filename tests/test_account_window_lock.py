"""Failure boundaries for durable cutover intent on the existing shared lock."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def lock(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts/v1/deployment_lock.py"
    spec = importlib.util.spec_from_file_location("window_lock_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "time", lambda: 1000)
    return module


class DynamoBoundary:
    def __init__(self):
        self.item = {"videoId": {"S": "__deployment_control_v2__"},
                     "owner": {"S": "ci-deploy:123:1"}, "ttl": {"N": "11800"}}
        self.calls = []
        self.lose_response = False

    def __call__(self, operation, *args):
        self.calls.append((operation, args))
        if operation == "get-item":
            assert "--consistent-read" in args
            return {"Item": copy.deepcopy(self.item)}
        values = json.loads(args[args.index("--expression-attribute-values") + 1])
        condition = args[args.index("--condition-expression") + 1]
        if operation == "put-item":
            assert "attribute_not_exists(videoId) OR #ttl < :now" == condition
            ok = not self.item or int(self.item["ttl"]["N"]) < int(values[":now"]["N"])
        else:
            assert "#owner = :owner" in condition
            ok = self.item.get("owner") == values[":owner"]
            if "#ttl >= :now" in condition:
                ok &= int(self.item.get("ttl", {}).get("N", "0")) >= int(values[":now"]["N"])
            if ":previous" in values:
                assert "#window = :previous" in condition
                ok &= self.item.get("accountWindow") == values[":previous"]
                ok &= self.item.get("accountWindowTtl") == values[":pinned"]
            elif operation == "delete-item" or ":state" in values:
                assert "attribute_not_exists(#window)" in condition
                assert "attribute_not_exists(#windowTtl)" in condition
                ok &= "accountWindow" not in self.item and "accountWindowTtl" not in self.item
            else:
                assert "#windowTtl = :pinned" in condition
                ok &= (("accountWindow" not in self.item and "accountWindowTtl" not in self.item)
                       or ("accountWindow" in self.item and self.item.get("accountWindowTtl") == values[":pinned"]))
        if not ok:
            raise RuntimeError("ConditionalCheckFailedException")
        if operation == "put-item":
            self.item = json.loads(args[args.index("--item") + 1])
        elif operation == "delete-item":
            self.item = {}
        elif ":state" in values:
            self.item.update(accountWindow=values[":state"], accountWindowTtl=values[":pinned"],
                             ttl=values[":pinned"])
        elif ":previous" in values:
            assert "#ttl = :pinned" in condition
            self.item.pop("accountWindow")
            self.item.pop("accountWindowTtl")
            self.item["ttl"] = values[":expires"]
        else:
            assert "SET #ttl = if_not_exists(#windowTtl, :expires)" in args
            self.item["ttl"] = self.item.get("accountWindowTtl", values[":expires"])
        if self.lose_response:
            self.lose_response = False
            raise RuntimeError("response lost after durable write")
        return {}


OWNER = "ci-deploy:123:1"
PLAN = {"run": 123, "phase": "planned", "source": "a" * 40}


@pytest.fixture
def boundary(lock, monkeypatch):
    aws = DynamoBoundary()
    monkeypatch.setattr(lock, "_aws", aws)
    return aws


def test_cancelled_window_cannot_expire_or_release_before_restoration(lock, boundary, monkeypatch):
    lock.save_window(lock.DEFAULT_TABLE, OWNER, PLAN)
    monkeypatch.setattr(lock.time, "time", lambda: 1000 + 86400)
    lock.renew(lock.DEFAULT_TABLE, OWNER, 10800)
    assert boundary.item["ttl"] == {"N": str(lock.WINDOW_PIN_TTL)}
    with pytest.raises(RuntimeError, match="already held"):
        lock.acquire(lock.DEFAULT_TABLE, "another-run", 10800)
    with pytest.raises(RuntimeError, match="unrestored"):
        lock.release(lock.DEFAULT_TABLE, OWNER)
    assert lock.load_window(lock.DEFAULT_TABLE, OWNER) == PLAN


def test_response_loss_recovers_intent_and_only_exact_restored_state_unpins(lock, boundary):
    boundary.lose_response = True
    with pytest.raises(RuntimeError, match="response lost"):
        lock.save_window(lock.DEFAULT_TABLE, OWNER, PLAN)
    assert lock.load_window(lock.DEFAULT_TABLE, OWNER) == PLAN
    restored = {**PLAN, "phase": "restored"}
    lock.save_window(lock.DEFAULT_TABLE, OWNER, restored, expected=PLAN)
    with pytest.raises(RuntimeError, match="ConditionalCheckFailed"):
        lock.clear_window(lock.DEFAULT_TABLE, OWNER, PLAN)
    lock.clear_window(lock.DEFAULT_TABLE, OWNER, restored)
    assert lock.load_window(lock.DEFAULT_TABLE, OWNER) is None
    assert boundary.item["ttl"] == {"N": "11800"}
    lock.release(lock.DEFAULT_TABLE, OWNER)
    assert boundary.item == {}


@pytest.mark.parametrize("action", ["load", "save", "clear"])
def test_foreign_owner_cannot_read_or_replace_window(lock, boundary, action):
    lock.save_window(lock.DEFAULT_TABLE, OWNER, PLAN)
    before = copy.deepcopy(boundary.item)
    with pytest.raises(RuntimeError):
        if action == "load":
            lock.load_window(lock.DEFAULT_TABLE, "another-run")
        elif action == "save":
            lock.save_window(lock.DEFAULT_TABLE, "another-run", {**PLAN, "phase": "opened"}, PLAN)
        else:
            lock.clear_window(lock.DEFAULT_TABLE, "another-run", PLAN)
    assert boundary.item == before


def test_expired_lease_cannot_start_window(lock, boundary):
    boundary.item["ttl"] = {"N": "999"}
    with pytest.raises(RuntimeError):
        lock.save_window(lock.DEFAULT_TABLE, OWNER, PLAN)
    assert "accountWindow" not in boundary.item


def test_ordinary_lease_lifecycle_remains_bounded(lock, boundary):
    assert lock.load_window(lock.DEFAULT_TABLE, OWNER) is None
    lock.renew(lock.DEFAULT_TABLE, OWNER, 600)
    assert boundary.item["ttl"] == {"N": "1600"}
    lock.release(lock.DEFAULT_TABLE, OWNER)
    lock.acquire(lock.DEFAULT_TABLE, "next", 600)
    assert boundary.item["owner"] == {"S": "next"}


@pytest.mark.parametrize("missing", ["accountWindow", "accountWindowTtl"])
def test_incomplete_pin_fails_closed_without_unlocking(lock, boundary, missing):
    lock.save_window(lock.DEFAULT_TABLE, OWNER, PLAN)
    boundary.item.pop(missing)
    before = copy.deepcopy(boundary.item)
    with pytest.raises(RuntimeError):
        lock.load_window(lock.DEFAULT_TABLE, OWNER)
    with pytest.raises(RuntimeError):
        lock.renew(lock.DEFAULT_TABLE, OWNER, 600)
    with pytest.raises(RuntimeError):
        lock.release(lock.DEFAULT_TABLE, OWNER)
    assert boundary.item == before

"""PostgreSQL-only, fail-closed metadata ledger for isolated candidate QA.

This module does not create fixtures, execute effects, delete data, or attest
cleanup. The owner must wire a verified root loader, a process-local admission
gate, and exact action/settlement/cleanup policies. Caller strings and hashes
identify an intent; they never authorize one. A DB row ID may be recorded in
the same outer transaction that creates that row. External effect uncertainty
remains unresolved until a separately trusted readback settles it.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import re
import time

from django.db import connection, transaction
from django.utils import timezone

from apps.core.models import CandidateQaAction, CandidateQaReceipt, CandidateQaRun
from apps.infrastructure.qa_lease import binding_sha256


EXPECTED_DATABASE = "academy_api_development"
EXPECTED_ROLE = "academy_api_development_app"
MAX_ACTIONS = 512
MAX_RECEIPTS = 8192
PAGE_SIZE = 128
LOCATOR_KINDS = {"db_row", "r2_object", "sqs_message", "redis_key", "provider_acceptance"}
ORIGIN_KINDS = {"api", "ai", "tools", "messaging"}


class QaReceiptHold(ValueError):
    pass


class QaReceiptReplay(QaReceiptHold):
    """The original action is preserved; no second effect may be executed."""


def _require(condition, message):
    if not condition:
        raise QaReceiptHold(message)


def _hex(value, size):
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{size}}}", value) is not None


def _digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ActionToken:
    lease_id: str
    action_id: str
    operation_revision: int


@dataclass(frozen=True)
class LedgerSnapshot:
    lease_id: str
    root_sha256: str
    binding_sha256: str
    version: int
    action_count: int
    receipt_count: int
    unresolved_action_ids: tuple[str, ...]
    actions: tuple[tuple, ...]
    receipts: tuple[tuple, ...]
    observed_at: int
    fenced: bool


class CandidateQaReceiptLedger:
    """All writers serialize on the run header and increment its version."""

    def __init__(self, *, root_provider=None, gate_provider=None, action_policy=None,
                 settlement_authorizer=None, cleanup_authorizer=None,
                 fence_authorizer=None, identity_reader=None, clock=time.time):
        self.root_provider = root_provider
        self.gate_provider = gate_provider
        self.action_policy = action_policy
        self.settlement_authorizer = settlement_authorizer
        self.cleanup_authorizer = cleanup_authorizer
        self.fence_authorizer = fence_authorizer
        self.identity_reader = identity_reader
        self.clock = clock

    def _database(self):
        try:
            if self.identity_reader is None:
                _require(connection.vendor == "postgresql", "QA receipts require PostgreSQL")
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_database(), current_user")
                    actual = cursor.fetchone()
            else:
                # Only an owner-injected test double may replace the live SQL read.
                actual = self.identity_reader()
        except QaReceiptHold:
            raise
        except Exception:
            raise QaReceiptHold("QA database identity unavailable") from None
        _require(actual == (EXPECTED_DATABASE, EXPECTED_ROLE),
                 "QA receipts are outside the exact isolated development database/role")

    def _gate(self, kind):
        _require(kind in ORIGIN_KINDS and callable(self.gate_provider),
                 "Trusted QA admission gate is not wired")
        gate = self.gate_provider(kind)
        _require(getattr(gate, "enabled", None) is True and getattr(gate, "kind", None) == kind,
                 "Exact QA admission gate required")
        return gate

    def _root(self, record):
        _require(callable(self.root_provider), "Verified QA root loader is not wired")
        root = self.root_provider(record)
        _require(root is not None and _hex(getattr(root, "sha256", None), 64)
                 and root.sha256 == record.get("resource_manifest_sha256")
                 and getattr(root, "data", None) is not None,
                 "Committed QA root receipt missing")
        data = root.data
        _require(isinstance(data, dict) and data.get("schema") == "academy-candidate-qa-root/v1"
                 and data.get("lease_id") == record.get("lease_id")
                 and data.get("owner_task") == record.get("owner_task")
                 and data.get("source_sha") == record.get("source_sha")
                 and data.get("images") == record.get("images")
                 and sorted(data.get("scope", [])) == sorted(record.get("scope", []))
                 and sorted(row["id"] for row in data.get("tenants", []))
                 == sorted(record.get("tenant_ids", [])),
                 "QA root scope differs from committed lease")
        domains = data.get("domains")
        expected = {"auth"} | ({"ppt", "matchup"} if 509 in record["scope"] else set()) | (
            {"omr"} if 511 in record["scope"] else set())
        _require(isinstance(domains, list) and set(domains) == expected
                 and len(domains) == len(expected), "QA root domain policy incomplete")
        return root

    @staticmethod
    def _run_matches(run, record, root):
        _require(run.root_sha256 == root.sha256
                 and run.binding_sha256 == binding_sha256(record)
                 and run.owner_task == record["owner_task"]
                 and run.source_sha == record["source_sha"]
                 and run.tenant_ids == sorted(record["tenant_ids"])
                 and set(run.domains) == set(root.data["domains"]),
                 "QA receipt run belongs to another root or lease")

    def ensure_run(self):
        """Register only a verified PREPARED/ACTIVE root; never create fixtures."""
        self._database()
        gate = self._gate("api")
        record = gate.inspect_lease()
        _require(record.get("state") in {"prepared", "active"}
                 and not record.get("control_hold"),
                 "QA run registration requires an unheld committed lease")
        root = self._root(record)
        values = {
            "root_sha256": root.sha256,
            "binding_sha256": binding_sha256(record),
            "owner_task": record["owner_task"],
            "source_sha": record["source_sha"],
            "tenant_ids": sorted(record["tenant_ids"]),
            "domains": sorted(root.data["domains"]),
        }
        with transaction.atomic():
            run, _ = CandidateQaRun.objects.get_or_create(
                lease_id=record["lease_id"], defaults=values,
            )
            run = CandidateQaRun.objects.select_for_update().get(pk=run.pk)
            self._run_matches(run, record, root)
        return run.lease_id

    @contextmanager
    def effect(self, *, action_id, domain, action_type, tenant_id, args_sha256,
               origin_kind="api"):
        """Hold one fresh gate.begin across exactly one logical caller effect.

        Reusing an action ID never enters the effect body, even with identical
        arguments. A planned action without a confirmed outcome is unresolved,
        including when the caller crashes after an external effect.
        """
        self._database()
        _require(_hex(action_id, 32) and _hex(args_sha256, 64)
                 and isinstance(action_type, str)
                 and re.fullmatch(r"[a-z][a-z0-9_]{1,47}", action_type)
                 and type(tenant_id) is int and tenant_id > 0,
                 "Canonical QA action metadata required")
        _require(callable(self.action_policy), "Exact QA action policy is not wired")
        gate = self._gate(origin_kind)
        with gate.begin(f"candidate-qa:{action_id}", tenant_id=tenant_id) as record:
            _require(record.get("state") == "active" and not record.get("control_hold")
                     and int(self.clock()) < record["expires_at"] - 30,
                     "QA effect admission is closed")
            root = self._root(record)
            _require(domain in root.data["domains"] and tenant_id in record["tenant_ids"]
                     and self.action_policy(record, root, origin_kind, domain, action_type,
                                            tenant_id) is True,
                     "QA action is outside trusted fixture policy")
            intent = _digest((record["lease_id"], binding_sha256(record), action_id,
                              domain, action_type, tenant_id, origin_kind, args_sha256))
            with transaction.atomic():
                run = CandidateQaRun.objects.select_for_update().get(pk=record["lease_id"])
                self._run_matches(run, record, root)
                _require(run.fenced_at is None, "QA receipt run is fenced")
                existing = CandidateQaAction.objects.filter(run=run, action_id=action_id).first()
                if existing is not None:
                    _require(existing.intent_sha256 == intent,
                             "QA action ID replay has different content")
                    raise QaReceiptReplay("QA action already registered; effect replay forbidden")
                _require(run.action_count < MAX_ACTIONS, "QA action capacity exhausted")
                CandidateQaAction.objects.create(
                    run=run, action_id=action_id, domain=domain, action_type=action_type,
                    tenant_id=tenant_id, origin_kind=origin_kind,
                    operation_revision=record["revision"], args_sha256=args_sha256,
                    intent_sha256=intent,
                )
                run.action_count += 1
                run.version += 1
                run.save(update_fields=["action_count", "version", "updated_at"])
            yield ActionToken(record["lease_id"], action_id, record["revision"])

    def _append(self, *, lease_id, action_id, event_id, kind, locator_kind="",
                locator_type="", locator_ref="", outcome="", proof_sha256=""):
        self._database()
        _require(_hex(lease_id, 32) and _hex(action_id, 32) and _hex(event_id, 32),
                 "Canonical QA receipt identity required")
        _require(kind in {"locator", "outcome", "cleanup"}, "Unknown QA receipt kind")
        if kind == "locator":
            _require(locator_kind in LOCATOR_KINDS
                     and isinstance(locator_type, str)
                     and ((locator_kind == "db_row" and re.fullmatch(
                         r"[a-z][a-z0-9_]*\.[A-Z][A-Za-z0-9_]*", locator_type))
                          or (locator_kind != "db_row" and locator_type in {
                              "r2", "sqs", "redis", "provider"}))
                     and isinstance(locator_ref, str)
                     and (re.fullmatch(r"[1-9][0-9]{0,18}", locator_ref)
                          or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", locator_ref)
                          or _hex(locator_ref, 32) or _hex(locator_ref, 64))
                     and outcome == "" and proof_sha256 == "",
                     "QA locator must be an opaque ID or fingerprint")
        else:
            _require(not locator_kind and not locator_type and not locator_ref
                     and _hex(proof_sha256, 64), "QA outcome requires a proof fingerprint")
            if kind == "outcome":
                _require(outcome in {"success", "failure", "unknown"},
                         "QA result uncertainty must be explicit")
            else:
                _require(outcome == "", "QA cleanup receipt is not an effect result")
        payload = (kind, locator_kind, locator_type, locator_ref, outcome, proof_sha256)
        event_sha256 = _digest(payload)
        with transaction.atomic():
            run = CandidateQaRun.objects.select_for_update().get(pk=lease_id)
            _require(run.fenced_at is None, "QA receipt run is fenced")
            action = CandidateQaAction.objects.get(run=run, action_id=action_id)
            authorizer = (self.cleanup_authorizer if kind == "cleanup"
                          else self.settlement_authorizer)
            _require(callable(authorizer)
                     and authorizer(run, action, kind, payload) is True,
                     "Trusted QA receipt writer or cleanup fence is not wired")
            existing = CandidateQaReceipt.objects.filter(action=action, event_id=event_id).first()
            if existing is not None:
                _require(existing.event_sha256 == event_sha256,
                         "QA event ID replay has different content")
                return existing.id
            _require(run.receipt_count < MAX_RECEIPTS, "QA receipt capacity exhausted")
            receipt = CandidateQaReceipt.objects.create(
                action=action, event_id=event_id, kind=kind,
                locator_kind=locator_kind, locator_type=locator_type,
                locator_ref=locator_ref, outcome=outcome,
                proof_sha256=proof_sha256, event_sha256=event_sha256,
            )
            run.receipt_count += 1
            run.version += 1
            run.save(update_fields=["receipt_count", "version", "updated_at"])
            return receipt.id

    def record_locator(self, *, lease_id, action_id, event_id, locator_kind,
                       locator_type, locator_ref):
        """Use inside the creator's DB transaction for DB auto-ID mapping."""
        return self._append(lease_id=lease_id, action_id=action_id, event_id=event_id,
                            kind="locator", locator_kind=locator_kind,
                            locator_type=locator_type, locator_ref=locator_ref)

    def record_outcome(self, *, lease_id, action_id, event_id, outcome, proof_sha256):
        return self._append(lease_id=lease_id, action_id=action_id, event_id=event_id,
                            kind="outcome", outcome=outcome, proof_sha256=proof_sha256)

    def record_cleanup(self, *, lease_id, action_id, event_id, proof_sha256):
        """Evidence only; separately fenced cleanup code owns actual deletion."""
        return self._append(lease_id=lease_id, action_id=action_id, event_id=event_id,
                            kind="cleanup", proof_sha256=proof_sha256)

    def snapshot(self, lease_id):
        """Complete bounded keyset read under the same lock used by every writer."""
        self._database()
        _require(_hex(lease_id, 32), "Canonical QA lease required")
        with transaction.atomic():
            run = CandidateQaRun.objects.select_for_update().get(pk=lease_id)
            actions = []
            last = 0
            while True:
                page = list(CandidateQaAction.objects.filter(run=run, pk__gt=last)
                            .order_by("pk")[:PAGE_SIZE])
                if not page:
                    break
                actions.extend(page)
                _require(len(actions) <= MAX_ACTIONS, "QA action snapshot exceeds bound")
                last = page[-1].pk
            receipts = []
            last = 0
            while True:
                page = list(CandidateQaReceipt.objects.filter(action__run=run, pk__gt=last)
                            .order_by("pk")[:PAGE_SIZE])
                if not page:
                    break
                receipts.extend(page)
                _require(len(receipts) <= MAX_RECEIPTS, "QA receipt snapshot exceeds bound")
                last = page[-1].pk
            _require(len(actions) == run.action_count and len(receipts) == run.receipt_count,
                     "QA receipt snapshot is incomplete")
            _require(run.version == run.action_count + run.receipt_count
                     + int(run.fenced_at is not None),
                     "QA ledger version or writer fence differs")
            latest = {action.pk: None for action in actions}
            action_ids = {action.pk: action.action_id for action in actions}
            for item in receipts:
                _require(item.action_id in latest, "QA receipt action missing")
                latest[item.action_id] = item
            unresolved = tuple(sorted(action.action_id for action in actions
                                      if latest[action.pk] is None
                                      or latest[action.pk].kind != "outcome"
                                      or latest[action.pk].outcome == "unknown"))
            return LedgerSnapshot(
                lease_id=run.lease_id, root_sha256=run.root_sha256,
                binding_sha256=run.binding_sha256, version=run.version,
                action_count=run.action_count, receipt_count=run.receipt_count,
                unresolved_action_ids=unresolved,
                actions=tuple((a.action_id, a.domain, a.tenant_id, a.operation_revision,
                               a.intent_sha256) for a in actions),
                receipts=tuple((action_ids[r.action_id], r.event_id, r.kind, r.locator_kind,
                                r.locator_type, r.locator_ref, r.outcome, r.proof_sha256,
                                r.event_sha256) for r in receipts),
                observed_at=int(self.clock()), fenced=run.fenced_at is not None,
            )

    def assert_current(self, snapshot):
        """A late event invalidates every earlier domain absence observation."""
        self._database()
        _require(isinstance(snapshot, LedgerSnapshot), "Typed QA ledger snapshot required")
        with transaction.atomic():
            run = CandidateQaRun.objects.select_for_update().get(pk=snapshot.lease_id)
            _require(run.version == snapshot.version
                     and run.root_sha256 == snapshot.root_sha256
                     and run.binding_sha256 == snapshot.binding_sha256,
                     "QA ledger changed after absence readback")

    def cleanup_zero(self, snapshot):
        """Ledger completeness is insufficient without live DB/R2/outbox proof."""
        self.assert_current(snapshot)
        raise QaReceiptHold("Trusted external domain absence probe is not wired")

    def fence(self, lease_id):
        """Seal writers only after a trusted drained/absent runtime readback.

        The fence callback is deliberately unwired. New effects, late results,
        and cleanup receipts cannot pass after sealing; recovery then requires
        explicit owner reconciliation rather than reopening ACTIVE admission.
        """
        self._database()
        _require(callable(self.fence_authorizer), "Trusted QA writer fence is not wired")
        snapshot = self.snapshot(lease_id)
        _require(not snapshot.fenced and not snapshot.unresolved_action_ids,
                 "Unresolved QA action prevents writer fence")
        _require(self.fence_authorizer(snapshot) is True,
                 "Trusted QA drained/absent fence proof missing")
        with transaction.atomic():
            run = CandidateQaRun.objects.select_for_update().get(pk=lease_id)
            _require(run.fenced_at is None, "QA receipt run already fenced")
            _require(run.version == snapshot.version
                     and run.root_sha256 == snapshot.root_sha256
                     and run.binding_sha256 == snapshot.binding_sha256,
                     "QA ledger changed during writer fence")
            run.fenced_at = timezone.now()
            run.version += 1
            run.save(update_fields=["fenced_at", "version", "updated_at"])
            return run.version

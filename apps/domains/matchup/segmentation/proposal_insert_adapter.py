"""Stage 6.0 — proposal insert adapter (dry-run default, sandbox-gated INSERT).

Stage 5.9 검증 통과한 `ProposalPayloadCandidate` 를 실제
`ProblemSegmentationProposal` INSERT path 로 연결하는 안전 어댑터. **본 stage 에서
운영 callback 연결 금지 — 기본값 dry-run 이며 sandbox 명시 flag 없이는 INSERT 0회.**

원칙 (사용자 directive Stage 6.0):
- 기본값 dry_run=True / allow_insert=False — 어떤 호출도 INSERT 0회
- 운영 callback path 변경 0회 (`apps.domains.ai.callbacks._handle_matchup_*` 미import)
- selected_problem_ids 변경 0회 (proposal_helpers.create_proposal 가 미접근 보장)
- MatchupProblem 수정/생성 0회 (proposal 만 INSERT, 승격은 별도 path)
- manual=true 문제 재자르기 0회
- approved/confirmed 자동 생성 0회 (status='approved' payload 는 INSERT 차단)
- strict allowlist 운영 ON 0회
- OCR/VLM 실 호출 0회
- R2 write 0회
- production tenant 대상 bulk INSERT 0회 (sandbox_tenant_ids gate 필수)

INSERT 가능 조건 (모두 AND):
1. dry_run=False
2. allow_insert=True
3. sandbox_tenant_ids 명시 + 비어있지 않음
4. 모든 payload.tenant_id ∈ sandbox_tenant_ids
5. len(payloads) ≤ max_payload_count
6. 각 payload 의 schema_ok AND field_ok
7. payload.status != "approved" (block_approved=True default)

위 조건 중 하나라도 불충족 → 전체 batch INSERT 차단 (`blocking_reason` 기록).

INSERT 시점에 `apps.domains.matchup.proposal_helpers.create_proposal` 호출 —
그 helper 가 manual_overlap 자동 검출 + status='rejected' 자동 처리.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional

from .mock_response_integrator import ProposalPayloadCandidate
from .proposal_payload_validator import (
    APPROVABLE_STATUSES, validate_payload,
)


SCHEMA_VERSION = "6.2A-insert-adapter-idempotent-1"

# 기본값 — 모두 보수적 (INSERT 차단 우선)
_DEFAULT_MAX_PAYLOAD_COUNT = 100
_DEFAULT_DRY_RUN = True
_DEFAULT_ALLOW_INSERT = False
_DEFAULT_IDEMPOTENT_CHECK = True   # Stage 6.2A — 같은 payload 재실행 시 duplicate INSERT 차단


# ── Stage 6.2A: idempotency key ───────────────────────────────────


def _idempotent_key(payload: ProposalPayloadCandidate) -> tuple:
    """payload 의 idempotent dedup key.

    `analysis_version_key` 가 비어있지 않으면 (tenant, doc, key, page, #) 5-튜플 사용.
    빈 값이면 fallback: (... , engine, bbox_round_4) 를 추가해 더 보수적 매칭.

    같은 payload 두 번 호출 시 같은 key 반환 → adapter 가 pre-INSERT 검증 시 매칭.
    """
    base = (
        int(payload.tenant_id),
        int(payload.document_id),
        str(payload.analysis_version_key or ""),
        int(payload.page_number),
        int(payload.detected_problem_number),
    )
    if payload.analysis_version_key:
        return base
    # fallback — analysis_version_key 없으면 engine + bbox round 추가
    bbox = payload.bbox or {}
    try:
        return base + (
            str(payload.engine or ""),
            round(float(bbox.get("x", 0.0)), 4),
            round(float(bbox.get("y", 0.0)), 4),
            round(float(bbox.get("w", 0.0)), 4),
            round(float(bbox.get("h", 0.0)), 4),
        )
    except (TypeError, ValueError):
        return base + (str(payload.engine or ""), 0.0, 0.0, 0.0, 0.0)


# ── 결과 schema ───────────────────────────────────────────────────


@dataclass
class InsertDecision:
    """단일 payload 의 INSERT 결정."""
    payload_index: int
    status: str            # 'inserted' | 'dry_run' | 'skipped_validation' |
                           # 'skipped_status_approved' | 'skipped_sandbox_gate' |
                           # 'skipped_overlimit' | 'skipped_blocking' |
                           # 'skipped_idempotent' (Stage 6.2A)
    reason: str
    payload_status: str    # payload.status (rejected 인 경우 status 유지 검증)
    inserted_proposal_id: Optional[int] = None    # 신규 INSERT 또는 idempotent 매칭 기존 id
    violations: list[dict] = field(default_factory=list)


@dataclass
class InsertSandboxResult:
    """batch INSERT 결과."""
    schema_version: str
    dry_run: bool
    allow_insert: bool
    sandbox_tenant_ids: list[int]
    total_payloads: int
    inserted_count: int = 0
    skipped_count: int = 0
    rejected_count: int = 0       # payload.status='rejected' 개수 (INSERT 여부 무관)
    dry_run_count: int = 0
    blocking_reason: Optional[str] = None
    decisions: list[InsertDecision] = field(default_factory=list)
    debug: dict = field(default_factory=dict)


# ── helper: payload → create_proposal kwargs ──────────────────────


def prepare_proposal_insert(
    payload: ProposalPayloadCandidate,
) -> dict[str, Any]:
    """payload → `proposal_helpers.create_proposal` 호출 kwargs.

    create_proposal signature 와 1:1 매핑:
        tenant_id, document_id, page_number, detected_problem_number,
        bbox, engine, model_version, confidence, image_key, raw_response,
        analysis_version_key, auto_status

    payload.status 는 auto_status 로 매핑 (manual_overlap 시 helper 가 'rejected' 강제).
    payload.validation_errors 는 helper 가 자체 생성 — 매핑 안 함.
    """
    return {
        "tenant_id": payload.tenant_id,
        "document_id": payload.document_id,
        "page_number": payload.page_number,
        "detected_problem_number": payload.detected_problem_number,
        "bbox": dict(payload.bbox),
        "engine": payload.engine,
        "model_version": payload.model_version,
        "confidence": payload.confidence,
        "image_key": payload.image_key,
        "raw_response": dict(payload.raw_response),
        "analysis_version_key": payload.analysis_version_key,
        "auto_status": payload.status,
    }


def validate_before_insert(
    payload: ProposalPayloadCandidate,
    *,
    block_approved: bool = True,
) -> tuple[bool, str, list[dict]]:
    """INSERT 직전 최종 검증.

    Returns: (ok, reason, violations_dicts)
    """
    # 1) approved 차단 (사용자 directive — auto-promotion 금지)
    if block_approved and payload.status == "approved":
        return (
            False, "status_approved_blocked",
            [{"field": "status", "code": "invalid_choice",
              "detail": "approved status auto-creation forbidden in adapter"}],
        )
    # 2) Stage 5.9 schema/field validation
    result = validate_payload(payload)
    if not result.schema_ok:
        return (
            False, "schema_violation",
            [asdict(v) for v in result.violations],
        )
    if not result.field_ok:
        return (
            False, "field_violation",
            [asdict(v) for v in result.violations],
        )
    return (True, "ok", [])


# ── batch INSERT 어댑터 ──────────────────────────────────────────


def _make_blocked_result(
    payloads: list[ProposalPayloadCandidate],
    *, dry_run: bool, allow_insert: bool,
    sandbox_tenant_ids: list[int],
    blocking_reason: str,
) -> InsertSandboxResult:
    """batch 전체 차단 시 반환 — 모든 decision = skipped_blocking."""
    decisions: list[InsertDecision] = []
    rejected = 0
    for i, p in enumerate(payloads):
        if p.status == "rejected":
            rejected += 1
        decisions.append(InsertDecision(
            payload_index=i, status="skipped_blocking",
            reason=blocking_reason,
            payload_status=p.status,
        ))
    return InsertSandboxResult(
        schema_version=SCHEMA_VERSION,
        dry_run=dry_run, allow_insert=allow_insert,
        sandbox_tenant_ids=list(sandbox_tenant_ids),
        total_payloads=len(payloads),
        inserted_count=0, skipped_count=len(payloads),
        rejected_count=rejected, dry_run_count=0,
        blocking_reason=blocking_reason,
        decisions=decisions,
    )


def insert_proposal_sandbox(
    payloads: Iterable[ProposalPayloadCandidate],
    *,
    dry_run: bool = _DEFAULT_DRY_RUN,
    allow_insert: bool = _DEFAULT_ALLOW_INSERT,
    sandbox_tenant_ids: Optional[Iterable[int]] = None,
    max_payload_count: int = _DEFAULT_MAX_PAYLOAD_COUNT,
    block_approved: bool = True,
    idempotent_check: bool = _DEFAULT_IDEMPOTENT_CHECK,
    existing_lookup_fn: Optional[Any] = None,
) -> InsertSandboxResult:
    """payload list 를 ProblemSegmentationProposal INSERT path 로 연결 (sandbox-gated).

    기본 동작 (dry_run=True OR allow_insert=False): INSERT 0회. 모든 decision=dry_run.

    INSERT 조건 (모두 AND):
        dry_run=False AND allow_insert=True AND sandbox_tenant_ids 비어있지 않음
        AND 모든 payload.tenant_id ∈ sandbox_tenant_ids AND
        len(payloads) ≤ max_payload_count AND 각 payload schema/field ok AND
        payload.status != 'approved'

    Stage 6.2A — idempotency guard:
        idempotent_check=True (default) 면 sandbox INSERT path 에서 payload 별로
        `_idempotent_key` 일치하는 기존 row 가 있는지 사전 SELECT. 있으면 INSERT 안 함
        (decision='skipped_idempotent', inserted_proposal_id=existing_id).

        existing_lookup_fn(key_tuple) -> Optional[int] callable 을 호출. None 이면
        기본 ORM lookup 사용 (lazy import). 호출자가 mock 으로 주입 가능.

    INSERT 시점:
        `apps.domains.matchup.proposal_helpers.create_proposal` 호출
        (manual_overlap 자동 검출 + status='rejected' 자동 처리).

    운영 callback 미연결 — `_handle_matchup_*` 호출 0회. selected_problem_ids 미접근.
    MatchupProblem 미생성 (승격은 별도 approve_proposal path).
    """
    payload_list = list(payloads)
    sandbox_list = list(sandbox_tenant_ids or [])

    # 1) dry_run=True OR allow_insert=False → 100% dry-run
    if dry_run or not allow_insert:
        decisions: list[InsertDecision] = []
        rejected = 0
        for i, p in enumerate(payload_list):
            if p.status == "rejected":
                rejected += 1
            ok, reason, violations = validate_before_insert(p, block_approved=block_approved)
            decisions.append(InsertDecision(
                payload_index=i,
                status="dry_run",
                reason=(
                    f"dry_run={dry_run} allow_insert={allow_insert} | "
                    f"validation={'ok' if ok else reason}"
                ),
                payload_status=p.status,
                violations=violations,
            ))
        return InsertSandboxResult(
            schema_version=SCHEMA_VERSION,
            dry_run=True, allow_insert=allow_insert,
            sandbox_tenant_ids=sandbox_list,
            total_payloads=len(payload_list),
            inserted_count=0, skipped_count=0,
            rejected_count=rejected,
            dry_run_count=len(payload_list),
            decisions=decisions,
            debug={"mode": "dry_run_path"},
        )

    # 2) allow_insert=True 진입 — sandbox gate 검증

    # 2-1) sandbox_tenant_ids 비어있으면 차단
    if not sandbox_list:
        return _make_blocked_result(
            payload_list, dry_run=False, allow_insert=True,
            sandbox_tenant_ids=sandbox_list,
            blocking_reason="no sandbox_tenant_ids provided — production INSERT forbidden",
        )

    sandbox_set = {int(t) for t in sandbox_list}

    # 2-2) 모든 payload tenant_id 가 sandbox 안인지
    out_of_sandbox = [
        i for i, p in enumerate(payload_list) if p.tenant_id not in sandbox_set
    ]
    if out_of_sandbox:
        return _make_blocked_result(
            payload_list, dry_run=False, allow_insert=True,
            sandbox_tenant_ids=sandbox_list,
            blocking_reason=(
                f"payload index {out_of_sandbox[:5]} tenant_id "
                f"not in sandbox_tenant_ids={sorted(sandbox_set)}"
            ),
        )

    # 2-3) bulk count cap
    if len(payload_list) > max_payload_count:
        return _make_blocked_result(
            payload_list, dry_run=False, allow_insert=True,
            sandbox_tenant_ids=sandbox_list,
            blocking_reason=(
                f"payload count {len(payload_list)} > max_payload_count {max_payload_count}"
            ),
        )

    # 3) per-payload INSERT (transaction.atomic 은 create_proposal 안에 이미 있음)
    # create_proposal 은 manual_overlap 검출 → status='rejected' 자동
    # Stage 6.2A — idempotent_check=True 면 sandbox lookup 진입 전에 dedup.
    decisions = []
    inserted = skipped = rejected = idempotent_skipped = 0
    create_proposal_fn = _import_create_proposal()
    if idempotent_check:
        lookup_fn = existing_lookup_fn or _default_existing_lookup()
    else:
        lookup_fn = None

    for i, p in enumerate(payload_list):
        if p.status == "rejected":
            rejected += 1
        ok, reason, violations = validate_before_insert(p, block_approved=block_approved)
        if not ok:
            decisions.append(InsertDecision(
                payload_index=i,
                status=("skipped_status_approved"
                        if reason == "status_approved_blocked"
                        else "skipped_validation"),
                reason=reason, payload_status=p.status,
                violations=violations,
            ))
            skipped += 1
            continue
        # Stage 6.2A — pre-INSERT idempotent check
        existing_id: Optional[int] = None
        if lookup_fn is not None:
            try:
                key = _idempotent_key(p)
                existing_id = lookup_fn(key)
            except Exception as exc:
                # lookup 실패 시 보수적 — INSERT 차단
                decisions.append(InsertDecision(
                    payload_index=i, status="skipped_validation",
                    reason=f"idempotent lookup failed: {type(exc).__name__}: {exc}",
                    payload_status=p.status,
                ))
                skipped += 1
                continue
        if existing_id is not None:
            decisions.append(InsertDecision(
                payload_index=i, status="skipped_idempotent",
                reason=f"existing proposal id={existing_id} matches idempotent key",
                payload_status=p.status,
                inserted_proposal_id=existing_id,
            ))
            idempotent_skipped += 1
            continue
        try:
            kwargs = prepare_proposal_insert(p)
            proposal = create_proposal_fn(**kwargs)
            decisions.append(InsertDecision(
                payload_index=i, status="inserted",
                reason="created via proposal_helpers.create_proposal",
                payload_status=p.status,
                inserted_proposal_id=getattr(proposal, "id", None),
            ))
            inserted += 1
        except Exception as exc:
            decisions.append(InsertDecision(
                payload_index=i, status="skipped_validation",
                reason=f"create_proposal raised: {type(exc).__name__}: {exc}",
                payload_status=p.status,
            ))
            skipped += 1

    return InsertSandboxResult(
        schema_version=SCHEMA_VERSION,
        dry_run=False, allow_insert=True,
        sandbox_tenant_ids=sandbox_list,
        total_payloads=len(payload_list),
        inserted_count=inserted, skipped_count=skipped + idempotent_skipped,
        rejected_count=rejected, dry_run_count=0,
        decisions=decisions,
        debug={
            "mode": "sandbox_insert_path",
            "idempotent_check": idempotent_check,
            "idempotent_skipped": idempotent_skipped,
        },
    )


def _import_create_proposal():
    """proposal_helpers.create_proposal 지연 import — 운영 callback 미import.

    이 함수는 `insert_proposal_sandbox` 가 sandbox INSERT path 로 진입한 경우만 호출.
    dry_run / allow_insert=False path 에선 import 0회.
    """
    from apps.domains.matchup.proposal_helpers import create_proposal
    return create_proposal


def _default_existing_lookup():
    """idempotent key → 기존 proposal id (Optional[int]) lookup callable.

    lazy ORM import — sandbox INSERT path 진입 시점만 호출.
    호출자가 mock 으로 주입 가능 (`existing_lookup_fn`).
    """
    def _lookup(key: tuple) -> Optional[int]:
        from apps.domains.matchup.models import ProblemSegmentationProposal
        if len(key) >= 5:
            tenant_id, document_id, version_key, page_number, problem_number = key[:5]
        else:
            return None
        qs = ProblemSegmentationProposal.objects.filter(
            tenant_id=tenant_id,
            document_id=document_id,
            analysis_version_key=version_key,
            page_number=page_number,
            detected_problem_number=problem_number,
        )
        if len(key) >= 10:
            # fallback path — engine + bbox 추가 매칭
            engine, bx, by, bw, bh = key[5:10]
            qs = qs.filter(engine=engine)
            for proposal in qs.only("id", "bbox"):
                bbox = proposal.bbox or {}
                try:
                    if (round(float(bbox.get("x", -1)), 4) == bx and
                            round(float(bbox.get("y", -1)), 4) == by and
                            round(float(bbox.get("w", -1)), 4) == bw and
                            round(float(bbox.get("h", -1)), 4) == bh):
                        return int(proposal.id)
                except (TypeError, ValueError):
                    continue
            return None
        existing_id = qs.values_list("id", flat=True).first()
        return int(existing_id) if existing_id is not None else None
    return _lookup


def result_to_dict(r: InsertSandboxResult) -> dict[str, Any]:
    return {
        "schema_version": r.schema_version,
        "dry_run": r.dry_run, "allow_insert": r.allow_insert,
        "sandbox_tenant_ids": r.sandbox_tenant_ids,
        "total_payloads": r.total_payloads,
        "inserted_count": r.inserted_count,
        "skipped_count": r.skipped_count,
        "rejected_count": r.rejected_count,
        "dry_run_count": r.dry_run_count,
        "blocking_reason": r.blocking_reason,
        "decisions": [asdict(d) for d in r.decisions],
        "debug": r.debug,
    }

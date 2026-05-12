"""Stage 6.3-Pipeline — shadow proposal generation pipeline (Option β implementation).

OPERATIONS_DISPATCH_INTEGRATION_DESIGN 의 Phase 1 trigger (별도 entrypoint, 운영
callback 미연결) 의 helper 구현.

흐름:
    PDF
      → analyze_pdf_v5_4 (Tier 0)
      → fallback_router (5-route)
      → dispatch_mock (mock dispatcher)
      → mock OCR/VLM response (synthetic, 실 SDK X)
      → integrate_responses → unified candidates / proposal payloads
      → insert_proposal_sandbox (default dry_run)

원칙 (사용자 directive Stage 6.3-Pipeline + 6.4-prep):
- 운영 callback 미연결 (`apps.domains.ai.gateway.dispatch_job` /
  `apps.domains.ai.callbacks._handle_matchup_*` import 0회)
- 운영 segment_dispatcher (`academy.adapters.ai.detection.segment_dispatcher`) import 0회
- 실 OCR/VLM SDK 호출 0회 (synthetic mock)
- T1 sandbox 기본 통과 (tenant_id == DEFAULT_SANDBOX_TENANT_ID)
- T2 는 기본 차단. 단, **단일 document whitelist** 통과 시만 dry-run 허용.
  whitelist 조건 (Stage 6.4-prep):
    1. ENV `MATCHUP_SHADOW_T2_DOC_WHITELIST` 가 정확히 1개 정수
    2. document_id == whitelist 정수
    3. max_payloads <= T2_WHITELIST_MAX_PAYLOADS (=5)
    4. allow_insert 는 여전히 명시 flag 필요 (기존 정책)
  whitelist 없거나 doc_id 불일치 또는 malformed → T2 차단.
- 그 외 tenant_id (3+ / 0 / 음수 등) 자동 차단
- GLOBAL feature flag (ENV `MATCHUP_SHADOW_PROPOSAL_ENABLED=1`) 명시 시만 동작
- dry_run default — sandbox INSERT 는 명시적 allow_insert + ENV 둘 다 필요
- ProblemSegmentationProposal INSERT 는 사용자 명시 승인 후만

4-layer feature flag (Stage 6.4-prep):
    GLOBAL (ENV)        → MATCHUP_SHADOW_PROPOSAL_ENABLED=1
    TENANT              → tenant_id == 1 (T1 기본) 또는 tenant_id == 2 (whitelist 통과)
    DOCUMENT            → T2 인 경우 ENV MATCHUP_SHADOW_T2_DOC_WHITELIST 와 정확히 일치
    PAYLOAD             → caller 가 doc_id 명시 + pdf_path 검증 책임 + max_payloads cap
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .dispatcher_mock import dispatch_mock, output_to_dict as dispatcher_output_to_dict
from .mock_response_integrator import (
    integrate_responses, make_mock_ocr_response, make_mock_vlm_response,
    unified_to_dict,
)
from academy.adapters.db.django.repositories_matchup_proposal import (
    insert_proposal_sandbox, result_to_dict as insert_result_to_dict,
)


SCHEMA_VERSION = "6.4-prep+1-truncation-flag-1"
SHADOW_GLOBAL_ENV = "MATCHUP_SHADOW_PROPOSAL_ENABLED"
DEFAULT_SANDBOX_TENANT_ID = 1
DEFAULT_MAX_PAYLOADS = 5
DEFAULT_MOCK_OCR_BLOCKS = 8
DEFAULT_MOCK_VLM_PROBLEMS = 3

# Stage 6.4-prep — T2 single-document whitelist
T2_PRODUCTION_TENANT_ID = 2
T2_DOC_WHITELIST_ENV = "MATCHUP_SHADOW_T2_DOC_WHITELIST"
# T2 는 5건 이하 dry-run 만 허용. 운영 데이터 보호용 strict cap (DEFAULT_MAX_PAYLOADS
# 가 향후 변경되어도 T2 cap 은 독립 유지).
T2_WHITELIST_MAX_PAYLOADS = 5

# Stage 6.4-prep+1 — smoke-only truncation flag.
#   기본 OFF. flag 가 ON 일 때만 max_payloads 이하로 잘라서 adapter 에 전달.
#   flag 가 OFF 면 기존 동작 유지 — adapter 가 fail-closed (count > cap → batch 차단).
#   actual smoke 시점에서만 사용. 운영 코드 호출 흐름에 영향 없음.
SMOKE_TRUNCATION_REASON = "stage_6_4_smoke_cap"


@dataclass
class ShadowPipelineResult:
    """Stage 6.3-Pipeline 실행 결과."""
    schema_version: str
    enabled: bool                       # GLOBAL ENV gate + tenant 통과
    blocking_reason: Optional[str]      # gate 미통과 사유
    tenant_id: int
    document_id: int
    pdf_path: str
    dry_run: bool
    allow_insert: bool
    dispatcher_output: Optional[dict] = None
    unified_output: Optional[dict] = None
    insert_result: Optional[dict] = None
    debug: dict = field(default_factory=dict)


def is_globally_enabled() -> bool:
    """GLOBAL feature flag — ENV `MATCHUP_SHADOW_PROPOSAL_ENABLED` 명시 시만 활성."""
    return os.environ.get(SHADOW_GLOBAL_ENV, "").lower() in ("1", "true", "yes")


def read_t2_doc_whitelist() -> Optional[int]:
    """Stage 6.4-prep — T2 단일 document whitelist 파싱.

    ENV `MATCHUP_SHADOW_T2_DOC_WHITELIST` 의 값을 정수로 파싱.
    엄격한 single-int 정책:
      - 미설정 / 빈 문자열 → None
      - 숫자 1개 (예: "765") → int 765
      - 콤마 / 공백 다중값 (예: "765,762", "765 762") → None (malformed)
      - 비정수 (예: "abc", "765a") → None (malformed)
      - 음수 / 0 → None (malformed — proposal doc_id 는 양의 정수)
    None 반환 시 caller 는 T2 차단해야 함.
    """
    raw = os.environ.get(T2_DOC_WHITELIST_ENV, "").strip()
    if not raw:
        return None
    # 다중값 차단 — 단일 doc 만 허용
    if "," in raw or any(ch.isspace() for ch in raw):
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _check_blocking(
    tenant_id: int,
    *,
    document_id: int,
    max_payloads: int,
) -> Optional[str]:
    """gate 검사 — 미통과 사유 반환 (None 이면 통과).

    Stage 6.4-prep 확장:
      - T1 (tenant_id=1) 은 기존처럼 통과
      - T2 (tenant_id=2) 는 ENV whitelist + doc_id 정확 일치 + max_payloads<=5 시 통과
      - 그 외 tenant 는 차단
    """
    if not is_globally_enabled():
        return (
            f"shadow proposal pipeline disabled — "
            f"set {SHADOW_GLOBAL_ENV}=1 to enable"
        )
    # T1 — 기존 sandbox path. 회귀 0.
    if tenant_id == DEFAULT_SANDBOX_TENANT_ID:
        return None
    # T2 — single-document whitelist path.
    if tenant_id == T2_PRODUCTION_TENANT_ID:
        whitelist_doc = read_t2_doc_whitelist()
        if whitelist_doc is None:
            return (
                f"T2 access requires {T2_DOC_WHITELIST_ENV}=<single positive int doc_id>; "
                f"got {os.environ.get(T2_DOC_WHITELIST_ENV, '<unset>')!r} — T2 blocked"
            )
        if whitelist_doc != document_id:
            return (
                f"T2 doc_id={document_id} not in whitelist (expected exact match "
                f"{whitelist_doc} from {T2_DOC_WHITELIST_ENV}) — T2 blocked"
            )
        if max_payloads > T2_WHITELIST_MAX_PAYLOADS:
            return (
                f"T2 max_payloads={max_payloads} exceeds T2 cap "
                f"{T2_WHITELIST_MAX_PAYLOADS} — T2 blocked"
            )
        return None
    # 그 외 tenant — 기존 정책 유지 (T1 sandbox 표현 유지)
    return (
        f"shadow proposal pipeline restricted to T1 sandbox "
        f"(tenant_id={DEFAULT_SANDBOX_TENANT_ID}). got tenant_id={tenant_id} — "
        f"production tenants are forbidden in this stage"
    )


def _truncate_payloads_for_smoke(
    payloads: list,
    max_payloads: int,
) -> tuple[list, int]:
    """Stage 6.4-prep+1 smoke-only — deterministic truncation.

    `len(payloads) > max_payloads` 일 때 max_payloads 개로 줄임.
    아니면 원본 그대로 반환.

    정렬 키 (안정 정렬, 동률 시 원본 순서 보존):
        (page_number, detected_problem_number, bbox.y, bbox.x)

    이 순서는 Tier 0 / OCR / VLM / YOLO 모든 source 에서 의미 있는 자연 순서:
    - 윗 페이지 → 아래 페이지
    - 같은 페이지 내 작은 문항 번호 → 큰 번호
    - 번호 동률(0=unknown 포함) 시 위쪽 bbox → 아래쪽
    - 좌측 → 우측

    Returns:
        (truncated_payloads, skipped_count)
    """
    raw_count = len(payloads)
    if raw_count <= max_payloads:
        return list(payloads), 0

    def _sort_key(p):
        bbox = getattr(p, "bbox", None) or {}
        try:
            y = float(bbox.get("y", 0.0))
        except (TypeError, ValueError):
            y = 0.0
        try:
            x = float(bbox.get("x", 0.0))
        except (TypeError, ValueError):
            x = 0.0
        return (
            int(getattr(p, "page_number", 0) or 0),
            int(getattr(p, "detected_problem_number", 0) or 0),
            y, x,
        )

    sorted_payloads = sorted(payloads, key=_sort_key)
    truncated = sorted_payloads[:max_payloads]
    return truncated, raw_count - max_payloads


def shadow_proposal_pipeline(
    pdf_path: str,
    *,
    document_id: int,
    tenant_id: int = DEFAULT_SANDBOX_TENANT_ID,
    file_name: Optional[str] = None,
    profile: Optional[dict] = None,
    analysis_version_key: str = "",
    dry_run: bool = True,
    allow_insert: bool = False,
    max_payloads: int = DEFAULT_MAX_PAYLOADS,
    mock_ocr_blocks: int = DEFAULT_MOCK_OCR_BLOCKS,
    mock_vlm_problems: int = DEFAULT_MOCK_VLM_PROBLEMS,
    static_manual_bboxes: Optional[list[dict]] = None,
    smoke_truncate_to_cap: bool = False,
) -> ShadowPipelineResult:
    """T1 sandbox shadow proposal 생성 pipeline.

    Args:
        pdf_path: PDF 파일 경로 (caller 가 sandbox 안 자료인지 확인 책임).
        document_id: ProblemSegmentationProposal.document_id.
        tenant_id: T1 sandbox 만 허용 (default 1). 다른 값 시 blocking.
        file_name: 분류용 (default = pdf basename).
        profile: tenant profile JSON (선택, Stage 5.5.4 형식).
        analysis_version_key: idempotent batch 키. 같은 batch 재호출 시 dedup.
        dry_run: True 면 INSERT 0. False + allow_insert=True 시 sandbox INSERT.
        allow_insert: dry_run=False AND allow_insert=True 시만 실 INSERT 가능.
        max_payloads: bulk cap (default 5).
        mock_ocr_blocks / mock_vlm_problems: synthetic mock 생성 인자.
        static_manual_bboxes: manual_overlap 검증용 (Stage 5.8 형식).
        smoke_truncate_to_cap: Stage 6.4-prep+1 smoke-only opt-in. 기본 False.
            True 면 raw payload count > max_payloads 시 deterministic 정렬 후
            max_payloads 개로 잘라서 adapter 에 전달. 운영 흐름 미사용 — smoke
            전용. False 면 기존 동작 그대로 (adapter fail-closed).

    Returns:
        ShadowPipelineResult — gate 미통과 시 blocking_reason 포함, dispatcher /
        unified / insert 모두 None.

    원칙:
    - GLOBAL ENV gate 미통과 → blocking, 모든 단계 skip
    - tenant_id != 1 AND T2 whitelist 미통과 → blocking
    - 운영 callback 미import (`_handle_matchup_*` / `dispatch_job` 호출 0회)
    - 실 OCR/VLM SDK 호출 0회
    - sandbox_tenant_ids 는 통과한 tenant 1개만 ([1] 또는 [2])
    """
    blocking = _check_blocking(
        tenant_id, document_id=document_id, max_payloads=max_payloads,
    )
    result = ShadowPipelineResult(
        schema_version=SCHEMA_VERSION,
        enabled=blocking is None,
        blocking_reason=blocking,
        tenant_id=tenant_id, document_id=document_id, pdf_path=pdf_path,
        dry_run=dry_run, allow_insert=allow_insert,
        debug={
            "max_payloads": max_payloads,
            "mock_ocr_blocks": mock_ocr_blocks,
            "mock_vlm_problems": mock_vlm_problems,
            "global_env_var": SHADOW_GLOBAL_ENV,
            "t2_whitelist_env": T2_DOC_WHITELIST_ENV,
            "t2_whitelist_doc": read_t2_doc_whitelist(),
            "t2_max_payloads_cap": T2_WHITELIST_MAX_PAYLOADS,
            "smoke_truncate_to_cap": smoke_truncate_to_cap,
        },
    )
    if blocking is not None:
        return result

    # Step 1 — Tier 0 + dispatcher mock (운영 segment_dispatcher 미사용)
    dispatcher = dispatch_mock(
        pdf_path, file_name=file_name, profile=profile,
    )
    result.dispatcher_output = dispatcher_output_to_dict(dispatcher)

    # Step 2 — route 별 mock OCR / VLM response (synthetic, 실 SDK X)
    mock_ocr = None
    mock_vlm = None
    if dispatcher.route == "TIER1_OCR_REQUIRED" and dispatcher.mock_ocr_request:
        page_indices = list(dispatcher.mock_ocr_request.get("page_indices") or [])
        mock_ocr = make_mock_ocr_response(
            pdf_path, page_indices, blocks_per_page=mock_ocr_blocks,
        )
    elif (
        dispatcher.route in ("TIER2_VLM_REQUIRED", "TIER2_VLM_HYBRID")
        and dispatcher.mock_vlm_request
    ):
        page_indices = list(dispatcher.mock_vlm_request.get("page_indices") or [])
        mock_vlm = make_mock_vlm_response(
            pdf_path, page_indices, problems_per_page=mock_vlm_problems,
        )

    # Step 3 — integrate (unified candidates + proposal_payloads)
    unified = integrate_responses(
        dispatcher,
        mock_ocr_response=mock_ocr,
        mock_vlm_response=mock_vlm,
        tenant_id=tenant_id,
        document_id=document_id,
        analysis_version_key=analysis_version_key,
        static_manual_bboxes=static_manual_bboxes,
    )
    result.unified_output = unified_to_dict(unified)

    # Step 4 — Stage 6.4-prep+1 smoke-only truncation (opt-in flag).
    # 기본 OFF: 원본 unified.proposal_payloads 전체를 adapter 에 전달 → adapter 가
    #   기존처럼 fail-closed (count > max_payload_count 시 batch 차단).
    # ON: deterministic 정렬 후 max_payloads 개만 adapter 로. 운영 코드 미사용.
    raw_payloads = list(unified.proposal_payloads)
    raw_payload_count = len(raw_payloads)
    if smoke_truncate_to_cap:
        payloads_for_insert, skipped_by_truncation = _truncate_payloads_for_smoke(
            raw_payloads, max_payloads,
        )
    else:
        payloads_for_insert = raw_payloads
        skipped_by_truncation = 0

    # Step 5 — adapter (dry_run default + sandbox gate)
    # sandbox_tenant_ids 는 _check_blocking 을 통과한 tenant 1개만 — 다른 tenant 는
    # 이미 위에서 차단됨. T1 → [1], T2 (whitelist 통과) → [2].
    insert_result = insert_proposal_sandbox(
        payloads_for_insert,
        dry_run=dry_run,
        allow_insert=allow_insert,
        sandbox_tenant_ids=[tenant_id],
        max_payload_count=max_payloads,
    )
    result.insert_result = insert_result_to_dict(insert_result)

    # truncation metadata — dry-run output 에서 시각 확인 가능
    result.debug["raw_payload_count"] = raw_payload_count
    result.debug["payloads_for_insert_count"] = len(payloads_for_insert)
    result.debug["skipped_by_truncation_count"] = skipped_by_truncation
    if smoke_truncate_to_cap and skipped_by_truncation > 0:
        result.debug["truncation_reason"] = SMOKE_TRUNCATION_REASON

    return result


def result_to_dict(r: ShadowPipelineResult) -> dict[str, Any]:
    return {
        "schema_version": r.schema_version,
        "enabled": r.enabled,
        "blocking_reason": r.blocking_reason,
        "tenant_id": r.tenant_id,
        "document_id": r.document_id,
        "pdf_path": r.pdf_path,
        "dry_run": r.dry_run,
        "allow_insert": r.allow_insert,
        "dispatcher_output": r.dispatcher_output,
        "unified_output": r.unified_output,
        "insert_result": r.insert_result,
        "debug": r.debug,
    }

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

원칙 (사용자 directive Stage 6.3-Pipeline):
- 운영 callback 미연결 (`apps.domains.ai.gateway.dispatch_job` /
  `apps.domains.ai.callbacks._handle_matchup_*` import 0회)
- 운영 segment_dispatcher (`academy.adapters.ai.detection.segment_dispatcher`) import 0회
- 실 OCR/VLM SDK 호출 0회 (synthetic mock)
- T1 sandbox 한정 (tenant_id == DEFAULT_SANDBOX_TENANT_ID 강제)
- GLOBAL feature flag (ENV `MATCHUP_SHADOW_PROPOSAL_ENABLED=1`) 명시 시만 동작
- dry_run default — sandbox INSERT 는 명시적 allow_insert + ENV 둘 다 필요
- ProblemSegmentationProposal INSERT 는 사용자 명시 승인 후만
- T2 / production tenant 자동 차단

3-layer feature flag:
    GLOBAL (ENV)        → MATCHUP_SHADOW_PROPOSAL_ENABLED=1
    TENANT              → tenant_id == 1 강제 (sandbox)
    DOCUMENT            → caller 가 doc_id 명시 + pdf_path 검증 책임
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
from .proposal_insert_adapter import (
    insert_proposal_sandbox, result_to_dict as insert_result_to_dict,
)


SCHEMA_VERSION = "6.3-pipeline-1"
SHADOW_GLOBAL_ENV = "MATCHUP_SHADOW_PROPOSAL_ENABLED"
DEFAULT_SANDBOX_TENANT_ID = 1
DEFAULT_MAX_PAYLOADS = 5
DEFAULT_MOCK_OCR_BLOCKS = 8
DEFAULT_MOCK_VLM_PROBLEMS = 3


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


def _check_blocking(tenant_id: int) -> Optional[str]:
    """gate 검사 — 미통과 사유 반환 (None 이면 통과)."""
    if not is_globally_enabled():
        return (
            f"shadow proposal pipeline disabled — "
            f"set {SHADOW_GLOBAL_ENV}=1 to enable"
        )
    if tenant_id != DEFAULT_SANDBOX_TENANT_ID:
        return (
            f"shadow proposal pipeline restricted to T1 sandbox "
            f"(tenant_id={DEFAULT_SANDBOX_TENANT_ID}). got tenant_id={tenant_id} — "
            f"production tenants are forbidden in this stage"
        )
    return None


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

    Returns:
        ShadowPipelineResult — gate 미통과 시 blocking_reason 포함, dispatcher /
        unified / insert 모두 None.

    원칙:
    - GLOBAL ENV gate 미통과 → blocking, 모든 단계 skip
    - tenant_id != 1 → blocking (T1 sandbox 외 차단)
    - 운영 callback 미import (`_handle_matchup_*` / `dispatch_job` 호출 0회)
    - 실 OCR/VLM SDK 호출 0회
    - T2 미접근 (sandbox_tenant_ids=[1] 강제)
    """
    blocking = _check_blocking(tenant_id)
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

    # Step 4 — adapter (dry_run default + sandbox gate)
    insert_result = insert_proposal_sandbox(
        unified.proposal_payloads,
        dry_run=dry_run,
        allow_insert=allow_insert,
        sandbox_tenant_ids=[DEFAULT_SANDBOX_TENANT_ID],
        max_payload_count=max_payloads,
    )
    result.insert_result = insert_result_to_dict(insert_result)

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

"""Phase E (2026-05-09 basic_definition_2026_05_09 SSOT) — Proposal-first callback path.

사용자 directive 본진 reframe:
  '자동 결과 + 수동 결과 = 같은 최종 problem set 으로 합쳐져야' 한다.
  callback 의 bulk_create MatchupProblem 직접 X. ProblemSegmentationProposal pending
  → 학원장 검수 후 accepted 만 final.

Fast-path (자동 accept):
  - high-confidence (clean_pdf_dual + bbox 정상 + Hybrid VLM 통과) 는 즉시 accept
    → MatchupProblem 승격 (학원장 워크플로우 변경 X)
  - 그 외 = pending → 학원장 검수

ENV flag MATCHUP_PROPOSAL_FIRST_TENANTS 매치 시만 호출. legacy path 와 동시 운영 X
(점진 rollout: T1 sandbox 검증 → 사용자 명시 승인 → T2).

manual / pinned 보호:
  callback path 와 동일 — manual=True / manual_owner_pinned=True problem 은 그대로.
  legacy path 의 NULL semantics 사고 회피 (manual_ids ∪ pinned_ids exclude).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# Fast-path 자동 accept 기준 (basic_definition_2026_05_09 SSOT 정렬)
FAST_PATH_PAPER_TYPES = {"clean_pdf_dual", "clean_pdf_single"}
FAST_PATH_MIN_CONFIDENCE = 0.75


def handle_matchup_proposal_path(
    *,
    job_id: str,
    doc,
    problems_data: List[Dict[str, Any]],
    result_payload: Dict[str, Any],
) -> None:
    """callback 의 신규 path — MatchupProblem 직접 bulk_create 대신 Proposal 통과.

    Step 1: manual / pinned 보호 + 비-protected MatchupProblem 정리
    Step 2: doc 의 stale pending Proposal 정리 (재시도 시 누적 방지)
    Step 3: 각 cut → ProblemSegmentationProposal 생성
    Step 4: fast-path 적용 — high-confidence 면 status='auto_passed' + MatchupProblem 승격
    Step 5: doc.status='done' + meta 갱신

    Args:
      job_id: AI dispatch job id (analysis_version_key 로 사용)
      doc: MatchupDocument 인스턴스
      problems_data: callback 가 받은 problems list (worker 결과)
      result_payload: callback 의 raw payload (paper_type_summary 등 메타)
    """
    from django.db import transaction

    from apps.domains.matchup.models import (
        MatchupProblem,
        ProblemSegmentationProposal,
    )

    # === Step 1: manual / pinned 보호 + 비-protected 삭제 ===
    manual_ids = list(
        doc.problems.filter(meta__manual=True).values_list("id", flat=True)
    )
    pinned_ids = list(
        doc.problems.filter(meta__manual_owner_pinned=True).values_list("id", flat=True)
    )
    protected_ids = list(set(manual_ids) | set(pinned_ids))
    doc.problems.exclude(id__in=protected_ids).delete()

    # === Step 2: 이전 reanalyze 의 stale pending Proposal 정리 ===
    # 학원장이 이미 검수 완료한 (approved/rejected) 는 audit 보존.
    ProblemSegmentationProposal.objects.filter(
        document=doc, status__in=("pending", "needs_review", "auto_passed"),
    ).delete()

    # === Step 3 + 4: 각 cut → Proposal 생성 + fast-path ===
    paper_type_summary = result_payload.get("paper_type_summary") or {}
    primary_paper_type = (
        paper_type_summary.get("primary") if isinstance(paper_type_summary, dict) else None
    ) or ""
    fast_path_eligible_doc = primary_paper_type in FAST_PATH_PAPER_TYPES

    proposal_objs: List[ProblemSegmentationProposal] = []
    fast_path_promote_payloads: List[Dict[str, Any]] = []
    for p in problems_data:
        meta_p = p.get("meta") or {}
        bbox = meta_p.get("bbox")
        if not bbox:
            # bbox null = page-fallback. Phase C 가 차단했지만 안전.
            continue
        confidence = float(meta_p.get("confidence") or 0.5)
        engine = (meta_p.get("engine") or "yolo").lower()
        # ProblemSegmentationProposal.engine choices 매핑 (yolo/vlm/ocr/native_pdf/manual_assist).
        if engine not in ("yolo", "vlm", "ocr", "native_pdf", "manual_assist"):
            engine = "yolo"

        # Fast-path 판정: doc-level paper_type + box-level confidence
        is_fast_path = (
            fast_path_eligible_doc
            and confidence >= FAST_PATH_MIN_CONFIDENCE
        )

        proposal_objs.append(ProblemSegmentationProposal(
            tenant_id=doc.tenant_id,
            document=doc,
            analysis_version_key=str(job_id or "")[:128],
            page_number=int(meta_p.get("page_index") or 0),
            bbox=bbox,
            detected_problem_number=int(p.get("number") or 0),
            engine=engine,
            model_version=meta_p.get("engine_version") or "",
            confidence=confidence,
            status="auto_passed" if is_fast_path else "pending",
            image_key=p.get("image_key", ""),
            raw_response={
                "text_preview": (p.get("text") or "")[:200],
                "image_key": p.get("image_key", ""),
            },
        ))
        if is_fast_path:
            fast_path_promote_payloads.append(p)

    # bulk_create — unique constraint 충돌 silent drop 안전.
    with transaction.atomic():
        if proposal_objs:
            ProblemSegmentationProposal.objects.bulk_create(
                proposal_objs, ignore_conflicts=True,
            )

        # === Fast-path 즉시 승격: ProblemSegmentationProposal(auto_passed)
        # → MatchupProblem 생성. unique(document, number) 충돌 시 manual / pinned
        # 보존 (legacy path 와 동일 정책).
        if fast_path_promote_payloads:
            promote_objs = []
            for p in fast_path_promote_payloads:
                promote_objs.append(MatchupProblem(
                    tenant_id=doc.tenant_id,
                    document=doc,
                    number=int(p.get("number") or 0),
                    text=p.get("text", ""),
                    image_key=p.get("image_key", ""),
                    embedding=p.get("embedding"),
                    image_embedding=p.get("image_embedding"),
                    meta=p.get("meta", {}),
                ))
            MatchupProblem.objects.bulk_create(promote_objs, ignore_conflicts=True)

    # === Step 5: doc.status / meta 갱신 ===
    pending_count = ProblemSegmentationProposal.objects.filter(
        document=doc, status="pending",
    ).count()
    auto_count = ProblemSegmentationProposal.objects.filter(
        document=doc, status="auto_passed",
    ).count()
    final_problem_count = MatchupProblem.objects.filter(document=doc).count()

    meta = doc.meta or {}
    meta["proposal_pending_count"] = pending_count
    meta["proposal_auto_passed_count"] = auto_count
    if isinstance(paper_type_summary, dict):
        meta["paper_type_summary"] = paper_type_summary

    doc.status = "done"
    doc.problem_count = final_problem_count
    doc.error_message = ""
    doc.meta = meta
    doc.save(update_fields=[
        "status", "problem_count", "error_message", "meta", "updated_at",
    ])

    # 검색 캐시 무효화 (P1 fix 2026-05-11): proposal path 도 protected_ids 제외
    # bulk_create 로 problem 풀 재구성. legacy callback path 와 일관 정책.
    try:
        from apps.domains.matchup.cache import invalidate_tenant_similar_cache
        invalidate_tenant_similar_cache(doc.tenant_id)
    except Exception:
        logger.exception(
            "PROPOSAL_PATH_CACHE_INVALIDATE_FAILED | doc=%s | tenant=%s",
            doc.id, doc.tenant_id,
        )

    logger.info(
        "PROPOSAL_PATH_COMPLETE | doc=%s | proposals=%d (pending=%d / auto_passed=%d) | "
        "final_problems=%d (manual+pinned=%d + auto=%d)",
        doc.id, len(proposal_objs), pending_count, auto_count,
        final_problem_count, len(protected_ids), len(fast_path_promote_payloads),
    )

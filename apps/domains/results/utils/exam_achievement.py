# apps/domains/results/utils/exam_achievement.py
"""
시험 성취(achievement) 계산 SSOT.

정책 (SSOT):
- is_pass: 1차 시험만 기준 (석차 계산용). 미채점 또는 pass_score=0이면 None.
- remediated: 1차 합격이 없고 ClinicLink(source_type=exam, resolution_type∈{EXAM_PASS, MANUAL_OVERRIDE})로 해소.
  (WAIVED=면제는 remediated=False; 성취로 인정하지 않음)
- final_pass: 1차 합격 OR remediated. 미응시 + 미해소 → None.
- achievement: "PASS" | "REMEDIATED" | "FAIL" | "NOT_SUBMITTED" | None
  · PASS: 1차 합격
  · REMEDIATED: 1차 불합격 + 해소(EXAM_PASS/MANUAL_OVERRIDE)
  · FAIL: 1차 불합격 + 미해소
  · NOT_SUBMITTED: 미응시(+미해소)
  · None: pass_score=0 등 판정 기준 없음
- is_provisional: legacy FINAL 또는 완전 자동 채점된 ONLINE 완료 근거가 없으면 미확정

여러 뷰에서 공통 사용해 드리프트 재발 방지.
"""
from __future__ import annotations

from typing import Any

from apps.domains.results.models import ExamResult, ExamAttempt, Result
from apps.domains.results.services.omr_subjective_completion import (
    pending_omr_result_ids,
)
from apps.domains.results.services.online_grading_completion import completed_online_result_pairs
from apps.domains.results.utils.initial_exam_score import (
    load_initial_exam_scores,
    project_initial_exam_score,
)
from apps.support.results.progress_read_dependencies import (
    exam_remediation_link_values,
)
from apps.support.progress.assessment_correction_dependencies import (
    stale_teacher_exam_resolution_link_ids,
)


def compute_first_pass(
    *,
    total_score: float | None,
    pass_score: float | None,
    is_not_submitted: bool,
) -> bool | None:
    """1차 합격 판정. 석차/성취 분리용 단일 진실."""
    if is_not_submitted or total_score is None:
        return None
    if pass_score is None or float(pass_score) <= 0:
        return None
    return float(total_score) >= float(pass_score)


def compute_final_pass(
    *,
    is_pass: bool | None,
    remediated: bool,
    is_not_submitted: bool,
) -> bool | None:
    """최종 합격 = 1차 합격 OR 클리닉 해소. 미응시+미해소 → None."""
    if is_not_submitted and not remediated:
        return None
    if is_pass is True or remediated:
        return True
    if is_pass is False:
        return False
    return None


def compute_achievement(
    *,
    is_pass: bool | None,
    remediated: bool,
    is_not_submitted: bool,
) -> str | None:
    """
    성취 분류. 프론트 뱃지/통계 공용 라벨.

    우선순위: PASS > REMEDIATED > NOT_SUBMITTED > FAIL
    """
    if is_pass is True:
        return "PASS"
    if remediated:
        return "REMEDIATED"
    if is_not_submitted:
        return "NOT_SUBMITTED"
    if is_pass is False:
        return "FAIL"
    return None


def compute_exam_achievement(
    *,
    enrollment_id: int,
    exam_id: int,
    session,
    total_score: float | None,
    pass_score: float | None,
    attempt_id: int | None = None,
    meta_status: str | None = None,
    tenant: Any,
) -> dict[str, Any]:
    """
    시험 하나에 대해 학생의 성취 상태를 통합 계산.

    student_result_service와 admin_exam_results_view가 공통 사용.
    입력:
      - enrollment_id, exam_id: 대상 학생/시험
      - session: 해당 시험의 대표 session (없으면 None — 클리닉 판정 스킵)
      - total_score, pass_score: Result / Exam 기반 점수
      - attempt_id: 대표 attempt (is_provisional / meta_status 판정용)
      - meta_status: 정확한 attempt에 상태가 없을 때만 쓰는 명시적 fallback
      - tenant: attempt/submission/clinic 관계를 검증할 필수 tenant

    반환: {
        is_pass, remediated, final_pass, clinic_retake, achievement,
        is_provisional, meta_status
    }
    """
    item = {
        "enrollment_id": enrollment_id,
        "exam_id": exam_id,
        "total_score": total_score,
        "pass_score": pass_score,
        "attempt_id": attempt_id,
        "session_id": session.id if session is not None else None,
        "meta_status": meta_status,
    }
    return compute_exam_achievement_bulk(
        items=[item],
        tenant=tenant,
    )[(int(enrollment_id), int(exam_id))]


def _build_clinic_retake_info(link_or_dict) -> dict[str, Any]:
    """ClinicLink 인스턴스 or values() dict 에서 clinic_retake info 생성."""
    if hasattr(link_or_dict, "resolution_evidence"):
        evidence = link_or_dict.resolution_evidence or {}
        resolved_at = link_or_dict.resolved_at
        resolution_type = link_or_dict.resolution_type
    else:
        evidence = link_or_dict.get("resolution_evidence") or {}
        resolved_at = link_or_dict.get("resolved_at")
        resolution_type = link_or_dict.get("resolution_type")
    return {
        "score": evidence.get("score"),
        "pass_score": evidence.get("pass_score"),
        "attempt_id": evidence.get("attempt_id"),
        "resolution_type": resolution_type,
        "resolved_at": resolved_at.isoformat() if resolved_at else None,
    }


def compute_exam_achievement_bulk(
    *,
    items: list[dict[str, Any]],
    tenant: Any,
    scoring_details_loader=None,
    include_first_attempt_readiness: bool = False,
) -> dict[tuple[int, int], dict[str, Any]]:
    """
    여러 (enrollment, exam) 쌍의 성취를 한 번에 계산 (N+1 방지).

    입력 (items 각각):
      {
        "enrollment_id": int,
        "exam_id": int,
        "total_score": float | None,
        "pass_score": float | None,
        "attempt_id": int | None,
        "session_id": int | None,
        "meta_status": str | None,  # 선택: 유효한 시도에 상태가 없을 때의 fallback
      }

    보강은 같은 수강·시험·차시의 현재 회차만 인정한다. 차시가 확정되지
    않으면 원점수 판정만 반환한다.

    반환: { (enrollment_id, exam_id): achievement_data }
    compute_exam_achievement 와 동일한 키 셋 (is_pass/remediated/final_pass/
    clinic_retake/achievement/is_provisional/meta_status).

    쿼리 수: items 수와 무관하게 ClinicLink/ExamAttempt/ExamResult 각 1회 (최대 3개).
    """
    if not items:
        return {}

    enrollment_ids = {int(i["enrollment_id"]) for i in items}
    exam_ids = {int(i["exam_id"]) for i in items}
    attempt_ids = {int(i["attempt_id"]) for i in items if i.get("attempt_id")}
    session_ids = {int(i["session_id"]) for i in items if i.get("session_id") is not None}
    initial_scores = load_initial_exam_scores(
        exam_ids=exam_ids,
        enrollment_ids=enrollment_ids,
    )
    attempt_ids.update(
        state.attempt_id
        for state in initial_scores.values()
        if state.attempt_id is not None
    )

    remediation_links = exam_remediation_link_values(
        enrollment_ids=enrollment_ids,
        exam_ids=exam_ids,
        session_ids=session_ids,
        tenant=tenant,
    )
    stale_teacher_link_ids = stale_teacher_exam_resolution_link_ids(
        links=remediation_links,
        tenant_id=int(tenant.id),
    )
    link_map = {
        (int(link["enrollment_id"]), int(link["source_id"]), int(link["session_id"])): link
        for link in remediation_links
        if int(link["id"]) not in stale_teacher_link_ids
    }

    # 2) ExamAttempt.meta.status + submission_id bulk
    meta_status_by_attempt: dict[int, str | None] = {}
    submission_by_attempt: dict[int, int] = {}
    pair_by_attempt: dict[int, tuple[int, int]] = {}
    completed_attempt_pairs: set[tuple[int, int, int]] = set()
    if attempt_ids:
        for a in ExamAttempt.objects.filter(id__in=attempt_ids).only(
            "id", "meta", "submission_id", "exam_id", "enrollment_id", "status",
        ):
            m = a.meta if isinstance(a.meta, dict) else {}
            meta_status_by_attempt[int(a.id)] = m.get("status")
            pair_by_attempt[int(a.id)] = (int(a.enrollment_id), int(a.exam_id))
            if a.submission_id:
                submission_by_attempt[int(a.id)] = int(a.submission_id)
                if a.status == "done":
                    completed_attempt_pairs.add((int(a.submission_id), int(a.enrollment_id), int(a.exam_id)))

    result_rows = list(
        Result.objects.filter(
            target_type="exam",
            target_id__in=exam_ids,
            enrollment_id__in=enrollment_ids,
            enrollment__tenant_id=int(tenant.id),
        )
    )
    pending_result_ids = pending_omr_result_ids(result_rows, scoring_details_loader=scoring_details_loader)
    pending_result_attempts = {
        (int(result.enrollment_id), int(result.target_id), result.attempt_id)
        for result in result_rows
        if result.enrollment_id is not None
        and int(result.id) in pending_result_ids
    }
    pending_result_pairs = {(enrollment_id, exam_id) for enrollment_id, exam_id, _ in pending_result_attempts}

    # 3) ExamResult.status bulk (is_provisional 판정)
    er_status_by_pair: dict[tuple[int, int, int], str] = {}
    completed_online_pairs = set()
    if submission_by_attempt:
        sub_ids = set(submission_by_attempt.values())
        legacy_rows = list(ExamResult.objects.filter(
            submission_id__in=sub_ids,
            exam_id__in=exam_ids,
            submission__tenant=tenant,
            submission__enrollment__tenant=tenant,
            submission__target_type="exam",
        ).values(
            "id",
            "submission_id",
            "submission__enrollment_id",
            "submission__target_id",
            "submission__source",
            "submission__status",
            "exam_id",
            "status",
        ))
        completed_online_pairs = completed_online_result_pairs(
            legacy_rows=legacy_rows,
            completed_attempt_pairs=completed_attempt_pairs,
            canonical_pairs={(int(row.enrollment_id), int(row.target_id)) for row in result_rows},
            tenant_id=int(tenant.id),
            scoring_details_loader=scoring_details_loader,
        )
        for er in legacy_rows:
            enrollment_id = er["submission__enrollment_id"]
            target_exam_id = er["submission__target_id"]
            exam_id = er["exam_id"]
            if enrollment_id is None or int(target_exam_id) != int(exam_id):
                continue
            er_status_by_pair[(
                int(er["submission_id"]),
                int(enrollment_id),
                int(exam_id),
            )] = er["status"]

    # 4) per-item 계산 (쿼리 없음)
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for i in items:
        e_id = int(i["enrollment_id"])
        x_id = int(i["exam_id"])
        supplied_attempt_id = int(i["attempt_id"]) if i.get("attempt_id") else None
        initial_state = initial_scores.get((x_id, e_id))
        initial_attempt_id = initial_state.attempt_id if initial_state else None
        # 1차 상태가 있으면 Result가 가리키는 최신 대표 재시험이 아니라 1차의
        # 미응시/확정 상태를 쓴다. 첫 시도가 없는 legacy만 Result.attempt로 호환한다.
        candidate_attempt_id = initial_attempt_id or supplied_attempt_id
        a_id = candidate_attempt_id if (
            candidate_attempt_id is not None
            and pair_by_attempt.get(candidate_attempt_id) == (e_id, x_id)
        ) else None
        sid = i.get("session_id")

        meta_status = meta_status_by_attempt.get(a_id) if a_id else None
        if meta_status is None:
            meta_status = i.get("meta_status")
        initial_score = project_initial_exam_score(
            state=initial_state,
            fallback_score=i.get("total_score"),
            fallback_max_score=None,
            fallback_not_submitted=meta_status == "NOT_SUBMITTED",
        )
        is_not_submitted = initial_score.not_submitted or meta_status == "NOT_SUBMITTED"
        if is_not_submitted:
            meta_status = "NOT_SUBMITTED"

        is_pass = compute_first_pass(
            total_score=initial_score.total_score,
            pass_score=i.get("pass_score"),
            is_not_submitted=is_not_submitted,
        )

        link_info = link_map.get((e_id, x_id, int(sid))) if sid is not None else None
        # A saved clinic resolution is history when the first attempt already passed.
        if link_info is not None and is_pass is not True:
            remediated = True
            clinic_retake = _build_clinic_retake_info(link_info)
        else:
            remediated = False
            clinic_retake = None

        final_pass = compute_final_pass(
            is_pass=is_pass, remediated=remediated, is_not_submitted=is_not_submitted,
        )
        achievement = compute_achievement(
            is_pass=is_pass, remediated=remediated, is_not_submitted=is_not_submitted,
        )

        is_provisional = False
        if a_id and a_id in submission_by_attempt:
            er_status = er_status_by_pair.get((
                submission_by_attempt[a_id],
                e_id,
                x_id,
            ))
            is_provisional = bool(
                er_status and er_status != ExamResult.Status.FINAL
                and (submission_by_attempt[a_id], e_id, x_id) not in completed_online_pairs
            )

        if include_first_attempt_readiness:
            # Preserve the separate latest-result hold on achievement labels.
            first_attempt_provisional = is_provisional or (
                (e_id, x_id, a_id) in pending_result_attempts
                if a_id is not None else (e_id, x_id) in pending_result_pairs
            )

        # First-attempt scoring remains the achievement SSOT, but a newer
        # representative OMR result can still be awaiting essay grading. In
        # that state no final pass/remediation label may be projected yet.
        if (e_id, x_id) in pending_result_pairs:
            is_pass = None
            remediated = False
            clinic_retake = None
            final_pass = None
            achievement = None
            is_provisional = True

        out[(e_id, x_id)] = {
            "is_pass": is_pass,
            "remediated": remediated,
            "final_pass": final_pass,
            "clinic_retake": clinic_retake,
            "achievement": achievement,
            "is_provisional": is_provisional,
            "meta_status": meta_status,
        }
        if include_first_attempt_readiness:
            out[(e_id, x_id)]["first_attempt_provisional"] = first_attempt_provisional

    return out

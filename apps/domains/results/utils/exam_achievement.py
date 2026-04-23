# apps/domains/results/utils/exam_achievement.py
"""
시험 성취(achievement) 계산 SSOT.

정책 (SSOT):
- is_pass: 1차 시험만 기준 (석차 계산용). pass_score=0이면 None.
- remediated: 1차 불합격 후 ClinicLink(source_type=exam, resolution_type∈{EXAM_PASS, MANUAL_OVERRIDE})로 해소.
  (WAIVED=면제는 remediated=False; 성취로 인정하지 않음)
- final_pass: 1차 합격 OR remediated. 미응시 + 미해소 → None.
- achievement: "PASS" | "REMEDIATED" | "FAIL" | "NOT_SUBMITTED" | None
  · PASS: 1차 합격
  · REMEDIATED: 1차 불합격 + 해소(EXAM_PASS/MANUAL_OVERRIDE)
  · FAIL: 1차 불합격 + 미해소
  · NOT_SUBMITTED: 미응시(+미해소)
  · None: pass_score=0 등 판정 기준 없음
- is_provisional: ExamResult.status ≠ FINAL → 채점 미확정

여러 뷰에서 공통 사용해 드리프트 재발 방지.
"""
from __future__ import annotations

from typing import Any

from apps.domains.progress.models import ClinicLink
from apps.domains.results.models import ExamResult, ExamAttempt


def compute_first_pass(
    *,
    total_score: float | None,
    pass_score: float | None,
    is_not_submitted: bool,
) -> bool | None:
    """1차 합격 판정. 석차/성취 분리용 단일 진실."""
    if is_not_submitted:
        return None
    if pass_score is None or float(pass_score) <= 0:
        return None
    return float(total_score or 0.0) >= float(pass_score)


def compute_remediation(
    *,
    enrollment_id: int,
    exam_id: int,
    session,
) -> tuple[bool, dict | None]:
    """
    1차 불합격 후 클리닉 해소 여부 + 재시험 정보.
    EXAM_PASS / MANUAL_OVERRIDE 두 타입을 remediated=True로 인정 (WAIVED 제외).
    """
    if session is None:
        return False, None
    link = (
        ClinicLink.objects.filter(
            enrollment_id=int(enrollment_id),
            session=session,
            source_type="exam",
            source_id=int(exam_id),
            resolved_at__isnull=False,
            resolution_type__in=[
                ClinicLink.ResolutionType.EXAM_PASS,
                ClinicLink.ResolutionType.MANUAL_OVERRIDE,
            ],
        )
        .order_by("-resolved_at")
        .first()
    )
    if not link:
        return False, None
    evidence = link.resolution_evidence or {}
    info = {
        "score": evidence.get("score"),
        "pass_score": evidence.get("pass_score"),
        "attempt_id": evidence.get("attempt_id"),
        "resolution_type": link.resolution_type,
        "resolved_at": link.resolved_at.isoformat() if link.resolved_at else None,
    }
    return True, info


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

    우선순위: REMEDIATED > PASS > NOT_SUBMITTED > FAIL
    (보강합격이 1차 합격보다 우선하는 건 아니지만, 판정 흐름상 remediated=True면 1차는 false)
    """
    if remediated:
        return "REMEDIATED"
    if is_pass is True:
        return "PASS"
    if is_not_submitted:
        return "NOT_SUBMITTED"
    if is_pass is False:
        return "FAIL"
    return None


def compute_is_provisional(*, attempt_id: int | None) -> bool:
    """연결된 ExamResult가 FINAL 상태가 아니면 provisional(임시점수)."""
    if not attempt_id:
        return False
    att = ExamAttempt.objects.filter(id=int(attempt_id)).only("submission_id").first()
    if not att or not att.submission_id:
        return False
    er_status = (
        ExamResult.objects.filter(submission_id=att.submission_id)
        .values_list("status", flat=True)
        .first()
    )
    return bool(er_status and er_status != ExamResult.Status.FINAL)


def compute_attempt_meta_status(*, attempt_id: int | None) -> str | None:
    """ExamAttempt.meta.status 조회 (예: NOT_SUBMITTED)."""
    if not attempt_id:
        return None
    att = ExamAttempt.objects.filter(id=int(attempt_id)).only("meta").first()
    if not att:
        return None
    return (att.meta or {}).get("status")


def compute_exam_achievement(
    *,
    enrollment_id: int,
    exam_id: int,
    session,
    total_score: float | None,
    pass_score: float | None,
    attempt_id: int | None = None,
    meta_status: str | None = None,
) -> dict[str, Any]:
    """
    시험 하나에 대해 학생의 성취 상태를 통합 계산.

    student_result_service와 admin_exam_results_view가 공통 사용.
    입력:
      - enrollment_id, exam_id: 대상 학생/시험
      - session: 해당 시험의 대표 session (없으면 None — 클리닉 판정 스킵)
      - total_score, pass_score: Result / Exam 기반 점수
      - attempt_id: 대표 attempt (is_provisional / meta_status 판정용)
      - meta_status: 미리 계산된 meta.status (있으면 재조회 스킵)

    반환: {
        is_pass, remediated, final_pass, clinic_retake, achievement,
        is_provisional, meta_status
    }
    """
    if meta_status is None:
        meta_status = compute_attempt_meta_status(attempt_id=attempt_id)
    is_not_submitted = meta_status == "NOT_SUBMITTED"

    is_pass = compute_first_pass(
        total_score=total_score,
        pass_score=pass_score,
        is_not_submitted=is_not_submitted,
    )
    remediated, clinic_retake = compute_remediation(
        enrollment_id=enrollment_id,
        exam_id=exam_id,
        session=session,
    )
    final_pass = compute_final_pass(
        is_pass=is_pass,
        remediated=remediated,
        is_not_submitted=is_not_submitted,
    )
    achievement = compute_achievement(
        is_pass=is_pass,
        remediated=remediated,
        is_not_submitted=is_not_submitted,
    )
    is_provisional = compute_is_provisional(attempt_id=attempt_id)

    return {
        "is_pass": is_pass,
        "remediated": remediated,
        "final_pass": final_pass,
        "clinic_retake": clinic_retake,
        "achievement": achievement,
        "is_provisional": is_provisional,
        "meta_status": meta_status,
    }


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
    use_session_filter: bool = True,
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
        "session": Session | None,  # None 이면 클리닉 판정 스킵
      }

    use_session_filter=True (기본):
        ClinicLink 조회 시 session_id 도 조건에 포함 —
        동일 (enrollment, exam)이 여러 session 을 거칠 때 session 일치만 인정.
    use_session_filter=False:
        session 무관하게 (enrollment, exam)로만 매칭 —
        admin_student_grades_view 처럼 이미 exam 별로 대표 session 을 고른 뷰용.

    반환: { (enrollment_id, exam_id): achievement_data }
    compute_exam_achievement 와 동일한 키 셋 (is_pass/remediated/final_pass/
    clinic_retake/achievement/is_provisional/meta_status).

    쿼리 수: items 수와 무관하게 ClinicLink/ExamAttempt/ExamResult 각 1회 (최대 3개).
    """
    if not items:
        return {}

    from apps.domains.progress.models import ClinicLink

    enrollment_ids = {int(i["enrollment_id"]) for i in items}
    exam_ids = {int(i["exam_id"]) for i in items}
    attempt_ids = {int(i["attempt_id"]) for i in items if i.get("attempt_id")}
    session_ids = {
        int(i["session"].id) for i in items
        if use_session_filter and i.get("session") is not None
    }

    # 1) ClinicLink bulk fetch (remediated 판정)
    link_filter: dict[str, Any] = dict(
        enrollment_id__in=enrollment_ids,
        source_type="exam",
        source_id__in=exam_ids,
        resolved_at__isnull=False,
        resolution_type__in=[
            ClinicLink.ResolutionType.EXAM_PASS,
            ClinicLink.ResolutionType.MANUAL_OVERRIDE,
        ],
    )
    if use_session_filter and session_ids:
        link_filter["session_id__in"] = session_ids

    # key 구조:
    #   use_session_filter=True  → (enrollment_id, exam_id, session_id or None)
    #   use_session_filter=False → (enrollment_id, exam_id, None) — session 무관
    # 동일 key 에 link 가 여러 개면 resolved_at 최신만 보관.
    link_map: dict[tuple[int, int, int | None], dict[str, Any]] = {}
    for cl in ClinicLink.objects.filter(**link_filter).values(
        "enrollment_id", "source_id", "session_id",
        "resolution_type", "resolution_evidence", "resolved_at",
    ):
        if use_session_filter:
            sid = int(cl["session_id"]) if cl.get("session_id") is not None else None
        else:
            sid = None
        key = (int(cl["enrollment_id"]), int(cl["source_id"]), sid)
        prev = link_map.get(key)
        if prev is None:
            link_map[key] = cl
            continue
        # order by -resolved_at (latest wins)
        prev_at = prev.get("resolved_at")
        cur_at = cl.get("resolved_at")
        if cur_at and (prev_at is None or cur_at > prev_at):
            link_map[key] = cl

    # 2) ExamAttempt.meta.status + submission_id bulk
    meta_status_by_attempt: dict[int, str | None] = {}
    submission_by_attempt: dict[int, int] = {}
    if attempt_ids:
        for a in ExamAttempt.objects.filter(id__in=attempt_ids).only(
            "id", "meta", "submission_id",
        ):
            m = a.meta if isinstance(a.meta, dict) else {}
            meta_status_by_attempt[int(a.id)] = m.get("status")
            if a.submission_id:
                submission_by_attempt[int(a.id)] = int(a.submission_id)

    # 3) ExamResult.status bulk (is_provisional 판정)
    er_status_by_submission: dict[int, str] = {}
    if submission_by_attempt:
        sub_ids = set(submission_by_attempt.values())
        for er in ExamResult.objects.filter(submission_id__in=sub_ids).values(
            "submission_id", "status",
        ):
            er_status_by_submission[int(er["submission_id"])] = er["status"]

    # 4) per-item 계산 (쿼리 없음)
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for i in items:
        e_id = int(i["enrollment_id"])
        x_id = int(i["exam_id"])
        a_id = int(i["attempt_id"]) if i.get("attempt_id") else None
        session = i.get("session")
        sid = int(session.id) if (use_session_filter and session is not None) else None

        meta_status = meta_status_by_attempt.get(a_id) if a_id else None
        is_not_submitted = meta_status == "NOT_SUBMITTED"

        is_pass = compute_first_pass(
            total_score=i.get("total_score"),
            pass_score=i.get("pass_score"),
            is_not_submitted=is_not_submitted,
        )

        # remediated 조회 키:
        #  - use_session_filter=True: session 이 있을 때만 (e, x, session.id) 로 조회
        #  - use_session_filter=False: session 유무 무관하게 (e, x, None) 으로 조회
        link_info = None
        if use_session_filter:
            if session is not None:
                link_info = link_map.get((e_id, x_id, sid))
        else:
            link_info = link_map.get((e_id, x_id, None))
        if link_info is not None:
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
            er_status = er_status_by_submission.get(submission_by_attempt[a_id])
            is_provisional = bool(er_status and er_status != ExamResult.Status.FINAL)

        out[(e_id, x_id)] = {
            "is_pass": is_pass,
            "remediated": remediated,
            "final_pass": final_pass,
            "clinic_retake": clinic_retake,
            "achievement": achievement,
            "is_provisional": is_provisional,
            "meta_status": meta_status,
        }

    return out

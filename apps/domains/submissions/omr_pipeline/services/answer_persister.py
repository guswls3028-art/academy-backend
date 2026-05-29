"""
워커가 보낸 answers payload 를 SubmissionAnswer 로 영속화하고 검토 메타를 생성.

이전: ai_omr_result_mapper.apply_omr_ai_result 본문 안에 ~120 줄로 박혀 있었다.
        identifier 매칭·상태 전이·메타 finalize 와 같은 함수에 섞여 있어
        테스트가 어렵고, "답안 저장만" 단위로 변경하기 위해 매번 전체 mapper 를
        읽어야 했다.

이 모듈은 그 묶음을 단일 책임으로 분리한다.

책임:
- worker question_id (= question_number) → ExamQuestion.id 매핑 (qnum_map_built
  가 True 인 경우만 — False 면 구버전 호환 fallback).
- SubmissionAnswer 멱등 upsert (update_or_create).
- answer_stats 집계 (total/ok/blank/ambiguous/error/avg_confidence).
- manual_review reasons 결정 (ANSWER_STATUS_NOT_OK / ANSWER_SCORE_AMBIGUOUS /
  ANSWER_LOW_CONFIDENCE / ALIGNMENT_FAILED / ANSWER_QNUM_NOT_IN_SHEET).

비책임 (의도적 제외):
- identifier matching — IdentifierMatcher 가 담당.
- exam enrollment lock / duplicate conflict — enrollment_finalizer 가 담당.
- submission.meta 직접 변경 — orchestrator 가 결과를 반영.
- 상태 전이 — orchestrator 가 transit() 호출.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.support.omr.answer_policy import (
    ambiguous_answer_can_change_score,
    answer_matches_expected,
)
from apps.support.omr.exam_structure import OmrExamStructure


logger = logging.getLogger(__name__)


@dataclass
class AnswerPersistResult:
    """answer 영속화 단계의 표준 출력. orchestrator 가 meta 와 manual_review 에 반영."""

    answer_stats: dict[str, Any]
    manual_review_reasons: list[str] = field(default_factory=list)
    unmapped_questions: list[int] = field(default_factory=list)
    manual_required: bool = False


def persist_answers(
    *,
    submission: Submission,
    answers_payload: list[dict[str, Any]],
    worker_result_meta: dict[str, Any],
    exam_structure: OmrExamStructure,
) -> AnswerPersistResult:
    """
    answers 를 SubmissionAnswer 로 저장하고 검토 메타를 만든다.

    Args:
        submission: 락 잡힌 Submission (caller 가 select_for_update 한 후 전달).
        answers_payload: worker result.answers — 각 dict 는 question_id / detected
            / status / marking / confidence / raw 를 포함.
        worker_result_meta: worker result 본문 메타 — alignment 등 답안 외 필드.
        exam_structure: load_submission_exam_structure 결과 (qnum→pk 매핑 + 정답).

    Returns:
        AnswerPersistResult.
    """
    qnum_to_pk = exam_structure.qnum_to_pk
    correct_answers_by_pk = exam_structure.correct_answers_by_pk
    qnum_map_built = exam_structure.qnum_map_built

    answer_stats: dict[str, Any] = {
        "total": 0,
        "ok": 0,
        "blank": 0,
        "ambiguous": 0,
        "error": 0,
        "sum_conf": 0.0,
        "n_conf": 0,
    }
    reasons: list[str] = []
    unmapped: list[int] = []
    manual_required = False

    for a in answers_payload:
        if not isinstance(a, dict):
            continue
        raw_id = a.get("exam_question_id") or a.get("question_id")
        if not raw_id:
            continue

        # question_number → ExamQuestion PK 변환.
        # qnum 매핑이 구축되었지만 해당 번호가 매핑에 없으면 다른 시험 PK 충돌 위험 → skip.
        # 매핑 자체가 미구축(qnum_map_built=False)인 구버전 데이터에서만 raw_id 를 PK 로 fallback.
        raw_id_int = int(raw_id)
        if qnum_map_built:
            eqid_opt = qnum_to_pk.get(raw_id_int)
            if eqid_opt is None:
                unmapped.append(raw_id_int)
                logger.warning(
                    "answer_persister: question %s not in sheet | submission=%s | exam=%s",
                    raw_id_int, submission.id, submission.target_id,
                )
                continue
            eqid = eqid_opt
        else:
            eqid = raw_id_int

        detected_values = [
            str(x).strip() for x in (a.get("detected") or []) if str(x).strip()
        ]
        detected_answer = ",".join(detected_values)
        expected_multi_ok = (
            len(detected_values) > 1
            and answer_matches_expected(
                detected_values, correct_answers_by_pk.get(str(eqid))
            )
        )

        # v10.1: worker 가 raw 안에 제공하는 bubble_rects/rect (검토 UI BBox overlay)
        raw_payload = a.get("raw") or {}
        bubble_rects = (
            raw_payload.get("bubble_rects") if isinstance(raw_payload, dict) else None
        )
        question_rect = (
            raw_payload.get("rect") if isinstance(raw_payload, dict) else None
        )

        omr_meta: dict[str, Any] = {
            "version": a.get("version") or worker_result_meta.get("version"),
            "detected": a.get("detected"),
            "marking": a.get("marking"),
            "confidence": a.get("confidence"),
            "status": a.get("status"),
        }
        if expected_multi_ok:
            omr_meta["expected_multi_answer"] = True
        if isinstance(bubble_rects, list) and bubble_rects:
            omr_meta["bubble_rects"] = bubble_rects
        if isinstance(question_rect, dict):
            omr_meta["rect"] = question_rect

        SubmissionAnswer.objects.update_or_create(
            submission=submission,
            exam_question_id=int(eqid),
            defaults={
                "tenant": submission.tenant,
                "answer": detected_answer,
                "meta": {"omr": omr_meta},
            },
        )

        st = str(a.get("status") or "").lower()
        mk = str(a.get("marking") or "").lower()  # noqa: F841 — kept for future use
        conf = a.get("confidence")

        try:
            conf_f = float(conf) if conf is not None else None
        except Exception:
            conf_f = None

        answer_stats["total"] += 1
        if st in ("ok", "blank", "ambiguous", "error"):
            answer_stats[st] += 1
        if conf_f is not None:
            answer_stats["sum_conf"] += conf_f
            answer_stats["n_conf"] += 1

        score_ambiguous = ambiguous_answer_can_change_score(
            detected_values=detected_values,
            correct_answer=correct_answers_by_pk.get(str(eqid)),
        )

        if st == "error":
            manual_required = True
            reasons.append("ANSWER_STATUS_NOT_OK")
        elif st not in ("ok", "blank") and not expected_multi_ok and score_ambiguous:
            manual_required = True
            reasons.append("ANSWER_SCORE_AMBIGUOUS")

        if st == "low_confidence" and not expected_multi_ok and score_ambiguous:
            manual_required = True
            reasons.append("ANSWER_LOW_CONFIDENCE")

    # ── 정렬 실패 명시 ──
    # worker 가 homography/contour/rotation 어느 경로로도 정렬 못 하면 aligned=False.
    # 답안은 대부분 blank 로 위장되므로 여기서 명시 사유를 추가해 운영자가 즉시 인지.
    if worker_result_meta.get("aligned") is False:
        manual_required = True
        reasons.append("ALIGNMENT_FAILED")

    if unmapped:
        manual_required = True
        reasons.append("ANSWER_QNUM_NOT_IN_SHEET")

    # 평균 신뢰도 보조 필드 + 누적값 정리
    if answer_stats["n_conf"] > 0:
        answer_stats["avg_confidence"] = round(
            answer_stats["sum_conf"] / answer_stats["n_conf"], 4
        )
    else:
        answer_stats["avg_confidence"] = None
    answer_stats.pop("sum_conf", None)
    answer_stats.pop("n_conf", None)

    return AnswerPersistResult(
        answer_stats=answer_stats,
        manual_review_reasons=reasons,
        unmapped_questions=sorted(set(unmapped)),
        manual_required=manual_required,
    )

#!/usr/bin/env python3
"""
OMR 자동채점 실측 acceptance harness

99% 정확도 게이트 검증용. 합성 테스트와 별개로 운영 스캔 데이터에 대한
회귀 측정을 자동화한다.

== 입력 ==
labels.json 스키마:
{
  "exam": {
    "mc_count": 30,
    "n_choices": 5,
    "essay_count": 0,
    "answer_key": {"1": "3", "2": "1", ...}    # 정답지 (선택)
  },
  "scans": [
    {
      "image": "scans/scan_001.jpg",            # labels.json 기준 상대경로
      "expected_identifier": "12345678",        # 학생 본인이 마킹한 8자리
      "expected_marks": {"1": "3", "2": null}   # 학생이 실제로 마킹한 답 (정답 아님)
                                                # null = 빈칸, "3,4" = 이중 마킹
    }
  ]
}

== 출력 ==
- per-question recognition accuracy (AI 인식 == 학생 실제 마킹)
- identifier digit accuracy (자리별)
- align method 분포
- failure mode breakdown (blank/ambiguous/error/wrong)
- 정답지가 있을 때: AI 점수 vs 실제 점수 차이
- acceptance gate: recognition accuracy ≥ ARG_THRESHOLD (default 0.99)

== 사용 ==
  cd backend/
  PYTHONIOENCODING=utf-8 python tests/omr/acceptance.py \\
      --labels /path/to/labels.json \\
      --threshold 0.99 \\
      --report /path/to/report.json

CI에 ≥99% gate 등록은 실측 데이터셋 확보 후 진행.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from academy.adapters.ai.omr.engine import detect_omr_answers_v7, AnswerDetectConfig
from academy.adapters.ai.omr.identifier import detect_identifier_v1, IdentifierConfigV1
from academy.adapters.ai.omr.warp import align_to_a4_landscape


# ─────────────────────────────────────────────
# 결과 집계 구조체
# ─────────────────────────────────────────────

@dataclass
class ScanResult:
    image: str
    aligned: bool
    align_method: str
    identifier_expected: str
    identifier_detected: str
    identifier_digits_correct: int
    identifier_digits_total: int
    answer_total: int
    answer_correct_recognition: int                  # AI == expected_marks
    answer_blank: int
    answer_ambiguous: int
    answer_error: int
    answer_wrong_recognition: int                    # AI != expected_marks (status=ok)
    avg_confidence: float
    grading_correct: Optional[int] = None            # 정답지 있을 때만
    grading_real: Optional[int] = None               # 학생 실제 점수
    failure_examples: List[str] = field(default_factory=list)


@dataclass
class Report:
    n_scans: int
    n_recognition_total: int
    n_recognition_correct: int
    recognition_accuracy: float
    n_identifier_digits_total: int
    n_identifier_digits_correct: int
    identifier_digit_accuracy: float
    n_identifier_full_match: int
    align_methods: Dict[str, int]
    failure_breakdown: Dict[str, int]
    avg_confidence: float
    grading: Dict[str, Any]
    threshold: float
    gate_pass: bool
    scans: List[ScanResult]


# ─────────────────────────────────────────────
# 단일 스캔 실행
# ─────────────────────────────────────────────

def _run_one(
    image_path: Path,
    expected_identifier: str,
    expected_marks: Dict[str, Optional[str]],
    answer_key: Optional[Dict[str, str]],
    meta: Dict[str, Any],
) -> ScanResult:
    img = cv2.imread(str(image_path))
    if img is None:
        return ScanResult(
            image=str(image_path), aligned=False, align_method="read_fail",
            identifier_expected=expected_identifier, identifier_detected="",
            identifier_digits_correct=0,
            identifier_digits_total=len(expected_identifier),
            answer_total=len(expected_marks),
            answer_correct_recognition=0, answer_blank=0,
            answer_ambiguous=0, answer_error=len(expected_marks),
            answer_wrong_recognition=0, avg_confidence=0.0,
            failure_examples=["IMAGE_READ_FAILED"],
        )

    align = align_to_a4_landscape(image_bgr=img, meta=meta)
    ident = detect_identifier_v1(image_bgr=align.image, meta=meta, cfg=IdentifierConfigV1())
    answer_results = detect_omr_answers_v7(image_bgr=align.image, meta=meta, config=AnswerDetectConfig())

    # ── identifier ──
    detected_id = "".join(
        str(d.get("value")) if d.get("value") is not None else "?"
        for d in ident.get("digits", [])
    )
    id_correct = sum(
        1
        for a, b in zip(expected_identifier, detected_id)
        if str(a) == str(b)
    )

    # ── answers ──
    blank = ambiguous = error = wrong = correct_reco = 0
    confs: List[float] = []
    failures: List[str] = []
    grading_correct = grading_real = None

    if answer_key is not None:
        grading_correct = 0
        grading_real = 0

    for ans in answer_results:
        qn = str(ans.question_id)
        expected = expected_marks.get(qn)  # 학생 실제 마킹 (None=빈칸)
        st = (ans.status or "").lower()
        mk = (ans.marking or "").lower()
        det = ans.detected or []

        if ans.confidence is not None:
            try:
                confs.append(float(ans.confidence))
            except Exception:
                pass

        if st == "blank":
            blank += 1
            ai_pick = None
        elif st == "ambiguous":
            ambiguous += 1
            ai_pick = ",".join(sorted(det)) if det else None
        elif st == "error":
            error += 1
            ai_pick = None
        else:  # ok
            ai_pick = det[0] if len(det) == 1 else (",".join(sorted(det)) if det else None)

        # recognition correctness: AI 인식 == 학생 실제 마킹
        if _normalize_mark(ai_pick) == _normalize_mark(expected):
            correct_reco += 1
        else:
            if st == "ok":
                wrong += 1
            failures.append(f"Q{qn}: expected={expected!r} got={ai_pick!r} ({st}/{mk})")

        # grading: 정답지가 있을 때 AI 점수와 실제 점수 비교
        if answer_key is not None:
            correct = answer_key.get(qn)
            # AI 채점 (best-effort policy 반영: status==ok, single, detected==correct)
            if st == "ok" and len(det) == 1 and _normalize_mark(det[0]) == _normalize_mark(correct):
                grading_correct += 1
            # 학생 실제 점수 (expected_marks 기반)
            if expected is not None and _normalize_mark(expected) == _normalize_mark(correct):
                grading_real += 1

    avg_conf = float(sum(confs) / len(confs)) if confs else 0.0

    return ScanResult(
        image=str(image_path),
        aligned=align.success,
        align_method=align.method,
        identifier_expected=expected_identifier,
        identifier_detected=detected_id,
        identifier_digits_correct=id_correct,
        identifier_digits_total=len(expected_identifier),
        answer_total=len(answer_results),
        answer_correct_recognition=correct_reco,
        answer_blank=blank,
        answer_ambiguous=ambiguous,
        answer_error=error,
        answer_wrong_recognition=wrong,
        avg_confidence=avg_conf,
        grading_correct=grading_correct,
        grading_real=grading_real,
        failure_examples=failures[:5],
    )


def _normalize_mark(v: Optional[str]) -> str:
    """비교 정규화: None→'', 공백/대문자 통일."""
    if v is None:
        return ""
    return str(v).strip().upper()


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────

def run(labels_path: Path, threshold: float, report_path: Optional[Path]) -> Report:
    with labels_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    base_dir = labels_path.parent
    exam = data["exam"]
    mc_count = int(exam.get("mc_count") or 30)
    n_choices = int(exam.get("n_choices") or 5)
    essay_count = int(exam.get("essay_count") or 0)
    answer_key = exam.get("answer_key")  # may be None

    meta = build_omr_meta(question_count=mc_count, n_choices=n_choices, essay_count=essay_count)

    results: List[ScanResult] = []
    for entry in data.get("scans", []):
        image = base_dir / entry["image"]
        expected_id = str(entry.get("expected_identifier") or "")
        expected_marks = {str(k): v for k, v in (entry.get("expected_marks") or {}).items()}
        results.append(_run_one(image, expected_id, expected_marks, answer_key, meta))

    # 집계
    n_scans = len(results)
    reco_total = sum(r.answer_total for r in results)
    reco_correct = sum(r.answer_correct_recognition for r in results)
    id_total = sum(r.identifier_digits_total for r in results)
    id_correct = sum(r.identifier_digits_correct for r in results)
    id_full = sum(
        1 for r in results
        if r.identifier_expected and r.identifier_detected
        and r.identifier_expected == r.identifier_detected
    )

    methods = Counter(r.align_method for r in results)
    failure_breakdown = {
        "blank": sum(r.answer_blank for r in results),
        "ambiguous": sum(r.answer_ambiguous for r in results),
        "error": sum(r.answer_error for r in results),
        "wrong_recognition": sum(r.answer_wrong_recognition for r in results),
    }

    avg_conf_all = (
        float(np.mean([r.avg_confidence for r in results if r.avg_confidence > 0]))
        if any(r.avg_confidence > 0 for r in results) else 0.0
    )

    grading_block: Dict[str, Any] = {}
    if answer_key:
        ai_total = sum((r.grading_correct or 0) for r in results)
        real_total = sum((r.grading_real or 0) for r in results)
        grading_block = {
            "ai_total": ai_total,
            "real_total": real_total,
            "diff": ai_total - real_total,
            "abs_diff_per_scan": (
                float(np.mean([
                    abs((r.grading_correct or 0) - (r.grading_real or 0))
                    for r in results
                ])) if results else 0.0
            ),
        }

    accuracy = reco_correct / reco_total if reco_total else 0.0

    report = Report(
        n_scans=n_scans,
        n_recognition_total=reco_total,
        n_recognition_correct=reco_correct,
        recognition_accuracy=accuracy,
        n_identifier_digits_total=id_total,
        n_identifier_digits_correct=id_correct,
        identifier_digit_accuracy=(id_correct / id_total) if id_total else 0.0,
        n_identifier_full_match=id_full,
        align_methods=dict(methods),
        failure_breakdown=failure_breakdown,
        avg_confidence=avg_conf_all,
        grading=grading_block,
        threshold=threshold,
        gate_pass=accuracy >= threshold,
        scans=results,
    )

    _print_summary(report)

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with report_path.open("w", encoding="utf-8") as f:
            json.dump(asdict(report), f, ensure_ascii=False, indent=2)
        print(f"\n📄 report saved: {report_path}")

    return report


def _print_summary(r: Report) -> None:
    print("=" * 78)
    print(f" OMR Acceptance Report  (n_scans={r.n_scans})")
    print("=" * 78)
    print(f" recognition accuracy : {r.recognition_accuracy*100:6.2f}%  "
          f"({r.n_recognition_correct}/{r.n_recognition_total})  "
          f"threshold={r.threshold*100:.1f}%  "
          f"{'PASS' if r.gate_pass else 'FAIL'}")
    print(f" identifier digit acc : {r.identifier_digit_accuracy*100:6.2f}%  "
          f"({r.n_identifier_digits_correct}/{r.n_identifier_digits_total})")
    print(f" identifier full match: {r.n_identifier_full_match}/{r.n_scans}")
    print(f" avg AI confidence    : {r.avg_confidence:.3f}")
    print(f" align methods        : {r.align_methods}")
    print(f" failure breakdown    : {r.failure_breakdown}")
    if r.grading:
        print(f" grading              : ai={r.grading['ai_total']} "
              f"real={r.grading['real_total']} "
              f"diff={r.grading['diff']:+d} "
              f"abs_per_scan={r.grading['abs_diff_per_scan']:.2f}")
    print("-" * 78)
    # 실패 사례 (앞 5건)
    fails = [s for s in r.scans if s.answer_correct_recognition < s.answer_total or
             s.identifier_digits_correct < s.identifier_digits_total]
    if fails:
        print(f" ⚠ failure cases ({len(fails)}):")
        for s in fails[:5]:
            print(f"   - {s.image}")
            print(f"       align={s.align_method} aligned={s.aligned} "
                  f"id={s.identifier_detected}/{s.identifier_expected} "
                  f"reco={s.answer_correct_recognition}/{s.answer_total}")
            for ex in s.failure_examples[:3]:
                print(f"         · {ex}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True, type=Path,
                    help="labels.json 경로 (스키마는 모듈 docstring 참고)")
    ap.add_argument("--threshold", type=float, default=0.99,
                    help="recognition accuracy gate (default 0.99)")
    ap.add_argument("--report", type=Path, default=None,
                    help="JSON 리포트 저장 경로 (선택)")
    args = ap.parse_args()

    if not args.labels.exists():
        print(f"labels not found: {args.labels}", file=sys.stderr)
        return 2

    report = run(args.labels, args.threshold, args.report)
    return 0 if report.gate_pass else 1


if __name__ == "__main__":
    sys.exit(main())

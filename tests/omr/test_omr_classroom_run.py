#!/usr/bin/env python3
"""
OMR 실전 시뮬레이션 — 학원 관리자 입장

시나리오:
  "3월 고3 수학 모의고사 3회" OMR 30문항을 10명 학생에게 배부.
  각 학생이 서로 다른 스타일로 마킹 (완벽/약한/이중/수정/빈칸).
  ADF 스캐너로 일괄 스캔 (삐뚤어짐/뒤집힘/그림자 등 일부 포함).
  AI 채점 파이프라인 실행 → 채점표 출력.

실제 dispatcher.handle_ai_job의 omr_grading path를 흉내 (align + identifier + engine).

실행:
  cd backend/
  PYTHONIOENCODING=utf-8 python tests/omr/test_omr_classroom_run.py
"""
import os
import sys
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cv2
import numpy as np
from pdf2image import convert_from_bytes

from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from apps.domains.assets.omr.renderer.pdf_renderer import OMRPdfRenderer
from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.worker.ai_worker.ai.omr.engine import detect_omr_answers_v7, AnswerDetectConfig
from apps.worker.ai_worker.ai.omr.identifier import detect_identifier_v1, IdentifierConfigV1
from apps.worker.omr.warp import align_to_a4_landscape
from tests.omr.test_omr_full_pipeline import distort


# ══════════════════════════════════════════
# 시험 설정 (학원 관리자 입력)
# ══════════════════════════════════════════
EXAM_TITLE = "3월 고3 수학 모의고사"
LECTURE = "고3 심화수학"
SESSION = "3회"
MC_COUNT = 30
N_CHOICES = 5
TENANT_LOGO_PATH = "apps/domains/assets/omr/renderer/logos/dnb.png"

# ══════════════════════════════════════════
# 정답지 (선생님이 작성)
# ══════════════════════════════════════════
ANSWER_KEY = {str(i): str(((i * 7 + 3) % 5) + 1) for i in range(1, MC_COUNT + 1)}


# ══════════════════════════════════════════
# 학생 시나리오 (10명)
# ══════════════════════════════════════════
@dataclass
class Student:
    name: str
    phone: List[int]            # 8자리 전화번호 뒤
    answers: Dict[str, str]     # {qn: choice} — 학생이 마킹한 답
    blanks: List[str] = field(default_factory=list)  # 빈칸으로 둔 문항
    double_marks: Dict[str, List[str]] = field(default_factory=dict)  # 이중 마킹
    mark_intensity: int = 0     # 0 = 순흑, 높을수록 연함 (연필)
    distort: Dict = field(default_factory=dict)  # 스캔 변형
    quirk_desc: str = ""


def make_students() -> List[Student]:
    """10명의 학생 케이스."""
    rng = random.Random(42)

    def phone():
        return [rng.randint(0, 9) for _ in range(8)]

    def answers_with_score(pct: float) -> Tuple[Dict, List]:
        """정답지 기반 pct% 맞고 나머지는 틀린 답/빈칸."""
        result, blanks = {}, []
        for qn, correct in ANSWER_KEY.items():
            roll = rng.random()
            if roll < pct:
                result[qn] = correct
            elif roll < pct + 0.1:
                blanks.append(qn)
            else:
                wrong_choices = [c for c in ["1", "2", "3", "4", "5"] if c != correct]
                result[qn] = rng.choice(wrong_choices)
        return result, blanks

    ans1, blk1 = answers_with_score(0.95)
    ans2, blk2 = answers_with_score(0.80)
    ans3, blk3 = answers_with_score(0.85)
    ans4, blk4 = answers_with_score(0.90)
    ans5, blk5 = answers_with_score(0.75)
    ans6, blk6 = answers_with_score(0.70)
    ans7, blk7 = answers_with_score(0.85)
    ans8, blk8 = answers_with_score(0.88)
    ans9, blk9 = answers_with_score(0.60)
    ans10, blk10 = answers_with_score(0.50)

    return [
        Student("김모범", phone(), ans1, blk1,
                quirk_desc="완벽한 사인펜 마킹, 깨끗한 스캔"),
        Student("박연필", phone(), ans2, blk2, mark_intensity=100,
                quirk_desc="연필로 흐리게 마킹"),
        Student("이수정", phone(), ans3, blk3,
                double_marks={"5": ["2", "3"], "12": ["1", "4"]},
                quirk_desc="2개 문항 이중 마킹 (잘못 채워 둘 다)"),
        Student("최비뚤", phone(), ans4, blk4,
                distort={"rotation_deg": 3.0},
                quirk_desc="ADF에 약간 삐뚤게 투입 (+3°)"),
        Student("정거꾸로", phone(), ans5, blk5,
                distort={"flip_180": True},
                quirk_desc="ADF에 답안지 거꾸로 투입 (180°)"),
        Student("강복사", phone(), ans6, blk6,
                distort={"jpeg_quality": 50, "noise_sigma": 8.0},
                quirk_desc="복사기 저품질 스캔 (JPEG q50 + 노이즈)"),
        Student("윤가장자리", phone(), ans7, blk7,
                distort={"crop_mm": 2.5, "rotation_deg": 1.5},
                quirk_desc="가장자리 2.5mm 잘림 + 약간 회전"),
        Student("임어둠", phone(), ans8, blk8,
                distort={"shadow_pct": 25.0, "noise_sigma": 6.0},
                quirk_desc="조명 불균일 (좌측 어두움)"),
        Student("오흐릿", phone(), ans9, blk9, mark_intensity=140,
                distort={"blur_ksize": 3},
                quirk_desc="흐린 마킹 + 흐린 스캔"),
        Student("한절반", phone(), ans10, blk10,
                quirk_desc="시간 부족해서 절반만 풀고 제출"),
    ]


# ══════════════════════════════════════════
# OMR 생성 + 학생별 답안 마킹
# ══════════════════════════════════════════
def render_exam_pdf(logo_bytes: Optional[bytes] = None) -> bytes:
    """학원 관리자가 생성하는 OMR PDF."""
    doc = OMRDocument(
        exam_title=EXAM_TITLE, lecture_name=LECTURE, session_name=SESSION,
        mc_count=MC_COUNT, essay_count=0, n_choices=N_CHOICES,
        logo_bytes=logo_bytes,
    )
    return OMRPdfRenderer().render(doc)


def apply_student_marks(blank_pdf_img: np.ndarray, meta: Dict,
                        student: Student) -> np.ndarray:
    """블랭크 OMR 이미지에 학생 마킹 overlay."""
    img = blank_pdf_img.copy()
    h, w = img.shape[:2]
    pw, ph = meta["page"]["width"], meta["page"]["height"]
    sx, sy = w / pw, h / ph
    color = (student.mark_intensity, student.mark_intensity, student.mark_intensity)

    # 답안 마킹 (blank 제외)
    for q in meta["questions"]:
        qn = str(q["question_number"])
        if qn in student.blanks:
            continue
        # 이중 마킹
        if qn in student.double_marks:
            for label in student.double_marks[qn]:
                for c in q["choices"]:
                    if c["label"] == label:
                        _fill_bubble(img, c, sx, sy, color)
                        break
            continue
        # 일반 마킹
        chosen = student.answers.get(qn)
        if chosen:
            for c in q["choices"]:
                if c["label"] == chosen:
                    _fill_bubble(img, c, sx, sy, color)
                    break

    # 식별번호 마킹
    for d_idx, value in enumerate(student.phone):
        digit = meta["identifier"]["digits"][d_idx]
        bub = digit["bubbles"][value]
        _fill_bubble(img, bub, sx, sy, color)

    return img


def _fill_bubble(img, bub, sx, sy, color):
    cx = int(round(bub["center"]["x"] * sx))
    cy = int(round(bub["center"]["y"] * sy))
    rx = int(round(bub["radius_x"] * sx))
    ry = int(round(bub["radius_y"] * sy))
    cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, color, -1)


def apply_scan_distortion(img: np.ndarray, student: Student) -> np.ndarray:
    """스캐너 변형 적용."""
    d = dict(student.distort)
    flip_180 = d.pop("flip_180", False)
    jpeg_q = d.pop("jpeg_quality", 0)
    out = img
    if flip_180:
        out = cv2.rotate(out, cv2.ROTATE_180)
    out = distort(out, dpi=300, **d)
    if jpeg_q > 0:
        _, enc = cv2.imencode('.jpg', out, [cv2.IMWRITE_JPEG_QUALITY, jpeg_q])
        out = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return out


# ══════════════════════════════════════════
# AI 파이프라인 (dispatcher 흉내)
# ══════════════════════════════════════════
def grade_one(meta: Dict, scan_img: np.ndarray) -> Dict:
    """align + identifier + engine 전체 흐름."""
    align = align_to_a4_landscape(image_bgr=scan_img, meta=meta)
    ident = detect_identifier_v1(image_bgr=align.image, meta=meta,
                                 cfg=IdentifierConfigV1())
    answers = detect_omr_answers_v7(image_bgr=align.image, meta=meta,
                                    config=AnswerDetectConfig())
    return {
        "align_method": align.method,
        "aligned": align.success,
        "identifier": ident,
        "answers": answers,
    }


# ══════════════════════════════════════════
# 메인 (학원 업무 흐름)
# ══════════════════════════════════════════
def main():
    print("═" * 82)
    print(f" 📋 {EXAM_TITLE} · {LECTURE} · {SESSION}")
    print(f"     객관식 {MC_COUNT}문항 · {N_CHOICES}지선다")
    print("═" * 82)

    # 1) 학원 관리자: OMR PDF 생성
    print("\n[1단계] 학원 관리자가 OMR PDF 생성 (테넌트 dnb 로고)")
    with open(TENANT_LOGO_PATH, "rb") as f:
        logo_bytes = f.read()
    pdf_bytes = render_exam_pdf(logo_bytes)
    meta = build_omr_meta(question_count=MC_COUNT, n_choices=N_CHOICES)
    print(f"  → PDF {len(pdf_bytes)} bytes, meta version {meta['version']}")

    # PDF를 이미지로 (인쇄된 종이를 스캔한 결과로 모사)
    print("\n[2단계] PDF → 고해상도 이미지 변환 (300dpi, 인쇄 기준선)")
    blank = convert_from_bytes(pdf_bytes, dpi=300)[0]
    blank_np = cv2.cvtColor(np.array(blank), cv2.COLOR_RGB2BGR)
    print(f"  → 이미지 {blank_np.shape[1]}x{blank_np.shape[0]}")

    # 2) 학생 답안 작성 + 스캔
    students = make_students()
    print(f"\n[3단계] 학생 {len(students)}명 마킹 + ADF 스캔 시뮬레이션")

    # 3) AI 채점
    print("\n[4단계] AI 채점 파이프라인 일괄 실행\n")

    results = []
    for i, s in enumerate(students, 1):
        marked = apply_student_marks(blank_np, meta, s)
        scanned = apply_scan_distortion(marked, s)
        graded = grade_one(meta, scanned)

        # 실제 전화번호 대비 검출 정확도
        detected_phone = [d.get("value") for d in graded["identifier"].get("digits", [])]
        phone_correct = sum(1 for a, b in zip(s.phone, detected_phone) if str(a) == str(b))
        phone_str_expected = "".join(str(d) for d in s.phone)
        phone_str_detected = "".join(str(d) if d is not None else "?" for d in detected_phone)

        # 답안 채점
        correct_count = 0
        wrong_items = []
        for ans_obj in graded["answers"]:
            qn = str(ans_obj.question_id)
            expected = ANSWER_KEY[qn]
            # 학생이 실제로 마킹한 건 s.answers[qn] (ground truth for AI recognition)
            # AI가 뭘 감지했나
            if ans_obj.detected == [expected] and ans_obj.status == "ok":
                correct_count += 1
            else:
                wrong_items.append(f"Q{qn}={expected}→{ans_obj.detected}({ans_obj.status})")

        # 학생의 실제 점수 (ground truth — AI 무관)
        real_score = sum(1 for qn, correct in ANSWER_KEY.items()
                         if s.answers.get(qn) == correct)

        results.append({
            "student": s,
            "align_method": graded["align_method"],
            "phone_ok": phone_correct,
            "phone_expected": phone_str_expected,
            "phone_detected": phone_str_detected,
            "ai_score": correct_count,
            "real_score": real_score,
            "wrong_items": wrong_items[:3],
        })

        tag = "✓" if phone_correct == 8 and correct_count >= real_score - 1 else "⚠"
        print(f"  [{i:2d}] {s.name:10s} {tag} "
              f"phone:{phone_correct}/8  AI채점:{correct_count}/{MC_COUNT}  "
              f"실점수:{real_score}/{MC_COUNT}  ({s.quirk_desc})")

    # 4) 보고서
    print("\n" + "═" * 82)
    print(" 📊 학급 채점 결과 보고서")
    print("═" * 82)
    print(f"\n{'학생':12s} {'정렬방식':22s} {'전화번호':12s} {'AI':>6s} {'실제':>6s} {'차이':>6s}")
    print("-" * 82)
    total_phone, total_ai, total_real, perfect_phone = 0, 0, 0, 0
    for r in results:
        diff = r["ai_score"] - r["real_score"]
        diff_str = f"{diff:+d}" if diff != 0 else "0"
        print(f"{r['student'].name:12s} {r['align_method']:22s} "
              f"{r['phone_detected']}/{r['phone_expected']}  "
              f"{r['ai_score']:>3d}/{MC_COUNT}  {r['real_score']:>3d}/{MC_COUNT}  "
              f"{diff_str:>6s}")
        total_phone += r["phone_ok"]
        total_ai += r["ai_score"]
        total_real += r["real_score"]
        if r["phone_ok"] == 8:
            perfect_phone += 1

    n = len(results)
    print("-" * 82)
    print(f"식별번호 완전 일치: {perfect_phone}/{n}명  "
          f"(자리 정확도: {total_phone}/{n*8} = {100*total_phone/(n*8):.1f}%)")
    print(f"AI 채점 vs 실제 점수 차이: {total_ai - total_real:+d}점 "
          f"(AI 합계 {total_ai}, 실제 합계 {total_real})")
    ai_accuracy = 100 * (1 - abs(total_ai - total_real) / total_real) if total_real else 0
    print(f"AI 채점 정확도 추정: {ai_accuracy:.1f}%")

    # 5) 문제 사례
    trouble = [r for r in results if r["phone_ok"] < 8 or abs(r["ai_score"] - r["real_score"]) > 2]
    if trouble:
        print(f"\n⚠ 수동 검토 필요 {len(trouble)}건:")
        for r in trouble:
            print(f"  {r['student'].name}: {r['student'].quirk_desc}")
            for w in r["wrong_items"]:
                print(f"    └ {w}")
    else:
        print("\n✅ 모든 학생 답안이 정상 처리됨 — 수동 검토 불필요")

    print("═" * 82)


if __name__ == "__main__":
    main()

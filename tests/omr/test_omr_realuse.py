#!/usr/bin/env python3
"""
OMR 실사용 시뮬레이션 [REAL_USE]

실제 pdf_renderer 출력 PDF → pdf2image로 PNG → 변형 → 전체 AI 파이프라인.
draw_omr_image (meta 기반 합성)는 meta↔pdf 좌표 불일치를 못 잡지만,
이 테스트는 실제 PDF 렌더링 결과를 쓰므로 불일치 시 인식 실패로 드러남.

시나리오:
  답안 + 식별번호를 "마킹"한 PDF를 만들고, 변형 후 인식.

실행:
  cd backend/
  PYTHONIOENCODING=utf-8 python tests/omr/test_omr_realuse.py
"""
import os
import sys
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cv2
import numpy as np
from pdf2image import convert_from_bytes
from reportlab.lib.pagesizes import landscape, A4
from reportlab.pdfgen import canvas
from reportlab.lib.colors import black
from reportlab.lib.units import mm as MM

from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from apps.domains.assets.omr.renderer.pdf_renderer import OMRPdfRenderer
from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.worker.ai_worker.ai.omr.engine import detect_omr_answers_v7, AnswerDetectConfig
from apps.worker.ai_worker.ai.omr.identifier import detect_identifier_v1, IdentifierConfigV1
from apps.worker.omr.warp import align_to_a4_landscape
from tests.omr.test_omr_full_pipeline import distort


def render_marked_pdf(meta, marks: dict, id_digits: dict, logo_bytes=None,
                      mark_intensity: int = 0, flip_180: bool = False,
                      jpeg_quality: int = 0, dpi: int = 300) -> np.ndarray:
    """OMRPdfRenderer 출력 → PNG → 마킹 overlay → (선택적) 180° 뒤집음/JPEG 압축."""
    doc = OMRDocument(
        exam_title="REAL USE", lecture_name="수학", session_name="테스트",
        mc_count=meta["mc_count"], essay_count=meta.get("essay_count", 0),
        n_choices=meta["n_choices"], logo_bytes=logo_bytes,
    )
    base_pdf = OMRPdfRenderer().render(doc)
    pages = convert_from_bytes(base_pdf, dpi=dpi)
    img = np.array(pages[0])
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    h, w = img.shape[:2]
    pw, ph = meta["page"]["width"], meta["page"]["height"]
    sx, sy = w / pw, h / ph

    color = (mark_intensity, mark_intensity, mark_intensity)

    # 답안 마킹
    for q in meta["questions"]:
        qn = q["question_number"]
        if str(qn) not in marks:
            continue
        for c in q["choices"]:
            if c["label"] == str(marks[str(qn)]):
                cx = int(round(c["center"]["x"] * sx))
                cy = int(round(c["center"]["y"] * sy))
                rx = int(round(c["radius_x"] * sx))
                ry = int(round(c["radius_y"] * sy))
                cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, color, -1)
                break

    # 식별번호 마킹
    for d_idx, value in id_digits.items():
        digit = meta["identifier"]["digits"][d_idx]
        bub = digit["bubbles"][value]
        cx = int(round(bub["center"]["x"] * sx))
        cy = int(round(bub["center"]["y"] * sy))
        rx = int(round(bub["radius_x"] * sx))
        ry = int(round(bub["radius_y"] * sy))
        cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, color, -1)

    # 180° 뒤집힘 (ADF에 종이 거꾸로 넣은 경우)
    if flip_180:
        img = cv2.rotate(img, cv2.ROTATE_180)

    # JPEG 압축 (스캐너가 JPEG 저장)
    if jpeg_quality > 0:
        _, enc = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        img = cv2.imdecode(enc, cv2.IMREAD_COLOR)

    return img


def score(answers, expected_marks):
    """답안 정확도."""
    correct, wrong = 0, []
    for r in answers:
        qid = str(r.question_id)
        expected = expected_marks.get(qid)
        if expected is None:
            if r.status == "blank":
                correct += 1
            else:
                wrong.append(f"Q{qid}:blank→{r.detected}")
        else:
            if r.detected == [expected] and r.status == "ok":
                correct += 1
            else:
                wrong.append(f"Q{qid}:{expected}→{r.detected}({r.status})")
    return correct, len(answers), wrong[:5]


def id_score(result, expected):
    """식별번호 정확도."""
    detected = result.get("digits", [])
    correct = sum(1 for d_idx, exp in expected.items()
                  if d_idx < len(detected) and str(detected[d_idx].get("value")) == str(exp))
    return correct, len(expected)


def run_real_use(qc: int, marks: dict, id_digits: dict, distort_kwargs: dict,
                 render_kwargs: dict = None):
    render_kwargs = render_kwargs or {}
    meta = build_omr_meta(question_count=qc, n_choices=5)
    img = render_marked_pdf(meta, marks, id_digits, **render_kwargs)
    dpi = render_kwargs.get("dpi", 300)
    distorted = distort(img, dpi=dpi, **distort_kwargs)

    align = align_to_a4_landscape(image_bgr=distorted, meta=meta)
    answers = detect_omr_answers_v7(image_bgr=align.image, meta=meta, config=AnswerDetectConfig())
    ans_ok, ans_total, wrong = score(answers, marks)
    id_result = detect_identifier_v1(image_bgr=align.image, meta=meta, cfg=IdentifierConfigV1())
    id_ok, id_total = id_score(id_result, id_digits)

    return {
        "method": align.method,
        "ans": (ans_ok, ans_total),
        "id": (id_ok, id_total),
        "wrong": wrong,
    }


# (name, distort_kwargs, render_kwargs)
SCENARIOS = [
    ("clean",         {}, {}),
    ("rot_+2",        {"rotation_deg": 2.0}, {}),
    ("rot_-3",        {"rotation_deg": -3.0}, {}),
    ("noise_s10",     {"noise_sigma": 10.0}, {}),
    ("blur_3",        {"blur_ksize": 3}, {}),
    ("crop_2mm",      {"crop_mm": 2.0}, {}),
    ("shadow_20",     {"shadow_pct": 20.0}, {}),
    ("combined",      {"rotation_deg": 2.0, "noise_sigma": 8.0, "blur_ksize": 3}, {}),
    # 실전 스트레스
    ("flip_180",      {}, {"flip_180": True}),
    ("flip_180+rot2", {"rotation_deg": 2.0}, {"flip_180": True}),
    ("weak_mark_120", {}, {"mark_intensity": 120}),
    ("weak_mark_80",  {}, {"mark_intensity": 80}),
    ("jpeg_q60",      {}, {"jpeg_quality": 60}),
    ("jpeg_q30",      {}, {"jpeg_quality": 30}),
    ("dpi_200",       {}, {"dpi": 200}),
    ("dpi_150",       {}, {"dpi": 150}),
    ("real_scanner",  {"rotation_deg": 1.0, "noise_sigma": 5.0}, {"jpeg_quality": 70, "dpi": 200}),
]


def main():
    print("=" * 80)
    print("OMR 실사용 시뮬레이션 [REAL_USE] — 실제 PDF 렌더 → 전체 파이프라인")
    print("=" * 80)

    np.random.seed(42)

    total_pass = 0
    total_total = 0

    for qc in [20, 30, 45]:
        marks = {str(i): str(((i - 1) % 5) + 1) for i in range(1, qc + 1) if i % 2 == 0}
        id_digits = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8}

        print(f"\n── {qc}문항 ──")
        for name, kwargs, render_kwargs in SCENARIOS:
            r = run_real_use(qc, marks, id_digits, kwargs, render_kwargs=render_kwargs)
            ans_ok, ans_total = r["ans"]
            id_ok, id_total = r["id"]
            ans_pct = 100.0 * ans_ok / ans_total
            id_pct = 100.0 * id_ok / id_total

            ans_pass = ans_ok == ans_total
            id_pass = id_ok == id_total
            scenario_pass = ans_pass and id_pass
            total_total += 1
            if scenario_pass:
                total_pass += 1

            tag = "PASS" if scenario_pass else "FAIL"
            print(f"  {name:14s} {r['method']:22s} 답안 {ans_pct:5.1f}%({ans_ok}/{ans_total})  "
                  f"식별 {id_pct:5.1f}%({id_ok}/{id_total}) [{tag}]")
            if r["wrong"]:
                for w in r["wrong"][:2]:
                    print(f"       └ {w}")

    print("\n" + "=" * 80)
    print(f"실사용 시뮬레이션: {total_pass}/{total_total} 시나리오 통과")
    print("=" * 80)
    sys.exit(0 if total_pass == total_total else 1)


if __name__ == "__main__":
    main()

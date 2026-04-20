#!/usr/bin/env python3
"""
OMR Full Pipeline 합성 시뮬레이션 [FULL_PIPELINE]

목적: marker_detector → align → engine 풀 파이프라인을 합성 이미지로 검증.
기존 test_omr_pipeline.py는 engine 단독 검증(align 우회)만 함.

검증 시나리오:
  - clean           : 변형 없음 (baseline)
  - rot ±2°, ±5°    : 종이 삐뚤어짐
  - crop 1/3mm      : ADF 스캐너 가장자리 잘림
  - noise σ=5/15    : 스캐너 노이즈
  - blur 3/5        : 초점/복사기 품질
  - shadow 15/30%   : 조명 불균일

목표: clean 100%, rot ±2° 95%+, rot ±5° 90%+, noise/blur 90%+, shadow 85%+

실행:
  cd backend/
  PYTHONIOENCODING=utf-8 python tests/omr/test_omr_full_pipeline.py
"""
import os
import sys
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cv2
import numpy as np

from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from apps.worker.ai_worker.ai.omr.engine import detect_omr_answers_v7, AnswerDetectConfig
from apps.worker.ai_worker.ai.omr.identifier import detect_identifier_v1, IdentifierConfigV1
from apps.worker.omr.warp import align_to_a4_landscape


# ══════════════════════════════════════════
# 합성 이미지 생성 — pdf_renderer와 동일한 마커/타이밍/버블 픽셀 재현
# ══════════════════════════════════════════

def draw_omr_image(meta: Dict, marks: Optional[Dict[str, str]] = None,
                   dpi: int = 300, mark_intensity: int = 0) -> np.ndarray:
    """meta로부터 합성 OMR 이미지 생성 (마커+타이밍+버블).

    Args:
        meta: build_omr_meta() 결과
        marks: {"1": "3", "5": "1", ...} 마킹할 문항별 정답
        dpi: 출력 해상도 (기본 300, A4 landscape → 3508x2480)
    """
    pw_mm = float(meta["page"]["width"])
    ph_mm = float(meta["page"]["height"])
    w = int(round(pw_mm * dpi / 25.4))
    h = int(round(ph_mm * dpi / 25.4))

    img = np.ones((h, w, 3), dtype=np.uint8) * 255
    sx, sy = w / pw_mm, h / ph_mm

    def _mm(x_mm: float, axis: str = "x") -> int:
        return int(round(x_mm * (sx if axis == "x" else sy)))

    # 1. 4코너 마커 v15.2 — 얇은 ㄱ자 브래킷 3개 + BL 삼각형
    off = 3.0
    sz = 4.0
    th = 1.0

    # TL ┐: 수평팔(위) + 수직팔(왼쪽)
    cv2.rectangle(img, (_mm(off, "x"), _mm(off, "y")),
                  (_mm(off + sz, "x"), _mm(off + th, "y")), (0, 0, 0), -1)
    cv2.rectangle(img, (_mm(off, "x"), _mm(off, "y")),
                  (_mm(off + th, "x"), _mm(off + sz, "y")), (0, 0, 0), -1)

    # TR ┌: 수평팔(위) + 수직팔(오른쪽)
    cv2.rectangle(img, (_mm(pw_mm - off - sz, "x"), _mm(off, "y")),
                  (_mm(pw_mm - off, "x"), _mm(off + th, "y")), (0, 0, 0), -1)
    cv2.rectangle(img, (_mm(pw_mm - off - th, "x"), _mm(off, "y")),
                  (_mm(pw_mm - off, "x"), _mm(off + sz, "y")), (0, 0, 0), -1)

    # BL 삼각형 (orientation 판별용)
    pts = np.array([
        [_mm(off, "x"), _mm(ph_mm - off, "y")],
        [_mm(off + sz, "x"), _mm(ph_mm - off, "y")],
        [_mm(off + sz / 2, "x"), _mm(ph_mm - off - sz, "y")],
    ], dtype=np.int32)
    cv2.fillPoly(img, [pts], (0, 0, 0))

    # BR ┘: 수평팔(아래) + 수직팔(오른쪽)
    cv2.rectangle(img, (_mm(pw_mm - off - sz, "x"), _mm(ph_mm - off - th, "y")),
                  (_mm(pw_mm - off, "x"), _mm(ph_mm - off, "y")), (0, 0, 0), -1)
    cv2.rectangle(img, (_mm(pw_mm - off - th, "x"), _mm(ph_mm - off - sz, "y")),
                  (_mm(pw_mm - off, "x"), _mm(ph_mm - off, "y")), (0, 0, 0), -1)

    # 2. 컬럼 로컬 앵커 (meta["columns"][*]["anchors"]) — engine이 local affine 보정에 사용
    for col in meta.get("columns", []):
        anchors = col.get("anchors") or {}
        for pos in ("top", "bottom"):
            a = anchors.get(pos)
            if not a:
                continue
            cx_mm = float(a["center"]["x"])
            cy_mm = float(a["center"]["y"])
            sz_mm = float(a.get("size", 2.0))
            half = sz_mm / 2
            cv2.rectangle(img,
                          (_mm(cx_mm - half, "x"), _mm(cy_mm - half, "y")),
                          (_mm(cx_mm + half, "x"), _mm(cy_mm + half, "y")),
                          (0, 0, 0), -1)

    # 2b. identifier 로컬 앵커 (meta["identifier"]["anchors"]) — identifier.py가 사용
    ident = meta.get("identifier") or {}
    id_anchors = ident.get("anchors") or {}
    for pos in ("TL", "BR"):
        a = id_anchors.get(pos)
        if not a:
            continue
        cx_mm = float(a["center"]["x"])
        cy_mm = float(a["center"]["y"])
        sz_mm = float(a.get("size", 2.0))
        half = sz_mm / 2
        cv2.rectangle(img,
                      (_mm(cx_mm - half, "x"), _mm(cy_mm - half, "y")),
                      (_mm(cx_mm + half, "x"), _mm(cy_mm + half, "y")),
                      (0, 0, 0), -1)

    # 3. 버블 (빈 테두리 + 마킹된 것 fill)
    marks = marks or {}
    for q in meta["questions"]:
        q_num = q["question_number"]
        for c in q["choices"]:
            cx = _mm(c["center"]["x"], "x")
            cy = _mm(c["center"]["y"], "y")
            rx = _mm(c["radius_x"], "x")
            ry = _mm(c["radius_y"], "y")
            cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, (180, 180, 180), 1)
            if str(q_num) in marks and str(marks[str(q_num)]) == c["label"]:
                color = (mark_intensity, mark_intensity, mark_intensity)
                cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, color, -1)

    return img


# ══════════════════════════════════════════
# 변형 시뮬레이션
# ══════════════════════════════════════════

def distort(img: np.ndarray, *,
            rotation_deg: float = 0.0,
            crop_mm: float = 0.0,
            noise_sigma: float = 0.0,
            blur_ksize: int = 0,
            shadow_pct: float = 0.0,
            dpi: int = 300) -> np.ndarray:
    """변형 체인. 실스캐너에서 나올 수 있는 왜곡 종류 시뮬."""
    out = img.copy()
    h, w = out.shape[:2]

    # 회전 — 새 bounding box 크기로 확장 (종이 코너가 스캔 영역 밖으로 나가지 않도록)
    # 실제 스캐너는 종이보다 스캔 영역이 크기 때문에, 삐뚤어진 종이의 코너가 다 잡힘.
    if abs(rotation_deg) > 1e-3:
        rad = np.deg2rad(abs(rotation_deg))
        cos_t, sin_t = abs(np.cos(rad)), abs(np.sin(rad))
        new_w = int(np.ceil(h * sin_t + w * cos_t))
        new_h = int(np.ceil(h * cos_t + w * sin_t))
        M = cv2.getRotationMatrix2D((w / 2, h / 2), rotation_deg, 1.0)
        M[0, 2] += (new_w - w) / 2
        M[1, 2] += (new_h - h) / 2
        out = cv2.warpAffine(out, M, (new_w, new_h), borderValue=(255, 255, 255))

    # ADF 가장자리 잘림 — 이미지 크기 축소 (실제 ADF 시나리오)
    # 회전 후 확장된 이미지 크기 기준으로 crop (회전+crop 조합이 마커를 범위 밖으로 밀어내는 버그 방지).
    if crop_mm > 0:
        ch, cw = out.shape[:2]
        px_per_mm = dpi / 25.4
        c = int(round(crop_mm * px_per_mm))
        if c > 0 and c * 2 < min(ch, cw):
            out = out[c:ch - c, c:cw - c]

    # 가우시안 노이즈 (메모리 절약 위해 float32 → int16 즉시 변환)
    if noise_sigma > 0:
        noise = (np.random.randn(*out.shape).astype(np.float32) * noise_sigma).astype(np.int16)
        out_i = out.astype(np.int16)
        out_i += noise
        out = np.clip(out_i, 0, 255).astype(np.uint8)
        del noise, out_i

    # 블러
    if blur_ksize > 0 and blur_ksize % 2 == 1:
        out = cv2.GaussianBlur(out, (blur_ksize, blur_ksize), 0)

    # 그림자 (좌측 반 어둡게, 조명 불균일)
    if shadow_pct > 0:
        h2, w2 = out.shape[:2]
        mask = np.ones((h2, w2), dtype=np.float32)
        half = w2 // 2
        for x in range(half):
            mask[:, x] = 1.0 - (shadow_pct / 100.0) * (1.0 - x / half)
        out = np.clip(out.astype(np.float32) * mask[..., None], 0, 255).astype(np.uint8)

    return out


# ══════════════════════════════════════════
# 정확도 측정
# ══════════════════════════════════════════

@dataclass
class ScenarioResult:
    name: str
    aligned: bool
    method: str
    correct: int
    total: int
    blank_correct: int
    blank_total: int
    wrong: List[str]

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total > 0 else 0.0


def run_scenario(name: str, meta: Dict, marks: Dict[str, str],
                 distort_kwargs: Dict, render_kwargs: Optional[Dict] = None) -> ScenarioResult:
    """마킹된 합성 이미지 → 변형 → 정렬 → 감지 → 정확도."""
    render_kwargs = render_kwargs or {}
    render_dpi = render_kwargs.pop("dpi", 300)
    # 마킹 + blank 혼합 (짝수 문항은 marks에 있고, 홀수는 blank)
    img = draw_omr_image(meta, marks=marks, dpi=render_dpi, **render_kwargs)
    distorted = distort(img, dpi=render_dpi, **distort_kwargs)

    align_result = align_to_a4_landscape(image_bgr=distorted, meta=meta)
    answers = detect_omr_answers_v7(
        image_bgr=align_result.image, meta=meta, config=AnswerDetectConfig(),
    )

    correct = 0
    blank_correct = 0
    blank_total = 0
    wrong: List[str] = []

    for r in answers:
        qid = str(r.question_id)
        expected = marks.get(qid)
        if expected is None:
            blank_total += 1
            if r.status == "blank":
                correct += 1
                blank_correct += 1
            else:
                wrong.append(f"Q{qid}:blank→{r.detected}")
        else:
            if r.detected == [expected] and r.status == "ok":
                correct += 1
            else:
                wrong.append(f"Q{qid}:{expected}→{r.detected}({r.status})")

    return ScenarioResult(
        name=name,
        aligned=align_result.success,
        method=align_result.method,
        correct=correct,
        total=len(answers),
        blank_correct=blank_correct,
        blank_total=blank_total,
        wrong=wrong[:5],  # 최대 5개만
    )


# ══════════════════════════════════════════
# 시나리오 정의 + 실행
# ══════════════════════════════════════════

# (name, distort_kwargs, target_accuracy, render_kwargs)
SCENARIOS = [
    ("clean",         {},                                                  1.00, {}),
    ("rot_+2",        {"rotation_deg": 2.0},                               0.95, {}),
    ("rot_-2",        {"rotation_deg": -2.0},                              0.95, {}),
    ("rot_+5",        {"rotation_deg": 5.0},                               0.90, {}),
    ("rot_-5",        {"rotation_deg": -5.0},                              0.90, {}),
    ("rot_+10",       {"rotation_deg": 10.0},                              0.85, {}),
    ("rot_-10",       {"rotation_deg": -10.0},                             0.85, {}),
    ("crop_1mm",      {"crop_mm": 1.0},                                    0.95, {}),
    ("crop_3mm",      {"crop_mm": 3.0},                                    0.85, {}),
    ("crop_5mm",      {"crop_mm": 5.0},                                    0.75, {}),
    ("noise_s5",      {"noise_sigma": 5.0},                                0.95, {}),
    ("noise_s15",     {"noise_sigma": 15.0},                               0.90, {}),
    ("noise_s25",     {"noise_sigma": 25.0},                               0.85, {}),
    ("blur_3",        {"blur_ksize": 3},                                   0.95, {}),
    ("blur_5",        {"blur_ksize": 5},                                   0.90, {}),
    ("blur_7",        {"blur_ksize": 7},                                   0.85, {}),
    ("shadow_15",     {"shadow_pct": 15.0},                                0.90, {}),
    ("shadow_30",     {"shadow_pct": 30.0},                                0.85, {}),
    ("shadow_50",     {"shadow_pct": 50.0},                                0.75, {}),
    ("low_dpi_200",   {},                                                  0.95, {"dpi": 200}),
    ("low_dpi_150",   {},                                                  0.90, {"dpi": 150}),
    # 연한 마킹 (학생이 흐릿하게 찍은 경우)
    ("weak_mark_60",  {},                                                  0.90, {"mark_intensity": 60}),
    ("weak_mark_100", {},                                                  0.85, {"mark_intensity": 100}),
    # 복합 변형
    ("combined_mild", {"rotation_deg": 2.0, "noise_sigma": 8.0, "blur_ksize": 3}, 0.90, {}),
    ("combined_med",  {"rotation_deg": 4.0, "noise_sigma": 12.0, "blur_ksize": 3, "shadow_pct": 20.0}, 0.85, {}),
    ("combined_harsh", {"rotation_deg": 5.0, "noise_sigma": 15.0, "blur_ksize": 5, "shadow_pct": 25.0, "crop_mm": 2.0}, 0.75, {}),
]


def run_identifier_scenario(meta: Dict, digits_marks: Dict[int, int],
                            distort_kwargs: Dict) -> Dict:
    """식별번호 합성 → 변형 → 정렬 → identifier detect."""
    img = draw_omr_image(meta, marks={}, dpi=300)
    # 식별번호 버블 마킹
    pw_mm = meta["page"]["width"]
    ph_mm = meta["page"]["height"]
    h, w = img.shape[:2]
    sx, sy = w / pw_mm, h / ph_mm
    for d_idx, value in digits_marks.items():
        digit = meta["identifier"]["digits"][d_idx]
        bub = digit["bubbles"][value]
        cx = int(round(bub["center"]["x"] * sx))
        cy = int(round(bub["center"]["y"] * sy))
        rx = int(round(bub["radius_x"] * sx))
        ry = int(round(bub["radius_y"] * sy))
        cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, (0, 0, 0), -1)

    distorted = distort(img, dpi=300, **distort_kwargs)
    align_result = align_to_a4_landscape(image_bgr=distorted, meta=meta)
    result = detect_identifier_v1(image_bgr=align_result.image, meta=meta,
                                  cfg=IdentifierConfigV1())

    detected = result.get("digits", [])
    correct = 0
    for d_idx, expected in digits_marks.items():
        detected_value = detected[d_idx].get("value") if d_idx < len(detected) else None
        if str(detected_value) == str(expected):
            correct += 1
    return {
        "method": align_result.method,
        "correct": correct,
        "total": len(digits_marks),
        "detected": [(d.get("digit_index"), d.get("value")) for d in detected],
    }


def main():
    print("=" * 70)
    print("OMR Full Pipeline 합성 시뮬레이션 [FULL_PIPELINE]")
    print("=" * 70)

    # 고정 seed — 재현 가능
    np.random.seed(42)

    meta = build_omr_meta(question_count=30, n_choices=5)

    # 마킹 패턴: 짝수 문항만 마킹 (blank 검증 동시)
    # 선택지는 모듈로 분산
    marks = {str(i): str(((i - 1) % 5) + 1) for i in range(1, 31) if i % 2 == 0}

    print(f"\n설정: 30문항, 5지선다, 짝수 문항(15개) 마킹, 홀수 문항(15개) blank")
    print(f"v15 meta: markers={list(meta['markers'].keys())} (인식마크 4코너만, 타이밍 스트립 없음)")
    print()

    results: List[ScenarioResult] = []
    for name, kwargs, _target, render_kwargs in SCENARIOS:
        try:
            r = run_scenario(name, meta, marks, kwargs, render_kwargs=dict(render_kwargs))
            results.append(r)
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
            traceback.print_exc()

    # ── 결과 표 ──
    print(f"{'Scenario':15s} {'Align':20s} {'Accuracy':12s} {'Blank':10s} {'Wrong':s}")
    print("-" * 100)
    n_pass = 0
    for (name, _kwargs, target, _rk), r in zip(SCENARIOS, results):
        status = "PASS" if r.accuracy >= target else "FAIL"
        if r.accuracy >= target:
            n_pass += 1
        acc_str = f"{r.accuracy*100:.1f}% ({r.correct}/{r.total})"
        blank_str = f"{r.blank_correct}/{r.blank_total}"
        wrong_str = ", ".join(r.wrong[:3]) if r.wrong else "—"
        print(f"{r.name:15s} {r.method:20s} {acc_str:12s} {blank_str:10s} "
              f"[{status} ≥{target:.0%}] {wrong_str}")

    print("-" * 100)
    print(f"\n결과: {n_pass}/{len(SCENARIOS)} 시나리오 통과")

    # 실패 시나리오 상세
    failed = [(s, r) for (s, _, t, _rk), r in zip(SCENARIOS, results) if r.accuracy < t]
    if failed:
        print(f"\n실패 {len(failed)}건:")
        for s, r in failed:
            print(f"  {r.name}: {r.accuracy*100:.1f}% (method={r.method}, aligned={r.aligned})")
            for w in r.wrong:
                print(f"    - {w}")

    # ── 식별번호(전화번호 8자리) 검출 검증 ──
    print("\n─ Identifier 8자리 검출 (앵커 로컬 정렬 사용) ─")
    id_digits = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8}  # 12345678
    id_scenarios = [
        ("clean", {}),
        ("rot_+3", {"rotation_deg": 3.0}),
        ("noise_s10", {"noise_sigma": 10.0}),
        ("blur_3", {"blur_ksize": 3}),
    ]
    id_pass = 0
    for name, kwargs in id_scenarios:
        r = run_identifier_scenario(meta, id_digits, kwargs)
        ok = r["correct"] == r["total"]
        if ok:
            id_pass += 1
        status = "PASS" if ok else "FAIL"
        print(f"  id[{name:12s}] {r['method']:20s} {r['correct']}/{r['total']} [{status}]")

    total_pass = n_pass + id_pass
    total_total = len(SCENARIOS) + len(id_scenarios)
    print(f"\n최종: {total_pass}/{total_total} 통과")

    sys.exit(0 if total_pass == total_total else 1)


if __name__ == "__main__":
    main()

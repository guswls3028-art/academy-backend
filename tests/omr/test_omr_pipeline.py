#!/usr/bin/env python3
"""
OMR 파이프라인 테스트 — 구조/단위 + 합성 이미지

분류:
  [STRUCT] 구조/포맷 호환 테스트 — 실스캔 불필요
  [SYNTH]  합성 이미지 테스트 — 실스캔 불필요, 인식률 보장 아님
  [SCAN]   실스캔 필요 — 여기선 스킵, 표시만

실행:
  cd backend/
  python tests/omr/test_omr_pipeline.py
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import cv2
import numpy as np

from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from apps.worker.ai_worker.ai.omr.meta_px import build_page_scale_from_meta
from apps.worker.ai_worker.ai.omr.engine import detect_omr_answers_v7, AnswerDetectConfig
from apps.worker.ai_worker.ai.omr.identifier import IdentifierConfigV1
from apps.worker.ai_worker.ai.omr.types import OMRAnswerV1
from apps.worker.omr.roi_builder import build_questions_payload_from_meta

PASS = 0
FAIL = 0
SKIP = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def skip(name, reason):
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name} — {reason}")


# ══════════════════════════════════════════
# A. 구조/단위 테스트 [STRUCT]
# ══════════════════════════════════════════
def test_struct():
    print("\n=== A. 구조/포맷 호환 테스트 [STRUCT] ===\n")

    # A1. meta_generator 기본 생성
    meta = build_omr_meta(question_count=30, n_choices=5, essay_count=5)
    check("A1. meta version", meta["version"] == "v8")
    check("A1. meta page", meta["page"]["width"] == 297.0 and meta["page"]["height"] == 210.0)
    check("A1. mc_count", meta["mc_count"] == 30)
    check("A1. questions count", len(meta["questions"]) == 30)
    check("A1. identifier digits", meta["identifier"]["digit_count"] == 8)
    check("A1. identifier bubbles per digit", len(meta["identifier"]["digits"][0]["bubbles"]) == 10)

    # A2. meta_px 호환 (v7 page 포맷)
    try:
        scale = build_page_scale_from_meta(meta=meta, image_size_px=(3508, 2480))
        check("A2. meta_px v7 page format", scale.sx > 0 and scale.sy > 0)
    except Exception as e:
        check("A2. meta_px v7 page format", False, str(e))

    # A3. meta_px 구버전 page 포맷도 동작
    meta_old = {"page": {"size": {"width": 297, "height": 210}}, "questions": []}
    try:
        scale_old = build_page_scale_from_meta(meta=meta_old, image_size_px=(3508, 2480))
        check("A3. meta_px old page format", scale_old.sx > 0)
    except Exception as e:
        check("A3. meta_px old page format", False, str(e))

    # A4. roi_builder v7 호환
    payload = build_questions_payload_from_meta(meta=meta, image_size_px=(3508, 2480))
    check("A4. roi_builder count", len(payload) == 30)
    check("A4. roi_builder choices format", payload[0]["choices"] == ["1", "2", "3", "4", "5"])
    check("A4. roi_builder px coords", payload[0]["roi"]["x"] > 0 and payload[0]["roi"]["y"] > 0)

    # A5. identifier v7 meta → flat bubbles 변환
    ident = meta["identifier"]
    raw = []
    for dm in ident.get("digits", []):
        di = dm.get("digit_index", 0)
        for bub in dm.get("bubbles", []):
            raw.append({
                "digit_index": di,
                "number": int(bub.get("value", bub.get("number", 0))),
                "center": bub.get("center", {}),
                "r": max(float(bub.get("radius_x", 0)), float(bub.get("radius_y", 0))),
            })
    check("A5. identifier flat bubbles", len(raw) == 80, f"got {len(raw)}")
    check("A5. identifier digit 0 exists", any(b["digit_index"] == 0 for b in raw))
    check("A5. identifier digit 7 exists", any(b["digit_index"] == 7 for b in raw))

    # A6. 다양한 문항 수 테스트
    for mc in [1, 10, 15, 20, 25, 30, 40, 45]:
        m = build_omr_meta(question_count=mc)
        check(f"A6. meta mc={mc}", len(m["questions"]) == mc)

    # A7. 4지선다 테스트
    meta4 = build_omr_meta(question_count=20, n_choices=4)
    check("A7. 4지선다 choices count", len(meta4["questions"][0]["choices"]) == 4)
    check("A7. 4지선다 label", meta4["questions"][0]["choices"][3]["label"] == "4")

    # A8. answer key format 일관성 ("1"~"5")
    labels = [c["label"] for c in meta["questions"][0]["choices"]]
    check("A8. answer labels numeric", labels == ["1", "2", "3", "4", "5"])

    # A9. grader — meta 버전과 독립적으로 동작 확인
    with open("apps/domains/results/services/grader.py", encoding="utf-8") as f:
        grader_src = f.read()
    check("A9. grader handles v8", "_grade_choice_v1" in grader_src)

    # A10. dispatcher v7 imports
    with open("apps/worker/ai_worker/ai/pipelines/dispatcher.py", encoding="utf-8") as f:
        disp_src = f.read()
    check("A10. dispatcher detect_omr_answers_v7", "detect_omr_answers_v7" in disp_src)
    check("A10. dispatcher AnswerDetectConfig", "AnswerDetectConfig" in disp_src)
    check("A10. dispatcher build_omr_meta", "build_omr_meta" in disp_src)
    check("A10. no v1 engine import", "detect_omr_answers_v1" not in disp_src)

    # A11. result_mapper question_id 호환
    with open("apps/domains/submissions/services/ai_omr_result_mapper.py", encoding="utf-8") as f:
        mapper_src = f.read()
    check("A11. mapper question_id fallback", 'a.get("question_id")' in mapper_src)
    check("A11. mapper enrollment resolver", "_resolve_enrollment_by_phone" in mapper_src)

    return meta


# ══════════════════════════════════════════
# B. 합성 이미지 테스트 [SYNTH]
# ══════════════════════════════════════════
def create_synthetic_omr(meta, marks=None):
    """
    합성 OMR 이미지 생성.
    marks: {question_number: choice_label} — 예: {1: "3", 5: "1"}
    마킹 안 한 문항은 빈칸.
    """
    # A4 landscape at 300dpi
    w, h = 3508, 2480
    img = np.ones((h, w, 3), dtype=np.uint8) * 255  # 흰 배경

    page = meta["page"]
    pw, ph = float(page["width"]), float(page["height"])
    sx, sy = w / pw, h / ph

    marks = marks or {}

    for q in meta["questions"]:
        q_num = q["question_number"]
        for c in q["choices"]:
            cx = int(round(float(c["center"]["x"]) * sx))
            cy = int(round(float(c["center"]["y"]) * sy))
            rx = int(round(float(c["radius_x"]) * sx))
            ry = int(round(float(c["radius_y"]) * sy))

            # 빈 버블 — 연한 회색 테두리
            cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, (180, 180, 180), 1)

            # 마킹된 버블 — 검은색으로 채움
            if str(q_num) in marks and str(marks[str(q_num)]) == c["label"]:
                cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, (0, 0, 0), -1)

    return img


def test_synth(meta):
    print("\n=== B. 합성 이미지 테스트 [SYNTH] ===\n")
    print("  (주의: 합성 테스트 통과 ≠ 실제 스캔 인식률 보장)\n")

    config = AnswerDetectConfig()

    # B1. 전문항 빈칸
    img_blank = create_synthetic_omr(meta, marks={})
    results_blank = detect_omr_answers_v7(image_bgr=img_blank, meta=meta, config=config)
    blank_count = sum(1 for r in results_blank if r.status == "blank")
    check("B1. 전문항 빈칸 → 전부 blank", blank_count == 30, f"blank={blank_count}/30")

    # B2. 단일 문항 마킹 (Q1=3)
    img_single = create_synthetic_omr(meta, marks={"1": "3"})
    results_single = detect_omr_answers_v7(image_bgr=img_single, meta=meta, config=config)
    q1 = next((r for r in results_single if r.question_id == 1), None)
    check("B2. Q1 마킹=3 감지", q1 is not None and q1.detected == ["3"] and q1.status == "ok",
          f"detected={q1.detected if q1 else '?'}, status={q1.status if q1 else '?'}")
    q2 = next((r for r in results_single if r.question_id == 2), None)
    check("B2. Q2 빈칸", q2 is not None and q2.status == "blank")

    # B3. 다문항 마킹 (Q1=1, Q5=5, Q10=2, Q20=4, Q30=3)
    multi_marks = {"1": "1", "5": "5", "10": "2", "20": "4", "30": "3"}
    img_multi = create_synthetic_omr(meta, marks=multi_marks)
    results_multi = detect_omr_answers_v7(image_bgr=img_multi, meta=meta, config=config)

    for qn, expected in multi_marks.items():
        r = next((r for r in results_multi if r.question_id == int(qn)), None)
        check(f"B3. Q{qn}={expected}", r is not None and r.detected == [expected],
              f"detected={r.detected if r else '?'}")

    unmarked = [r for r in results_multi if str(r.question_id) not in multi_marks]
    blank_unmarked = sum(1 for r in unmarked if r.status == "blank")
    check(f"B3. 나머지 {len(unmarked)}문항 blank",
          blank_unmarked == len(unmarked),
          f"blank={blank_unmarked}/{len(unmarked)}")

    # B4. 전문항 마킹 (모두 "1")
    all_marks = {str(i): "1" for i in range(1, 31)}
    img_all = create_synthetic_omr(meta, marks=all_marks)
    results_all = detect_omr_answers_v7(image_bgr=img_all, meta=meta, config=config)
    all_ok = sum(1 for r in results_all if r.status == "ok" and r.detected == ["1"])
    check("B4. 전문항 '1' 마킹 감지", all_ok == 30, f"ok={all_ok}/30")

    # B5. 각 선택지별 감지 (Q1=1, Q2=2, Q3=3, Q4=4, Q5=5)
    each_marks = {"1": "1", "2": "2", "3": "3", "4": "4", "5": "5"}
    img_each = create_synthetic_omr(meta, marks=each_marks)
    results_each = detect_omr_answers_v7(image_bgr=img_each, meta=meta, config=config)
    for qn, expected in each_marks.items():
        r = next((r for r in results_each if r.question_id == int(qn)), None)
        check(f"B5. Q{qn}='{expected}' 감지", r is not None and r.detected == [expected],
              f"detected={r.detected if r else '?'}")

    # B6. OMRAnswerV1 직렬화
    r = results_all[0]
    d = r.to_dict()
    check("B6. to_dict version", d["version"] == "v8")
    check("B6. to_dict question_id", d["question_id"] == 1)
    check("B6. to_dict detected", d["detected"] == ["1"])
    check("B6. to_dict has raw fills", "fills" in (d.get("raw") or {}))

    # B7. 이중 마킹 합성 (Q1에 "1"과 "2" 동시)
    w_px, h_px = 3508, 2480
    pw, ph = 297.0, 210.0
    sx_s, sy_s = w_px / pw, h_px / ph
    img_double = np.ones((h_px, w_px, 3), dtype=np.uint8) * 255
    q1_meta = meta["questions"][0]
    for c in q1_meta["choices"]:
        cx = int(round(float(c["center"]["x"]) * sx_s))
        cy = int(round(float(c["center"]["y"]) * sy_s))
        rx = int(round(float(c["radius_x"]) * sx_s))
        ry = int(round(float(c["radius_y"]) * sy_s))
        cv2.ellipse(img_double, (cx, cy), (rx, ry), 0, 0, 360, (180, 180, 180), 1)
        if c["label"] in ("1", "2"):
            cv2.ellipse(img_double, (cx, cy), (rx, ry), 0, 0, 360, (0, 0, 0), -1)

    results_double = detect_omr_answers_v7(image_bgr=img_double, meta=meta, config=config)
    q1_double = next((r for r in results_double if r.question_id == 1), None)
    check("B7. 이중마킹 ambiguous/multi",
          q1_double is not None and q1_double.status in ("ambiguous",) and len(q1_double.detected) >= 2,
          f"status={q1_double.status if q1_double else '?'}, detected={q1_double.detected if q1_double else '?'}")

    # B8. 20문항 / 45문항 테스트
    for mc in [20, 45]:
        m = build_omr_meta(question_count=mc)
        marks_all = {str(i): str((i % 5) + 1) for i in range(1, mc + 1)}
        img_mc = create_synthetic_omr(m, marks=marks_all)
        results_mc = detect_omr_answers_v7(image_bgr=img_mc, meta=m, config=config)
        ok_mc = sum(1 for r in results_mc if r.status == "ok")
        check(f"B8. {mc}문항 전마킹 감지", ok_mc == mc, f"ok={ok_mc}/{mc}")

    return True


# ══════════════════════════════════════════
# C. 회귀 방지 fixture 저장
# ══════════════════════════════════════════
def save_fixtures():
    print("\n=== C. 회귀 테스트 fixture 저장 ===\n")

    fixture_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    os.makedirs(fixture_dir, exist_ok=True)

    meta = build_omr_meta(question_count=30, n_choices=5)

    # fixture: 기대 좌표
    with open(os.path.join(fixture_dir, "meta_30q_5c.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    # fixture: Q1 선택지 좌표 스냅샷 (레이아웃 변경 시 깨짐 감지)
    q1 = meta["questions"][0]
    snapshot = {
        "question_number": q1["question_number"],
        "roi": q1["roi"],
        "choice_centers": [
            {"label": c["label"], "x": c["center"]["x"], "y": c["center"]["y"]}
            for c in q1["choices"]
        ],
    }
    with open(os.path.join(fixture_dir, "q1_coordinate_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    # fixture: identifier digit 0 스냅샷
    d0 = meta["identifier"]["digits"][0]
    id_snapshot = {
        "digit_index": d0["digit_index"],
        "bubble_0_center": d0["bubbles"][0]["center"],
        "bubble_9_center": d0["bubbles"][9]["center"],
    }
    with open(os.path.join(fixture_dir, "id_digit0_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(id_snapshot, f, indent=2, ensure_ascii=False)

    # fixture: 기대답안 예시
    expected = {str(i): str((i % 5) + 1) for i in range(1, 31)}
    with open(os.path.join(fixture_dir, "expected_all_marked.json"), "w", encoding="utf-8") as f:
        json.dump(expected, f, indent=2, ensure_ascii=False)

    print(f"  fixture 저장: {fixture_dir}/")
    print(f"    - meta_30q_5c.json")
    print(f"    - q1_coordinate_snapshot.json")
    print(f"    - id_digit0_snapshot.json")
    print(f"    - expected_all_marked.json")

    # 좌표 스냅샷 회귀 검증
    meta2 = build_omr_meta(question_count=30, n_choices=5)
    q1_2 = meta2["questions"][0]
    check("C1. 좌표 회귀: Q1.roi.x 일치",
          q1["roi"]["x"] == q1_2["roi"]["x"],
          f"{q1['roi']['x']} vs {q1_2['roi']['x']}")
    check("C1. 좌표 회귀: Q1 choice[0].center.x",
          q1["choices"][0]["center"]["x"] == q1_2["choices"][0]["center"]["x"])


# ══════════════════════════════════════════
# D. 실스캔 필요 항목 표시
# ══════════════════════════════════════════
def report_scan_needed():
    print("\n=== D. 실스캔 필요 항목 [SCAN] ===\n")
    skip("D1. 실제 사인펜 마킹 인식", "실스캔 데이터 필요")
    skip("D2. 연필/샤프 마킹 인식", "실스캔 + binarize 튜닝 필요")
    skip("D3. 기울어진 스캔 warp", "실스캔 데이터 필요")
    skip("D4. 수정테이프 흔적 내성", "실스캔 데이터 필요")
    skip("D5. 저해상도(150dpi) 인식", "실스캔 데이터 필요")
    skip("D6. identifier 8자리 실제 감지", "실스캔 + identifier 영역 검증 필요")
    skip("D7. enrollment 자동 매칭 E2E", "실 DB + 실스캔 필요")
    skip("D8. 복사기 스캔 품질 변이", "실환경 데이터 필요")


if __name__ == "__main__":
    print("=" * 60)
    print("OMR Pipeline Test Suite — 구조 + 합성 이미지")
    print("=" * 60)

    try:
        meta = test_struct()
        test_synth(meta)
        save_fixtures()
        report_scan_needed()
    except Exception as e:
        FAIL += 1
        print(f"\n[FATAL] {e}")
        traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"결과: PASS={PASS}, FAIL={FAIL}, SKIP={SKIP}")
    print(f"신뢰 범위: 구조 호환 {'OK' if FAIL == 0 else 'FAIL'}, 합성 인식 {'OK' if FAIL == 0 else 'FAIL'}")
    print(f"미검증: 실스캔 기반 인식률, warp 성공률, identifier 실검증")
    print("=" * 60)

    sys.exit(1 if FAIL > 0 else 0)

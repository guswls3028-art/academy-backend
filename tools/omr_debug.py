#!/usr/bin/env python3
"""
OMR 디버그/튜닝 CLI 도구

사용법:
  python tools/omr_debug.py scan <image_path> [--mc=30] [--choices=5] [--debug-dir=./omr_debug_out]
  python tools/omr_debug.py meta [--mc=30] [--choices=5]
  python tools/omr_debug.py coords <image_path> [--mc=30]  # 좌표 시각화 (ROI 그리기)

요구사항:
  pip install opencv-python numpy

예시:
  # 1. OMR 답안지를 인쇄 → 마킹 → 스캔 → scan.jpg로 저장
  # 2. 스캔 이미지로 디버그 실행:
  python tools/omr_debug.py scan scan.jpg --mc=30 --debug-dir=./debug_out
  # 3. debug_out/ 폴더에서 결과 확인:
  #    - aligned.jpg (워프된 이미지)
  #    - roi_overlay.jpg (ROI 좌표 시각화)
  #    - fills.json (각 문항별 fill ratio)
  #    - result.json (최종 판정)
  #    - identifier.json (식별번호 감지 결과)
"""
import argparse
import json
import os
import sys

# Django 없이 실행 가능하도록 path 설정
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cmd_meta(args):
    """메타 좌표 출력."""
    from apps.domains.assets.omr.services.meta_generator import build_omr_meta
    meta = build_omr_meta(
        question_count=args.mc,
        n_choices=args.choices,
        essay_count=args.essay,
    )
    print(json.dumps(meta, indent=2, ensure_ascii=False))


def cmd_scan(args):
    """스캔 이미지에서 OMR 감지 실행."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("ERROR: opencv-python, numpy 설치 필요: pip install opencv-python numpy")
        sys.exit(1)

    from apps.domains.assets.omr.services.meta_generator import build_omr_meta
    from apps.worker.ai_worker.ai.omr.engine import detect_omr_answers_v7, AnswerDetectConfig
    from apps.worker.ai_worker.ai.omr.identifier import detect_identifier_v1, IdentifierConfigV1

    image_path = args.image
    if not os.path.exists(image_path):
        print(f"ERROR: 파일 없음: {image_path}")
        sys.exit(1)

    debug_dir = args.debug_dir
    os.makedirs(debug_dir, exist_ok=True)

    # 1) 메타 생성
    meta = build_omr_meta(
        question_count=args.mc,
        n_choices=args.choices,
        essay_count=args.essay,
    )
    with open(os.path.join(debug_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"[1/6] 메타 생성: {args.mc}문항, {args.choices}지선다")

    # 2) 이미지 로드
    img_bgr = cv2.imread(image_path)
    if img_bgr is None:
        print(f"ERROR: 이미지 로드 실패: {image_path}")
        sys.exit(1)
    print(f"[2/6] 이미지 로드: {img_bgr.shape[1]}x{img_bgr.shape[0]}")

    # 3) 워프 시도
    from apps.worker.omr.warp import warp_to_a4_landscape
    aligned = img_bgr
    warped = warp_to_a4_landscape(image_bgr=img_bgr)
    if warped is not None:
        aligned = warped
        print(f"[3/6] 워프 성공: {aligned.shape[1]}x{aligned.shape[0]}")
    else:
        print(f"[3/6] 워프 실패 — 원본 이미지 사용 (스캔 이미지는 워프 불필요할 수 있음)")

    cv2.imwrite(os.path.join(debug_dir, "aligned.jpg"), aligned)

    # 4) Identifier 감지
    print("[4/6] 식별자 감지 중...")
    try:
        ident = detect_identifier_v1(
            image_bgr=aligned,
            meta=meta,
            cfg=IdentifierConfigV1(),
        )
        with open(os.path.join(debug_dir, "identifier.json"), "w", encoding="utf-8") as f:
            json.dump(ident, f, indent=2, ensure_ascii=False)
        id_str = ident.get("identifier", "???")
        id_status = ident.get("status", "???")
        print(f"       식별번호: {id_str} (status: {id_status})")
    except Exception as e:
        print(f"       식별자 감지 실패: {e}")
        ident = {"status": "error", "error": str(e)}

    # 5) 답안 감지
    print("[5/6] 답안 감지 중...")
    config = AnswerDetectConfig(
        blank_threshold=args.blank_threshold,
        conf_gap_threshold=args.gap_threshold,
        binarize_threshold=args.binarize_threshold,
        roi_expand_k=args.roi_expand,
    )
    results = detect_omr_answers_v7(
        image_bgr=aligned,
        meta=meta,
        config=config,
    )
    result_dicts = [r.to_dict() for r in results]

    # Fill ratios 추출
    fills = {}
    for r in result_dicts:
        raw = r.get("raw") or {}
        fills[f"Q{r['question_id']}"] = {
            "detected": r["detected"],
            "status": r["status"],
            "confidence": r["confidence"],
            "fills": raw.get("fills", {}),
        }

    with open(os.path.join(debug_dir, "fills.json"), "w", encoding="utf-8") as f:
        json.dump(fills, f, indent=2, ensure_ascii=False)
    with open(os.path.join(debug_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(result_dicts, f, indent=2, ensure_ascii=False)

    # 판정 요약
    ok_count = sum(1 for r in results if r.status == "ok")
    blank_count = sum(1 for r in results if r.status == "blank")
    ambig_count = sum(1 for r in results if r.status == "ambiguous")
    err_count = sum(1 for r in results if r.status == "error")
    print(f"       결과: OK={ok_count}, blank={blank_count}, ambiguous={ambig_count}, error={err_count}")

    # 6) ROI 시각화
    print("[6/6] ROI 시각화 생성 중...")
    overlay = aligned.copy()
    page = meta.get("page", {})
    pw, ph = float(page.get("width", 297)), float(page.get("height", 210))
    h, w = aligned.shape[:2]
    sx, sy = w / pw, h / ph

    for q in meta.get("questions", []):
        roi = q.get("roi", {})
        x1 = int(round(float(roi.get("x", 0)) * sx))
        y1 = int(round(float(roi.get("y", 0)) * sy))
        x2 = int(round((float(roi.get("x", 0)) + float(roi.get("w", 0))) * sx))
        y2 = int(round((float(roi.get("y", 0)) + float(roi.get("h", 0))) * sy))
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 0), 1)

        for c in q.get("choices", []):
            cx = int(round(float(c["center"]["x"]) * sx))
            cy = int(round(float(c["center"]["y"]) * sy))
            rx = int(round(float(c["radius_x"]) * sx))
            ry = int(round(float(c["radius_y"]) * sy))
            cv2.ellipse(overlay, (cx, cy), (rx, ry), 0, 0, 360, (0, 0, 255), 1)
            cv2.putText(overlay, c["label"], (cx - 4, cy + 3), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 0, 255), 1)

    # Identifier ROI
    ident_meta = meta.get("identifier", {})
    for digit in ident_meta.get("digits", []):
        for bub in digit.get("bubbles", []):
            cx = int(round(float(bub["center"]["x"]) * sx))
            cy = int(round(float(bub["center"]["y"]) * sy))
            rx = int(round(float(bub["radius_x"]) * sx))
            ry = int(round(float(bub["radius_y"]) * sy))
            cv2.ellipse(overlay, (cx, cy), (rx, ry), 0, 0, 360, (255, 0, 0), 1)

    cv2.imwrite(os.path.join(debug_dir, "roi_overlay.jpg"), overlay)

    print(f"\n✅ 디버그 결과 저장: {debug_dir}/")
    print(f"   - aligned.jpg     : 워프된 이미지")
    print(f"   - roi_overlay.jpg : ROI 좌표 시각화")
    print(f"   - meta.json       : 좌표 메타")
    print(f"   - fills.json      : 문항별 fill ratio")
    print(f"   - result.json     : 최종 감지 결과")
    print(f"   - identifier.json : 식별번호 결과")


def cmd_coords(args):
    """좌표 시각화 전용 (빈 이미지에 ROI 그리기)."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        print("ERROR: pip install opencv-python numpy")
        sys.exit(1)

    from apps.domains.assets.omr.services.meta_generator import build_omr_meta

    meta = build_omr_meta(question_count=args.mc, n_choices=args.choices)

    # A4 landscape at 300dpi
    w, h = 3508, 2480
    canvas = np.ones((h, w, 3), dtype=np.uint8) * 255

    pw, ph = 297.0, 210.0
    sx, sy = w / pw, h / ph

    for q in meta.get("questions", []):
        roi = q.get("roi", {})
        x1 = int(float(roi["x"]) * sx)
        y1 = int(float(roi["y"]) * sy)
        x2 = int((float(roi["x"]) + float(roi["w"])) * sx)
        y2 = int((float(roi["y"]) + float(roi["h"])) * sy)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (200, 200, 200), 1)

        for c in q.get("choices", []):
            cx = int(float(c["center"]["x"]) * sx)
            cy = int(float(c["center"]["y"]) * sy)
            rx = int(float(c["radius_x"]) * sx)
            ry = int(float(c["radius_y"]) * sy)
            cv2.ellipse(canvas, (cx, cy), (rx, ry), 0, 0, 360, (0, 0, 0), 2)
            cv2.putText(canvas, c["label"], (cx - 8, cy + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)

        qn = str(q["question_number"])
        cv2.putText(canvas, qn, (x1 + 10, int((y1 + y2) / 2) + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

    out = args.output or "omr_coords.jpg"
    cv2.imwrite(out, canvas)
    print(f"좌표 시각화 저장: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OMR 디버그/튜닝 CLI")
    sub = parser.add_subparsers(dest="cmd")

    # meta
    p_meta = sub.add_parser("meta", help="OMR 메타 좌표 출력")
    p_meta.add_argument("--mc", type=int, default=30)
    p_meta.add_argument("--choices", type=int, default=5)
    p_meta.add_argument("--essay", type=int, default=0)

    # scan
    p_scan = sub.add_parser("scan", help="스캔 이미지 OMR 감지")
    p_scan.add_argument("image", help="스캔 이미지 경로")
    p_scan.add_argument("--mc", type=int, default=30)
    p_scan.add_argument("--choices", type=int, default=5)
    p_scan.add_argument("--essay", type=int, default=0)
    p_scan.add_argument("--debug-dir", default="./omr_debug_out")
    p_scan.add_argument("--blank-threshold", type=float, default=0.060)
    p_scan.add_argument("--gap-threshold", type=float, default=0.055)
    p_scan.add_argument("--binarize-threshold", type=int, default=140)
    p_scan.add_argument("--roi-expand", type=float, default=1.55)

    # coords
    p_coords = sub.add_parser("coords", help="좌표 시각화 (빈 캔버스)")
    p_coords.add_argument("--mc", type=int, default=30)
    p_coords.add_argument("--choices", type=int, default=5)
    p_coords.add_argument("--output", default=None)

    # batch — 여러 이미지 일괄 검증
    p_batch = sub.add_parser("batch", help="폴더 내 모든 이미지 일괄 스캔 + 기대답안 비교")
    p_batch.add_argument("scan_dir", help="스캔 이미지 폴더 (sample_scans/)")
    p_batch.add_argument("--mc", type=int, default=30)
    p_batch.add_argument("--choices", type=int, default=5)
    p_batch.add_argument("--debug-dir", default="./omr_batch_out")
    p_batch.add_argument("--blank-threshold", type=float, default=0.060)
    p_batch.add_argument("--gap-threshold", type=float, default=0.055)
    p_batch.add_argument("--binarize-threshold", type=int, default=140)
    p_batch.add_argument("--roi-expand", type=float, default=1.55)

    args = parser.parse_args()
    if args.cmd == "meta":
        cmd_meta(args)
    elif args.cmd == "scan":
        cmd_scan(args)
    elif args.cmd == "coords":
        cmd_coords(args)
    elif args.cmd == "batch":
        cmd_batch(args)
    else:
        parser.print_help()


def cmd_batch(args):
    """폴더 내 모든 이미지를 일괄 스캔하고 기대답안과 비교."""
    import glob
    scan_dir = args.scan_dir
    if not os.path.isdir(scan_dir):
        print(f"ERROR: 폴더 없음: {scan_dir}")
        sys.exit(1)

    images = sorted(
        glob.glob(os.path.join(scan_dir, "*.jpg")) +
        glob.glob(os.path.join(scan_dir, "*.jpeg")) +
        glob.glob(os.path.join(scan_dir, "*.png")) +
        glob.glob(os.path.join(scan_dir, "*.tiff"))
    )
    if not images:
        print(f"이미지 없음: {scan_dir}")
        sys.exit(1)

    os.makedirs(args.debug_dir, exist_ok=True)
    summary = []

    for img_path in images:
        name = os.path.splitext(os.path.basename(img_path))[0]
        sub_dir = os.path.join(args.debug_dir, name)
        print(f"\n{'='*50}")
        print(f"처리 중: {name}")

        # scan 명령과 동일한 args 구성
        class ScanArgs:
            pass
        sa = ScanArgs()
        sa.image = img_path
        sa.mc = args.mc
        sa.choices = args.choices
        sa.essay = 0
        sa.debug_dir = sub_dir
        sa.blank_threshold = args.blank_threshold
        sa.gap_threshold = args.gap_threshold
        sa.binarize_threshold = args.binarize_threshold
        sa.roi_expand = args.roi_expand

        try:
            cmd_scan(sa)
            # 결과 읽기
            result_path = os.path.join(sub_dir, "result.json")
            if os.path.exists(result_path):
                with open(result_path) as f:
                    results = json.load(f)
                ok = sum(1 for r in results if r["status"] == "ok")
                blank = sum(1 for r in results if r["status"] == "blank")
                ambig = sum(1 for r in results if r["status"] == "ambiguous")
                summary.append({"name": name, "ok": ok, "blank": blank, "ambiguous": ambig, "total": len(results)})
        except Exception as e:
            print(f"  ERROR: {e}")
            summary.append({"name": name, "error": str(e)})

        # 기대답안 비교 (expected_<name>.json 있으면)
        expected_path = os.path.join(scan_dir, f"expected_{name}.json")
        if os.path.exists(expected_path):
            with open(expected_path) as f:
                expected = json.load(f)
            result_path = os.path.join(sub_dir, "result.json")
            if os.path.exists(result_path):
                with open(result_path) as f:
                    actual = json.load(f)
                _compare_results(name, expected, actual)

    # 전체 요약
    print(f"\n{'='*50}")
    print("📊 전체 요약")
    print(f"{'='*50}")
    for s in summary:
        if "error" in s:
            print(f"  {s['name']}: ERROR — {s['error']}")
        else:
            pct = round(100 * s["ok"] / s["total"]) if s["total"] > 0 else 0
            print(f"  {s['name']}: OK={s['ok']}/{s['total']} ({pct}%), blank={s['blank']}, ambiguous={s['ambiguous']}")

    with open(os.path.join(args.debug_dir, "batch_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\n요약 저장: {args.debug_dir}/batch_summary.json")


def _compare_results(name, expected, actual):
    """기대답안과 실제 결과 비교."""
    # expected format: {"1": "3", "2": "1", ...}  (question_number → answer)
    actual_map = {}
    for r in actual:
        qid = str(r.get("question_id", ""))
        detected = r.get("detected", [])
        actual_map[qid] = detected[0] if len(detected) == 1 else ""

    correct, wrong, missing = 0, 0, 0
    for q_num, exp_ans in expected.items():
        act_ans = actual_map.get(str(q_num), "")
        if act_ans == str(exp_ans):
            correct += 1
        elif act_ans == "":
            missing += 1
        else:
            wrong += 1
            print(f"    ❌ Q{q_num}: expected={exp_ans}, got={act_ans}")

    total = correct + wrong + missing
    pct = round(100 * correct / total) if total > 0 else 0
    print(f"  📋 {name}: 정답률 {correct}/{total} ({pct}%), 오답={wrong}, 미감지={missing}")

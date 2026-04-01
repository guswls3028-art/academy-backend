#!/usr/bin/env python3
"""
OMR v9 합성 테스트 스캔 생성기 — 실제 스캔 특성 기반

실제 스캔 6장에서 분석한 특성:
- 배경: mean=244, σ=30 (순백 아님, 노이즈 있음)
- 마킹 버블: 84~157 intensity (사인펜, 품질 다양)
- 미마킹 버블: 248~253 (배경과 거의 동일)
- 인쇄선: 197~226 (연한 회색)
- 스캐너 노이즈 σ: 23~40
"""
import json
import math
import os
import random
import sys

import cv2
import numpy as np

# Direct import (no Django needed — meta_generator is pure Python math)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from apps.domains.assets.omr.services.meta_generator import build_omr_meta

OUT_DIR = "/tmp/omr_test_scans"
os.makedirs(OUT_DIR, exist_ok=True)

PAGE_W_MM, PAGE_H_MM = 297.0, 210.0
OUT_W, OUT_H = 3508, 2480
PX_PER_MM_X = OUT_W / PAGE_W_MM
PX_PER_MM_Y = OUT_H / PAGE_H_MM

def mm2px(x_mm, y_mm):
    return int(round(x_mm * PX_PER_MM_X)), int(round(y_mm * PX_PER_MM_Y))

def mm2px_len(mm):
    return int(round(mm * (PX_PER_MM_X + PX_PER_MM_Y) / 2))

# Students (Tenant 1)
STUDENTS = [
    {"name": "박시현", "phone8": "63537370"},
    {"name": "박해환", "phone8": "52729918"},
    {"name": "이다현", "phone8": "52431867"},
    {"name": "이은호", "phone8": "87559548"},
    {"name": "이준우", "phone8": "38410218"},
    {"name": "황상혁", "phone8": "82378990"},
]

# Variant configs (실제 스캔 특성 기반)
VARIANTS = [
    {"name": "clean",   "mark_intensity": (60, 90),   "noise_sigma": 25, "offset_mm": 0.0, "rotation_deg": 0.0, "fill_pct": (0.80, 0.95)},
    {"name": "heavy",   "mark_intensity": (40, 70),   "noise_sigma": 30, "offset_mm": 0.1, "rotation_deg": 0.0, "fill_pct": (0.85, 0.98)},
    {"name": "light",   "mark_intensity": (110, 160),  "noise_sigma": 35, "offset_mm": 0.2, "rotation_deg": 0.0, "fill_pct": (0.50, 0.70)},
    {"name": "offset",  "mark_intensity": (70, 110),  "noise_sigma": 28, "offset_mm": 0.5, "rotation_deg": 0.0, "fill_pct": (0.70, 0.85)},
    {"name": "rotated", "mark_intensity": (65, 100),  "noise_sigma": 40, "offset_mm": 0.2, "rotation_deg": 0.7, "fill_pct": (0.75, 0.90)},
]


def draw_corner_markers(img, meta):
    """v9 비대칭 코너 마커"""
    markers = meta.get("markers", {})

    # TL: solid square
    tl = markers.get("TL", {})
    cx, cy = mm2px(tl.get("center", {}).get("x", 5), tl.get("center", {}).get("y", 5))
    s = mm2px_len(tl.get("size", 4) / 2)
    cv2.rectangle(img, (cx-s, cy-s), (cx+s, cy+s), 0, -1)

    # TR: L-shape
    tr = markers.get("TR", {})
    cx, cy = mm2px(tr.get("center", {}).get("x", 292), tr.get("center", {}).get("y", 5))
    arm = mm2px_len(tr.get("arm_length", 5) / 2)
    stroke = max(2, mm2px_len(tr.get("stroke", 0.5)))
    cv2.rectangle(img, (cx-arm, cy-stroke//2), (cx+arm, cy+stroke//2), 0, -1)  # horizontal
    cv2.rectangle(img, (cx-stroke//2, cy-arm), (cx+stroke//2, cy), 0, -1)  # vertical (up from center)

    # BL: T-shape
    bl = markers.get("BL", {})
    cx, cy = mm2px(bl.get("center", {}).get("x", 5), bl.get("center", {}).get("y", 205))
    arm_h = mm2px_len(bl.get("arm_h", 5) / 2)
    arm_v = mm2px_len(bl.get("arm_v", 3))
    cv2.rectangle(img, (cx-arm_h, cy-stroke//2), (cx+arm_h, cy+stroke//2), 0, -1)  # horizontal
    cv2.rectangle(img, (cx-stroke//2, cy), (cx+stroke//2, cy+arm_v), 0, -1)  # vertical down

    # BR: plus
    br = markers.get("BR", {})
    cx, cy = mm2px(br.get("center", {}).get("x", 292), br.get("center", {}).get("y", 205))
    arm = mm2px_len(br.get("arm_length", 5) / 2)
    cv2.rectangle(img, (cx-arm, cy-stroke//2), (cx+arm, cy+stroke//2), 0, -1)
    cv2.rectangle(img, (cx-stroke//2, cy-arm), (cx+stroke//2, cy+arm), 0, -1)


def draw_anchors(img, meta):
    """식별자 + 컬럼 앵커"""
    # Identifier anchors
    ident = meta.get("identifier", {})
    anchors = ident.get("anchors", {})
    for key in ("TL", "BR"):
        a = anchors.get(key, {})
        center = a.get("center", {})
        if center:
            cx, cy = mm2px(center["x"], center["y"])
            s = mm2px_len(a.get("size", 2) / 2)
            cv2.rectangle(img, (cx-s, cy-s), (cx+s, cy+s), 0, -1)

    # Column anchors
    for col in meta.get("columns", []):
        col_anchors = col.get("anchors", {})
        for key in ("top", "bottom"):
            a = col_anchors.get(key, {})
            center = a.get("center", {})
            if center:
                cx, cy = mm2px(center["x"], center["y"])
                r = mm2px_len(a.get("radius", 1.5) / 2)
                cv2.circle(img, (cx, cy), r, 0, -1)


def draw_bubble_outlines(img, meta, line_color=210):
    """빈 버블 윤곽선 (인쇄선 수준: 연한 회색)"""
    # Answer bubbles
    for q in meta["questions"]:
        for choice in q["choices"]:
            cx, cy = mm2px(choice["center"]["x"], choice["center"]["y"])
            rx = mm2px_len(choice["radius_x"])
            ry = mm2px_len(choice["radius_y"])
            cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, line_color, max(1, mm2px_len(0.15)))

    # Identifier bubbles
    for digit in meta["identifier"]["digits"]:
        for bub in digit["bubbles"]:
            cx, cy = mm2px(bub["center"]["x"], bub["center"]["y"])
            rx = mm2px_len(bub["radius_x"])
            ry = mm2px_len(bub["radius_y"])
            cv2.ellipse(img, (cx, cy), (rx, ry), 0, 0, 360, line_color, max(1, mm2px_len(0.15)))


def draw_page_border(img, meta, line_color=200):
    """페이지 테두리 + 좌측 패널 구분선"""
    # Content area border
    cx1, cy1 = mm2px(10, 9)
    cx2, cy2 = mm2px(287, 204)
    cv2.rectangle(img, (cx1, cy1), (cx2, cy2), line_color, max(1, mm2px_len(0.4)))

    # Left panel border
    lp_x2, _ = mm2px(72, 0)
    cv2.rectangle(img, (cx1, cy1), (lp_x2, cy2), line_color, max(1, mm2px_len(0.5)))

    # Column headers
    for col in meta.get("columns", []):
        col_x = col["col_x"]
        x1, y1 = mm2px(col_x, 9)
        x2, y2 = mm2px(col_x + 44, 9 + 5.5)
        cv2.rectangle(img, (x1, y1), (x2, y2), line_color, max(1, mm2px_len(0.3)))


def fill_bubble(img, cx_mm, cy_mm, rx_mm, ry_mm, variant, rng):
    """마킹된 버블 — 실제 사인펜 특성 시뮬레이션"""
    offset_mm = variant["offset_mm"]
    dx = rng.uniform(-offset_mm, offset_mm)
    dy = rng.uniform(-offset_mm, offset_mm)

    cx, cy = mm2px(cx_mm + dx, cy_mm + dy)
    rx = mm2px_len(rx_mm)
    ry = mm2px_len(ry_mm)

    # Fill percentage
    fill_lo, fill_hi = variant["fill_pct"]
    fill_pct = rng.uniform(fill_lo, fill_hi)

    # Mark intensity (실제: 84~160)
    int_lo, int_hi = variant["mark_intensity"]
    intensity = int(rng.uniform(int_lo, int_hi))

    # Create elliptical mask
    mask = np.zeros((ry*2+10, rx*2+10), dtype=np.uint8)
    center = (rx+5, ry+5)
    # Slightly irregular ellipse (simulate pen stroke)
    rx_actual = int(rx * rng.uniform(0.85, 1.0))
    ry_actual = int(ry * rng.uniform(0.85, 1.0))
    cv2.ellipse(mask, center, (rx_actual, ry_actual), rng.uniform(-5, 5), 0, 360, 255, -1)

    # Random fill: erode to simulate incomplete fill
    if fill_pct < 0.9:
        kernel_size = max(1, int((1 - fill_pct) * 8))
        if kernel_size > 1:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
            mask = cv2.erode(mask, kernel)

    # Add per-pixel noise to the fill
    noise = np.random.normal(0, 15, mask.shape).astype(np.int16)

    # Apply to image
    y1 = max(0, cy - ry - 5)
    y2 = min(img.shape[0], cy + ry + 5)
    x1 = max(0, cx - rx - 5)
    x2 = min(img.shape[1], cx + rx + 5)

    h = y2 - y1
    w = x2 - x1
    if h <= 0 or w <= 0:
        return

    mask_crop = mask[:h, :w]
    noise_crop = noise[:h, :w]

    roi = img[y1:y2, x1:x2].astype(np.int16)
    mark_values = np.full_like(roi, intensity, dtype=np.int16) + noise_crop

    # Blend where mask is active
    alpha = (mask_crop.astype(np.float32) / 255.0).reshape(h, w)
    result = (roi * (1 - alpha) + mark_values * alpha).clip(0, 255).astype(np.uint8)
    img[y1:y2, x1:x2] = result


def generate_scan(meta, student, answers, variant, rng):
    """1장 합성 스캔 생성"""
    # Start with noisy background (실제: mean=244, σ=25-40)
    bg_mean = 244
    bg_noise = variant["noise_sigma"]
    img = np.random.normal(bg_mean, bg_noise, (OUT_H, OUT_W)).clip(0, 255).astype(np.uint8)

    # Draw printed elements
    draw_page_border(img, meta, line_color=int(rng.uniform(195, 225)))
    draw_bubble_outlines(img, meta, line_color=int(rng.uniform(205, 225)))
    draw_corner_markers(img, meta)
    draw_anchors(img, meta)

    # Fill answer bubbles
    for q in meta["questions"]:
        q_num = q["question_number"]
        ans = answers[q_num - 1]  # 1-indexed
        choice = q["choices"][ans - 1]  # 1-indexed answer
        fill_bubble(img, choice["center"]["x"], choice["center"]["y"],
                    choice["radius_x"], choice["radius_y"], variant, rng)

    # Fill phone number bubbles
    phone8 = student["phone8"]
    for d_idx, digit_char in enumerate(phone8):
        digit_val = int(digit_char)
        digit_meta = meta["identifier"]["digits"][d_idx]
        bub = digit_meta["bubbles"][digit_val]
        fill_bubble(img, bub["center"]["x"], bub["center"]["y"],
                    bub["radius_x"], bub["radius_y"], variant, rng)

    # Apply rotation if specified
    rot_deg = variant["rotation_deg"]
    if abs(rot_deg) > 0.01:
        h, w = img.shape[:2]
        M = cv2.getRotationMatrix2D((w/2, h/2), rot_deg, 1.0)
        img = cv2.warpAffine(img, M, (w, h), borderValue=int(bg_mean))

    # Add scanner margin (실제 스캐너: 용지 주변 검은/회색 여백)
    # 실제 스캔 이미지는 3507x2480 (스캐너 해상도 기준)이 아니라
    # 페이지보다 약간 큰 이미지에 주변 여백이 있음
    margin_px = random.randint(30, 80)  # 1~3mm 여백
    margin_color = random.randint(20, 60)  # 어두운 여백 (스캐너 커버)

    h, w = img.shape[:2]
    padded = np.full((h + margin_px * 2, w + margin_px * 2), margin_color, dtype=np.uint8)
    padded[margin_px:margin_px+h, margin_px:margin_px+w] = img

    # 여백-용지 경계를 약간 부드럽게 (실제 스캔에서 보이는 그림자)
    # 상하좌우 경계에 gradient
    for i in range(min(10, margin_px)):
        alpha = i / 10.0
        # 상단 경계
        padded[margin_px-i-1, margin_px:margin_px+w] = np.clip(
            padded[margin_px-i-1, margin_px:margin_px+w].astype(float) * (1-alpha) +
            img[0, :].astype(float) * alpha, 0, 255
        ).astype(np.uint8)

    return padded


def main():
    meta = build_omr_meta(question_count=20)
    rng = random.Random(42)
    np_rng = np.random.RandomState(42)

    manifest = []

    for student in STUDENTS:
        for variant in VARIANTS:
            # Random answers (1-5 for each of 20 questions)
            answers = [rng.randint(1, 5) for _ in range(20)]

            # Set numpy seed for reproducibility per scan
            seed = hash((student["name"], variant["name"])) % (2**31)
            np.random.seed(seed)

            img = generate_scan(meta, student, answers, variant, rng)

            fname = f"scan_{student['name']}_{variant['name']}.png"
            fpath = os.path.join(OUT_DIR, fname)
            cv2.imwrite(fpath, img)

            manifest.append({
                "filename": fname,
                "student_name": student["name"],
                "phone8": student["phone8"],
                "variant": variant["name"],
                "answers": answers,
            })

            size_kb = os.path.getsize(fpath) // 1024
            print(f"  {fname} ({size_kb}KB)")

    # Save manifest
    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n{len(manifest)} scans generated in {OUT_DIR}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()

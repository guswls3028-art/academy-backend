#!/usr/bin/env python3
"""
Generate 30 synthetic OMR v9 test scan images with realistic student markings.

Uses the meta_generator SSOT for exact coordinates.
Output: /tmp/omr_test_scans/ (30 PNGs + manifest.json)
"""
from __future__ import annotations

import json
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# ── Import meta_generator directly (pure Python, no Django needed) ──
sys.path.insert(0, "/mnt/c/academy/backend")
from apps.domains.assets.omr.services.meta_generator import build_omr_meta  # noqa: E402

# ══════════════════════════════════════════
# Constants
# ══════════════════════════════════════════
IMG_W, IMG_H = 3508, 2480  # A4 landscape @ 300 DPI
PAGE_W_MM, PAGE_H_MM = 297.0, 210.0
OUTPUT_DIR = "/tmp/omr_test_scans"
QUESTION_COUNT = 20
N_CHOICES = 5

# Conversion helpers
def mm2px_x(mm: float) -> float:
    return mm * IMG_W / PAGE_W_MM

def mm2px_y(mm: float) -> float:
    return mm * IMG_H / PAGE_H_MM

def mm2px(mm: float) -> float:
    """Average conversion for size elements."""
    return mm * ((IMG_W / PAGE_W_MM) + (IMG_H / PAGE_H_MM)) / 2

# ══════════════════════════════════════════
# Test data
# ══════════════════════════════════════════
STUDENTS = [
    {"phone8": "63537370", "name": "박시현"},
    {"phone8": "52729918", "name": "박해환"},
    {"phone8": "52431867", "name": "이다현"},
    {"phone8": "87559548", "name": "이은호"},
    {"phone8": "38410218", "name": "이준우"},
    {"phone8": "82378990", "name": "황상혁"},
]

VARIANTS = [
    {"id": "clean", "fill_pct": 0.90, "offset_mm": 0.0, "noise_sigma": 0, "rotation_deg": 0.0},
    {"id": "light", "fill_pct": 0.60, "offset_mm": 0.0, "noise_sigma": 0, "rotation_deg": 0.0},
    {"id": "offset", "fill_pct": 0.85, "offset_mm": 0.5, "noise_sigma": 0, "rotation_deg": 0.0},
    {"id": "noisy", "fill_pct": 0.85, "offset_mm": 0.0, "noise_sigma": 10, "rotation_deg": 0.0},
    {"id": "rotated", "fill_pct": 0.85, "offset_mm": 0.0, "noise_sigma": 0, "rotation_deg": 0.5},
]


def draw_filled_rect(draw: ImageDraw.ImageDraw, cx: float, cy: float, w: float, h: float, fill: int = 0):
    """Draw a filled rectangle centered at (cx, cy) with size (w, h)."""
    x0, y0 = cx - w / 2, cy - h / 2
    x1, y1 = cx + w / 2, cy + h / 2
    draw.rectangle([x0, y0, x1, y1], fill=fill)


def draw_marker_tl(draw: ImageDraw.ImageDraw, marker: dict):
    """TL: solid black square."""
    cx = mm2px_x(marker["center"]["x"])
    cy = mm2px_y(marker["center"]["y"])
    s = mm2px(marker["size"])
    draw_filled_rect(draw, cx, cy, s, s, fill=0)


def draw_marker_tr(draw: ImageDraw.ImageDraw, marker: dict):
    """TR: L-shape (horizontal + vertical arm at top-right corner)."""
    cx = mm2px_x(marker["center"]["x"])
    cy = mm2px_y(marker["center"]["y"])
    arm = mm2px(marker["arm_length"])
    stroke = mm2px(marker["stroke"])
    # Horizontal bar going left from center
    draw.rectangle([cx - arm, cy - stroke / 2, cx, cy + stroke / 2], fill=0)
    # Vertical bar going down from center
    draw.rectangle([cx - stroke, cy - stroke / 2, cx, cy + arm], fill=0)


def draw_marker_bl(draw: ImageDraw.ImageDraw, marker: dict):
    """BL: Inverted T — horizontal bar + vertical drop from center."""
    cx = mm2px_x(marker["center"]["x"])
    cy = mm2px_y(marker["center"]["y"])
    arm_h = mm2px(marker["arm_h"])
    arm_v = mm2px(marker["arm_v"])
    stroke = mm2px(marker["stroke"])
    # Horizontal bar centered
    draw.rectangle([cx - arm_h / 2, cy - stroke / 2, cx + arm_h / 2, cy + stroke / 2], fill=0)
    # Vertical bar dropping down from center
    draw.rectangle([cx - stroke / 2, cy + stroke / 2, cx + stroke / 2, cy + arm_v + stroke / 2], fill=0)


def draw_marker_br(draw: ImageDraw.ImageDraw, marker: dict):
    """BR: Plus/cross — centered horizontal + vertical."""
    cx = mm2px_x(marker["center"]["x"])
    cy = mm2px_y(marker["center"]["y"])
    arm = mm2px(marker["arm_length"])
    stroke = mm2px(marker["stroke"])
    # Horizontal bar
    draw.rectangle([cx - arm / 2, cy - stroke / 2, cx + arm / 2, cy + stroke / 2], fill=0)
    # Vertical bar
    draw.rectangle([cx - stroke / 2, cy - arm / 2, cx + stroke / 2, cy + arm / 2], fill=0)


def draw_markers(draw: ImageDraw.ImageDraw, markers: dict):
    """Draw all 4 corner markers."""
    draw_marker_tl(draw, markers["TL"])
    draw_marker_tr(draw, markers["TR"])
    draw_marker_bl(draw, markers["BL"])
    draw_marker_br(draw, markers["BR"])


def draw_square_anchor(draw: ImageDraw.ImageDraw, anchor: dict):
    """Draw a small square anchor."""
    cx = mm2px_x(anchor["center"]["x"])
    cy = mm2px_y(anchor["center"]["y"])
    s = mm2px(anchor["size"])
    draw_filled_rect(draw, cx, cy, s, s, fill=0)


def draw_circle_anchor(draw: ImageDraw.ImageDraw, anchor: dict):
    """Draw a small circle anchor."""
    cx = mm2px_x(anchor["center"]["x"])
    cy = mm2px_y(anchor["center"]["y"])
    r = mm2px(anchor["radius"])
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)


def draw_bubble_outline(draw: ImageDraw.ImageDraw, cx_px: float, cy_px: float, rx_px: float, ry_px: float):
    """Draw an empty bubble outline (ellipse)."""
    draw.ellipse(
        [cx_px - rx_px, cy_px - ry_px, cx_px + rx_px, cy_px + ry_px],
        outline=0,
        width=max(1, int(mm2px(0.15))),
    )


def draw_filled_bubble(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    cx_px: float,
    cy_px: float,
    rx_px: float,
    ry_px: float,
    fill_pct: float = 0.90,
    offset_mm: float = 0.0,
):
    """Draw a filled bubble simulating pen marking."""
    # Random offset to simulate imprecise marking
    if offset_mm > 0:
        ox = random.uniform(-offset_mm, offset_mm)
        oy = random.uniform(-offset_mm, offset_mm)
        cx_px += mm2px_x(ox) - mm2px_x(0)  # delta
        cy_px += mm2px_y(oy) - mm2px_y(0)

    # Base intensity: dark gray (lower = darker)
    base_intensity = random.randint(30, 60)

    # Scale radii by fill percentage (sqrt to make area proportional)
    import math
    scale = math.sqrt(fill_pct)
    frx = rx_px * scale
    fry = ry_px * scale

    # Draw filled ellipse
    draw.ellipse(
        [cx_px - frx, cy_px - fry, cx_px + frx, cy_px + fry],
        fill=base_intensity,
    )

    # Add some noise texture within the bubble for realism
    arr = np.array(img)
    y0 = max(0, int(cy_px - ry_px))
    y1 = min(IMG_H, int(cy_px + ry_px))
    x0 = max(0, int(cx_px - rx_px))
    x1 = min(IMG_W, int(cx_px + rx_px))
    if y1 > y0 and x1 > x0:
        region = arr[y0:y1, x0:x1]
        # Only add noise to dark pixels (the filled area)
        mask = region < 200
        noise = np.random.randint(-15, 15, region.shape, dtype=np.int16)
        noised = np.clip(region.astype(np.int16) + noise * mask.astype(np.int16), 0, 255).astype(np.uint8)
        arr[y0:y1, x0:x1] = noised
        # Write back (we modify in-place via numpy view, so update the image)
        img.paste(Image.fromarray(arr), (0, 0))


def generate_scan(
    meta: dict,
    student: dict,
    answers: list[int],
    variant: dict,
    seed: int,
) -> Image.Image:
    """Generate a single synthetic OMR scan image."""
    random.seed(seed)
    np.random.seed(seed % (2**31))

    img = Image.new("L", (IMG_W, IMG_H), 255)  # White background, grayscale
    draw = ImageDraw.Draw(img)

    # ── 1. Corner markers ──
    draw_markers(draw, meta["markers"])

    # ── 2. Identifier anchors ──
    id_meta = meta["identifier"]
    draw_square_anchor(draw, id_meta["anchors"]["TL"])
    draw_square_anchor(draw, id_meta["anchors"]["BR"])

    # ── 3. Column anchors ──
    for col in meta["columns"]:
        draw_circle_anchor(draw, col["anchors"]["top"])
        draw_circle_anchor(draw, col["anchors"]["bottom"])

    # ── 4. Phone number bubble grid ──
    phone8 = student["phone8"]
    for d_idx, digit_meta in enumerate(id_meta["digits"]):
        digit_val = int(phone8[d_idx])
        for bub in digit_meta["bubbles"]:
            cx = mm2px_x(bub["center"]["x"])
            cy = mm2px_y(bub["center"]["y"])
            rx = mm2px_x(bub["radius_x"])
            ry = mm2px_y(bub["radius_y"])
            # Draw outline for all bubbles
            draw_bubble_outline(draw, cx, cy, rx, ry)
            # Fill the selected digit bubble
            if int(bub["value"]) == digit_val:
                draw_filled_bubble(
                    img, draw, cx, cy, rx, ry,
                    fill_pct=variant["fill_pct"],
                    offset_mm=variant["offset_mm"],
                )
                # Re-acquire draw after numpy modification
                draw = ImageDraw.Draw(img)

    # ── 5. Answer bubbles ──
    for q_meta in meta["questions"]:
        q_num = q_meta["question_number"]
        selected = answers[q_num - 1]  # 1-based answer
        for choice in q_meta["choices"]:
            cx = mm2px_x(choice["center"]["x"])
            cy = mm2px_y(choice["center"]["y"])
            rx = mm2px_x(choice["radius_x"])
            ry = mm2px_y(choice["radius_y"])
            draw_bubble_outline(draw, cx, cy, rx, ry)
            if int(choice["label"]) == selected:
                draw_filled_bubble(
                    img, draw, cx, cy, rx, ry,
                    fill_pct=variant["fill_pct"],
                    offset_mm=variant["offset_mm"],
                )
                draw = ImageDraw.Draw(img)

    # ── 6. Post-processing ──
    # Add Gaussian noise if specified
    if variant["noise_sigma"] > 0:
        arr = np.array(img).astype(np.int16)
        noise = np.random.normal(0, variant["noise_sigma"], arr.shape).astype(np.int16)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

    # Apply rotation if specified
    if variant["rotation_deg"] != 0.0:
        img = img.rotate(
            variant["rotation_deg"],
            resample=Image.BICUBIC,
            expand=False,
            fillcolor=255,
        )

    return img


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    meta = build_omr_meta(question_count=QUESTION_COUNT)
    manifest = {"version": "v9", "question_count": QUESTION_COUNT, "scans": []}

    total = 0
    for student in STUDENTS:
        for v_idx, variant in enumerate(VARIANTS):
            # Generate random answers (1-5) for each question
            seed = hash((student["phone8"], variant["id"])) % (2**31)
            random.seed(seed)
            answers = [random.randint(1, N_CHOICES) for _ in range(QUESTION_COUNT)]

            filename = f"scan_{student['name']}_{variant['id']}.png"
            filepath = os.path.join(OUTPUT_DIR, filename)

            print(f"  [{total + 1:2d}/30] Generating {filename} ...", end="", flush=True)

            img = generate_scan(meta, student, answers, variant, seed=seed + v_idx)
            img.save(filepath, "PNG")

            size_kb = os.path.getsize(filepath) / 1024
            print(f" OK ({size_kb:.0f} KB, {img.size[0]}x{img.size[1]})")

            manifest["scans"].append({
                "filename": filename,
                "student_name": student["name"],
                "phone8": student["phone8"],
                "variant": variant["id"],
                "answers": answers,
            })
            total += 1

    # Write manifest
    manifest_path = os.path.join(OUTPUT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\nDone: {total} scans + manifest.json in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

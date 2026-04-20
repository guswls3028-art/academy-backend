# apps/domains/assets/omr/services/meta_generator.py
"""
OMR v15 좌표 메타 생성기 — SSOT

pdf_renderer.py(렌더링 SSOT) + omr_sheet.html(프리뷰 SSOT)와 동기화된 mm 단위 좌표.

v15.2 변경 (타 학원프로그램 min.t 참고):
  - 코너 마커를 얇은 ㄱ자 브래킷 스타일로 전환 (4mm 팔 × 1mm 두께, 오프셋 3mm)
    TL = ┐ (우+하), TR = ┌ (좌+하), BR = ┘ (좌+상) — 모두 l_shape
    BL = filled 삼각형 (orientation 판별용 비대칭 신호, 유일한 non-L)
  - marker_detector는 기존 로직 재사용 (type별 assignment, L 3개 + triangle 1개)
  - 4변 타이밍 스트립 없음 (AI 미사용 = 장식 금지)
  - 컬럼 로컬 앵커 + identifier 앵커는 engine이 실사용하므로 유지

AI 워커는 이 메타의 `markers`(homography)와 `questions[*].choices[*].center`
(버블 좌표)를 사용한다. pdf_renderer/omr_sheet.html 레이아웃이 바뀌면
이 파일도 반드시 함께 수정한다.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

# ══════════════════════════════════════════
# 페이지 상수 (omr-sheet.html CSS와 동기화)
# ══════════════════════════════════════════
PAGE_W = 297.0
PAGE_H = 210.0

MARGIN_L = 10.0
MARGIN_T = 9.0
MARGIN_R = 10.0
MARGIN_B = 8.0

CONTENT_X = MARGIN_L
CONTENT_Y = MARGIN_T
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R   # 277mm
CONTENT_H = PAGE_H - MARGIN_T - MARGIN_B   # 195mm

# ── Left Panel ──
LP_W = 62.0
LP_GAP = 3.0
LP_BORDER = 0.5
LP_PAD_X = 2.5

# ── Answer Area ──
ANS_X = CONTENT_X + LP_W + LP_GAP          # 75mm

# ── MC Column ──
MC_COL_W = 44.0
MC_COL_GAP = 2.5
MC_HEADER_H = 5.5
MC_NUM_W = 8.0
MC_BUB_PAD = 0.5

# ── Bubble (쌀톨형 세로 타원) ──
BUB_W = 3.6
BUB_H = 5.2

# ── Identifier (전화번호 뒤 8자리) ──
# v15.2: 답안 버블과 규격 통일. phone 섹션 높이 확장으로 10개 버블 수용.
ID_DIGIT_W = 5.8
ID_SEP_W = 3.5
ID_BUB_W = 3.6       # 답안 BUB_W와 동일
ID_BUB_H = 5.2       # 답안 BUB_H와 동일
ID_BUB_GAP = 1.2
ID_DIGITS = 8
ID_VALUES = 10

# ══════════════════════════════════════════
# 레이아웃 안전 제한 (판독 안정성 최우선)
# ══════════════════════════════════════════
MIN_ROW_H_MM = 8.0              # 행 최소 높이 (버블 5.2mm + 여백)
MIN_VERTICAL_GAP_MM = 2.0       # 버블 간 최소 수직 간격
MAX_MC_QUESTIONS = 60           # 1페이지 최대 객관식 수
MAX_QUESTIONS_PER_COL = 20      # 컬럼당 최대 문항 수
MAX_COLS = 3                    # 최대 컬럼 수


def compute_safe_layout(question_count: int) -> Dict[str, Any]:
    """
    문항수에 대해 안전한 컬럼/행 레이아웃 결정.

    Returns:
        {"n_cols": int, "per_col": int, "row_h_mm": float, "safe": bool, "reason": str}
    """
    if question_count <= 0:
        return {"n_cols": 0, "per_col": 0, "row_h_mm": 0, "safe": True, "reason": ""}

    body_h = CONTENT_H - MC_HEADER_H  # ~189.5mm

    if question_count > MAX_MC_QUESTIONS:
        return {
            "n_cols": 0, "per_col": 0, "row_h_mm": 0, "safe": False,
            "reason": f"객관식 {question_count}문항은 1페이지 최대 {MAX_MC_QUESTIONS}문항을 초과합니다.",
        }

    # 최소 컬럼 수 결정: per_col <= MAX_QUESTIONS_PER_COL && row_h >= MIN_ROW_H_MM
    for n_cols in range(1, MAX_COLS + 1):
        per_col = math.ceil(question_count / n_cols)
        if per_col > MAX_QUESTIONS_PER_COL:
            continue
        row_h = body_h / per_col
        if row_h >= MIN_ROW_H_MM:
            return {
                "n_cols": n_cols, "per_col": per_col,
                "row_h_mm": round(row_h, 2), "safe": True, "reason": "",
            }

    # 3컬럼으로도 안전 기준 미달 → 차단
    per_col = math.ceil(question_count / MAX_COLS)
    row_h = body_h / per_col
    return {
        "n_cols": MAX_COLS, "per_col": per_col,
        "row_h_mm": round(row_h, 2), "safe": False,
        "reason": f"객관식 {question_count}문항: 행 높이 {round(row_h, 1)}mm < 최소 {MIN_ROW_H_MM}mm. 문항 수를 줄여주세요.",
    }


def validate_layout(question_count: int, essay_count: int = 0) -> List[str]:
    """레이아웃 유효성 검증. 에러 메시지 리스트 반환 (빈 리스트 = OK)."""
    errors: List[str] = []
    if question_count < 0:
        errors.append("객관식 문항 수는 0 이상이어야 합니다.")
    if essay_count < 0:
        errors.append("서술형 문항 수는 0 이상이어야 합니다.")
    if question_count > MAX_MC_QUESTIONS:
        errors.append(f"객관식 {question_count}문항은 1페이지 최대 {MAX_MC_QUESTIONS}문항을 초과합니다.")
    layout = compute_safe_layout(question_count)
    if not layout["safe"]:
        errors.append(layout["reason"])

    # 컬럼 수 × 폭이 페이지를 초과하는지
    n_cols = layout["n_cols"]
    total_mc_w = n_cols * MC_COL_W + max(0, n_cols - 1) * MC_COL_GAP
    ans_w = PAGE_W - MARGIN_R - ANS_X  # 사용 가능 너비
    if essay_count > 0:
        min_essay_w = 40.0
        if total_mc_w + MC_COL_GAP + min_essay_w > ans_w:
            errors.append(f"객관식 {n_cols}컬럼 + 서술형이 페이지 너비를 초과합니다. 문항 수를 줄여주세요.")

    return errors


def _calc_bubble_centers_x(col_x: float, n_choices: int) -> List[float]:
    """MC column 내 버블 중심 x좌표 (space-evenly)."""
    area_x = col_x + MC_NUM_W + MC_BUB_PAD
    area_w = MC_COL_W - MC_NUM_W - 2 * MC_BUB_PAD
    n_gaps = n_choices + 1
    gap = (area_w - n_choices * BUB_W) / n_gaps
    return [
        round(area_x + gap * (i + 1) + BUB_W * i + BUB_W / 2, 2)
        for i in range(n_choices)
    ]


# ══════════════════════════════════════════
# 코너 마커 상수 v15.2 — min.t 참고 얇은 브래킷 스타일
# ══════════════════════════════════════════
MARKER_OFF = 3.0   # 페이지 가장자리 오프셋 (ADF 여유)
MARKER_SZ = 4.0    # 팔 길이
MARKER_TH = 1.0    # 팔 두께 — 얇은 bracket

# ══════════════════════════════════════════
# 컬럼 로컬 앵커 — 종이 비선형 왜곡(ADF 늘어짐/구김) 시 컬럼별 local affine 보정용
# engine._compute_column_transforms가 사용. 4코너 homography 이후 잔차 보정.
# ══════════════════════════════════════════
COL_ANCHOR_SZ = 2.0       # 정사각형 한 변 (mm)
COL_ANCHOR_OUTSET = 1.0   # 답안 프레임 상/하단 외부 거리 (mm)

# ══════════════════════════════════════════
# 좌측 패널 수직 분할 SSOT — pdf_renderer와 반드시 일치
# v15.2: phone 75→85 (ID 버블 5.2mm × 10개 수용), 로고 영역 축소
# ══════════════════════════════════════════
LP_H_NOTE = 28.0   # 답안지 작성 안내 영역 높이
LP_H_PHONE = 85.0  # 학생 식별번호 영역 높이 (ID 버블 10개 + 헤더/쓰기칸 포함)
LP_H_NAME = 16.0   # 성명 영역 높이
# 로고 영역 = CONTENT_H - LP_H_NOTE - LP_H_PHONE - LP_H_NAME = 66mm

# 식별번호 영역 내부 구조 (pdf_renderer._phone과 일치)
ID_HEADER_H = 5.5          # 섹션 헤더 높이
ID_WRITE_TOP_PAD = 2.0     # 헤더 → 쓰기칸 상단 간격
ID_WRITE_H = 7.0           # 쓰기칸 높이
ID_WRITE_BOT_PAD = 3.5     # 쓰기칸 하단 → 버블 시작 간격
ID_ANCHOR_SZ = 2.0         # identifier 로컬 앵커 크기


def _build_marker_meta() -> Dict[str, Any]:
    """v15.2 코너 마커 — 얇은 ㄱ자 브래킷 3개 + BL filled 삼각형 1개 (min.t 참고).

    center 좌표 = 각 도형 실제 centroid (homography destination point).
    TL/TR/BR은 모두 l_shape (brackets 방향만 다름). marker_detector는 corner 영역 +
    type 매칭으로 자동 분배. BL 삼각형이 orientation 판별용 비대칭 신호.
    """
    off = MARKER_OFF
    sz = MARKER_SZ
    th = MARKER_TH
    pw, ph = PAGE_W, PAGE_H

    # ㄱ자 브래킷 centroid 공식 (면적 가중)
    # 수평 팔: SZ × TH, 수직 팔: TH × (SZ - TH) (겹치는 코너 TH×TH 제외)
    # 브래킷 귀퉁이 (corner_x, corner_y)에서 바깥으로 펼침.
    area_h = sz * th
    area_v = th * (sz - th)
    total = area_h + area_v
    # 귀퉁이 기준 상대 centroid offset (팔이 +방향으로 뻗을 때)
    offset_along = (area_h * (sz / 2) + area_v * (th / 2)) / total           # 수평 팔 방향
    offset_perp = (area_h * (th / 2) + area_v * (th + (sz - th) / 2)) / total  # 수직 팔 방향

    # TL ┐ (corner at off,off, 팔 오른쪽+아래로)
    tl_cx = off + offset_along
    tl_cy = off + offset_perp

    # TR ┌ (corner at pw-off,off, 팔 왼쪽+아래로) — x 거울
    tr_cx = pw - off - offset_along
    tr_cy = off + offset_perp

    # BR ┘ (corner at pw-off,ph-off, 팔 왼쪽+위로) — x,y 둘 다 거울
    br_cx = pw - off - offset_along
    br_cy = ph - off - offset_perp

    # BL filled 삼각형 (꼭짓점 위). 꼭짓점 (off, ph-off), (off+sz, ph-off), (off+sz/2, ph-off-sz)
    bl_cx = off + sz / 2
    bl_cy = ph - off - sz / 3

    return {
        "TL": {
            "type": "l_shape",
            "center": {"x": round(tl_cx, 3), "y": round(tl_cy, 3)},
            "size": sz,
            "thickness": th,
            "direction": "TL",  # ┐
        },
        "TR": {
            "type": "l_shape",
            "center": {"x": round(tr_cx, 3), "y": round(tr_cy, 3)},
            "size": sz,
            "thickness": th,
            "direction": "TR",  # ┌
        },
        "BL": {
            "type": "triangle",
            "center": {"x": round(bl_cx, 3), "y": round(bl_cy, 3)},
            "size": sz,
        },
        "BR": {
            "type": "l_shape",
            "center": {"x": round(br_cx, 3), "y": round(br_cy, 3)},
            "size": sz,
            "thickness": th,
            "direction": "BR",  # ┘
        },
    }


def build_omr_meta(
    *,
    question_count: int,
    n_choices: int = 5,
    essay_count: int = 0,
) -> Dict[str, Any]:
    """OMR 메타 생성 (좌표 SSOT). v14: 5mm 비대칭 마커 + 버블 정렬 인식마크."""
    layout = compute_safe_layout(question_count)
    per_col = layout["per_col"]
    n_cols = layout["n_cols"]

    body_y = CONTENT_Y + MC_HEADER_H
    body_h = CONTENT_H - MC_HEADER_H

    questions: List[Dict[str, Any]] = []
    columns: List[Dict[str, Any]] = []

    for c in range(n_cols):
        col_x = ANS_X + c * (MC_COL_W + MC_COL_GAP)
        start = c * per_col + 1
        end = min(start + per_col - 1, question_count)
        count_in_col = end - start + 1
        row_h = body_h / count_in_col if count_in_col > 0 else body_h

        bubble_xs = _calc_bubble_centers_x(col_x, n_choices)

        col_questions: List[Dict[str, Any]] = []
        for q_idx in range(count_in_col):
            q_num = start + q_idx
            row_cy = body_y + (q_idx + 0.5) * row_h
            choices = [
                {
                    "label": str(ci + 1),
                    "center": {"x": bx, "y": round(row_cy, 2)},
                    "radius_x": round(BUB_W / 2, 2),
                    "radius_y": round(BUB_H / 2, 2),
                }
                for ci, bx in enumerate(bubble_xs)
            ]
            q_entry = {
                "question_number": q_num,
                "type": "choice",
                "column": c,  # column index for per-column alignment
                "roi": {
                    "x": round(col_x, 2),
                    "y": round(row_cy - row_h / 2, 2),
                    "w": round(MC_COL_W, 2),
                    "h": round(row_h, 2),
                },
                "choices": choices,
            }
            questions.append(q_entry)
            col_questions.append(q_entry)

        # v15.2: 컬럼 로컬 앵커 — 답안 프레임 외부 상/하단 (프레임과 1mm 떨어짐).
        # 번호 글자와 겹치지 않음. engine._compute_column_transforms가 사용.
        # x 위치: 번호 칼럼 중앙 (코너 마커와 x축으로 분리, 답안 컬럼 식별 가능)
        anchor_cx = col_x + MC_NUM_W / 2
        anchor_top_cy = CONTENT_Y - COL_ANCHOR_OUTSET - COL_ANCHOR_SZ / 2
        anchor_bot_cy = CONTENT_Y + CONTENT_H + COL_ANCHOR_OUTSET + COL_ANCHOR_SZ / 2
        columns.append({
            "column_index": c,
            "col_x": round(col_x, 2),
            "anchors": {
                "top": {
                    "center": {"x": round(anchor_cx, 2), "y": round(anchor_top_cy, 2)},
                    "size": COL_ANCHOR_SZ,
                },
                "bottom": {
                    "center": {"x": round(anchor_cx, 2), "y": round(anchor_bot_cy, 2)},
                    "size": COL_ANCHOR_SZ,
                },
            },
            "questions": col_questions,
        })

    return {
        "version": "v15",
        "units": "mm",
        "page": {"width": PAGE_W, "height": PAGE_H},
        "markers": _build_marker_meta(),
        "mc_count": question_count,
        "essay_count": essay_count,
        "n_choices": n_choices,
        "layout": {
            "n_cols": n_cols,
            "per_col": per_col,
            "row_h_mm": layout["row_h_mm"],
            "safe": layout["safe"],
        },
        "questions": questions,           # flat list (backward compatible)
        "columns": columns,               # grouped by column (v15: 앵커 제거, col_x만)
        "identifier": _build_identifier_meta(),
    }


def _build_identifier_meta() -> Dict[str, Any]:
    """전화번호 뒤 8자리 버블 그리드 좌표 (pdf_renderer._phone과 SSOT 동기화)."""
    lp_inner_x = CONTENT_X + LP_BORDER + LP_PAD_X
    grid_w = ID_DIGITS * ID_DIGIT_W + ID_SEP_W
    sec_pad = 2.5
    lp_content_w = LP_W - 2 * LP_BORDER - 2 * LP_PAD_X
    available_w = lp_content_w - 2 * sec_pad
    grid_offset_x = (available_w - grid_w) / 2
    grid_start_x = lp_inner_x + sec_pad + grid_offset_x

    # Y: pdf_renderer._left / _phone 레이아웃과 일치.
    #   note_top = CONTENT_Y + CONTENT_H - LP_H_NOTE
    #   phone_sec_top = note_top - LP_H_PHONE
    #   bubbles_start_y = phone_sec_top + ID_HEADER_H + ID_WRITE_TOP_PAD + ID_WRITE_H + ID_WRITE_BOT_PAD
    note_top = CONTENT_Y + CONTENT_H - LP_H_NOTE
    phone_sec_top = note_top - LP_H_PHONE
    bubbles_start_y = (
        phone_sec_top + ID_HEADER_H + ID_WRITE_TOP_PAD + ID_WRITE_H + ID_WRITE_BOT_PAD
    )

    digits = []
    for d in range(ID_DIGITS):
        if d < 4:
            dx = grid_start_x + d * ID_DIGIT_W
        else:
            dx = grid_start_x + 4 * ID_DIGIT_W + ID_SEP_W + (d - 4) * ID_DIGIT_W
        cx = dx + ID_DIGIT_W / 2

        bubbles = [
            {
                "value": str(v),
                "center": {
                    "x": round(cx, 2),
                    "y": round(bubbles_start_y + v * (ID_BUB_H + ID_BUB_GAP) + ID_BUB_H / 2, 2),
                },
                "radius_x": round(ID_BUB_W / 2, 2),
                "radius_y": round(ID_BUB_H / 2, 2),
            }
            for v in range(ID_VALUES)
        ]
        digits.append({"digit_index": d, "bubbles": bubbles})

    # identifier 로컬 앵커 — pdf_renderer._phone에서 실제로 같은 좌표로 그린다.
    # 위치: 그리드 상단 좌측, 하단 우측 (버블과 겹치지 않는 여백)
    grid_end_x = grid_start_x + grid_w
    grid_end_y = bubbles_start_y + (ID_VALUES - 1) * (ID_BUB_H + ID_BUB_GAP) + ID_BUB_H
    half = ID_ANCHOR_SZ / 2
    anchors = {
        "TL": {
            "type": "square",
            "center": {
                "x": round(grid_start_x - half - 0.5, 2),
                "y": round(bubbles_start_y - half - 0.5, 2),
            },
            "size": ID_ANCHOR_SZ,
        },
        "BR": {
            "type": "square",
            "center": {
                "x": round(grid_end_x + half + 0.5, 2),
                "y": round(grid_end_y + half + 0.5, 2),
            },
            "size": ID_ANCHOR_SZ,
        },
    }

    return {
        "version": "v9",
        "digits": digits,
        "digit_count": ID_DIGITS,
        "anchors": anchors,
    }


# ── 하위호환 래퍼 ──
def build_objective_template_meta(question_count: int, **kwargs) -> Dict[str, Any]:
    return build_omr_meta(question_count=question_count, **kwargs)

# apps/domains/assets/omr/renderer/html_renderer.py
"""
OMR HTML 프리뷰 렌더러 — Django 템플릿 기반

OMRDocument를 입력받아 iframe 삽입 가능한 HTML을 생성한다.
CSS는 omr-sheet.html과 동일한 스타일을 사용.
"""
from __future__ import annotations

import math
from typing import Any

from django.template.loader import render_to_string

from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.services.meta_generator import (
    MARKER_OFF, MARKER_SZ, MARKER_TH,
)


class OMRHtmlRenderer:
    """OMR HTML 프리뷰 생성기."""

    def render(self, doc: OMRDocument) -> bytes:
        """OMRDocument → HTML 바이트열."""
        context = self._build_context(doc)
        html = render_to_string("omr/omr_sheet.html", context)
        return html.encode("utf-8")

    def _build_context(self, doc: OMRDocument) -> dict[str, Any]:
        mc_columns = self._build_mc_columns(doc)
        essay_rows = self._build_essay_rows(doc)

        sub_parts = []
        if doc.lecture_name:
            sub_parts.append(doc.lecture_name)
        if doc.session_name:
            sub_parts.append(doc.session_name)

        # 시험명 길이에 따라 폰트 크기 결정
        title_len = len(doc.exam_title) if doc.exam_title else 0
        if title_len > 20:
            title_font_pt = 9
        elif title_len > 15:
            title_font_pt = 10
        else:
            title_font_pt = 12

        return {
            "exam_title": doc.exam_title,
            "lecture_name": doc.lecture_name,
            "session_name": doc.session_name,
            "sub_line": " / ".join(sub_parts),
            "mc_count": doc.mc_count,
            "essay_count": doc.essay_count,
            "n_choices": doc.n_choices,
            "logo_url": doc.logo_url,
            "brand_color": doc.brand_color,
            "title_font_pt": title_font_pt,
            "mc_columns": mc_columns,
            "essay_rows": essay_rows,
            "choices_labels": [str(i + 1) for i in range(doc.n_choices)],
            "phone_digits": list(range(8)),
            "phone_values": list(range(10)),
            # v15 인식 마크 SSOT
            "marker_off_mm": MARKER_OFF,
            "marker_sz_mm": MARKER_SZ,
            "marker_th_mm": MARKER_TH,
            "marker_half_sz_mm": MARKER_SZ / 2,              # CSS triangle border-left/right 폭
            "marker_br_center_mm": (MARKER_SZ - MARKER_TH) / 2,  # BR 십자 중심 정렬 offset
        }

    def _build_mc_columns(self, doc: OMRDocument) -> list[dict]:
        if doc.mc_count <= 0:
            return []

        mc = doc.mc_count
        if mc <= 20:
            per_col, n_cols = mc, 1
        elif mc <= 40:
            per_col = math.ceil(mc / 2)
            n_cols = 2
        else:
            per_col = math.ceil(mc / 3)
            n_cols = 3

        columns = []
        for col_idx in range(n_cols):
            start = col_idx * per_col + 1
            end = min(start + per_col - 1, mc)

            count_in_col = end - start + 1
            if n_cols > 1:
                label = f"{start}번 ~ {end}번"
            else:
                label = f"객관식 {count_in_col}문항"

            rows = []
            for q in range(start, end + 1):
                row_in_col = q - start + 1
                # 5행 그룹 인덱스 (0-based)
                group_idx = (row_in_col - 1) // 5
                rows.append({
                    "number": q,
                    "is_g5": (row_in_col % 5 == 0 and q != end),
                    "is_g10": (row_in_col % 10 == 0 and q != end),
                    "is_zebra": (group_idx % 2 == 1),
                })

            columns.append({
                "label": label,
                "rows": rows,
            })

        return columns

    def _build_essay_rows(self, doc: OMRDocument) -> list[dict]:
        if doc.essay_count <= 0:
            return []

        rows = []
        for i in range(1, doc.essay_count + 1):
            group_idx = (i - 1) // 5
            rows.append({
                "number": i,
                "is_g5": (i % 5 == 0 and i != doc.essay_count),
                "is_g10": (i % 10 == 0 and i != doc.essay_count),
                "is_zebra": (group_idx % 2 == 1),
            })
        return rows

# apps/domains/assets/omr/renderer/html_renderer.py
"""
OMR HTML 프리뷰 렌더러 — Django 템플릿 기반

OMRDocument를 입력받아 iframe 삽입 가능한 HTML을 생성한다.
CSS는 omr-sheet.html과 동일한 스타일을 사용.
"""
from __future__ import annotations

import math
import os
from typing import Any

from django.template.loader import render_to_string

from apps.domains.assets.omr.dto.omr_document import OMRDocument


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

        return {
            "exam_title": doc.exam_title,
            "lecture_name": doc.lecture_name,
            "session_name": doc.session_name,
            "sub_line": " / ".join(sub_parts),
            "mc_count": doc.mc_count,
            "essay_count": doc.essay_count,
            "n_choices": doc.n_choices,
            "logo_url": doc.logo_url,
            "mc_columns": mc_columns,
            "essay_rows": essay_rows,
            "choices_labels": [str(i + 1) for i in range(doc.n_choices)],
            "phone_digits": list(range(8)),
            "phone_values": list(range(10)),
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

            if n_cols > 1:
                label = f"{start}번 ~ {end}번"
            else:
                label = "객관식"

            rows = []
            for q in range(start, end + 1):
                row_in_col = q - start + 1
                rows.append({
                    "number": q,
                    "is_g5": (row_in_col % 5 == 0 and q != end),
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
            rows.append({
                "number": i,
                "is_g5": (i % 5 == 0 and i != doc.essay_count),
            })
        return rows

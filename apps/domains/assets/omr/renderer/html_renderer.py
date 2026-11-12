# apps/domains/assets/omr/renderer/html_renderer.py
"""
OMR HTML 프리뷰 렌더러 — Django 템플릿 기반

OMRDocument를 입력받아 iframe 삽입 가능한 HTML을 생성한다.
CSS는 omr-sheet.html과 동일한 스타일을 사용.
"""
from __future__ import annotations

from typing import Any

from django.template.loader import render_to_string

from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.services.meta_generator import (
    MARKER_OFF, MARKER_SZ, MARKER_TH, build_mc_column_ranges,
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
        render_essay_count = doc.render_essay_count
        instructions = [
            "전화번호는 본인 휴대폰 번호 뒤 8자리를 적어주세요. "
            "휴대폰이 없는 학생은 부모님 번호 뒤 8자리를 적어주세요."
        ]
        if doc.mc_count > 0:
            instructions.append(
                "객관식은 컴퓨터용 사인펜으로 버블을 빈틈없이 칠해주세요."
            )
        if render_essay_count > 0:
            instructions.append(doc.essay_instruction)
        instructions.append("수정테이프를 사용해주세요. (수정액 사용 금지)")

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
            "essay_count": render_essay_count,
            "essay_label": doc.render_essay_label,
            "logical_essay_count": doc.essay_count,
            "n_choices": doc.n_choices,
            "logo_url": doc.logo_url,
            "logo_variant": doc.logo_variant,
            "brand_color": doc.brand_color,
            "title_font_pt": title_font_pt,
            "mc_columns": mc_columns,
            "essay_rows": essay_rows,
            "essay_score_marks": self._build_essay_score_marks(doc),
            "instructions": instructions,
            "marking_instruction_number": len(instructions) + 1,
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

        columns = []
        question_numbers = doc.resolved_choice_question_numbers
        for column_range in build_mc_column_ranges(doc.mc_count):
            start = column_range["start"]
            end = column_range["end"]

            column_numbers = question_numbers[start - 1:end]
            is_contiguous = column_numbers == tuple(range(column_numbers[0], column_numbers[-1] + 1))
            label = (
                f"객관식 {column_numbers[0]}번 ~ {column_numbers[-1]}번"
                if is_contiguous
                else f"객관식 {len(column_numbers)}문항"
            )

            rows = []
            for row_in_col, q in enumerate(column_numbers, start=1):
                # 5행 그룹 인덱스 (0-based)
                group_idx = (row_in_col - 1) // 5
                rows.append({
                    "number": q,
                    "is_g5": (row_in_col % 5 == 0 and row_in_col != len(column_numbers)),
                    "is_g10": (row_in_col % 10 == 0 and row_in_col != len(column_numbers)),
                    "is_zebra": (group_idx % 2 == 1),
                })

            columns.append({
                "label": label,
                "rows": rows,
            })

        return columns

    def _build_essay_rows(self, doc: OMRDocument) -> list[dict]:
        render_essay_count = doc.render_essay_count
        if render_essay_count <= 0:
            return []

        rows = []
        for i in range(1, render_essay_count + 1):
            group_idx = (i - 1) // 5
            number = doc.display_essay_question_numbers[i - 1] if doc.essay_count > 0 else i
            rows.append({
                "number": number,
                "is_g5": (i % 5 == 0 and i != render_essay_count),
                "is_g10": (i % 10 == 0 and i != render_essay_count),
                "is_zebra": (group_idx % 2 == 1),
            })
        return rows

    def _build_essay_score_marks(self, doc: OMRDocument) -> list[dict]:
        """Absolute mm boxes for the teacher score strips (same SSOT as the PDF)."""
        marks = []
        for mark in doc.essay_score_marks:
            strip = mark["strip"]
            cy = float(mark["anchors"]["left"]["center"]["y"])
            bubbles = []
            labels = []
            for group in mark["groups"]:
                if group.get("label"):
                    labels.append({
                        "text": group["label"],
                        "left": round(float(group["label_x"]), 2),
                        "top": round(float(group["cy"]) - 1.8, 2),
                        "w": float(group["label_w"]),
                    })
                for bubble in group["bubbles"]:
                    rx = float(bubble["radius_x"])
                    ry = float(bubble["radius_y"])
                    bubbles.append({
                        "text": (
                            f".{bubble['value']}"
                            if group["place"] == "tenths"
                            else str(bubble["value"])
                        ),
                        "left": round(float(bubble["center"]["x"]) - rx, 2),
                        "top": round(float(bubble["center"]["y"]) - ry, 2),
                        "w": round(2 * rx, 2),
                        "h": round(2 * ry, 2),
                    })
            anchors = []
            for side in ("left", "right"):
                anchor = mark["anchors"][side]
                half = float(anchor["size"]) / 2
                anchors.append({
                    "left": round(float(anchor["center"]["x"]) - half, 2),
                    "top": round(float(anchor["center"]["y"]) - half, 2),
                    "size": round(2 * half, 2),
                })
            marks.append({
                "strip": {key: float(strip[key]) for key in ("x", "y", "w", "h")},
                "title_left": float(mark["title_x"]),
                "title_top": round(cy - 2.6, 2),
                "labels": labels,
                "bubbles": bubbles,
                "anchors": anchors,
            })
        return marks

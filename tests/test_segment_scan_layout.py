from __future__ import annotations

from academy.adapters.ai.detection import segment_dispatcher
from academy.adapters.ai.detection.segment_opencv import _merge_scan_content_regions


def test_merge_scan_content_regions_drops_header_and_merges_small_gaps():
    h_img = 1000
    regions = [
        (20, 120),   # header strip
        (160, 280),  # q1 stem
        (292, 390),  # q1 choices, small intra-question gap
        (455, 650),  # q2
        (910, 980),  # footer fragment
    ]

    assert _merge_scan_content_regions(regions, h_img) == [
        (160, 390),
        (455, 650),
        (910, 980),
    ]


def test_merge_scan_content_regions_keeps_large_combined_question_apart():
    h_img = 1000
    regions = [
        (125, 365),  # q4
        (413, 544),  # q5, close to q6 but already a complete short item
        (560, 829),  # q6
    ]

    assert _merge_scan_content_regions(regions, h_img) == [
        (125, 365),
        (413, 544),
        (560, 829),
    ]


def test_aggressive_merge_scan_content_regions_joins_fragmented_tables():
    h_img = 2000
    regions = [
        (330, 1050),   # stem/table body
        (1098, 1500),  # choices separated by a scan gap
    ]

    assert _merge_scan_content_regions(regions, h_img) == [
        (330, 1050),
        (1098, 1500),
    ]
    assert _merge_scan_content_regions(regions, h_img, aggressive=True) == [
        (330, 1500),
    ]


def test_visual_x_expansion_skips_when_regions_already_span_columns():
    class Region:
        def __init__(self, bbox):
            self.bbox = bbox

    regions = [
        Region((20.0, 80.0, 280.0, 300.0)),
        Region((320.0, 80.0, 580.0, 300.0)),
    ]

    assert segment_dispatcher._regions_already_span_columns(
        regions,
        page_width=600.0,
    )


def test_visual_x_expansion_skips_short_text_regions(tmp_path):
    import cv2
    import numpy as np

    class Region:
        def __init__(self, bbox):
            self.bbox = bbox

    image = np.full((100, 100), 255, dtype=np.uint8)
    image[50:52, :] = 0
    image_path = tmp_path / "short-region.png"
    cv2.imwrite(str(image_path), image)
    region = Region((20.0, 50.0, 40.0, 56.0))

    segment_dispatcher._expand_single_text_regions_to_visual_content(
        str(image_path),
        [region],
        page_width=100.0,
        page_height=100.0,
    )

    assert region.bbox == (20.0, 50.0, 40.0, 56.0)


def test_pdf_school_exam_scan_uses_scan_layout_fallback(monkeypatch):
    page_info = {
        "image_path": "page.png",
        "has_embedded_text": False,
        "text_boxes": [],
        "text_regions": [],
        "is_skip_page": False,
        "paper_type": "unknown",
        "paper_type_debug": {},
    }

    monkeypatch.setattr(segment_dispatcher, "is_ocr_available", lambda: False)
    monkeypatch.setattr(
        segment_dispatcher,
        "segment_questions_scan_layout",
        lambda image_path, *, apply_clahe=False, **kwargs: [(10, 20, 300, 400)],
    )
    monkeypatch.setattr(
        segment_dispatcher,
        "_classify_and_record_paper_type",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        segment_dispatcher,
        "_segment_single_image",
        lambda *args, **kwargs: [(_ for _ in ()).throw(AssertionError("generic fallback should not run"))],
    )

    boxes, regions = segment_dispatcher._boxes_and_regions_for_pdf_page(
        page_info,
        0,
        source_type="school_exam_pdf",
    )

    assert boxes == [(10, 20, 300, 400)]
    assert regions == []


def test_pdf_scan_paper_type_uses_scan_layout_even_for_other_source(monkeypatch):
    page_info = {
        "image_path": "page.png",
        "has_embedded_text": False,
        "text_boxes": [],
        "text_regions": [],
        "is_skip_page": False,
        "paper_type": "unknown",
        "paper_type_debug": {},
    }

    monkeypatch.setattr(segment_dispatcher, "is_ocr_available", lambda: False)

    def classify_scan(page_info, *_args, **_kwargs):
        page_info["paper_type"] = "scan_dual"

    monkeypatch.setattr(
        segment_dispatcher,
        "_classify_and_record_paper_type",
        classify_scan,
    )
    monkeypatch.setattr(
        segment_dispatcher,
        "segment_questions_scan_layout",
        lambda image_path, *, apply_clahe=False, merge_fragmented_columns=False: [
            (11, 22, 333, 444)
        ] if not merge_fragmented_columns else [],
    )
    monkeypatch.setattr(
        segment_dispatcher,
        "_segment_single_image",
        lambda *args, **kwargs: [(_ for _ in ()).throw(AssertionError("generic fallback should not run"))],
    )

    boxes, regions = segment_dispatcher._boxes_and_regions_for_pdf_page(
        page_info,
        0,
        source_type="other",
    )

    assert boxes == [(11, 22, 333, 444)]
    assert regions == []


def test_fragmented_scan_merge_requires_document_pattern():
    def scan_page(default_count: int, merged_count: int) -> dict:
        return {
            "has_embedded_text": False,
            "is_skip_page": False,
            "paper_type": "scan_dual",
            segment_dispatcher._SCAN_LAYOUT_BOXES_DEFAULT: [
                (0, i, 100, 100) for i in range(default_count)
            ],
            segment_dispatcher._SCAN_LAYOUT_BOXES_FRAGMENT_MERGED: [
                (0, i, 100, 100) for i in range(merged_count)
            ],
        }

    workbook_like = [scan_page(5, 2) for _ in range(10)]
    assert segment_dispatcher._should_use_fragmented_scan_workbook_merge(
        workbook_like,
        source_type="other",
    )

    dense_exam_like = [scan_page(6, 4) for _ in range(10)]
    assert not segment_dispatcher._should_use_fragmented_scan_workbook_merge(
        dense_exam_like,
        source_type="other",
    )
    assert not segment_dispatcher._should_use_fragmented_scan_workbook_merge(
        workbook_like,
        source_type="school_exam_pdf",
    )

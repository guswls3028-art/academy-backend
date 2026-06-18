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
        lambda image_path, *, apply_clahe=False: [(10, 20, 300, 400)],
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

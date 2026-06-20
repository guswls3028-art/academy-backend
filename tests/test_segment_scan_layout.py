from __future__ import annotations

from academy.adapters.ai.detection import segment_dispatcher
from academy.adapters.ai.detection.segment_opencv import _merge_scan_content_regions


class _TextLayerBlock:
    def __init__(self, text: str, *, y0: float = 100.0, y1: float = 112.0):
        self.text = text
        self.y0 = y0
        self.y1 = y1


class _TextLayerWord:
    def __init__(
        self,
        text: str,
        *,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
    ):
        self.text = text
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1


def test_sparse_problem_text_overlay_detects_anchor_only_scan_pages():
    assert segment_dispatcher._looks_like_sparse_problem_text_overlay(
        [_TextLayerBlock("14.\n15."), _TextLayerBlock("16.")],
        page_height=1000.0,
    )
    assert segment_dispatcher._looks_like_sparse_problem_text_overlay(
        [_TextLayerBlock("19.")],
        page_height=1000.0,
    )
    assert segment_dispatcher._looks_like_sparse_problem_text_overlay(
        [_TextLayerBlock("2")],
        page_height=1000.0,
    )


def test_sparse_problem_text_overlay_rejects_footer_and_answer_noise():
    assert not segment_dispatcher._looks_like_sparse_problem_text_overlay(
        [_TextLayerBlock("2", y0=970.0, y1=982.0)],
        page_height=1000.0,
    )
    assert not segment_dispatcher._looks_like_sparse_problem_text_overlay(
        [_TextLayerBlock("정답 1. ①")],
        page_height=1000.0,
    )
    assert not segment_dispatcher._looks_like_sparse_problem_text_overlay(
        [_TextLayerBlock("12. 그림은 화산 분출 이후 기온 변화를 나타낸 것이다.")],
        page_height=1000.0,
    )


def test_sparse_problem_number_anchors_from_words_keep_body_problem_numbers():
    anchors = segment_dispatcher._sparse_problem_number_anchors_from_words(
        [
            _TextLayerWord("429.", x0=50.0, y0=127.0, x1=80.0, y1=141.0),
            _TextLayerWord("14.", x0=49.0, y0=132.0, x1=63.0, y1=145.0),
            _TextLayerWord("15.", x0=363.0, y0=138.0, x1=376.0, y1=151.0),
            _TextLayerWord("16.", x0=368.0, y0=518.0, x1=382.0, y1=531.0),
            _TextLayerWord("2", x0=360.0, y0=980.0, x1=366.0, y1=992.0),
        ],
        page_width=728.0,
        page_height=1031.0,
    )

    assert [anchor["number"] for anchor in anchors] == [14, 15, 16]


def test_sparse_scan_anchor_regions_replace_fragmented_columns():
    boxes = [
        (0, 504, 728, 307),
        (0, 895, 728, 1073),
        (728, 281, 728, 246),
        (728, 637, 728, 484),
        (728, 1171, 728, 632),
    ]
    page_info = {
        "page_width": 728.0,
        "page_height": 1031.0,
        "image_size": (1456, 2062),
        "sparse_problem_anchors": [
            {"number": 14, "x0": 49.0, "y0": 132.0, "x1": 63.0, "y1": 145.0},
            {"number": 15, "x0": 363.0, "y0": 138.0, "x1": 376.0, "y1": 151.0},
            {"number": 16, "x0": 368.0, "y0": 518.0, "x1": 382.0, "y1": 531.0},
        ],
    }

    merged, numbers = segment_dispatcher._scan_boxes_with_sparse_problem_anchors(
        page_info,
        boxes,
    )

    assert numbers == [14, 15, 16]
    assert len(merged) == 3
    assert merged[0][1] < boxes[0][1]
    assert merged[0][1] + merged[0][3] == boxes[1][1] + boxes[1][3]


def test_sparse_scan_single_right_written_anchor_merges_left_first_item():
    boxes = [
        (0, 76, 716, 1053),
        (716, 81, 740, 259),
        (716, 392, 740, 1579),
        (0, 1203, 716, 763),
    ]
    page_info = {
        "page_width": 728.0,
        "page_height": 1031.0,
        "image_size": (1456, 2062),
        "sparse_problem_anchors": [
            {"number": 2, "x0": 397.9, "y0": 138.0, "x1": 403.4, "y1": 151.3},
        ],
    }

    merged, numbers = segment_dispatcher._scan_boxes_with_sparse_problem_anchors(
        page_info,
        boxes,
    )

    assert numbers == [1, 2]
    assert len(merged) == 2
    assert merged[0] == (0, 76, 716, 1890)


def test_sparse_scan_anchor_regions_keep_leading_question_and_backfill_prior_column():
    boxes = [
        (0, 478, 728, 558),
        (0, 1204, 728, 762),
        (728, 280, 728, 329),
        (728, 684, 728, 660),
        (728, 1476, 728, 528),
    ]
    page_info = {
        "page_width": 728.0,
        "page_height": 1031.0,
        "image_size": (1456, 2062),
        "sparse_problem_anchors": [
            {"number": 8, "x0": 373.0, "y0": 476.6, "x1": 380.7, "y1": 489.9},
            {"number": 9, "x0": 372.0, "y0": 677.5, "x1": 379.7, "y1": 690.8},
        ],
    }

    merged, numbers = segment_dispatcher._scan_boxes_with_sparse_problem_anchors(
        page_info,
        boxes,
    )

    assert numbers == [5, 6, 7, 8, 9]
    assert merged[2] == boxes[2]


def test_partial_text_overlay_scan_detects_missing_ink_column(tmp_path):
    import cv2
    import numpy as np

    image = np.full((1000, 800), 255, dtype=np.uint8)
    image[200:800, 80:300] = 0
    image[300:700, 540:660] = 0
    image_path = tmp_path / "partial-overlay.png"
    cv2.imwrite(str(image_path), image)

    blocks = [
        _TextLayerBlock("8. right text", y0=250.0, y1=265.0),
        _TextLayerBlock("9. right text", y0=500.0, y1=515.0),
    ]
    for block in blocks:
        block.x0 = 500.0
        block.x1 = 700.0

    assert segment_dispatcher._looks_like_partial_text_overlay_scan_page(
        blocks,
        image_path=str(image_path),
        page_width=800.0,
    )


def test_partial_text_overlay_scan_rejects_light_missing_side_ink(tmp_path):
    import cv2
    import numpy as np

    image = np.full((1000, 800), 255, dtype=np.uint8)
    image[180:820, 80:320] = 0
    image[300:620, 560:660] = 0
    image_path = tmp_path / "single-column-with-light-figure.png"
    cv2.imwrite(str(image_path), image)

    blocks = [
        _TextLayerBlock("5. left text", y0=250.0, y1=265.0),
        _TextLayerBlock("6. left text", y0=500.0, y1=515.0),
    ]
    for block in blocks:
        block.x0 = 90.0
        block.x1 = 310.0

    assert not segment_dispatcher._looks_like_partial_text_overlay_scan_page(
        blocks,
        image_path=str(image_path),
        page_width=800.0,
    )


def test_backfill_scan_numbers_from_next_numbered_page():
    page_infos = [
        {"paper_type": "scan_dual"},
        {"paper_type": "scan_dual", "scan_box_numbers": [5, 6, 7, 8, 9]},
    ]
    boxes_per_page = [
        [(0, 0, 100, 100), (0, 110, 100, 100), (100, 0, 100, 100), (100, 110, 100, 100)],
        [(0, 0, 100, 100) for _ in range(5)],
    ]

    segment_dispatcher._backfill_scan_numbers_from_next_numbered_page(
        page_infos,
        boxes_per_page,
        [[], []],
    )

    assert page_infos[0]["scan_box_numbers"] == [1, 2, 3, 4]


def test_drop_terminal_unnumbered_scan_cover_page():
    page_infos = [
        {"paper_type": "scan_dual", "image_size": (1000, 1500), "scan_box_numbers": [1]},
        {"paper_type": "scan_dual", "image_size": (1000, 1500)},
    ]
    boxes_per_page = [
        [(50, 100, 900, 500)],
        [(0, 600, 500, 120)],
    ]

    segment_dispatcher._drop_terminal_unnumbered_scan_cover_pages(
        page_infos,
        boxes_per_page,
        [[], []],
    )

    assert boxes_per_page[0]
    assert boxes_per_page[1] == []


def test_drop_terminal_unnumbered_scan_cover_page_reads_image_size(tmp_path):
    import cv2
    import numpy as np

    image = np.full((1500, 1000), 255, dtype=np.uint8)
    image_path = tmp_path / "terminal-cover.png"
    cv2.imwrite(str(image_path), image)
    page_infos = [
        {"paper_type": "scan_dual", "image_path": str(image_path)},
    ]
    boxes_per_page = [[(0, 600, 500, 120)]]

    segment_dispatcher._drop_terminal_unnumbered_scan_cover_pages(
        page_infos,
        boxes_per_page,
        [[]],
    )

    assert boxes_per_page[0] == []


def test_drop_terminal_unnumbered_scan_cover_page_keeps_numbered_last_page():
    page_infos = [
        {"paper_type": "scan_dual", "image_size": (1000, 1500), "scan_box_numbers": [1]},
    ]
    boxes_per_page = [[(50, 100, 900, 500)]]

    segment_dispatcher._drop_terminal_unnumbered_scan_cover_pages(
        page_infos,
        boxes_per_page,
        [[]],
    )

    assert boxes_per_page[0] == [(50, 100, 900, 500)]


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


def test_aggressive_merge_scan_content_regions_pairs_short_stems_without_chaining():
    h_img = 2062
    regions = [
        (275, 454),    # q3 stem
        (641, 946),    # q3 choices
        (1115, 1574),  # q4 stem
        (1651, 2005),  # q4 choices
    ]

    assert _merge_scan_content_regions(regions, h_img, aggressive=True) == [
        (275, 946),
        (1115, 2005),
    ]


def test_aggressive_merge_scan_content_regions_joins_short_stem_with_tall_body():
    h_img = 2062
    regions = [
        (504, 811),    # q14 visual stem
        (895, 1968),   # q14 choices/body
    ]

    assert _merge_scan_content_regions(regions, h_img, aggressive=True) == [
        (504, 1968),
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


def test_visual_x_expansion_preserves_region_audit_bbox(tmp_path):
    import cv2
    import numpy as np

    from academy.domain.tools.question_splitter import QuestionRegion

    image = np.full((100, 100), 255, dtype=np.uint8)
    image[20:80, 10:90] = 0
    image_path = tmp_path / "wide-visual.png"
    cv2.imwrite(str(image_path), image)
    region = QuestionRegion(
        number=1,
        bbox=(40.0, 20.0, 55.0, 80.0),
        page_index=0,
        semantic_flags=("visual_context",),
    )
    original_audit = region.audit_bbox

    segment_dispatcher._expand_single_text_regions_to_visual_content(
        str(image_path),
        [region],
        page_width=100.0,
        page_height=100.0,
    )

    assert region.bbox[0] < 20.0
    assert region.bbox[2] > 80.0
    assert region.display_bbox == region.bbox
    assert region.audit_bbox == original_audit


def test_visual_x_expansion_uses_wide_content_without_visual_flag(tmp_path):
    import cv2
    import numpy as np

    from academy.domain.tools.question_splitter import QuestionRegion

    image = np.full((100, 120), 255, dtype=np.uint8)
    image[45:53, 12:112] = 0
    image_path = tmp_path / "wide-content.png"
    cv2.imwrite(str(image_path), image)
    region = QuestionRegion(
        number=1,
        bbox=(12.0, 20.0, 52.0, 80.0),
        page_index=0,
    )

    segment_dispatcher._expand_single_text_regions_to_visual_content(
        str(image_path),
        [region],
        page_width=120.0,
        page_height=100.0,
    )

    assert region.bbox[2] > 108.0
    assert "wide_content" in region.semantic_flags


def test_visual_x_expansion_ignores_top_frame_ink_without_visual_flag(tmp_path):
    import cv2
    import numpy as np

    from academy.domain.tools.question_splitter import QuestionRegion

    image = np.full((100, 120), 255, dtype=np.uint8)
    image[21:24, 12:112] = 0
    image[45:55, 12:45] = 0
    image_path = tmp_path / "top-frame.png"
    cv2.imwrite(str(image_path), image)
    region = QuestionRegion(
        number=1,
        bbox=(12.0, 20.0, 52.0, 80.0),
        page_index=0,
    )

    segment_dispatcher._expand_single_text_regions_to_visual_content(
        str(image_path),
        [region],
        page_width=120.0,
        page_height=100.0,
    )

    assert region.bbox == (12.0, 20.0, 52.0, 80.0)


def test_commercial_written_response_answer_space_extends_body_and_audit_bbox():
    from academy.domain.tools.question_splitter import QuestionRegion

    region = QuestionRegion(
        number=1,
        bbox=(10.0, 20.0, 80.0, 32.0),
        page_index=0,
        semantic_flags=("written_response",),
    )
    next_region = QuestionRegion(
        number=2,
        bbox=(10.0, 70.0, 80.0, 90.0),
        page_index=0,
    )
    segment_dispatcher._expand_commercial_written_response_answer_space(
        [region, next_region],
        page_width=100.0,
        page_height=100.0,
    )

    assert region.bbox == (10.0, 20.0, 80.0, 36.5)
    assert region.display_bbox == region.bbox
    assert region.body_bbox == region.bbox
    assert region.audit_bbox == region.bbox
    assert "answer_space" in region.semantic_flags


def test_commercial_short_written_stem_does_not_get_answer_space():
    from academy.domain.tools.question_splitter import QuestionRegion

    region = QuestionRegion(
        number=30,
        bbox=(10.0, 20.0, 80.0, 27.0),
        page_index=0,
        semantic_flags=("short_workbook_prompt", "written_response"),
    )

    segment_dispatcher._expand_commercial_written_response_answer_space(
        [region],
        page_width=100.0,
        page_height=100.0,
    )

    assert region.bbox == (10.0, 20.0, 80.0, 27.0)
    assert "answer_space" not in region.semantic_flags


def test_commercial_reasoning_written_stem_gets_answer_space():
    from academy.domain.tools.question_splitter import QuestionRegion

    region = QuestionRegion(
        number=183,
        bbox=(10.0, 20.0, 80.0, 27.5),
        page_index=0,
        semantic_flags=("reasoning_response", "written_response"),
    )

    segment_dispatcher._expand_commercial_written_response_answer_space(
        [region],
        page_width=100.0,
        page_height=100.0,
    )

    assert region.bbox == (10.0, 20.0, 80.0, 36.5)
    assert "answer_space" in region.semantic_flags


def test_commercial_short_workbook_prompt_answer_space_extends_display_bbox():
    from academy.domain.tools.question_splitter import QuestionRegion

    region = QuestionRegion(
        number=284,
        bbox=(10.0, 20.0, 80.0, 32.0),
        page_index=0,
        semantic_flags=("short_workbook_prompt",),
    )
    next_region = QuestionRegion(
        number=285,
        bbox=(10.0, 70.0, 80.0, 90.0),
        page_index=0,
    )

    segment_dispatcher._expand_commercial_written_response_answer_space(
        [region, next_region],
        page_width=100.0,
        page_height=100.0,
    )

    assert region.bbox == (10.0, 20.0, 80.0, 36.5)
    assert region.body_bbox == region.bbox
    assert "answer_space" in region.semantic_flags


def test_commercial_visual_short_prompt_does_not_get_answer_space():
    from academy.domain.tools.question_splitter import QuestionRegion

    region = QuestionRegion(
        number=1,
        bbox=(10.0, 20.0, 80.0, 31.0),
        page_index=0,
        semantic_flags=("short_workbook_prompt", "visual_context"),
    )

    segment_dispatcher._expand_commercial_written_response_answer_space(
        [region],
        page_width=100.0,
        page_height=100.0,
    )

    assert region.bbox == (10.0, 20.0, 80.0, 31.0)
    assert "answer_space" not in region.semantic_flags


def test_commercial_first_shared_context_uses_context_display():
    from academy.domain.tools.question_splitter import QuestionRegion

    region = QuestionRegion(
        number=12,
        bbox=(10.0, 10.0, 80.0, 40.0),
        page_index=0,
        body_bbox=(10.0, 30.0, 80.0, 40.0),
        context_bbox=(10.0, 10.0, 80.0, 90.0),
        semantic_flags=("shared_context_first", "written_response"),
    )

    segment_dispatcher._prefer_commercial_first_shared_context_display([region])

    assert region.bbox == (10.0, 10.0, 80.0, 90.0)
    assert region.body_bbox == (10.0, 30.0, 80.0, 40.0)
    assert "shared_context_answer_space" in region.semantic_flags


def test_commercial_first_shared_context_caps_display_height():
    from academy.domain.tools.question_splitter import QuestionRegion

    region = QuestionRegion(
        number=5,
        bbox=(10.0, 10.0, 80.0, 35.0),
        page_index=0,
        body_bbox=(10.0, 25.0, 80.0, 35.0),
        context_bbox=(10.0, 10.0, 80.0, 90.0),
        semantic_flags=("shared_context_first", "written_response"),
    )

    segment_dispatcher._prefer_commercial_first_shared_context_display(
        [region],
        page_height=100.0,
    )

    assert region.bbox == (10.0, 10.0, 80.0, 45.0)
    assert "shared_context_answer_space" in region.semantic_flags


def test_commercial_later_shared_written_prefers_body_display():
    from academy.domain.tools.question_splitter import QuestionRegion

    region = QuestionRegion(
        number=13,
        bbox=(10.0, 10.0, 80.0, 90.0),
        page_index=0,
        body_bbox=(10.0, 55.0, 80.0, 70.0),
        semantic_flags=("shared_context_later", "written_response"),
    )

    segment_dispatcher._prefer_commercial_later_shared_body_display([region])

    assert region.bbox == (10.0, 55.0, 80.0, 70.0)
    assert region.body_bbox == (10.0, 55.0, 80.0, 70.0)
    assert "shared_body_display" in region.semantic_flags


def test_commercial_later_shared_reference_keeps_context_display():
    from academy.domain.tools.question_splitter import QuestionRegion

    region = QuestionRegion(
        number=9,
        bbox=(10.0, 10.0, 80.0, 90.0),
        page_index=0,
        body_bbox=(10.0, 55.0, 80.0, 70.0),
        semantic_flags=("shared_context_later", "references_prior_context"),
    )

    segment_dispatcher._prefer_commercial_later_shared_body_display([region])

    assert region.bbox == (10.0, 10.0, 80.0, 90.0)


def test_other_source_ink_trim_drops_isolated_footer_band(tmp_path):
    import cv2
    import numpy as np

    from academy.domain.tools.question_splitter import QuestionRegion

    image = np.full((200, 120), 255, dtype=np.uint8)
    image[25:95, 8:55] = 0       # question content
    image[174:188, 70:114] = 0   # isolated footer/copyright note
    image_path = tmp_path / "school-exam-footer.png"
    cv2.imwrite(str(image_path), image)
    region = QuestionRegion(
        number=104,
        bbox=(5.0, 20.0, 118.0, 195.0),
        page_index=0,
        semantic_flags=("written_response",),
    )
    original_audit = region.audit_bbox

    segment_dispatcher._trim_other_source_text_regions_to_ink(
        str(image_path),
        [region],
        page_width=120.0,
        page_height=200.0,
        source_type="other",
    )

    assert region.bbox[2] < 70.0
    assert region.bbox[3] < 125.0
    assert region.audit_bbox == original_audit
    assert "ink_trimmed" in region.semantic_flags


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

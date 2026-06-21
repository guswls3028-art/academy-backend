"""매치업 세그멘테이션 후처리 회귀 테스트."""
from __future__ import annotations

import inspect

from academy.application.use_cases.ai.pipelines import matchup_pipeline


def test_auto_merge_fragment_uses_top_level_page_index():
    """_boxes_to_questions 결과는 page_index가 top-level에 있다."""
    questions = [
        {
            "number": 1,
            "page_index": 0,
            "image_path": "/tmp/p0.png",
            "bbox": [10, 10, 200, 100],
            "meta_extra": {"number_source": "counter_fallback"},
        },
        {
            "number": 2,
            "page_index": 0,
            "image_path": "/tmp/p0.png",
            "bbox": [10, 115, 200, 35],
            "meta_extra": {"number_source": "counter_fallback"},
        },
    ]

    merged = matchup_pipeline._auto_merge_fragment_questions(
        questions,
        paper_type_summary={"primary": "clean_pdf_single"},
        enabled=True,
        document_id="doc-test",
    )

    assert len(merged) == 1
    assert merged[0]["bbox"] == [10.0, 10.0, 200.0, 140.0]
    assert merged[0]["meta_extra"]["auto_merged_fragment_count"] == 2
    assert merged[0]["meta_extra"]["auto_merged_numbers"] == [1, 2]


def test_auto_merge_fragment_keeps_distinct_segment_numbers():
    questions = [
        {
            "number": 1,
            "page_index": 0,
            "image_path": "/tmp/p0.png",
            "bbox": [10, 10, 200, 100],
            "meta_extra": {"number_source": "segment"},
        },
        {
            "number": 2,
            "page_index": 0,
            "image_path": "/tmp/p0.png",
            "bbox": [10, 115, 200, 100],
            "meta_extra": {"number_source": "segment"},
        },
    ]

    merged = matchup_pipeline._auto_merge_fragment_questions(
        questions,
        paper_type_summary={"primary": "clean_pdf_single"},
        enabled=True,
        document_id="doc-test",
    )

    assert len(merged) == 2
    assert all("auto_merged_fragment_count" not in q.get("meta_extra", {}) for q in merged)


def test_segmentation_postprocess_runs_before_ocr_and_embedding():
    src = inspect.getsource(matchup_pipeline.run_matchup_pipeline)

    assert src.index("_filter_questions_by_min_area(") < src.index("_insert_skeleton_problems(")
    assert src.index("_auto_merge_fragment_questions(") < src.index("_extract_texts(questions_raw")
    assert src.index("_filter_questions_by_hybrid_vlm(") < src.index("_generate_embeddings(questions_raw")

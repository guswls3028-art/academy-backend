from __future__ import annotations

from unittest.mock import patch

from apps.shared.contracts.ai_job import AIJob
from academy.application.use_cases.ai.pipelines.pdf_question_pipeline import (
    _build_question_list,
    _extract_explanations,
    _find_academy_review_cover_pages,
    _find_solution_tail_start,
    run_pdf_question_pipeline,
)


def _page(page_index: int, number: int | None, bbox: tuple[int, int, int, int]):
    return {
        "page_index": page_index,
        "image_path": f"page-{page_index}.png",
        "boxes": [bbox],
        "numbers": [number],
        "has_embedded_text": True,
        "paper_type": "clean_pdf_dual",
    }


def test_short_dated_academy_review_cover_is_filtered_after_segmentation():
    pages = [
        _page(0, 1, (10, 20, 100, 200)),
        _page(1, 1, (10, 20, 100, 300)),
    ]
    cover_text = "1/8(목) 고1 Hyper\n공통수학2 Remake\n복습 Test\n1. 평면좌표"
    problem_text = cover_text + (" 다음 조건을 만족시키는 좌표를 구하시오." * 20)

    assert _find_academy_review_cover_pages(
        {0: cover_text, 1: problem_text},
        pages,
    ) == {0}


def test_solution_tail_requires_an_earlier_problem_page():
    pages = [
        {**_page(0, None, (0, 0, 0, 0)), "boxes": []},
        _page(1, 1, (10, 20, 100, 200)),
    ]

    assert _find_solution_tail_start({0: "정답 및 해설", 1: "1. 문제"}, pages) is None
    assert _find_solution_tail_start({0: "표지", 1: "정답 및 해설"}, pages) is None

    pages.append(_page(2, 1, (10, 20, 100, 300)))
    assert (
        _find_solution_tail_start(
            {2: "2. 다음 정답 및 해설을 참고하여 물음에 답하시오."},
            pages,
        )
        is None
    )
    assert _find_solution_tail_start({2: "정 답 및 해 설\n1. 풀이"}, pages) == 2


def test_question_list_uses_dispatcher_bbox_and_skips_solution_tail():
    pages = [
        _page(0, 1, (10, 20, 100, 260)),
        _page(1, 2, (10, 20, 100, 420)),
        _page(2, 1, (10, 20, 100, 500)),
    ]

    questions = _build_question_list(
        pages,
        {},
        excluded_page_indexes={2},
    )

    assert [question["number"] for question in questions] == [1, 2]
    assert [question["bbox"] for question in questions] == [
        (10, 20, 100, 260),
        (10, 20, 100, 420),
    ]
    assert [question["meta"]["original_number"] for question in questions] == [1, 2]


def test_explanations_continue_across_tail_pages_without_repeated_heading():
    explanations = _extract_explanations(
        {
            3: "정답 및 해설\n1. 첫 풀이\n2. 둘째 풀이",
            4: "3. 셋째 풀이",
        },
        solution_tail_start=3,
    )

    assert [item["question_number"] for item in explanations] == [1, 2, 3]
    assert [item["page_index"] for item in explanations] == [3, 3, 4]


@patch(
    "academy.application.use_cases.ai.pipelines.pdf_question_pipeline."
    "_crop_and_upload_explanation_images",
    return_value={1: "explanations/q001.png"},
)
@patch(
    "academy.application.use_cases.ai.pipelines.pdf_question_pipeline."
    "_crop_and_upload_question_images",
    return_value={1: "questions/q001.png"},
)
@patch(
    "academy.application.use_cases.ai.pipelines.pdf_question_pipeline."
    "_extract_pdf_text",
)
@patch(
    "academy.application.use_cases.ai.pipelines.pdf_question_pipeline."
    "register_pdf_seg_tmp_dirs",
)
@patch(
    "academy.application.use_cases.ai.pipelines.pdf_question_pipeline."
    "segment_questions_multipage",
)
def test_pipeline_reuses_dispatcher_crops_and_drops_solution_questions(
    segment_multipage,
    register_tmp,
    extract_pdf_text,
    crop_images,
    crop_explanations,
):
    problem_bbox = (10, 20, 100, 260)
    solution_bbox = (10, 20, 100, 500)
    segment_multipage.return_value = {
        "is_pdf": True,
        "tmp_dirs": ["pdf-seg-test"],
        "pages": [
            _page(0, 5, solution_bbox),
            _page(1, 1, problem_bbox),
            _page(2, 1, solution_bbox),
            _page(3, 2, solution_bbox),
        ],
    }
    extract_pdf_text.return_value = (
        {},
        {
            0: "1/22(목) 고1 Hyper\n공통수학2 Remake\n복습 Test\n5. 범위",
            1: "1. 문제",
            2: "정답 및 해설\n1. 첫 풀이",
            3: "2. 문항에 없는 풀이",
        },
    )
    job = AIJob.new(type="question_segmentation", payload={})

    result = run_pdf_question_pipeline(
        job=job,
        local_path="source.pdf",
        payload={"exam_id": "10"},
        tenant_id="tenant-1",
        record_progress=lambda *args, **kwargs: None,
    )

    assert result.status == "DONE"
    assert result.result["total_questions"] == 1
    assert result.result["boxes"] == [problem_bbox]
    assert result.result["questions"] == [
        {
            "number": 1,
            "bbox": list(problem_bbox),
            "page_index": 1,
            "text": None,
            "original_number": 1,
        }
    ]
    assert [item["question_number"] for item in result.result["explanations"]] == [1]
    assert result.result["explanations"][0]["image_key"] == "explanations/q001.png"
    crop_explanations.assert_called_once()
    passed_questions = crop_images.call_args.kwargs["questions"]
    assert [item["bbox"] for item in passed_questions] == [problem_bbox]
    register_tmp.assert_called_once_with(["pdf-seg-test"])

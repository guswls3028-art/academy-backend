from unittest.mock import patch

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from academy.application.use_cases.ai.pipelines.paired_source_pipeline import (
    attach_paired_source_results,
)


def _job() -> AIJob:
    return AIJob.new(
        type="question_segmentation",
        tenant_id="7",
        source_domain="exams",
        source_id="31",
        payload={
            "exam_id": "31",
            "filename": "problems.pdf",
            "download_url": "https://files.test/problems.pdf",
            "answer_filename": "answers.pdf",
            "answer_download_url": "https://files.test/answers.pdf",
            "answer_source_requested": True,
            "explanation_filename": "explanations.png",
            "explanation_download_url": "https://files.test/explanations.png",
            "explanation_source_requested": True,
        },
    )


@patch(
    "academy.application.use_cases.ai.pipelines.paired_source_pipeline."
    "cleanup_tmp_for_path",
)
@patch(
    "academy.application.use_cases.ai.pipelines.paired_source_pipeline."
    "run_pdf_question_pipeline",
)
@patch(
    "academy.application.use_cases.ai.pipelines.paired_source_pipeline."
    "download_to_tmp",
    side_effect=["explanations.png", "answers.pdf"],
)
def test_dispatcher_matches_three_sources_by_original_number_and_reports_partial(
    _download,
    run_pipeline,
    _cleanup,
):
    primary_result = AIResult.done(
        "job",
        {
            "exam_id": "31",
            "questions": [
                {"number": 1, "original_number": 10},
                {"number": 2, "original_number": 11},
            ],
            "explanations": [{"question_number": 10, "text": "본문 해설"}],
        },
    )
    run_pipeline.side_effect = [
        AIResult.done(
            "job",
            {
                "explanations": [
                    {
                        "question_number": 10,
                        "text": "교사 원문 풀이",
                        "image_key": "teacher-page-1.png",
                    },
                    {"question_number": 99, "text": "다른 시험 풀이"},
                ],
                "source_issues": [],
            },
        ),
        AIResult.done(
            "job",
            {
                "answers": [
                    {
                        "question_number": 10,
                        "answer": "4",
                        "source_image_key": "answer-page-1.png",
                    }
                ],
                "source_issues": [],
            },
        ),
    ]

    job = _job()
    result = attach_paired_source_results(
        job=job,
        primary_result=primary_result,
        payload=job.payload,
        tenant_id=job.tenant_id,
        record_progress=lambda *args, **kwargs: None,
    )

    assert result.status == "DONE"
    assert result.result["answers"] == [
        {
            "question_number": 10,
            "answer": "4",
            "source_image_key": "answer-page-1.png",
        }
    ]
    assert result.result["explanations"] == [
        {
            "question_number": 10,
            "text": "교사 원문 풀이",
            "image_key": "teacher-page-1.png",
        }
    ]
    assert result.result["unmatched_teacher_explanation_numbers"] == [99]
    assert result.result["missing_answer_numbers"] == [11]
    assert result.result["missing_explanation_numbers"] == [11]
    assert result.result["paired_source_status"] == "partial"
    assert set(result.result["source_issues"]) == {
        "answer_coverage_incomplete",
        "explanation_coverage_incomplete",
    }
    assert _cleanup.call_count == 2


@patch(
    "academy.application.use_cases.ai.pipelines.paired_source_pipeline."
    "cleanup_tmp_for_path",
)
@patch(
    "academy.application.use_cases.ai.pipelines.paired_source_pipeline."
    "run_pdf_question_pipeline",
)
@patch(
    "academy.application.use_cases.ai.pipelines.paired_source_pipeline."
    "download_to_tmp",
)
def test_dispatcher_does_not_erase_primary_explanation_without_paired_source(
    _download,
    run_pipeline,
    _cleanup,
):
    job = AIJob.new(
        type="question_segmentation",
        tenant_id="7",
        payload={
            "exam_id": "31",
            "filename": "problems.pdf",
            "download_url": "https://files.test/problems.pdf",
        },
    )
    primary_result = AIResult.done(
        "job",
        {
            "questions": [{"number": 1, "original_number": 1}],
            "explanations": [
                {"question_number": 1, "text": "문제지 뒤쪽 교사 원문"}
            ],
        },
    )

    result = attach_paired_source_results(
        job=job,
        primary_result=primary_result,
        payload=job.payload,
        tenant_id=job.tenant_id,
        record_progress=lambda *args, **kwargs: None,
    )

    assert result.status == "DONE"
    assert result.result["explanations"] == [
        {"question_number": 1, "text": "문제지 뒤쪽 교사 원문"}
    ]
    assert result.result["teacher_explanation_count"] == 1
    _download.assert_not_called()
    run_pipeline.assert_not_called()
    _cleanup.assert_not_called()

"""Numberless photo fallback must not block validated printed question numbers."""
from types import SimpleNamespace

import pytest

from academy.application.use_cases.ai.pipelines import matchup_pipeline as pipeline


def run_photo(monkeypatch, numbers, *, source="student_exam_photo", trusted=False,
              other_page_number=None, result=True):
    monkeypatch.setenv("MATCHUP_VLM_AUTO_SPLIT", "1")
    monkeypatch.setenv("MATCHUP_VLM_FILL_UNDERFILLED_PAGES", "1")
    monkeypatch.setenv("MATCHUP_VLM_VISION_ADAPTER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    page = {"page_index": 0, "image_path": "/fake/photo.png",
            "paper_type": "student_answer_photo", "numbers": [None, None],
            "boxes": [(0, 100, 1900, 1000), (0, 1100, 1900, 1000)]}
    questions = pipeline._boxes_to_questions([page])
    if trusted:
        questions[0]["meta_extra"]["number_source"] = "segment"
    original = [dict(q) for q in questions]
    if other_page_number is not None:
        questions.append({"number": other_page_number, "page_index": 1,
                          "bbox": [10, 100, 800, 800], "image_path": "/fake/other.png"})
    problems = [SimpleNamespace(number=n, bbox=(10, 100 + i * 300, 800, 500),
                                shared_with=[]) for i, n in enumerate(numbers)]
    # Validated shared stem: both numbered questions legitimately use one crop.
    if numbers == [6, 7, 8, 9, 10, 11]:
        problems[2].bbox = problems[1].bbox
        problems[1].shared_with = [8]
        problems[2].shared_with = [7]
    vlm = SimpleNamespace(problems=problems) if result else None
    monkeypatch.setattr(pipeline, "_try_vlm_problem_bboxes",
                        lambda *args, **kwargs: (vlm, "student_answer_photo"))
    stats = pipeline._augment_questions_with_vlm_for_underfilled_pages(
        [page], questions, source_type=source, document_id=1, tenant_id=1)
    return questions, original, problems, stats


@pytest.mark.parametrize("numbers", [
    [12, 13, 14, 15], [6, 7, 8, 9, 10, 11], [1, 2, 3, 4, 5],
    [29, 30, 31, 32], [25, 26, 27, 28], [21, 22, 23, 24], [16, 17, 18, 19, 20],
])
def test_validated_photo_replaces_unanchored_counter_boxes(monkeypatch, numbers):
    questions, _, problems, stats = run_photo(monkeypatch, numbers)
    assert [q["number"] for q in questions] == numbers
    assert [q["bbox"] for q in questions] == [list(p.bbox) for p in problems]
    assert all(q["meta_extra"]["engine"] == "vlm" for q in questions)
    assert stats["replacement_pages"] == 1
    assert stats["replaced_auto"] == 2
    if 7 in numbers:
        assert questions[1]["meta_extra"]["shared_with"] == [8]
        assert questions[2]["meta_extra"]["shared_with"] == [7]


@pytest.mark.parametrize("kwargs", [
    {"source": "school_exam_pdf"}, {"trusted": True}, {"result": False},
    {"other_page_number": 12},
])
def test_replacement_does_not_remove_trusted_or_conflicting_work(monkeypatch, kwargs):
    questions, original, _, stats = run_photo(monkeypatch, [12, 13], **kwargs)
    assert stats.get("replacement_pages", 0) == 0
    assert all(any(q["bbox"] == old["bbox"] for q in questions) for old in original)
    if kwargs.get("other_page_number"):
        assert any(q["page_index"] == 1 and q["number"] == 12 for q in questions)


def test_validated_photo_keeps_unrelated_page(monkeypatch):
    questions, _, _, stats = run_photo(monkeypatch, [12, 13], other_page_number=21)
    assert sorted(q["number"] for q in questions) == [12, 13, 21]
    assert stats["replacement_pages"] == 1


@pytest.mark.parametrize("numbers", [[0, 12], [-1, 12], [12, 12]])
def test_invalid_replacement_numbers_do_not_discard_existing_candidates(monkeypatch, numbers):
    questions, original, _, stats = run_photo(monkeypatch, numbers)
    assert stats.get("replacement_pages", 0) == 0
    assert all(any(q["bbox"] == old["bbox"] for q in questions) for old in original)

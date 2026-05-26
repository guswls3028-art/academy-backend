from apps.domains.results.aggregations.exam_report import summarize_result_items


def test_summarize_result_items_returns_wrong_numbers_in_question_order():
    analysis = summarize_result_items([
        {"question_id": 30, "question_number": 3, "is_correct": False},
        {"question_id": 10, "question_number": 1, "is_correct": True},
        {"question_id": 20, "question_number": 2, "is_correct": False},
    ])

    assert analysis == {
        "total_questions": 3,
        "correct_count": 1,
        "wrong_count": 2,
        "accuracy_rate": 33.3,
        "wrong_question_numbers": [2, 3],
    }


def test_summarize_result_items_returns_empty_analysis_without_items():
    assert summarize_result_items([]) == {
        "total_questions": 0,
        "correct_count": 0,
        "wrong_count": 0,
        "accuracy_rate": None,
        "wrong_question_numbers": [],
    }

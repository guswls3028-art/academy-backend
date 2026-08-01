from scripts.problem_studio_pdf_prototype import (
    _is_korean_explanation,
    _objective_result_is_consistent,
    _reconcile_objective_answer,
    _item_input_sha256,
    _verification_label,
)


def test_item_input_fingerprint_changes_with_solve_input():
    item = {
        "number": 1,
        "prompt": "문제",
        "choices": ["① A", "② B"],
        "source_answer": "②",
        "visual_file": "visuals/question-0001.jpg",
        "visual_mime": "image/jpeg",
        "visual_role": "question_crop",
    }

    first = _item_input_sha256(item)
    second = _item_input_sha256({**item, "prompt": "수정된 문제"})

    assert len(first) == 64
    assert first != second
    assert first == _item_input_sha256(dict(item))


def test_verification_label_distinguishes_manual_and_low_confidence_source():
    assert _verification_label({
        "answer_source": "source_reference",
        "confidence": "low",
    })[0] == "검수 필요 · 원본답 해설 불충분"
    assert _verification_label({
        "answer_source": "source_reference",
        "verification_status": "manual_source_review",
    })[0] == "원본 모범답안 · 직접 검산"
    assert _verification_label({
        "answer_source": "ai_generated",
        "verification_status": "solve_validation_failed",
    })[0] == "검수 필요 · 자동 풀이 검증 실패"


def test_korean_explanation_gate_rejects_non_korean_output():
    assert _is_korean_explanation("정답 근거를 한국어 문장으로 충분히 설명합니다.")
    assert not _is_korean_explanation("选择①正确，因为图示条件相符。")
    assert not _is_korean_explanation("너무 짧음")


def test_objective_result_gate_requires_answer_choice_truth_consistency():
    item = {
        "prompt": "옳은 것만 고른 것은?",
        "choices": ["① ㄱ", "② ㄴ", "③ ㄱ, ㄴ"],
    }

    assert _objective_result_is_consistent(item, {
        "answer": "③",
        "true_statements": ["ㄱ", "ㄴ"],
        "selected_choice_text": "③ ㄱ, ㄴ",
    })
    assert not _objective_result_is_consistent(item, {
        "answer": "①",
        "true_statements": ["ㄱ", "ㄴ"],
        "selected_choice_text": "① ㄱ",
    })

    reconciled = _reconcile_objective_answer(item, {
        "answer": "① ㄱ",
        "true_statements": ["ㄱ", "ㄴ"],
        "selected_choice_text": "① ㄱ",
    })
    assert reconciled["answer"] == "③"
    assert reconciled["selected_choice_text"] == "③ ㄱ, ㄴ"
    assert _objective_result_is_consistent(item, reconciled)

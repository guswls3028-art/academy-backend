from scripts.exam_source_hwp_qa import _acceptance_summary, _qa_exit_code


def test_hwp_qa_accepts_only_fully_combined_documents():
    status, blocking = _acceptance_summary({"combined_document_ready": 133})
    summary = {"acceptance_status": status}

    assert status == "pass"
    assert blocking == {}
    assert _qa_exit_code(summary) == 0


def test_hwp_qa_fails_closed_when_a_problem_source_is_missing():
    status, blocking = _acceptance_summary(
        {
            "combined_document_ready": 132,
            "paired_problem_file_required": 1,
        }
    )
    summary = {"acceptance_status": status}

    assert status == "remediation_required"
    assert blocking == {"paired_problem_file_required": 1}
    assert _qa_exit_code(summary) == 1


def test_hwp_qa_distinguishes_extraction_errors_from_source_remediation():
    status, blocking = _acceptance_summary(
        {"combined_document_ready": 132, "error": 1}
    )
    summary = {"acceptance_status": status}

    assert status == "error"
    assert blocking == {"error": 1}
    assert _qa_exit_code(summary) == 1

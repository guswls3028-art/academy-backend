from types import SimpleNamespace

import fitz

from apps.domains.results.services.wrong_note_pdf_service import build_wrong_note_pdf


def test_text_only_explanation_keeps_last_line_after_page_break():
    text = "\n".join(["풀이 과정을 확인합니다."] * 45 + ["LAST-STEP"])
    pdf = build_wrong_note_pdf(
        enrollment=SimpleNamespace(student=SimpleNamespace(name="QA")),
        tenant_name="QA",
        items=[{"question_number": 3, "correct_answer": "4", "extra": {"explanation_text": text}}],
        from_session_order=1,
        exam_id=None,
    )
    with fitz.open(stream=pdf, filetype="pdf") as document:
        assert "LAST-STEP" not in document[1].get_text()
        assert "LAST-STEP" in document[-1].get_text()
        assert "1/2" in document[-2].get_text()

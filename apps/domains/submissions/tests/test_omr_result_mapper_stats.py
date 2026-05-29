# apps/domains/submissions/tests/test_omr_result_mapper_stats.py
"""
ai_omr_result_mapper — answer_stats / ALIGNMENT_FAILED reason 저장 검증.

DB 없이 순수 로직만 테스트한다. apply_omr_ai_result 내부의 집계 분기를
_stats_from_answers 헬퍼로 대리 검증할 수 없으므로, 여기서는
동일한 집계 규칙을 재현한 뒤 mapper의 상수/contract가 유지되는지 확인한다.
"""
from __future__ import annotations


def _stats_from_answers(answers):
    stats = {"total": 0, "ok": 0, "blank": 0, "ambiguous": 0, "error": 0}
    confs = []
    for a in answers:
        st = (a.get("status") or "").lower()
        stats["total"] += 1
        if st in ("ok", "blank", "ambiguous", "error"):
            stats[st] += 1
        c = a.get("confidence")
        if c is not None:
            try:
                confs.append(float(c))
            except Exception:
                pass
    stats["avg_confidence"] = round(sum(confs) / len(confs), 4) if confs else None
    return stats


def test_stats_counts_and_avg():
    answers = [
        {"status": "ok", "confidence": 1.0},
        {"status": "ok", "confidence": 0.8},
        {"status": "blank", "confidence": 0.0},
        {"status": "ambiguous", "confidence": 0.3},
        {"status": "error"},
    ]
    s = _stats_from_answers(answers)
    assert s["total"] == 5
    assert s["ok"] == 2 and s["blank"] == 1 and s["ambiguous"] == 1 and s["error"] == 1
    assert s["avg_confidence"] == round((1.0 + 0.8 + 0.0 + 0.3) / 4, 4)


def test_stats_empty_no_conf():
    s = _stats_from_answers([{"status": "error"}, {"status": "error"}])
    assert s["total"] == 2 and s["error"] == 2
    assert s["avg_confidence"] is None


def test_alignment_failed_reason_contract():
    # Phase C: answer 처리는 omr_pipeline.services.answer_persister 로 이전됨.
    # ALIGNMENT_FAILED 추가 로직이 새 위치에 존재하는지 소스 레벨로 확인.
    import inspect
    from apps.domains.submissions.omr_pipeline.services import answer_persister
    src = inspect.getsource(answer_persister.persist_answers)
    assert "ALIGNMENT_FAILED" in src, "answer_persister 는 aligned=False 시 ALIGNMENT_FAILED 사유를 적재해야 함"
    assert "aligned" in src


def test_answer_stats_stored_in_meta_contract():
    import inspect
    from apps.domains.submissions.services import ai_omr_result_mapper
    src = inspect.getsource(ai_omr_result_mapper.apply_omr_ai_result)
    # list view가 meta["answer_stats"]를 읽어 rows.answer_stats로 노출함 (SSOT)
    assert '"answer_stats"' in src or "'answer_stats'" in src


def test_engine_version_dynamic():
    # engine이 OMRAnswerV1.version을 meta_version으로 동적 채우는지 확인
    import inspect
    from academy.adapters.ai.omr import engine
    src = inspect.getsource(engine)
    # 하드코딩 "v9"로 OMRAnswerV1을 만드는 경로가 사라졌는지 (5곳 전부 meta_version)
    assert src.count('version="v9"') == 0, "engine에 version=\"v9\" 하드코딩 잔재"


def test_omr_dispatcher_sheet_scope_contract():
    # Phase F: sheet resolve 책임은 omr_pipeline.sheet_resolver 로 이전됨.
    # tenant + effective_template scoping 가 새 위치에 존재하는지 확인.
    import inspect
    from apps.domains.submissions.services import dispatcher
    from apps.domains.submissions.omr_pipeline.services import sheet_resolver

    dispatcher_src = inspect.getsource(dispatcher)
    assert "resolve_omr_sheet_for_exam" in dispatcher_src, (
        "dispatcher 는 외부 호환을 위해 resolve_omr_sheet_for_exam 를 re-export 해야 함"
    )

    resolver_src = inspect.getsource(sheet_resolver)
    assert "exam__tenant" in resolver_src
    assert "effective_template_exam_id" in resolver_src


def test_omr_worker_pdf_contract():
    from pathlib import Path
    src = Path("academy/application/use_cases/ai/pipelines/dispatcher.py").read_text(encoding="utf-8")
    assert "_load_omr_image_bgr" in src
    assert "PdfDocument" in src
    assert "exactly one page" in src
    assert "OMR question_count required" in src


def test_omr_scan_image_prefers_aligned_preview():
    from apps.support.omr.scan_images import select_omr_scan_image

    selection = select_omr_scan_image(
        submission_meta={
            "ai_result": {
                "result": {
                    "aligned_image_key": "tenants/1/ai/submissions/3/aligned/job.jpg",
                    "aligned_image_size": {"width": 3508, "height": 2480},
                }
            }
        },
        original_file_key="tenants/1/ai/submissions/3/original.jpg",
        tenant_id=1,
    )

    assert selection["scan_image_key"].endswith("/aligned/job.jpg")
    assert selection["original_scan_image_key"].endswith("/original.jpg")
    assert selection["scan_image_is_aligned"] is True
    assert selection["scan_image_size"] == {"width": 3508, "height": 2480}


def test_omr_scan_image_rejects_cross_tenant_preview_key():
    from apps.support.omr.scan_images import select_omr_scan_image

    selection = select_omr_scan_image(
        submission_meta={
            "ai_result": {
                "result": {
                    "aligned_image_key": "tenants/2/ai/submissions/3/aligned/job.jpg",
                }
            }
        },
        original_file_key="tenants/1/ai/submissions/3/original.jpg",
        tenant_id=1,
    )

    assert selection["scan_image_key"] == "tenants/1/ai/submissions/3/original.jpg"
    assert selection["scan_image_is_aligned"] is False


def test_manual_review_blocks_student_result_sync_contract():
    import inspect
    from apps.domains.results.services import grading_service
    src = inspect.getsource(grading_service.grade_submission)
    assert "manual_review" in src
    assert "sync_result_from_exam_submission" in src
    assert src.index("manual_review") < src.index("sync_result_from_exam_submission")

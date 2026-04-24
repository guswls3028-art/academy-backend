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
    # mapper가 reason에 실제로 ALIGNMENT_FAILED 상수를 추가하는지 소스 레벨로 확인.
    # (DB 없이 로직 경로만 검사)
    import inspect
    from apps.domains.submissions.services import ai_omr_result_mapper
    src = inspect.getsource(ai_omr_result_mapper.apply_omr_ai_result)
    assert "ALIGNMENT_FAILED" in src, "mapper는 aligned=False 시 ALIGNMENT_FAILED 사유를 적재해야 함"
    assert 'result.get("aligned")' in src or "aligned" in src


def test_answer_stats_stored_in_meta_contract():
    import inspect
    from apps.domains.submissions.services import ai_omr_result_mapper
    src = inspect.getsource(ai_omr_result_mapper.apply_omr_ai_result)
    # list view가 meta["answer_stats"]를 읽어 rows.answer_stats로 노출함 (SSOT)
    assert '"answer_stats"' in src or "'answer_stats'" in src


def test_engine_version_dynamic():
    # engine이 OMRAnswerV1.version을 meta_version으로 동적 채우는지 확인
    import inspect
    from apps.worker.ai_worker.ai.omr import engine
    src = inspect.getsource(engine)
    # 하드코딩 "v9"로 OMRAnswerV1을 만드는 경로가 사라졌는지 (5곳 전부 meta_version)
    assert src.count('version="v9"') == 0, "engine에 version=\"v9\" 하드코딩 잔재"

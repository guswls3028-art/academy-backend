# PATH: apps/domains/submissions/tests/test_cascade_discard_signal.py
"""
cascade_discard signal 단위 테스트.

Exam/Homework 삭제 시 active submission 이 자동 폐기되는지 검증.
mock 기반 — 실 DB 사용 안 함, ORM 호출만 stub.

검증 범위:
- DONE / SUPERSEDED 는 historical 보존 (cascade 제외)
- 활성 status (submitted, dispatched, ..., needs_identification, answers_ready, grading) 는 FAILED 로 전환
- 이미 FAILED 면 status 유지 + meta.discarded 만 보강
- meta.discarded 의 reason / at / by_user_id=None 정확히 기록
- meta.manual_review.required=False 강제
- error_message 가 'discarded:reason' 형식으로 기록
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.domains.submissions.models import Submission
from apps.domains.submissions.signals import _cascade_discard


def _stub_sub(status: str, meta: dict | None = None, sid: int = 1) -> MagicMock:
    """Submission 인스턴스 stub — save 시 update_fields 캡처."""
    s = MagicMock(spec=Submission)
    s.id = sid
    s.status = status
    s.meta = meta if meta is not None else {}
    s.error_message = ""
    s._saved_fields = []

    def _save(update_fields=None):
        s._saved_fields = list(update_fields or [])

    s.save.side_effect = _save
    return s


@pytest.mark.django_db
class TestCascadeDiscardSignal:
    """transaction.atomic 회피용 마커. 실 DB 미사용."""

    def _patch_qs(self, submissions):
        """Submission.objects.filter 가 submissions 리스트 반환."""
        qs = MagicMock()
        qs.__iter__ = MagicMock(return_value=iter(submissions))
        objects = MagicMock()
        objects.filter = MagicMock(return_value=qs)
        return patch.object(Submission, "objects", objects), objects

    def test_active_submissions_discarded(self):
        """submitted/dispatched/extracting/needs_identification/answers_ready/grading → FAILED + meta.discarded."""
        active_statuses = [
            Submission.Status.SUBMITTED,
            Submission.Status.DISPATCHED,
            Submission.Status.EXTRACTING,
            Submission.Status.NEEDS_IDENTIFICATION,
            Submission.Status.ANSWERS_READY,
            Submission.Status.GRADING,
        ]
        subs = [_stub_sub(status=s, sid=i + 1) for i, s in enumerate(active_statuses)]

        patcher, objects = self._patch_qs(subs)
        with patcher:
            count = _cascade_discard(
                target_type="exam",
                target_id=42,
                tenant_id=1,
                reason="cascade_exam_deleted",
            )

        assert count == len(active_statuses)
        # filter 호출이 active status 만 포함하는지 확인
        objects.filter.assert_called_once()
        kwargs = objects.filter.call_args.kwargs
        assert kwargs["target_type"] == "exam"
        assert kwargs["target_id"] == 42
        assert kwargs["tenant_id"] == 1
        # status__in 는 active 만 (DONE / SUPERSEDED 제외)
        assert Submission.Status.DONE not in kwargs["status__in"]
        assert Submission.Status.SUPERSEDED not in kwargs["status__in"]

        for s in subs:
            # 모두 FAILED 로 전환 + error_message 기록
            assert s.status == Submission.Status.FAILED
            assert s.error_message == "discarded:cascade_exam_deleted"
            # meta.discarded 검증
            assert s.meta["discarded"]["reason"] == "cascade_exam_deleted"
            assert s.meta["discarded"]["by_user_id"] is None
            assert "at" in s.meta["discarded"]
            # manual_review.required=False
            assert s.meta["manual_review"]["required"] is False
            # save update_fields 에 status / meta / error_message 포함
            assert set(s._saved_fields) >= {"status", "meta", "error_message", "updated_at"}

    def test_already_failed_keeps_status_but_records_discarded(self):
        """이미 FAILED 인 row 는 status 유지 + meta.discarded 만 보강."""
        sub = _stub_sub(status=Submission.Status.FAILED)
        original_error = sub.error_message
        patcher, _ = self._patch_qs([sub])
        with patcher:
            count = _cascade_discard(
                target_type="homework",
                target_id=7,
                tenant_id=2,
                reason="cascade_homework_deleted",
            )
        assert count == 1
        # status 변경 없음, error_message 도 그대로
        assert sub.status == Submission.Status.FAILED
        assert sub.error_message == original_error
        # meta.discarded 만 추가
        assert sub.meta["discarded"]["reason"] == "cascade_homework_deleted"
        # save update_fields 에 status 미포함
        assert "status" not in sub._saved_fields
        assert "meta" in sub._saved_fields

    def test_zero_active_returns_zero(self):
        """매칭되는 active submission 이 없으면 0 반환."""
        patcher, _ = self._patch_qs([])
        with patcher:
            count = _cascade_discard(
                target_type="exam",
                target_id=999,
                tenant_id=1,
                reason="cascade_exam_deleted",
            )
        assert count == 0

    def test_existing_meta_preserved(self):
        """기존 meta 키는 보존되고 discarded / manual_review 만 추가/덮어쓰기."""
        existing_meta = {
            "ai_result": {"score": 80},
            "manual_review": {"required": True, "reasons": ["blank"]},
            "custom": "keep_me",
        }
        sub = _stub_sub(status=Submission.Status.NEEDS_IDENTIFICATION, meta=existing_meta)
        patcher, _ = self._patch_qs([sub])
        with patcher:
            _cascade_discard(
                target_type="exam",
                target_id=10,
                tenant_id=1,
                reason="cascade_exam_deleted",
            )
        # 기존 키 보존
        assert sub.meta["ai_result"] == {"score": 80}
        assert sub.meta["custom"] == "keep_me"
        # manual_review.required 만 False 로 강제 (reasons 보존)
        assert sub.meta["manual_review"]["required"] is False
        assert sub.meta["manual_review"]["reasons"] == ["blank"]
        # discarded 추가
        assert sub.meta["discarded"]["reason"] == "cascade_exam_deleted"

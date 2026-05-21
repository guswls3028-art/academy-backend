# PATH: apps/domains/matchup/tests/test_page_states.py
#
# Phase A (2026-05-09) — page-level state (auto/skip/manual) 단위 테스트.
# basic_definition_2026_05_09 SSOT MVP 1단계 검증.
#
# 검증 포인트:
#   1. set_page_state 단일 upsert + meta.excluded_pages 자동 동기화
#   2. bulk_set_page_states 다중 upsert + 병합 동기화
#   3. get_page_states backward compat (legacy excluded_pages → state='skip')
#   4. auto_recommend_page_states paper_type_summary 기반 추천
#   5. state 값 검증 (auto/skip/manual 외 reject)
#   6. unique constraint (document, page_index)
#   7. page_index 범위 검증

import pytest
from django.contrib.auth import get_user_model

# 2026-05-12: 파일 단위 django_db mark. Phase A page-state 모델 INSERT/UPSERT DB 필요.
pytestmark = pytest.mark.django_db

from apps.core.models import Tenant
from apps.domains.inventory.models import InventoryFile, InventoryFolder
from apps.domains.matchup.models import MatchupDocument, MatchupPageState, MatchupProblem
from apps.domains.matchup.services import (
    PAGE_STATE_AUTO,
    PAGE_STATE_SKIP,
    PAGE_STATE_MANUAL,
    auto_recommend_page_states,
    bulk_set_page_states,
    get_page_states,
    set_page_state,
)


pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    return Tenant.objects.create(name="t-page", code="t-page")  # 2026-05-12: Tenant.subdomain 필드 없음. code unique=True 가 식별자.


@pytest.fixture
def actor(tenant):
    User = get_user_model()
    return User.objects.create(username="staff-page", tenant=tenant)


@pytest.fixture
def document(tenant):
    folder = InventoryFolder.objects.create(
        tenant=tenant, scope="admin", student_ps="", parent=None, name="root",
    )
    inv = InventoryFile.objects.create(
        tenant=tenant, folder=folder, scope="admin", student_ps="",
        original_name="t.pdf", r2_key="tenants/x/test.pdf",
        size_bytes=100, content_type="application/pdf",
    )
    return MatchupDocument.objects.create(
        tenant=tenant, inventory_file=inv, title="t",
        r2_key="tenants/x/test.pdf", original_name="t.pdf",
        size_bytes=100, content_type="application/pdf",
        status="done",
        meta={"page_count": 10, "excluded_pages": [3]},
    )


class TestSetPageState:
    def test_default_state_is_auto(self, document):
        states = get_page_states(document)
        assert len(states) == 10
        assert all(s["state"] == PAGE_STATE_AUTO for s in states if s["page_index"] != 3)

    def test_legacy_excluded_pages_inferred_as_skip(self, document):
        states = get_page_states(document)
        skip_state = next(s for s in states if s["page_index"] == 3)
        assert skip_state["state"] == PAGE_STATE_SKIP
        assert skip_state["source"] == "legacy_meta"

    def test_set_skip_syncs_excluded_pages(self, document, actor):
        result = set_page_state(document, 5, PAGE_STATE_SKIP, actor=actor)
        assert result["state"] == PAGE_STATE_SKIP
        document.refresh_from_db()
        assert 5 in document.meta["excluded_pages"]
        assert 3 in document.meta["excluded_pages"]

    def test_set_skip_removes_visible_unprotected_page_problems(self, document, actor):
        unprotected = MatchupProblem.objects.create(
            tenant=document.tenant,
            document=document,
            number=101,
            text="auto",
            meta={"page_index": 5},
        )
        manual = MatchupProblem.objects.create(
            tenant=document.tenant,
            document=document,
            number=102,
            text="manual",
            meta={"page_index": 5, "manual": True},
        )
        pinned = MatchupProblem.objects.create(
            tenant=document.tenant,
            document=document,
            number=103,
            text="pinned",
            meta={"page_index": 5, "manual_owner_pinned": True},
        )

        result = set_page_state(document, 5, PAGE_STATE_SKIP, actor=actor)

        assert result["removed_problems"] == 1
        assert result["preserved_manual"] == 1
        assert result["preserved_pinned"] == 1
        assert not MatchupProblem.objects.filter(pk=unprotected.pk).exists()
        assert MatchupProblem.objects.filter(pk=manual.pk).exists()
        assert MatchupProblem.objects.filter(pk=pinned.pk).exists()

    def test_set_auto_removes_from_excluded(self, document, actor):
        set_page_state(document, 3, PAGE_STATE_AUTO, actor=actor)
        document.refresh_from_db()
        assert 3 not in document.meta["excluded_pages"]

    def test_set_manual_does_not_add_to_excluded(self, document, actor):
        set_page_state(document, 4, PAGE_STATE_MANUAL, actor=actor)
        document.refresh_from_db()
        assert 4 not in document.meta["excluded_pages"]

    def test_invalid_state_raises(self, document):
        with pytest.raises(ValueError):
            set_page_state(document, 0, "bogus")

    def test_page_index_range_check(self, document):
        with pytest.raises(ValueError):
            set_page_state(document, -1, PAGE_STATE_AUTO)
        with pytest.raises(ValueError):
            set_page_state(document, 1000, PAGE_STATE_AUTO)

    def test_actor_clears_auto_reason(self, document, actor):
        # 시스템이 추천한 reason 이 있다가 학원장이 수동 변경하면 클리어
        set_page_state(document, 7, PAGE_STATE_SKIP, actor=None, auto_reason="paper_type_cover")
        ps = MatchupPageState.objects.get(document=document, page_index=7)
        assert ps.auto_reason == "paper_type_cover"
        assert ps.updated_by_id is None

        set_page_state(document, 7, PAGE_STATE_AUTO, actor=actor)
        ps.refresh_from_db()
        assert ps.auto_reason == ""
        assert ps.updated_by_id == actor.id


class TestBulkSetPageStates:
    def test_bulk_apply_multiple(self, document, actor):
        items = [
            {"page_index": 0, "state": PAGE_STATE_SKIP},
            {"page_index": 1, "state": PAGE_STATE_SKIP},
            {"page_index": 2, "state": PAGE_STATE_MANUAL},
        ]
        result = bulk_set_page_states(document, items, actor=actor)
        assert result["applied"] == [0, 1, 2]
        assert result["failed"] == []
        document.refresh_from_db()
        assert set(document.meta["excluded_pages"]) == {0, 1, 3}  # 기존 3 + 새 0,1

    def test_bulk_partial_failure(self, document):
        items = [
            {"page_index": 0, "state": PAGE_STATE_SKIP},
            {"page_index": "bogus", "state": PAGE_STATE_SKIP},
            {"page_index": 1, "state": "invalid"},
        ]
        result = bulk_set_page_states(document, items)
        assert result["applied"] == [0]
        assert len(result["failed"]) == 2

    def test_bulk_skip_then_unskip_overall_sync(self, document, actor):
        bulk_set_page_states(document, [
            {"page_index": 5, "state": PAGE_STATE_SKIP},
            {"page_index": 5, "state": PAGE_STATE_AUTO},  # 마지막 wins
        ], actor=actor)
        document.refresh_from_db()
        assert 5 not in document.meta["excluded_pages"]

    def test_bulk_skip_removes_visible_unprotected_page_problems(self, document, actor):
        unprotected = MatchupProblem.objects.create(
            tenant=document.tenant,
            document=document,
            number=201,
            text="auto",
            meta={"page_index": 6},
        )

        result = bulk_set_page_states(document, [{"page_index": 6, "state": PAGE_STATE_SKIP}], actor=actor)

        assert result["removed_problems"] == 1
        assert not MatchupProblem.objects.filter(pk=unprotected.pk).exists()


class TestAutoRecommendPageStates:
    def test_recommends_skip_for_cover_explanation_answer_key(self, document):
        document.meta = {
            **document.meta,
            "paper_type_summary": {
                "pages": [
                    {"page_index": 0, "paper_type": "cover"},
                    {"page_index": 1, "paper_type": "explanation"},
                    {"page_index": 2, "paper_type": "answer_key"},
                    {"page_index": 3, "paper_type": "problem"},  # auto
                ],
            },
        }
        document.save()
        recs = auto_recommend_page_states(document)
        skip_indexes = {r["page_index"] for r in recs}
        assert skip_indexes == {0, 1, 2}
        assert all(r["state"] == PAGE_STATE_SKIP for r in recs)

    def test_no_paper_type_summary(self, document):
        recs = auto_recommend_page_states(document)
        assert recs == []

    def test_malformed_paper_type_summary(self, document):
        document.meta = {**document.meta, "paper_type_summary": "garbage"}
        document.save()
        assert auto_recommend_page_states(document) == []


class TestUniqueConstraint:
    def test_idempotent_upsert(self, document, actor):
        set_page_state(document, 8, PAGE_STATE_SKIP, actor=actor)
        set_page_state(document, 8, PAGE_STATE_SKIP, actor=actor)
        # MatchupPageState row 가 1건만
        assert MatchupPageState.objects.filter(document=document, page_index=8).count() == 1

    def test_state_change_updates_existing(self, document, actor):
        set_page_state(document, 9, PAGE_STATE_SKIP, actor=actor)
        set_page_state(document, 9, PAGE_STATE_MANUAL, actor=actor)
        ps = MatchupPageState.objects.get(document=document, page_index=9)
        assert ps.state == PAGE_STATE_MANUAL

"""매치업 callback의 manual 보존 + skeleton row 삭제 회귀 락.

운영 사고 (2026-05-03): callbacks._handle_matchup_ai_result의
`doc.problems.exclude(meta__manual=True).delete()`가 PostgreSQL JSONB의
NULL semantics 때문에 manual 키가 없는 row(skeleton 등)를 삭제하지 못해
T2 1355 problems가 dead skeleton 상태로 영구 보존된 결함.

   SQL: NOT ((meta -> 'manual') = 'true')
   key 없는 row → meta -> 'manual' = NULL → NOT (NULL = 'true') = NULL → false
   → exclude 결과에서 빠짐 → delete 0건

Fix: ID 기반 명시 exclude로 NULL 우회.
   manual_ids = problems.filter(meta__manual=True).values_list("id", flat=True)
   problems.exclude(id__in=manual_ids).delete()

이 테스트는 fix 회귀 락. fail이면 운영 사고 재현 신호.
"""
from __future__ import annotations

import pytest
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.models import MatchupDocument, MatchupProblem


class MatchupCallbackManualExcludeTests(TestCase):
    """callbacks._handle_matchup_ai_result + services.retry_document 회귀 락.

    PostgreSQL JSONB의 NULL semantics 의존 — SQLite JSON1은 다르게 동작할 수 있음.
    fix 패턴(ID 기반 exclude)은 모든 backend에서 동일하게 동작하므로 SQLite에서도
    검증 가능.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(code="t-callback-manual", name="t-callback")
        # MatchupDocument는 inventory_file FK NOT NULL — fixture로 함께 생성
        self.inv = InventoryFile.objects.create(
            tenant=self.tenant,
            scope="admin",
            display_name="test_source.pdf",
            original_name="test_source.pdf",
            r2_key="tenants/x/matchup/test/source.pdf",
            size_bytes=0,
        )
        self.doc = MatchupDocument.objects.create(
            tenant=self.tenant,
            inventory_file=self.inv,
            title="test_doc",
            r2_key="tenants/x/matchup/test/source.pdf",
            original_name="source.pdf",
            status="processing",
        )

    def _create_problem(self, number: int, meta: dict) -> MatchupProblem:
        return MatchupProblem.objects.create(
            tenant=self.tenant,
            document=self.doc,
            number=number,
            text="",
            image_key="",
            embedding=None,
            image_embedding=None,
            meta=meta,
        )

    # ── ID 기반 fix 검증 (모든 backend) ──

    def test_id_based_exclude_deletes_skeleton_with_no_manual_key(self):
        """skeleton row(meta.manual 키 없음) 100% delete 검증.

        운영 사고 재현: meta__manual exclude는 PostgreSQL JSONB NULL semantics로
        skeleton row를 빠뜨림. ID 기반은 명시적 IN 비교라 NULL 영향 없음.
        """
        # skeleton 10 + manual 2 + 자동 결과 5 = 17
        for i in range(1, 11):
            self._create_problem(i, {"is_partial": True, "page_index": 0, "bbox": [0, 0, 100, 100]})
        for i in range(11, 13):
            self._create_problem(i, {"manual": True, "page_index": 0, "bbox": [0, 0, 100, 100]})
        for i in range(13, 18):
            self._create_problem(i, {"page_index": 0, "bbox": [0, 0, 100, 100], "format": "choice"})

        assert self.doc.problems.count() == 17

        # fix 적용: ID 기반 명시 exclude
        manual_ids = list(
            self.doc.problems.filter(meta__manual=True).values_list("id", flat=True)
        )
        assert len(manual_ids) == 2  # filter는 NULL safe — manual=True만 매칭
        deleted, _ = self.doc.problems.exclude(id__in=manual_ids).delete()
        assert deleted == 15  # skeleton 10 + 자동 결과 5 모두 삭제 ✓

        remaining = self.doc.problems.all()
        assert remaining.count() == 2
        assert all((p.meta or {}).get("manual") is True for p in remaining)

    def test_filter_meta_manual_true_is_null_safe(self):
        """filter(meta__manual=True)는 NULL safe — manual=True인 것만 매칭.

        skeleton(NULL key) + manual=False + manual=True 섞여있을 때 filter가 정확히
        manual=True인 row만 잡는지 확인. 이건 SQLite/PostgreSQL 모두 일관.
        """
        self._create_problem(1, {"is_partial": True})  # NULL key
        self._create_problem(2, {"manual": False})     # 명시적 False
        self._create_problem(3, {"manual": True})      # 명시적 True

        manual_qs = self.doc.problems.filter(meta__manual=True)
        assert manual_qs.count() == 1
        assert manual_qs.first().number == 3

    def test_callback_fix_handles_zero_manual_problems(self):
        """manual=True row 0건이어도 nominal delete + bulk_create 정상 작동."""
        for i in range(1, 4):
            self._create_problem(i, {"is_partial": True})

        manual_ids = list(
            self.doc.problems.filter(meta__manual=True).values_list("id", flat=True)
        )
        assert manual_ids == []
        deleted, _ = self.doc.problems.exclude(id__in=manual_ids).delete()
        assert deleted == 3
        assert self.doc.problems.count() == 0

    def test_callback_fix_handles_only_manual(self):
        """모든 row가 manual=True여도 ID exclude는 0건 delete (보존)."""
        for i in range(1, 4):
            self._create_problem(i, {"manual": True})

        manual_ids = list(
            self.doc.problems.filter(meta__manual=True).values_list("id", flat=True)
        )
        assert len(manual_ids) == 3
        deleted, _ = self.doc.problems.exclude(id__in=manual_ids).delete()
        assert deleted == 0
        assert self.doc.problems.count() == 3

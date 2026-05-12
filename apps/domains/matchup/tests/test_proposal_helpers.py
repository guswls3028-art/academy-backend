"""Stage 3 Phase 3.2 (2026-05-06): proposal_helpers 단위 테스트.

검증 항목 (사용자 directive):
- IoU 계산 정확도
- normalize_bbox 다양한 입력 형식 처리
- manual=true bbox 와 IoU > 0.3 → manual_overlap 검출
- 변환 불가 bbox → 보수적 manual_overlap=True (manual cut 보호 우선)
- create_proposal 가 manual=true row 를 읽기만 함 (변경 X)
- selected_problem_ids 미접근
- pending/rejected proposal 은 추천 풀 진입 X (Stage 0/4 blocklist)

DB 격리: 일부 테스트는 mock, 일부는 in-memory 격리.
"""
from __future__ import annotations

from django.test import TestCase  # 2026-05-12: unittest.TestCase → django.test.TestCase. CreateProposalContractTests 가 transaction.atomic 경유로 DB connection 필요.
from unittest.mock import MagicMock, patch

from apps.domains.matchup.proposal_helpers import (
    MANUAL_OVERLAP_IOU_THRESHOLD,
    iou_normalized,
    normalize_bbox,
    overlaps_existing_manual,
)


class IoUTests(TestCase):
    """iou_normalized 수학적 정확도."""

    def test_identical_boxes_iou_1(self):
        self.assertAlmostEqual(iou_normalized((0.1, 0.1, 0.2, 0.2), (0.1, 0.1, 0.2, 0.2)), 1.0)

    def test_disjoint_boxes_iou_0(self):
        self.assertEqual(iou_normalized((0.0, 0.0, 0.1, 0.1), (0.5, 0.5, 0.1, 0.1)), 0.0)

    def test_half_overlap(self):
        # 두 1x1 박스, 면적 1, 교집합 0.5x1 = 0.5, 합집합 1+1-0.5 = 1.5 → IoU = 1/3
        a = (0.0, 0.0, 1.0, 1.0)
        b = (0.5, 0.0, 1.0, 1.0)
        self.assertAlmostEqual(iou_normalized(a, b), 1 / 3, places=5)

    def test_zero_area_returns_0(self):
        self.assertEqual(iou_normalized((0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.1, 0.1)), 0.0)

    def test_negative_dim_returns_0(self):
        self.assertEqual(iou_normalized((0.0, 0.0, -0.1, 0.1), (0.0, 0.0, 0.1, 0.1)), 0.0)

    def test_above_threshold_with_default(self):
        # 두 1x1, 0.4 만큼 겹침 → 교집합 0.6, 합집합 1+1-0.6 = 1.4 → IoU ≈ 0.428
        iou = iou_normalized((0.0, 0.0, 1.0, 1.0), (0.4, 0.0, 1.0, 1.0))
        self.assertGreater(iou, MANUAL_OVERLAP_IOU_THRESHOLD)


class NormalizeBboxTests(TestCase):
    """normalize_bbox 다양한 입력 형식 처리."""

    def test_dict_norm_true_passthrough(self):
        result = normalize_bbox({"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4, "norm": True})
        self.assertEqual(result, (0.1, 0.2, 0.3, 0.4))

    def test_dict_norm_false_with_page_dim(self):
        # px → 100x200 페이지에서 50/100=0.5, 100/200=0.5
        result = normalize_bbox(
            {"x": 50, "y": 100, "w": 25, "h": 50, "norm": False},
            page_width=100, page_height=200,
        )
        self.assertEqual(result, (0.5, 0.5, 0.25, 0.25))

    def test_dict_norm_false_without_page_dim_returns_none(self):
        result = normalize_bbox({"x": 50, "y": 100, "w": 25, "h": 50, "norm": False})
        self.assertIsNone(result)

    def test_list_normalized_values_recognized(self):
        # 모두 0~1.5 범위 → norm 추정
        result = normalize_bbox([0.1, 0.2, 0.3, 0.4])
        self.assertEqual(result, (0.1, 0.2, 0.3, 0.4))

    def test_list_px_values_with_page_dim(self):
        # 1500 같은 큰 값 = px → page_dim 필요
        result = normalize_bbox([100, 200, 50, 100], page_width=1000, page_height=2000)
        self.assertEqual(result, (0.1, 0.1, 0.05, 0.05))

    def test_list_px_values_without_page_dim_returns_none(self):
        result = normalize_bbox([100, 200, 50, 100])
        self.assertIsNone(result)

    def test_invalid_input_returns_none(self):
        for invalid in (None, {}, [], "abc", [1, 2, 3]):
            self.assertIsNone(normalize_bbox(invalid))

    def test_real_manual_bbox_norm_format(self):
        """운영 manual cut 실측 형식: list of 4 floats 0~1."""
        result = normalize_bbox([0.08652890354290764, 0.5774152683162767, 0.44088388078597207, 0.37749085494089574])
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result[0], 0.08652890354290764)


class OverlapsExistingManualTests(TestCase):
    """overlaps_existing_manual: manual=true bbox 와 IoU 검사.

    DB 미접속 mock 으로 manager 흉내. read-only 동작 검증.
    """

    def _make_problem(self, problem_id, bbox_norm=None, bbox=None):
        p = MagicMock()
        p.id = problem_id
        meta = {"manual": True}
        if bbox_norm is not None:
            meta["bbox_norm"] = bbox_norm
        if bbox is not None:
            meta["bbox"] = bbox
        p.meta = meta
        return p

    def _patch_doc_and_manuals(self, doc_meta=None, manuals=None):
        """document.objects.only().get() + problems filter 둘 다 mock."""
        from apps.domains.matchup.models import MatchupDocument, MatchupProblem

        doc_mock = MagicMock()
        doc_mock.id = 100
        doc_mock.meta = doc_meta or {}

        doc_only = MagicMock()
        doc_only.get = MagicMock(return_value=doc_mock)
        doc_objects = MagicMock()
        doc_objects.only = MagicMock(return_value=doc_only)

        manual_qs = MagicMock()
        manual_qs.__iter__ = MagicMock(return_value=iter(manuals or []))
        manual_qs.only = MagicMock(return_value=manual_qs)

        problem_objects = MagicMock()
        problem_filter = MagicMock(return_value=manual_qs)
        problem_objects.filter = problem_filter

        return (
            patch.object(MatchupDocument, "objects", doc_objects),
            patch.object(MatchupProblem, "objects", problem_objects),
        )

    def test_no_manuals_returns_no_overlap(self):
        p1, p2 = self._patch_doc_and_manuals(manuals=[])
        with p1, p2:
            overlaps, iou, conflict = overlaps_existing_manual(
                document_id=100,
                candidate_bbox=[0.1, 0.1, 0.2, 0.2],
                page_number=1,
            )
            self.assertFalse(overlaps)
            self.assertEqual(iou, 0.0)
            self.assertIsNone(conflict)

    def test_high_overlap_detected(self):
        manual = self._make_problem(
            42, bbox_norm=[0.1, 0.1, 0.2, 0.2],
        )
        p1, p2 = self._patch_doc_and_manuals(manuals=[manual])
        with p1, p2:
            overlaps, iou, conflict = overlaps_existing_manual(
                document_id=100,
                candidate_bbox=[0.1, 0.1, 0.2, 0.2],  # 완전 동일
                page_number=1,
            )
            self.assertTrue(overlaps)
            self.assertAlmostEqual(iou, 1.0)
            self.assertEqual(conflict, 42)

    def test_low_overlap_below_threshold(self):
        manual = self._make_problem(
            42, bbox_norm=[0.0, 0.0, 0.1, 0.1],
        )
        p1, p2 = self._patch_doc_and_manuals(manuals=[manual])
        with p1, p2:
            overlaps, iou, conflict = overlaps_existing_manual(
                document_id=100,
                candidate_bbox=[0.5, 0.5, 0.1, 0.1],  # 다른 위치
                page_number=1,
            )
            self.assertFalse(overlaps)
            self.assertEqual(iou, 0.0)

    def test_unconvertible_bbox_returns_conservative_overlap_true(self):
        """변환 실패 시 보수적으로 manual_overlap=True (사용자 directive)."""
        p1, p2 = self._patch_doc_and_manuals(manuals=[])
        with p1, p2:
            overlaps, iou, conflict = overlaps_existing_manual(
                document_id=100,
                candidate_bbox=None,
                page_number=1,
            )
            self.assertTrue(overlaps)
            self.assertEqual(iou, -1.0)

    def test_picks_max_iou_among_multiple_manuals(self):
        """여러 manual 중 max IoU 가 threshold 초과면 detect."""
        m_low = self._make_problem(10, bbox_norm=[0.0, 0.0, 0.05, 0.05])
        m_high = self._make_problem(20, bbox_norm=[0.5, 0.5, 0.2, 0.2])
        p1, p2 = self._patch_doc_and_manuals(manuals=[m_low, m_high])
        with p1, p2:
            overlaps, iou, conflict = overlaps_existing_manual(
                document_id=100,
                candidate_bbox=[0.55, 0.55, 0.2, 0.2],  # m_high 와 큰 겹침
                page_number=1,
            )
            self.assertTrue(overlaps)
            self.assertEqual(conflict, 20)

    def test_manual_with_no_bbox_skipped(self):
        """bbox/bbox_norm 둘 다 없는 manual problem 은 skip."""
        empty = self._make_problem(99)  # bbox 없음
        p1, p2 = self._patch_doc_and_manuals(manuals=[empty])
        with p1, p2:
            overlaps, iou, conflict = overlaps_existing_manual(
                document_id=100,
                candidate_bbox=[0.1, 0.1, 0.2, 0.2],
                page_number=1,
            )
            self.assertFalse(overlaps)


class CreateProposalContractTests(TestCase):
    """create_proposal 동작 계약 — DB 미접속 mock.

    검증:
    - manual_overlap 검출되면 status='rejected' + validation_errors 에 manual_overlap 기록
    - manual_overlap 없으면 auto_status 그대로 (default 'pending')
    - bbox dict 그대로 / list 는 dict 형식으로 정규화 후 저장
    - create_proposal 호출이 selected_problem_ids 같은 컬럼 미접근 (signature 분리)
    """

    def _patch_create(self):
        """ProblemSegmentationProposal.objects.create mock — 호출 인자 포착."""
        from apps.domains.matchup.models import ProblemSegmentationProposal

        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            mock_proposal = MagicMock(**kwargs)
            return mock_proposal

        objects = MagicMock()
        objects.create = MagicMock(side_effect=fake_create)
        return patch.object(ProblemSegmentationProposal, "objects", objects), captured

    def _patch_overlaps(self, returns):
        """overlaps_existing_manual 결과 강제 — proposal_helpers 모듈 안의 reference patch."""
        return patch(
            "apps.domains.matchup.proposal_helpers.overlaps_existing_manual",
            return_value=returns,
        )

    def test_no_overlap_keeps_pending_status(self):
        from apps.domains.matchup.proposal_helpers import create_proposal

        patch_create, captured = self._patch_create()
        patch_ov = self._patch_overlaps((False, 0.0, None))
        with patch_create, patch_ov:
            create_proposal(
                tenant_id=2, document_id=100, page_number=1,
                detected_problem_number=5,
                bbox=[0.1, 0.1, 0.2, 0.2],
                engine="yolo",
            )
        self.assertEqual(captured["status"], "pending")
        self.assertEqual(captured["validation_errors"], [])

    def test_overlap_overrides_to_rejected(self):
        from apps.domains.matchup.proposal_helpers import create_proposal

        patch_create, captured = self._patch_create()
        patch_ov = self._patch_overlaps((True, 0.55, 42))
        with patch_create, patch_ov:
            create_proposal(
                tenant_id=2, document_id=100, page_number=1,
                detected_problem_number=5,
                bbox=[0.1, 0.1, 0.2, 0.2],
                engine="vlm",
                auto_status="auto_passed",  # 호출자가 auto_passed 줘도 overlap 이면 reject
            )
        self.assertEqual(captured["status"], "rejected")
        self.assertEqual(len(captured["validation_errors"]), 1)
        err = captured["validation_errors"][0]
        self.assertEqual(err["code"], "manual_overlap")
        self.assertEqual(err["bbox_iou"], 0.55)
        self.assertEqual(err["conflicting_problem_id"], 42)
        self.assertEqual(err["threshold"], MANUAL_OVERLAP_IOU_THRESHOLD)

    def test_list_bbox_normalized_to_dict_for_storage(self):
        from apps.domains.matchup.proposal_helpers import create_proposal

        patch_create, captured = self._patch_create()
        patch_ov = self._patch_overlaps((False, 0.0, None))
        with patch_create, patch_ov:
            create_proposal(
                tenant_id=2, document_id=100, page_number=1,
                detected_problem_number=5,
                bbox=[0.1, 0.2, 0.3, 0.4],  # list 입력
                engine="yolo",
            )
        bbox_saved = captured["bbox"]
        self.assertIsInstance(bbox_saved, dict)
        self.assertEqual(bbox_saved["x"], 0.1)
        self.assertEqual(bbox_saved["y"], 0.2)
        self.assertEqual(bbox_saved["w"], 0.3)
        self.assertEqual(bbox_saved["h"], 0.4)
        self.assertTrue(bbox_saved["norm"])  # 0~1.5 범위 → norm 추정

    def test_dict_bbox_passed_through(self):
        from apps.domains.matchup.proposal_helpers import create_proposal

        patch_create, captured = self._patch_create()
        patch_ov = self._patch_overlaps((False, 0.0, None))
        with patch_create, patch_ov:
            create_proposal(
                tenant_id=2, document_id=100, page_number=2,
                detected_problem_number=5,
                bbox={"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4, "norm": True},
                engine="vlm",
            )
        self.assertEqual(captured["bbox"]["x"], 0.1)
        self.assertTrue(captured["bbox"]["norm"])

    def test_create_proposal_does_not_touch_selected_problem_ids(self):
        """create_proposal signature 에 selected_problem_ids 인자 없음."""
        import inspect
        from apps.domains.matchup.proposal_helpers import create_proposal
        sig = inspect.signature(create_proposal)
        for param_name in sig.parameters:
            self.assertNotIn("selected_problem_ids", param_name.lower())
            self.assertNotIn("hit_report", param_name.lower())
            self.assertNotIn("entry", param_name.lower())

    def test_create_proposal_does_not_modify_matchup_problem_table(self):
        """create_proposal 호출이 MatchupProblem.objects.update/.delete/.create 호출 안 함.

        오로지 ProblemSegmentationProposal.objects.create 만.
        """
        from apps.domains.matchup.models import MatchupProblem
        from apps.domains.matchup.proposal_helpers import create_proposal

        patch_create, _ = self._patch_create()
        patch_ov = self._patch_overlaps((False, 0.0, None))
        problem_objects_mock = MagicMock()
        with patch.object(MatchupProblem, "objects", problem_objects_mock):
            with patch_create, patch_ov:
                create_proposal(
                    tenant_id=2, document_id=100, page_number=1,
                    detected_problem_number=5,
                    bbox=[0.1, 0.1, 0.2, 0.2],
                    engine="yolo",
                )
        # MatchupProblem.objects 의 update/delete/create 어떤 것도 호출 X
        problem_objects_mock.update.assert_not_called()
        problem_objects_mock.bulk_update.assert_not_called()
        problem_objects_mock.bulk_create.assert_not_called()
        problem_objects_mock.create.assert_not_called()
        # filter 는 overlaps_existing_manual 안에서 호출되지만 그건 patched 됨 — 여기 mock 은 안 거침.

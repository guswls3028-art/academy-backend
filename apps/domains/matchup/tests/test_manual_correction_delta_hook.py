"""Stage 6.5 (2026-05-08) — manual_crop hook → ManualCorrectionDelta 자동 캡처.

검증:
- `_bbox_iou_dict()` IoU 계산 정확성 (정상 / 누락 / 형식 불량 / union==0)
- `_record_manual_correction_delta()`:
    * AI proposal 없을 때 (manual_only path) — engine="manual_crop", original_bbox=None
    * AI proposal 있을 때 — original_bbox 기록 + iou 계산 + engine 매칭
    * 같은 number 재cut → correction_type=bbox_adjust
    * 신규 → correction_type=manual_create
    * paper_type_at_action — doc.meta.paper_type_summary.primary 추출
    * actor=request.user → created_by 기록 (인증 ON 만)
    * actor=AnonymousUser → created_by=None
    * tenant FK 자동 채움
- manual_crop 본 흐름과의 격리 — hook 실패 시 `manually_crop_problem` 안의 try/except 가 흡수

ORM 미접속 mock + minimal helper 검증.
"""
from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.domains.matchup.services import (
    _bbox_iou_dict,
    _record_manual_correction_delta,
)


# ── _bbox_iou_dict ────────────────────────────────────────────────


class BboxIouDictTests(TestCase):
    def test_identical_bbox_iou_1(self):
        a = {"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.3}
        self.assertEqual(_bbox_iou_dict(a, a), 1.0)

    def test_no_overlap_iou_0(self):
        a = {"x": 0.0, "y": 0.0, "w": 0.2, "h": 0.2}
        b = {"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2}
        self.assertEqual(_bbox_iou_dict(a, b), 0.0)

    def test_partial_overlap(self):
        # 50% width / 100% height overlap → IoU = 0.5*0.2 / (1.0*0.2 + 0.5*0.2 - 0.5*0.2)
        a = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.2}
        b = {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.2}
        # inter = 0.5 * 0.2 = 0.1; union = 0.2 + 0.1 - 0.1 = 0.2; IoU = 0.5
        self.assertEqual(_bbox_iou_dict(a, b), 0.5)

    def test_partial_overlap_typical_manual_cut_correction(self):
        # 학원장이 AI bbox 와 약간 다르게 잡은 케이스 (학교 시험지 typology Group 1)
        ai = {"x": 0.10, "y": 0.20, "w": 0.77, "h": 0.34}
        manual = {"x": 0.11, "y": 0.21, "w": 0.76, "h": 0.33}
        iou = _bbox_iou_dict(ai, manual)
        # 거의 일치 — IoU > 0.9 기대
        self.assertIsNotNone(iou)
        self.assertGreater(iou, 0.9)

    def test_none_input_returns_none(self):
        self.assertIsNone(_bbox_iou_dict(None, {"x": 0, "y": 0, "w": 0.1, "h": 0.1}))
        self.assertIsNone(_bbox_iou_dict({"x": 0, "y": 0, "w": 0.1, "h": 0.1}, None))

    def test_non_dict_returns_none(self):
        self.assertIsNone(_bbox_iou_dict([0, 0, 0.1, 0.1], [0, 0, 0.1, 0.1]))

    def test_zero_area_returns_none(self):
        a = {"x": 0, "y": 0, "w": 0, "h": 0.1}
        b = {"x": 0, "y": 0, "w": 0.1, "h": 0.1}
        self.assertIsNone(_bbox_iou_dict(a, b))

    def test_malformed_keys_returns_none(self):
        a = {"x": "bad", "y": 0, "w": 0.1, "h": 0.1}
        b = {"x": 0, "y": 0, "w": 0.1, "h": 0.1}
        self.assertIsNone(_bbox_iou_dict(a, b))


# ── _record_manual_correction_delta — mocked ORM ───────────────────


class _FakeProblem:
    def __init__(self, *, id=1, tenant_id=2, number=3):
        self.id = id
        self.tenant_id = tenant_id
        self.number = number


class _FakeDocument:
    def __init__(self, *, id=765, tenant_id=2, paper_type_primary="clean_pdf_dual"):
        self.id = id
        self.tenant_id = tenant_id
        if paper_type_primary is None:
            self.meta = None
        else:
            self.meta = {"paper_type_summary": {"primary": paper_type_primary}}


class _FakeUser:
    def __init__(self, *, id=42, is_authenticated=True):
        self.id = id
        self.is_authenticated = is_authenticated


class RecordManualCorrectionDeltaTests(TestCase):
    """ORM 호출은 mock — `objects.create` / `objects.filter().exclude().order_by().first()`."""

    def _patch_proposal_lookup(self, return_value):
        """ProblemSegmentationProposal lookup chain mock."""
        first_mock = MagicMock(return_value=return_value)
        order_mock = MagicMock()
        order_mock.first = first_mock
        exclude_mock = MagicMock(return_value=order_mock)
        order_chain = MagicMock()
        order_chain.exclude = MagicMock(return_value=exclude_mock)
        filter_mock = MagicMock(return_value=order_chain)
        # filter().exclude().order_by().first()
        exclude_mock.order_by = MagicMock(return_value=order_mock)
        objects_mock = MagicMock()
        objects_mock.filter = filter_mock
        return objects_mock

    def _capture_delta_create(self):
        """ManualCorrectionDelta.objects.create kwargs 캡처."""
        captured = {}
        def _create(**kwargs):
            captured.update(kwargs)
            return MagicMock()
        return captured, _create

    def test_manual_only_path_no_ai_proposal(self):
        """AI proposal 없으면 original_bbox=None, iou=None, engine='manual_crop'."""
        from apps.domains.matchup.models import (
            ProblemSegmentationProposal, ManualCorrectionDelta,
        )
        captured, fake_create = self._capture_delta_create()

        with patch.object(ProblemSegmentationProposal, "objects",
                          self._patch_proposal_lookup(return_value=None)), \
             patch.object(ManualCorrectionDelta.objects, "create", side_effect=fake_create):
            _record_manual_correction_delta(
                _FakeProblem(),
                _FakeDocument(),
                page_index=2, bbox_norm=(0.1, 0.2, 0.4, 0.3),
                is_recreate=False,
            )

        self.assertEqual(captured["correction_type"], "manual_create")
        self.assertEqual(captured["source"], "user_ui")
        self.assertIsNone(captured["original_bbox"])
        self.assertIsNone(captured["iou_with_ai"])
        self.assertEqual(captured["engine_at_action"], "manual_crop")
        self.assertEqual(captured["paper_type_at_action"], "clean_pdf_dual")
        self.assertEqual(captured["tenant_id"], 2)
        self.assertIsNone(captured["proposal"])
        self.assertEqual(captured["corrected_bbox"]["x"], 0.1)
        self.assertEqual(captured["corrected_bbox"]["page"], 2)
        self.assertTrue(captured["corrected_bbox"]["norm"])
        self.assertIsNone(captured["created_by"])

    def test_ai_proposal_match_records_iou(self):
        """AI proposal 있으면 original_bbox + iou + engine 기록."""
        from apps.domains.matchup.models import (
            ProblemSegmentationProposal, ManualCorrectionDelta,
        )
        ai_proposal = MagicMock()
        ai_proposal.id = 999
        ai_proposal.bbox = {"x": 0.10, "y": 0.20, "w": 0.40, "h": 0.30}
        ai_proposal.engine = "yolo"

        captured, fake_create = self._capture_delta_create()
        with patch.object(ProblemSegmentationProposal, "objects",
                          self._patch_proposal_lookup(return_value=ai_proposal)), \
             patch.object(ManualCorrectionDelta.objects, "create", side_effect=fake_create):
            _record_manual_correction_delta(
                _FakeProblem(),
                _FakeDocument(),
                page_index=2, bbox_norm=(0.10, 0.20, 0.40, 0.30),  # AI 와 동일
                is_recreate=False,
            )

        self.assertEqual(captured["proposal"], ai_proposal)
        self.assertEqual(captured["original_bbox"]["x"], 0.10)
        self.assertEqual(captured["iou_with_ai"], 1.0)
        self.assertEqual(captured["engine_at_action"], "yolo")

    def test_recreate_correction_type_bbox_adjust(self):
        from apps.domains.matchup.models import (
            ProblemSegmentationProposal, ManualCorrectionDelta,
        )
        captured, fake_create = self._capture_delta_create()
        with patch.object(ProblemSegmentationProposal, "objects",
                          self._patch_proposal_lookup(return_value=None)), \
             patch.object(ManualCorrectionDelta.objects, "create", side_effect=fake_create):
            _record_manual_correction_delta(
                _FakeProblem(),
                _FakeDocument(),
                page_index=0, bbox_norm=(0.0, 0.0, 0.5, 0.5),
                is_recreate=True,
            )
        self.assertEqual(captured["correction_type"], "bbox_adjust")

    def test_paper_type_missing_empty(self):
        """doc.meta.paper_type_summary 누락 시 paper_type_at_action 빈 문자열."""
        from apps.domains.matchup.models import (
            ProblemSegmentationProposal, ManualCorrectionDelta,
        )
        captured, fake_create = self._capture_delta_create()
        doc = _FakeDocument(paper_type_primary=None)  # meta=None
        with patch.object(ProblemSegmentationProposal, "objects",
                          self._patch_proposal_lookup(return_value=None)), \
             patch.object(ManualCorrectionDelta.objects, "create", side_effect=fake_create):
            _record_manual_correction_delta(
                _FakeProblem(),
                doc,
                page_index=0, bbox_norm=(0.0, 0.0, 0.1, 0.1),
                is_recreate=False,
            )
        self.assertEqual(captured["paper_type_at_action"], "")

    def test_authenticated_actor_recorded(self):
        from apps.domains.matchup.models import (
            ProblemSegmentationProposal, ManualCorrectionDelta,
        )
        user = _FakeUser(id=42, is_authenticated=True)
        captured, fake_create = self._capture_delta_create()
        with patch.object(ProblemSegmentationProposal, "objects",
                          self._patch_proposal_lookup(return_value=None)), \
             patch.object(ManualCorrectionDelta.objects, "create", side_effect=fake_create):
            _record_manual_correction_delta(
                _FakeProblem(),
                _FakeDocument(),
                page_index=0, bbox_norm=(0, 0, 0.1, 0.1),
                is_recreate=False,
                actor=user,
            )
        self.assertEqual(captured["created_by"], user)

    def test_unauthenticated_actor_not_recorded(self):
        from apps.domains.matchup.models import (
            ProblemSegmentationProposal, ManualCorrectionDelta,
        )
        anon = _FakeUser(id=None, is_authenticated=False)
        captured, fake_create = self._capture_delta_create()
        with patch.object(ProblemSegmentationProposal, "objects",
                          self._patch_proposal_lookup(return_value=None)), \
             patch.object(ManualCorrectionDelta.objects, "create", side_effect=fake_create):
            _record_manual_correction_delta(
                _FakeProblem(),
                _FakeDocument(),
                page_index=0, bbox_norm=(0, 0, 0.1, 0.1),
                is_recreate=False,
                actor=anon,
            )
        self.assertIsNone(captured["created_by"])

    def test_actor_none_means_no_user(self):
        from apps.domains.matchup.models import (
            ProblemSegmentationProposal, ManualCorrectionDelta,
        )
        captured, fake_create = self._capture_delta_create()
        with patch.object(ProblemSegmentationProposal, "objects",
                          self._patch_proposal_lookup(return_value=None)), \
             patch.object(ManualCorrectionDelta.objects, "create", side_effect=fake_create):
            _record_manual_correction_delta(
                _FakeProblem(),
                _FakeDocument(),
                page_index=0, bbox_norm=(0, 0, 0.1, 0.1),
                is_recreate=False,
                actor=None,
            )
        self.assertIsNone(captured["created_by"])

    def test_corrected_bbox_normalized_dict(self):
        """corrected_bbox 항상 dict 형식 + page + norm 필드."""
        from apps.domains.matchup.models import (
            ProblemSegmentationProposal, ManualCorrectionDelta,
        )
        captured, fake_create = self._capture_delta_create()
        with patch.object(ProblemSegmentationProposal, "objects",
                          self._patch_proposal_lookup(return_value=None)), \
             patch.object(ManualCorrectionDelta.objects, "create", side_effect=fake_create):
            _record_manual_correction_delta(
                _FakeProblem(),
                _FakeDocument(),
                page_index=5, bbox_norm=(0.123, 0.456, 0.789, 0.234),
                is_recreate=False,
            )
        cb = captured["corrected_bbox"]
        self.assertEqual(cb, {
            "x": 0.123, "y": 0.456, "w": 0.789, "h": 0.234,
            "page": 5, "norm": True,
        })


# ── 정적 import / 안전성 회귀 ─────────────────────────────────────


def _strip_function_docstring(src: str) -> str:
    """함수 source 의 docstring 부분만 제거 — body 만 남김. ast 기반."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                # 첫 ast.Expr(Constant) 가 docstring — 그 부분만 제거
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body = node.body[1:]
            return ast.unparse(node)
    return src


class HookSafetyRegressionTests(TestCase):
    def test_record_helper_does_not_import_callbacks(self):
        """selected_problem_ids / hit_report / callback path import 0회.

        docstring 은 검사 대상에서 제외 — 정책 설명에 언급한 단어 자체는 OK.
        """
        import inspect
        src = inspect.getsource(_record_manual_correction_delta)
        body = _strip_function_docstring(src)
        # body 안에서 실제 import / call 패턴만 차단
        for forbidden in (
            "from apps.domains.ai.callbacks", "from apps.domains.ai.gateway",
            "_handle_matchup_ai_result", "_handle_matchup_index_result",
            "_handle_matchup_manual_result", "dispatch_job(",
            ".selected_problem_ids", "selected_problem_ids =",
            "MatchupHitReport.objects", "MatchupHitReportEntry.objects",
        ):
            self.assertNotIn(
                forbidden, body,
                f"_record_manual_correction_delta body 에서 forbidden token "
                f"'{forbidden}' 발견 (docstring 제외)",
            )

    def test_manually_crop_problem_signature_actor_optional(self):
        """signature 회귀 — actor 는 keyword-only optional, default None."""
        from apps.domains.matchup.services import manually_crop_problem
        import inspect
        sig = inspect.signature(manually_crop_problem)
        self.assertIn("actor", sig.parameters)
        actor_param = sig.parameters["actor"]
        self.assertEqual(actor_param.default, None)
        # KEYWORD_ONLY 또는 POSITIONAL_OR_KEYWORD — 둘 다 backward compat
        self.assertIn(
            actor_param.kind,
            (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD),
        )

    def test_record_helper_inserts_one_row_only(self):
        """동일 호출에서 ManualCorrectionDelta.objects.create 1회만 호출."""
        from apps.domains.matchup.models import (
            ProblemSegmentationProposal, ManualCorrectionDelta,
        )
        call_count = {"n": 0}
        def _track(**kwargs):
            call_count["n"] += 1
            return MagicMock()
        first_mock = MagicMock(return_value=None)
        order_mock = MagicMock()
        order_mock.first = first_mock
        exclude_mock = MagicMock(return_value=order_mock)
        exclude_mock.order_by = MagicMock(return_value=order_mock)
        filter_mock = MagicMock()
        filter_mock.exclude = MagicMock(return_value=exclude_mock)
        objects_mock = MagicMock()
        objects_mock.filter = MagicMock(return_value=filter_mock)
        with patch.object(ProblemSegmentationProposal, "objects", objects_mock), \
             patch.object(ManualCorrectionDelta.objects, "create", side_effect=_track):
            _record_manual_correction_delta(
                _FakeProblem(),
                _FakeDocument(),
                page_index=0, bbox_norm=(0, 0, 0.1, 0.1),
                is_recreate=False,
            )
        self.assertEqual(call_count["n"], 1)

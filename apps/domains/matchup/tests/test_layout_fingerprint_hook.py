"""Stage 6.6 V1 (2026-05-08) — manual_crop hook → LayoutFingerprint 자동 upsert.

검증:
- `_column_count_from_paper_type()` 매핑 (clean_pdf_dual=2, scan_dual=2,
  quadrant=4, 그 외=1, 빈 문자열=1)
- `_record_layout_fingerprint()`:
    * tenant FK 자동 채움 (cross-tenant 매칭 영구 금지 정책 보존)
    * paper_type 추출 (doc.meta.paper_type_summary.primary)
    * column_count 자동 추론
    * page_count / page_size 정확 저장
    * V2 enrichment 자리 default (text_density=0, x0_clusters=[], ...)
    * fingerprint_version=1 고정
    * update_or_create — same doc 재호출 시 idempotent (paper_type 갱신)
- `manually_crop_problem` signature 회귀 — actor / hook 호출부 보존
- selected_problem_ids / hit_report / callback / R2 미접근 (정적 검사)

ORM mock 기반 — DB 무관.
"""
from __future__ import annotations

import ast
import inspect
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.domains.matchup.services import (
    _column_count_from_paper_type,
    _record_layout_fingerprint,
)


# ── _column_count_from_paper_type ─────────────────────────────────


class ColumnCountFromPaperTypeTests(TestCase):
    def test_clean_pdf_dual_returns_2(self):
        self.assertEqual(_column_count_from_paper_type("clean_pdf_dual"), 2)

    def test_scan_dual_returns_2(self):
        self.assertEqual(_column_count_from_paper_type("scan_dual"), 2)

    def test_quadrant_returns_4(self):
        self.assertEqual(_column_count_from_paper_type("quadrant"), 4)

    def test_clean_pdf_single_returns_1(self):
        self.assertEqual(_column_count_from_paper_type("clean_pdf_single"), 1)

    def test_scan_single_returns_1(self):
        self.assertEqual(_column_count_from_paper_type("scan_single"), 1)

    def test_other_paper_types_default_1(self):
        for v in ("student_answer_photo", "side_notes", "non_question",
                  "unknown", "school_exam_pdf", "academy_workbook"):
            self.assertEqual(
                _column_count_from_paper_type(v), 1,
                f"{v} expected default 1",
            )

    def test_empty_string_returns_1(self):
        self.assertEqual(_column_count_from_paper_type(""), 1)

    def test_none_safe_default(self):
        # None 도 default 1 으로 안전 처리 — comparison fails to all branches
        self.assertEqual(_column_count_from_paper_type(None), 1)


# ── _record_layout_fingerprint ────────────────────────────────────


class _FakeDocument:
    def __init__(self, *, id=765, tenant_id=2, paper_type_primary="clean_pdf_dual"):
        self.id = id
        self.tenant_id = tenant_id
        if paper_type_primary is None:
            self.meta = None
        else:
            self.meta = {"paper_type_summary": {"primary": paper_type_primary}}


class RecordLayoutFingerprintTests(TestCase):
    """ORM mock — `LayoutFingerprint.objects.update_or_create` 호출 kwargs 검증."""

    def _capture_update_or_create(self):
        captured = {}
        def _uoc(**kwargs):
            captured.update(kwargs)
            return (MagicMock(), True)
        return captured, _uoc

    def test_clean_pdf_dual_2_columns(self):
        from apps.domains.matchup.models import LayoutFingerprint
        captured, fake = self._capture_update_or_create()
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=fake):
            _record_layout_fingerprint(
                _FakeDocument(paper_type_primary="clean_pdf_dual"),
                page_count=24, page_size={"width": 595, "height": 842},
            )
        self.assertEqual(captured["tenant_id"], 2)
        self.assertEqual(captured["fingerprint_version"], 1)
        defaults = captured["defaults"]
        self.assertEqual(defaults["paper_type"], "clean_pdf_dual")
        self.assertEqual(defaults["column_count"], 2)
        self.assertEqual(defaults["page_count"], 24)
        self.assertEqual(defaults["page_size"], {"width": 595, "height": 842})

    def test_quadrant_4_columns(self):
        from apps.domains.matchup.models import LayoutFingerprint
        captured, fake = self._capture_update_or_create()
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=fake):
            _record_layout_fingerprint(
                _FakeDocument(paper_type_primary="quadrant"),
                page_count=10, page_size={"width": 1000, "height": 1414},
            )
        self.assertEqual(captured["defaults"]["column_count"], 4)
        self.assertEqual(captured["defaults"]["paper_type"], "quadrant")

    def test_unknown_paper_type_default_1_column(self):
        from apps.domains.matchup.models import LayoutFingerprint
        captured, fake = self._capture_update_or_create()
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=fake):
            _record_layout_fingerprint(
                _FakeDocument(paper_type_primary="unknown"),
                page_count=5, page_size={"width": 1, "height": 1},
            )
        self.assertEqual(captured["defaults"]["column_count"], 1)
        self.assertEqual(captured["defaults"]["paper_type"], "unknown")

    def test_no_meta_paper_type_empty(self):
        """doc.meta=None 시 paper_type 빈 문자열, column_count=1."""
        from apps.domains.matchup.models import LayoutFingerprint
        captured, fake = self._capture_update_or_create()
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=fake):
            _record_layout_fingerprint(
                _FakeDocument(paper_type_primary=None),
                page_count=1, page_size={},
            )
        self.assertEqual(captured["defaults"]["paper_type"], "")
        self.assertEqual(captured["defaults"]["column_count"], 1)

    def test_v2_enrichment_fields_default_zero_or_empty(self):
        """V2 enrichment 자리 — text_density / x0_clusters 등 default 0/[]/{}."""
        from apps.domains.matchup.models import LayoutFingerprint
        captured, fake = self._capture_update_or_create()
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=fake):
            _record_layout_fingerprint(
                _FakeDocument(),
                page_count=10, page_size={"width": 595, "height": 842},
            )
        defaults = captured["defaults"]
        self.assertEqual(defaults["text_density"], 0.0)
        self.assertEqual(defaults["image_density"], 0.0)
        self.assertEqual(defaults["anchor_density"], 0.0)
        self.assertEqual(defaults["x0_clusters"], [])
        self.assertEqual(defaults["y_gap_distribution"], {})
        self.assertEqual(defaults["font_size_distribution"], {})
        self.assertEqual(defaults["filename_patterns"], [])
        self.assertEqual(defaults["similarity_cluster_id"], "")

    def test_fingerprint_version_always_1(self):
        from apps.domains.matchup.models import LayoutFingerprint
        captured, fake = self._capture_update_or_create()
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=fake):
            _record_layout_fingerprint(
                _FakeDocument(),
                page_count=1, page_size={},
            )
        self.assertEqual(captured["fingerprint_version"], 1)

    def test_document_passed_as_keyword(self):
        from apps.domains.matchup.models import LayoutFingerprint
        doc = _FakeDocument(id=777)
        captured, fake = self._capture_update_or_create()
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=fake):
            _record_layout_fingerprint(doc, page_count=1, page_size={})
        self.assertIs(captured["document"], doc)

    def test_paper_type_truncated_to_32_chars(self):
        from apps.domains.matchup.models import LayoutFingerprint
        long_type = "a" * 100
        captured, fake = self._capture_update_or_create()
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=fake):
            _record_layout_fingerprint(
                _FakeDocument(paper_type_primary=long_type),
                page_count=1, page_size={},
            )
        self.assertEqual(len(captured["defaults"]["paper_type"]), 32)
        self.assertEqual(captured["defaults"]["paper_type"], "a" * 32)

    def test_page_count_none_defaults_zero(self):
        from apps.domains.matchup.models import LayoutFingerprint
        captured, fake = self._capture_update_or_create()
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=fake):
            _record_layout_fingerprint(
                _FakeDocument(),
                page_count=None, page_size=None,
            )
        self.assertEqual(captured["defaults"]["page_count"], 0)
        self.assertEqual(captured["defaults"]["page_size"], {})


# ── 정적 안전성 회귀 ──────────────────────────────────────────────


def _strip_function_docstring(src: str) -> str:
    """함수 source 의 docstring 부분만 제거 — body 만 남김. ast 기반."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:]
            return ast.unparse(node)
    return src


class FingerprintHookSafetyRegressionTests(TestCase):
    def test_record_helper_does_not_import_callbacks(self):
        body = _strip_function_docstring(inspect.getsource(_record_layout_fingerprint))
        for forbidden in (
            "from apps.domains.ai.callbacks", "from apps.domains.ai.gateway",
            "_handle_matchup_ai_result", "_handle_matchup_index_result",
            "_handle_matchup_manual_result", "dispatch_job(",
            ".selected_problem_ids", "selected_problem_ids =",
            "MatchupHitReport.objects", "MatchupHitReportEntry.objects",
            "from academy.adapters.ai.detection.segment_dispatcher",
        ):
            self.assertNotIn(
                forbidden, body,
                f"_record_layout_fingerprint body 에서 forbidden token "
                f"'{forbidden}' 발견 (docstring 제외)",
            )

    def test_record_helper_does_not_write_r2(self):
        """R2 write 0 — fingerprint upsert 는 DB 한 row 만."""
        body = _strip_function_docstring(inspect.getsource(_record_layout_fingerprint))
        for forbidden in (
            "upload_fileobj_to_r2_storage", "upload_to_r2",
            "r2.put_object", "boto3", "S3",
        ):
            self.assertNotIn(
                forbidden, body,
                f"_record_layout_fingerprint 안에서 R2/S3 write 패턴 '{forbidden}' 발견",
            )

    def test_manually_crop_problem_signature_actor_unchanged(self):
        """6.5 actor signature 회귀 — 6.6 추가로 깨지지 않음."""
        from apps.domains.matchup.services import manually_crop_problem
        sig = inspect.signature(manually_crop_problem)
        self.assertIn("actor", sig.parameters)
        self.assertEqual(sig.parameters["actor"].default, None)

    def test_record_helper_inserts_one_row_only(self):
        """동일 호출에서 LayoutFingerprint.objects.update_or_create 1회만."""
        from apps.domains.matchup.models import LayoutFingerprint
        call_count = {"n": 0}
        def _track(**kwargs):
            call_count["n"] += 1
            return (MagicMock(), True)
        with patch.object(LayoutFingerprint.objects, "update_or_create", side_effect=_track):
            _record_layout_fingerprint(
                _FakeDocument(),
                page_count=10, page_size={"width": 595, "height": 842},
            )
        self.assertEqual(call_count["n"], 1)

    def test_helper_uses_update_or_create_for_idempotency(self):
        """create() 가 아닌 update_or_create — 같은 doc 재cut 시 row 누적 X."""
        body = inspect.getsource(_record_layout_fingerprint)
        # update_or_create 사용 명시
        self.assertIn("update_or_create", body)
        # objects.create( 직접 호출 X (idempotency 보장)
        self.assertNotIn("LayoutFingerprint.objects.create(", body)

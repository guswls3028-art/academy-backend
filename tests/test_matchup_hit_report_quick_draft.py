from __future__ import annotations

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.core.models import Tenant
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.models import (
    MatchupDocument,
    MatchupHitReport,
    MatchupHitReportEntry,
    MatchupProblem,
)
from apps.domains.matchup.views_hit_report import HitReportDraftView


User = get_user_model()


class HitReportQuickDraftTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="quick-draft", name="빠른 보고서")
        self.user = User.objects.create_user(
            username="quick-teacher",
            password="x",
            tenant=self.tenant,
            is_staff=True,
        )

    def _inventory_file(self, name: str) -> InventoryFile:
        suffix = uuid.uuid4().hex[:10]
        return InventoryFile.objects.create(
            tenant=self.tenant,
            scope="admin",
            display_name=name,
            r2_key=f"quick-draft/{suffix}.pdf",
            original_name=f"{suffix}.pdf",
            content_type="application/pdf",
        )

    def _document(self, title: str, source_type: str) -> MatchupDocument:
        return MatchupDocument.objects.create(
            tenant=self.tenant,
            author=self.user,
            inventory_file=self._inventory_file(title),
            title=title,
            category="2026 테스트 학교",
            status="done",
            r2_key=f"matchup/{uuid.uuid4().hex[:10]}.pdf",
            original_name=f"{title}.pdf",
            meta={"source_type": source_type},
        )

    def test_quick_mode_returns_report_shell_and_existing_selection_without_candidates(self):
        exam_doc = self._document("시험지", "school_exam_pdf")
        ref_doc = self._document("학원 대비자료", "academy_workbook")
        exam_problem = MatchupProblem.objects.create(
            tenant=self.tenant,
            document=exam_doc,
            number=1,
            text="시험 문제 본문",
        )
        ref_problem = MatchupProblem.objects.create(
            tenant=self.tenant,
            document=ref_doc,
            number=7,
            text="대비 자료 본문",
        )
        report = MatchupHitReport.objects.create(
            tenant=self.tenant,
            document=exam_doc,
            author=self.user,
            title="빠른 보고서",
        )
        MatchupHitReportEntry.objects.create(
            tenant=self.tenant,
            report=report,
            exam_problem=exam_problem,
            selected_problem_ids=[ref_problem.id],
            comment="이미 선택한 자료",
            order=3,
        )

        request = RequestFactory().get(
            f"/api/v1/matchup/documents/{exam_doc.id}/hit-report-draft/?mode=quick",
        )
        request.user = self.user
        request.tenant = self.tenant

        response = HitReportDraftView().get(request, exam_doc.id)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["report"]["id"], report.id)
        self.assertEqual(len(payload["exam_problems"]), 1)
        self.assertEqual(payload["exam_problems"][0]["id"], exam_problem.id)
        self.assertEqual(payload["exam_problems"][0]["candidates"], [])
        self.assertEqual(
            payload["exam_problems"][0]["entry"]["selected_problem_ids"],
            [ref_problem.id],
        )
        self.assertEqual(payload["selected_problem_meta"][0]["id"], ref_problem.id)
        self.assertEqual(
            payload["selected_problem_meta"][0]["document_title"],
            "학원 대비자료",
        )

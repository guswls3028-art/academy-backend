from django.apps import apps as django_apps
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.ai.callbacks import _handle_matchup_manual_result
from apps.domains.ai.models import AIJobModel
from apps.support.ai.callback_dependencies import (
    approve_matchup_proposal,
    get_matchup_proposal_approval_error,
)


InventoryFile = django_apps.get_model("inventory", "InventoryFile")
MatchupDocument = django_apps.get_model("matchup", "MatchupDocument")
MatchupProblem = django_apps.get_model("matchup", "MatchupProblem")
ProblemSegmentationProposal = django_apps.get_model(
    "matchup", "ProblemSegmentationProposal"
)


class MatchupManualCallbackScopeTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", code="ai-manual-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", code="ai-manual-b")
        self.document_a = self._document(self.tenant_a, "a")
        self.document_b = self._document(self.tenant_b, "b")
        self.problem_a = MatchupProblem.objects.create(
            tenant=self.tenant_a,
            document=self.document_a,
            number=1,
            text="",
            embedding=None,
            meta={"manual": True, "page_index": 0, "format": "choice"},
        )
        self.problem_a_other = MatchupProblem.objects.create(
            tenant=self.tenant_a,
            document=self.document_a,
            number=2,
            text="",
            embedding=None,
            meta={"manual": True, "page_index": 0, "format": "choice"},
        )
        self.problem_b = MatchupProblem.objects.create(
            tenant=self.tenant_b,
            document=self.document_b,
            number=1,
            text="victim",
            embedding=None,
            meta={"manual": True, "page_index": 0, "format": "choice"},
        )

    def _document(self, tenant: Tenant, suffix: str) -> MatchupDocument:
        inventory = InventoryFile.objects.create(
            tenant=tenant,
            scope="admin",
            student_ps="",
            display_name=f"manual-{suffix}.png",
            r2_key=f"manual-{suffix}.png",
            original_name=f"manual-{suffix}.png",
            content_type="image/png",
            size_bytes=1,
        )
        return MatchupDocument.objects.create(
            tenant=tenant,
            inventory_file=inventory,
            title=f"manual-{suffix}",
            r2_key=inventory.r2_key,
            original_name=inventory.original_name,
            content_type=inventory.content_type,
            size_bytes=inventory.size_bytes,
        )

    def _job_for_problem(self, *, job_id: str, problem: MatchupProblem, tenant: Tenant | None = None):
        return AIJobModel.objects.create(
            job_id=job_id,
            job_type="matchup_manual_index",
            status="RUNNING",
            tenant_id=str((tenant or problem.tenant).id),
            source_domain="matchup_manual",
            source_id=str(problem.id),
        )

    def test_same_tenant_result_creates_proposal_without_updating_manual_problem(self):
        self._job_for_problem(job_id="manual-ok", problem=self.problem_a)

        _handle_matchup_manual_result(
            job_id="manual-ok",
            status="DONE",
            result_payload={
                "problem_id": str(self.problem_a.id),
                "text": "indexed text",
                "embedding": [0.1, 0.2],
                "image_embedding": [0.3, 0.4],
                "format": "short_answer",
            },
            error=None,
            source_id=str(self.problem_a.id),
        )

        self.problem_a.refresh_from_db()
        self.assertEqual(self.problem_a.text, "")
        self.assertIsNone(self.problem_a.embedding)
        self.assertIsNone(self.problem_a.image_embedding)
        self.assertEqual(self.problem_a.meta["format"], "choice")
        proposal = ProblemSegmentationProposal.objects.get(target_problem=self.problem_a)
        self.assertEqual(proposal.proposal_kind, "manual_index")
        self.assertEqual(proposal.status, "needs_review")
        self.assertEqual(proposal.raw_response["text"], "indexed text")

    def test_duplicate_callback_reuses_the_same_pending_proposal(self):
        self._job_for_problem(job_id="manual-idempotent", problem=self.problem_a)
        payload = {
            "problem_id": str(self.problem_a.id),
            "text": "indexed once",
            "embedding": [0.1, 0.2],
            "format": "essay",
        }

        for _ in range(2):
            _handle_matchup_manual_result(
                job_id="manual-idempotent",
                status="DONE",
                result_payload=payload,
                error=None,
                source_id=str(self.problem_a.id),
            )

        self.assertEqual(
            ProblemSegmentationProposal.objects.filter(target_problem=self.problem_a).count(),
            1,
        )

    def test_approved_proposal_applies_index_result_once(self):
        self._job_for_problem(job_id="manual-approve", problem=self.problem_a)
        _handle_matchup_manual_result(
            job_id="manual-approve",
            status="DONE",
            result_payload={
                "problem_id": str(self.problem_a.id),
                "text": "approved text",
                "embedding": [0.1, 0.2],
                "image_embedding": [0.3, 0.4],
                "format": "essay",
            },
            error=None,
            source_id=str(self.problem_a.id),
        )

        proposal = ProblemSegmentationProposal.objects.get(target_problem=self.problem_a)
        approved = approve_matchup_proposal(proposal.id, None)
        approved.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(approved.id, self.problem_a.id)
        self.assertEqual(approved.text, "approved text")
        self.assertEqual(approved.embedding, [0.1, 0.2])
        self.assertEqual(approved.image_embedding, [0.3, 0.4])
        self.assertEqual(approved.meta["format"], "essay")
        self.assertEqual(approved.meta["manual"], True)
        self.assertEqual(proposal.status, "approved")
        self.assertEqual(proposal.promoted_problem_id, self.problem_a.id)

    def test_invalid_callback_scalars_are_normalized_without_mutating_problem(self):
        self.problem_a.meta = {
            "manual": True,
            "page_index": "invalid",
            "format": "choice",
        }
        self.problem_a.save(update_fields=["meta", "updated_at"])
        self._job_for_problem(job_id="manual-invalid-scalars", problem=self.problem_a)

        _handle_matchup_manual_result(
            job_id="manual-invalid-scalars",
            status="DONE",
            result_payload={
                "problem_id": str(self.problem_a.id),
                "text": 123,
                "confidence": "invalid",
                "format": "unexpected",
            },
            error=None,
            source_id=str(self.problem_a.id),
        )

        self.problem_a.refresh_from_db()
        proposal = ProblemSegmentationProposal.objects.get(target_problem=self.problem_a)
        self.assertEqual(self.problem_a.text, "")
        self.assertEqual(proposal.page_number, 0)
        self.assertEqual(proposal.confidence, 0.0)
        self.assertEqual(proposal.raw_response["text"], "123")
        self.assertEqual(proposal.raw_response["format"], "choice")

    def test_approval_rejects_invalid_embedding_payload(self):
        proposal = ProblemSegmentationProposal.objects.create(
            tenant=self.tenant_a,
            document=self.document_a,
            target_problem=self.problem_a,
            proposal_kind="manual_index",
            analysis_version_key="manual-index:invalid-vector",
            status="needs_review",
            raw_response={"embedding": [0.1, "not-a-number"]},
        )

        ProposalApprovalError = get_matchup_proposal_approval_error()

        with self.assertRaises(ProposalApprovalError):
            approve_matchup_proposal(proposal.id, None)

        self.problem_a.refresh_from_db()
        proposal.refresh_from_db()
        self.assertIsNone(self.problem_a.embedding)
        self.assertEqual(proposal.status, "needs_review")

    def test_approval_rejects_stale_text_embedding_after_manual_edit(self):
        self.problem_a.text = "owner edited text"
        self.problem_a.save(update_fields=["text", "updated_at"])
        proposal = ProblemSegmentationProposal.objects.create(
            tenant=self.tenant_a,
            document=self.document_a,
            target_problem=self.problem_a,
            proposal_kind="manual_index",
            analysis_version_key="manual-index:stale-text",
            status="needs_review",
            raw_response={
                "text": "older OCR text",
                "embedding": [0.1, 0.2],
                "image_embedding": [0.3, 0.4],
                "format": "essay",
            },
        )

        ProposalApprovalError = get_matchup_proposal_approval_error()

        with self.assertRaises(ProposalApprovalError):
            approve_matchup_proposal(proposal.id, None)

        self.problem_a.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(self.problem_a.text, "owner edited text")
        self.assertIsNone(self.problem_a.embedding)
        self.assertIsNone(self.problem_a.image_embedding)
        self.assertEqual(self.problem_a.meta["format"], "choice")
        self.assertEqual(proposal.status, "needs_review")

    def test_payload_problem_id_cannot_override_job_source_id(self):
        self._job_for_problem(job_id="manual-source-mismatch", problem=self.problem_a)

        _handle_matchup_manual_result(
            job_id="manual-source-mismatch",
            status="DONE",
            result_payload={
                "problem_id": str(self.problem_a_other.id),
                "text": "wrong target",
                "embedding": [9],
            },
            error=None,
            source_id=str(self.problem_a.id),
        )

        self.problem_a.refresh_from_db()
        self.problem_a_other.refresh_from_db()
        self.assertEqual(self.problem_a.text, "")
        self.assertEqual(self.problem_a_other.text, "")
        self.assertIsNone(self.problem_a_other.embedding)
        self.assertFalse(ProblemSegmentationProposal.objects.exists())

    def test_cross_tenant_payload_problem_id_is_not_updated(self):
        self._job_for_problem(job_id="manual-cross-tenant", problem=self.problem_a)

        _handle_matchup_manual_result(
            job_id="manual-cross-tenant",
            status="DONE",
            result_payload={
                "problem_id": str(self.problem_b.id),
                "text": "cross tenant overwrite",
                "embedding": [7],
            },
            error=None,
            source_id=str(self.problem_a.id),
        )

        self.problem_b.refresh_from_db()
        self.assertEqual(self.problem_b.text, "victim")
        self.assertIsNone(self.problem_b.embedding)
        self.assertFalse(ProblemSegmentationProposal.objects.exists())

    def test_job_tenant_must_match_problem_tenant(self):
        self._job_for_problem(
            job_id="manual-wrong-job-tenant",
            problem=self.problem_b,
            tenant=self.tenant_a,
        )

        _handle_matchup_manual_result(
            job_id="manual-wrong-job-tenant",
            status="DONE",
            result_payload={
                "problem_id": str(self.problem_b.id),
                "text": "wrong tenant",
                "embedding": [5],
            },
            error=None,
            source_id=str(self.problem_b.id),
        )

        self.problem_b.refresh_from_db()
        self.assertEqual(self.problem_b.text, "victim")
        self.assertIsNone(self.problem_b.embedding)
        self.assertFalse(ProblemSegmentationProposal.objects.exists())

    def test_job_type_and_source_domain_must_match_manual_index_contract(self):
        for job_id, job_type, source_domain in (
            ("manual-wrong-type", "matchup_analysis", "matchup_manual"),
            ("manual-wrong-domain", "matchup_manual_index", "matchup"),
        ):
            AIJobModel.objects.create(
                job_id=job_id,
                job_type=job_type,
                status="RUNNING",
                tenant_id=str(self.tenant_a.id),
                source_domain=source_domain,
                source_id=str(self.problem_a.id),
            )
            _handle_matchup_manual_result(
                job_id=job_id,
                status="DONE",
                result_payload={
                    "problem_id": str(self.problem_a.id),
                    "text": "spoofed callback",
                    "embedding": [5],
                },
                error=None,
                source_id=str(self.problem_a.id),
            )

        self.problem_a.refresh_from_db()
        self.assertEqual(self.problem_a.text, "")
        self.assertIsNone(self.problem_a.embedding)
        self.assertFalse(ProblemSegmentationProposal.objects.exists())

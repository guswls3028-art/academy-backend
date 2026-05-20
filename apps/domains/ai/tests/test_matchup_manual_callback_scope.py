from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.ai.callbacks import _handle_matchup_manual_result
from apps.domains.ai.models import AIJobModel
from apps.domains.matchup.models import MatchupProblem


class MatchupManualCallbackScopeTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", code="ai-manual-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", code="ai-manual-b")
        self.problem_a = MatchupProblem.objects.create(
            tenant=self.tenant_a,
            number=1,
            text="",
            embedding=None,
            meta={},
        )
        self.problem_a_other = MatchupProblem.objects.create(
            tenant=self.tenant_a,
            number=2,
            text="",
            embedding=None,
            meta={},
        )
        self.problem_b = MatchupProblem.objects.create(
            tenant=self.tenant_b,
            number=1,
            text="victim",
            embedding=None,
            meta={},
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

    def test_same_tenant_source_problem_is_updated(self):
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
        self.assertEqual(self.problem_a.text, "indexed text")
        self.assertEqual(self.problem_a.embedding, [0.1, 0.2])
        self.assertEqual(self.problem_a.image_embedding, [0.3, 0.4])
        self.assertEqual(self.problem_a.meta["format"], "short_answer")

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

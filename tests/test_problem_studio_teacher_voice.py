from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from academy.adapters.ai.problem.generator import generate_problem_package_from_text
from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
from apps.core.models import Tenant, TenantMembership
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.tools.problem_studio.models import (
    ProblemStudioGenerationReview,
    ProblemStudioVoiceProfile,
    ProblemStudioVoiceSample,
)
from apps.domains.tools.problem_studio.views import (
    ProblemStudioGenerationReviewView,
    ProblemStudioJobCreateView,
    ProblemStudioJobStatusView,
    ProblemStudioTransferJobStatusView,
    ProblemStudioVoiceProfileCollectionView,
    ProblemStudioVoiceSampleCollectionView,
)
from apps.domains.tools.problem_studio.voice_profiles import (
    add_voice_sample,
    build_voice_profile_snapshot,
    create_voice_profile,
    sanitize_voice_text,
)
from apps.domains.tools.problem_studio.worker import handle_problem_studio_package_job
from apps.shared.contracts.ai_job import AIJob


User = get_user_model()


class ProblemStudioTeacherVoiceTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="문체 테스트", code="voice-test", is_active=True)
        self.other_tenant = Tenant.objects.create(name="다른 학원", code="voice-other", is_active=True)
        self.user = User.objects.create_user(
            username="voice-owner",
            password="test-password",
            tenant=self.tenant,
        )
        self.other_user = User.objects.create_user(
            username="voice-other-user",
            password="test-password",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="owner")
        TenantMembership.ensure_active(tenant=self.tenant, user=self.other_user, role="teacher")

    def _auth(self, request, *, user=None, tenant=None):
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.user)
        return request

    def _profile(self):
        return create_voice_profile(
            tenant=self.tenant,
            user=self.user,
            name="내 생명과학 해설",
            subject="생명과학",
            style_instructions="핵심 개념을 먼저 쓰고 마지막에 오답 이유를 짚습니다.",
            is_default=True,
        )

    def test_publisher_material_cannot_be_registered_as_style(self):
        profile = self._profile()
        request = self.factory.post(
            f"/api/v1/tools/problem-studio/voice-profiles/{profile.id}/samples/",
            {
                "usage_scope": "style",
                "origin": "publisher_reference",
                "explanation": "출판사 해설 문장",
                "rights_confirmed": True,
            },
            format="json",
        )
        response = ProblemStudioVoiceSampleCollectionView.as_view()(
            self._auth(request),
            profile_id=profile.id,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("직접 작성", response.data["detail"])
        self.assertFalse(ProblemStudioVoiceSample.objects.exists())

    def test_rights_confirmation_requires_json_true(self):
        profile = self._profile()
        request = self.factory.post(
            f"/api/v1/tools/problem-studio/voice-profiles/{profile.id}/samples/",
            {
                "usage_scope": "style",
                "origin": "teacher_authored",
                "explanation": "직접 작성했다고 주장하는 해설",
                "rights_confirmed": "false",
            },
            format="json",
        )
        response = ProblemStudioVoiceSampleCollectionView.as_view()(
            self._auth(request),
            profile_id=profile.id,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("권리 확인", response.data["detail"])
        self.assertFalse(ProblemStudioVoiceSample.objects.exists())

    def test_voice_text_masks_common_identity_fields(self):
        sanitized = sanitize_voice_text(
            "이름: 홍길동, 학번: 20260001, 연락처 010-1234-5678, a@example.com",
            max_chars=500,
        )
        self.assertNotIn("홍길동", sanitized)
        self.assertNotIn("20260001", sanitized)
        self.assertNotIn("010-1234-5678", sanitized)
        self.assertNotIn("a@example.com", sanitized)

    def test_profile_snapshot_is_owner_scoped_and_separates_style_from_reference(self):
        profile = self._profile()
        add_voice_sample(
            profile=profile,
            user=self.user,
            usage_scope="style",
            origin="teacher_authored",
            explanation="먼저 핵심 정의를 확인합니다. 따라서 정답은 ③입니다.",
            rights_confirmed=True,
        )
        add_voice_sample(
            profile=profile,
            user=self.user,
            usage_scope="content_reference",
            origin="publisher_reference",
            problem_text="광합성 과정에서 생성되는 물질을 고르시오.",
            rights_confirmed=True,
        )

        snapshot = build_voice_profile_snapshot(
            tenant=self.tenant,
            user=self.user,
            profile_id=profile.id,
        )
        self.assertEqual(snapshot["style_sample_count"], 1)
        self.assertEqual(snapshot["reference_sample_count"], 1)
        self.assertIn("핵심 정의", snapshot["style_examples"][0]["explanation"])
        self.assertIn("광합성", snapshot["content_references"][0]["problem"])

        with self.assertRaisesMessage(ValueError, "찾을 수 없습니다"):
            build_voice_profile_snapshot(
                tenant=self.tenant,
                user=self.other_user,
                profile_id=profile.id,
            )

    @patch("apps.domains.tools.problem_studio.views.dispatch_tools_ai_job")
    def test_job_dispatch_uses_server_resolved_voice_snapshot(self, dispatch):
        profile = self._profile()
        add_voice_sample(
            profile=profile,
            user=self.user,
            usage_scope="style",
            origin="teacher_authored",
            explanation="결론부터 말하면, 이 선택지는 조건을 만족하지 않습니다.",
            rights_confirmed=True,
        )
        dispatch.return_value = {"ok": True, "job_id": str(uuid.uuid4())}
        request = self.factory.post(
            "/api/v1/tools/problem-studio/jobs/",
            {
                "payload": json.dumps(
                    {
                        "title": "후보 생성",
                        "subject": "과학",
                        "voice_profile_id": str(profile.id),
                        "_resolved_voice_profile": {
                            "name": "공격자가 주입한 문체",
                            "style_examples": [{"explanation": "노출 금지"}],
                        },
                        "questions": [],
                    },
                    ensure_ascii=False,
                ),
            },
            format="multipart",
        )
        response = ProblemStudioJobCreateView.as_view()(self._auth(request))
        self.assertEqual(response.status_code, 202, response.data)
        payload = dispatch.call_args.kwargs["payload"]
        snapshot = payload["problem_studio_payload"]["_resolved_voice_profile"]
        self.assertEqual(snapshot["name"], profile.name)
        self.assertNotIn("공격자", json.dumps(snapshot, ensure_ascii=False))
        self.assertEqual(payload["tenant_id"], str(self.tenant.id))
        self.assertEqual(payload["request_user_id"], str(self.user.id))

    def test_job_status_is_hidden_from_other_teacher_in_same_tenant(self):
        job = AIJobModel.objects.create(
            job_id=str(uuid.uuid4()),
            job_type="problem_studio_package",
            status="DONE",
            tenant_id=str(self.tenant.id),
            source_domain="tools_problem_studio",
            payload={
                "tenant_id": str(self.tenant.id),
                "request_user_id": str(self.user.id),
            },
        )
        AIResultModel.objects.create(job=job, payload={"questions": []})
        request = self.factory.get(f"/api/v1/tools/problem-studio/jobs/{job.job_id}/")
        response = ProblemStudioJobStatusView.as_view()(
            self._auth(request, user=self.other_user),
            job_id=job.job_id,
        )
        self.assertEqual(response.status_code, 404)

    def test_transfer_result_is_hidden_from_other_teacher_in_same_tenant(self):
        job = AIJobModel.objects.create(
            job_id=str(uuid.uuid4()),
            job_type="problem_studio_transcription",
            status="DONE",
            tenant_id=str(self.tenant.id),
            source_domain="tools_problem_studio",
            payload={
                "tenant_id": str(self.tenant.id),
                "request_user_id": str(self.user.id),
            },
        )
        request = self.factory.get(
            f"/api/v1/tools/problem-studio/transfer-jobs/{job.job_id}/",
        )
        response = ProblemStudioTransferJobStatusView.as_view()(
            self._auth(request, user=self.other_user),
            job_id=job.job_id,
        )
        self.assertEqual(response.status_code, 404)

    def test_terminal_job_scrubs_source_and_voice_sample_text_but_keeps_review_scope(self):
        profile = self._profile()
        job = AIJobModel.objects.create(
            job_id=str(uuid.uuid4()),
            job_type="problem_studio_package",
            status="RUNNING",
            tenant_id=str(self.tenant.id),
            source_domain="tools_problem_studio",
            payload={
                "tenant_id": str(self.tenant.id),
                "request_user_id": str(self.user.id),
                "source_files": [
                    {"name": "private.pdf", "extracted_text": "보관하면 안 되는 원문"},
                ],
                "problem_studio_payload": {
                    "title": "개인 제목",
                    "questions": [{"prompt": "사용자 초안"}],
                    "voice_profile_id": str(profile.id),
                    "_resolved_voice_profile": {
                        "id": str(profile.id),
                        "name": profile.name,
                        "version": 3,
                        "style_examples": [{"explanation": "개인 문체 원문"}],
                        "content_references": [{"problem": "출판 참고 원문"}],
                        "style_sample_count": 1,
                        "reference_sample_count": 1,
                    },
                },
            },
        )
        DjangoAIJobRepository().mark_done(
            job.job_id,
            timezone.now(),
            {"questions": []},
        )
        job.refresh_from_db()
        serialized = json.dumps(job.payload, ensure_ascii=False)
        self.assertTrue(job.payload["privacy_scrubbed"])
        self.assertNotIn("보관하면 안 되는 원문", serialized)
        self.assertNotIn("개인 문체 원문", serialized)
        self.assertNotIn("출판 참고 원문", serialized)
        self.assertEqual(
            job.payload["problem_studio_payload"]["_resolved_voice_profile"]["id"],
            str(profile.id),
        )

    def test_review_is_append_only_and_approved_edit_becomes_style_sample(self):
        profile = self._profile()
        job = AIJobModel.objects.create(
            job_id=str(uuid.uuid4()),
            job_type="problem_studio_package",
            status="DONE",
            tenant_id=str(self.tenant.id),
            source_domain="tools_problem_studio",
            payload={
                "tenant_id": str(self.tenant.id),
                "request_user_id": str(self.user.id),
                "problem_studio_payload": {
                    "_resolved_voice_profile": {
                        "id": str(profile.id),
                        "version": profile.version,
                    },
                },
            },
        )
        AIResultModel.objects.create(
            job=job,
            payload={
                "questions": [
                    {
                        "prompt": "원래 문제",
                        "choices": [],
                        "answer": "1",
                        "explanation": "원래 해설",
                    },
                ],
            },
        )
        body = {
            "question_index": 0,
            "outcome": "edited",
            "final_question": {
                "prompt": "선생님이 고친 문제",
                "choices": [],
                "answer": "1",
                "explanation": "핵심 조건부터 확인하면 정답은 1입니다.",
            },
            "learn_from_this": True,
            "rights_confirmed": True,
        }
        request = self.factory.post(
            f"/api/v1/tools/problem-studio/jobs/{job.job_id}/reviews/",
            body,
            format="json",
        )
        response = ProblemStudioGenerationReviewView.as_view()(
            self._auth(request),
            job_id=job.job_id,
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["learned"])
        review = ProblemStudioGenerationReview.objects.get(job_id=job.job_id)
        self.assertEqual(review.final_payload["prompt"], "선생님이 고친 문제")
        self.assertEqual(review.learned_sample.origin, "approved_output")

        duplicate = self.factory.post(
            f"/api/v1/tools/problem-studio/jobs/{job.job_id}/reviews/",
            {**body, "final_question": {**body["final_question"], "explanation": "다른 값"}},
            format="json",
        )
        duplicate_response = ProblemStudioGenerationReviewView.as_view()(
            self._auth(duplicate),
            job_id=job.job_id,
        )
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertFalse(duplicate_response.data["created"])
        self.assertEqual(ProblemStudioGenerationReview.objects.count(), 1)
        self.assertEqual(
            ProblemStudioVoiceSample.objects.filter(origin="approved_output").count(),
            1,
        )

    def test_worker_rejects_tenant_or_user_contract_mismatch(self):
        missing_user = AIJob.new(
            type="problem_studio_package",
            tenant_id="7",
            payload={"tenant_id": "7", "problem_studio_payload": {}, "source_files": []},
        )
        self.assertEqual(handle_problem_studio_package_job(missing_user).error, "request_user_id missing")

        wrong_tenant = AIJob.new(
            type="problem_studio_package",
            tenant_id="7",
            payload={
                "tenant_id": "8",
                "request_user_id": "1",
                "problem_studio_payload": {},
                "source_files": [],
            },
        )
        self.assertEqual(handle_problem_studio_package_job(wrong_tenant).error, "tenant_id mismatch")

    @patch("academy.adapters.ai.problem.generator._get_client")
    @patch("apps.domains.ai.services.quota.consume_ai_quota")
    @patch("academy.adapters.ai.problem.generator.AIConfig.load")
    def test_generator_formats_voice_prompt_and_returns_review_metadata(
        self,
        config_load,
        _quota,
        get_client,
    ):
        config_load.return_value = SimpleNamespace(PROBLEM_GEN_MODEL="test-model")
        get_client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "questions": [
                                    {
                                        "prompt": "새 문제",
                                        "choices": ["① A", "② B"],
                                        "answer": "②",
                                        "explanation": "조건을 대입하면 ②입니다.",
                                        "source_index": 1,
                                        "variant_index": 1,
                                        "source_evidence": [1],
                                        "answer_check": "조건 대입 완료",
                                        "confidence": "high",
                                    },
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ),
            ],
        )
        questions = generate_problem_package_from_text(
            source_text="원본 문제",
            mode="same-type",
            variant_count=1,
            note_policy="짧게",
            subject="과학",
            max_questions=3,
            voice_profile={
                "name": "내 문체",
                "version": 2,
                "style_examples": [{"explanation": "핵심부터 확인합니다."}],
                "content_references": [{"problem": "참고 문제"}],
            },
        )
        self.assertEqual(questions[0]["confidence"], "high")
        self.assertEqual(questions[0]["source_evidence"], [1])
        prompt = get_client.return_value.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("핵심부터 확인", prompt)
        self.assertIn("내용 참고 자료", prompt)

    def test_sanitized_fixture_import_is_locked_to_tenant_9999(self):
        isolated = Tenant.objects.create(
            id=9999,
            name="격리 테스트",
            code="e2e-isolated",
            is_active=True,
        )
        test_user = User.objects.create_user(
            username="isolated-voice-user",
            password="test-password",
            tenant=isolated,
        )
        TenantMembership.ensure_active(tenant=isolated, user=test_user, role="teacher")
        fixture = {
            "schema": "problem-studio-sanitized-reference/v1",
            "privacy": {
                "contains_original_files": False,
                "contains_user_ids": False,
                "contains_document_names": False,
                "phone_and_email_masked": True,
            },
            "rights_contract": {
                "references": "content_reference_only",
                "style_samples": "teacher_authored_matchup_comments",
                "authorized_by": "workspace_user_request",
            },
            "references": [
                {
                    "fingerprint": "a" * 64,
                    "problem_text": "격리된 참고 문제의 개념 텍스트입니다.",
                },
            ],
            "style_samples": [
                {
                    "fingerprint": "b" * 64,
                    "explanation": "핵심 조건을 먼저 확인합니다.",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            fixture_path = Path(directory) / "fixture.json"
            fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(CommandError):
                call_command(
                    "import_problem_studio_reference_fixture",
                    input=str(fixture_path),
                    target_tenant_id=self.tenant.id,
                    target_user_id=self.user.id,
                    tag="[E2E-VOICE]",
                )
            call_command(
                "import_problem_studio_reference_fixture",
                input=str(fixture_path),
                target_tenant_id=9999,
                target_user_id=test_user.id,
                tag="[E2E-VOICE]",
            )
            call_command(
                "import_problem_studio_reference_fixture",
                input=str(fixture_path),
                target_tenant_id=9999,
                target_user_id=test_user.id,
                tag="[E2E-VOICE]",
            )

        profile = ProblemStudioVoiceProfile.objects.get(
            tenant=isolated,
            owner=test_user,
        )
        self.assertEqual(profile.samples.filter(usage_scope="style").count(), 1)
        self.assertEqual(profile.samples.filter(usage_scope="content_reference").count(), 1)
        self.assertFalse(
            ProblemStudioVoiceProfile.objects.filter(tenant=self.tenant).exists(),
        )

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.tools.problem_review.renderers import (
    render_problem_review_pdf,
    render_problem_review_pptx,
)
from apps.domains.tools.problem_review.schema import (
    MAX_QUESTIONS,
    build_source_draft,
    normalize_report_payload,
)
from apps.domains.tools.problem_review.worker import handle_problem_review_export_job
from apps.domains.tools.problem_review.views import (
    ProblemReviewReportCollectionView,
    ProblemReviewReportDetailView,
)
from apps.domains.tools.problem_studio.models import ProblemReviewReport
from apps.shared.contracts.ai_job import AIJob


def _sample_report() -> dict:
    return normalize_report_payload({
        "metadata": {
            "title": "통합과학 중간고사 문제 리뷰",
            "school": "아카데미고",
            "subject": "통합과학",
            "grade": "1학년",
            "exam_name": "1학기 중간고사",
            "exam_date": "2026-04-24",
        },
        "summary": {
            "one_line": "개념 연결과 자료 해석을 함께 확인한 시험입니다.",
            "character": "단순 암기보다 조건을 끝까지 읽는 힘이 중요합니다.",
            "total_questions": 2,
            "total_points": "100점",
            "student_burden": "복합 자료가 포함된 후반 문항의 체감 부담이 큽니다.",
        },
        "assessment_axes": [
            {"title": "개념 연결", "description": "서로 다른 단원의 개념을 연결합니다."},
            {"title": "자료 해석", "description": "표와 그래프의 조건을 비교합니다."},
        ],
        "domains": [
            {
                "name": "물질과 규칙성",
                "question_numbers": ["1", "2"],
                "points": "100점",
                "ratio": "100%",
                "insight": "핵심 개념과 적용을 균형 있게 확인합니다.",
            },
        ],
        "difficulty": {
            "distribution": [
                {"label": "중", "question_numbers": ["1"], "points": "40점", "note": "개념 확인"},
                {"label": "상", "question_numbers": ["2"], "points": "60점", "note": "자료 적용"},
            ],
            "grade_estimate_note": "실제 등급 컷은 학교 결과 확인이 필요합니다.",
        },
        "questions": [
            {
                "number": 1,
                "source_number": 1,
                "unit": "원소의 생성",
                "answer": "3",
                "points": "40점",
                "difficulty": "중",
                "key_point": "별의 진화 순서를 구분합니다.",
                "trap": "생성 시기를 뒤바꾸기 쉽습니다.",
                "validity": "조건과 정답이 일치합니다.",
                "review_note": "표현을 한 번 더 다듬어 주세요.",
            },
            {
                "number": 2,
                "source_number": 2,
                "unit": "주기율",
                "answer": "5",
                "points": "60점",
                "difficulty": "상",
                "key_point": "주기적 성질을 자료에 적용합니다.",
                "trap": "원자 번호와 족을 혼동하기 쉽습니다.",
                "validity": "선지 간 중복이 없습니다.",
                "review_note": "변별 문항으로 적절합니다.",
            },
        ],
        "key_items": [
            {
                "rank": 1,
                "title": "자료 해석 변별 문항",
                "question_numbers": ["2"],
                "reason": "두 조건을 동시에 적용해야 합니다.",
                "collapse_point": "첫 조건만 보고 답을 고르기 쉽습니다.",
                "prescription": "조건을 표에 표시하는 연습이 필요합니다.",
            },
        ],
        "failure_patterns": [
            {
                "title": "조건 누락",
                "symptom": "첫 조건만 적용합니다.",
                "cause": "조건 표시가 부족합니다.",
                "prescription": "조건마다 표시하고 확인합니다.",
            },
        ],
        "parent_guidance": {
            "avoid": ["공부를 안 했다"],
            "recommended": ["복합 조건을 정리하는 연습이 더 필요합니다."],
        },
        "conclusion": {
            "headline": "조건을 구조화하는 연습이 다음 성적을 만듭니다.",
            "actions": ["복합 조건 표시하기", "오답 선지의 이유 쓰기"],
        },
    })


class ProblemReviewSchemaAndRendererTests(SimpleTestCase):
    def test_source_draft_preserves_excerpt_and_bounds_question_count(self):
        questions = [
            {"number": index + 1, "prompt": f"교사 원문 {index + 1}", "answer": "1"}
            for index in range(MAX_QUESTIONS + 5)
        ]

        draft = build_source_draft(metadata={"title": "검수용"}, questions=questions, warnings=[])

        self.assertEqual(len(draft["questions"]), MAX_QUESTIONS)
        self.assertEqual(draft["questions"][0]["source_excerpt"], "교사 원문 1")
        edited = normalize_report_payload(
            {"questions": [{"number": 1, "source_excerpt": "덮어쓰기 시도"}]},
            fallback=draft,
        )
        self.assertEqual(edited["questions"][0]["source_excerpt"], "교사 원문 1")
        first_only = normalize_report_payload(
            {
                "questions": [{
                    **draft["questions"][0],
                    "number": 7,
                    "source_excerpt": "덮어쓰기 시도",
                }],
            },
            fallback=draft,
            preserve_question_set=False,
        )
        self.assertEqual(len(first_only["questions"]), 1)
        self.assertEqual(first_only["questions"][0]["number"], 7)
        self.assertEqual(first_only["questions"][0]["source_number"], 1)
        self.assertEqual(first_only["questions"][0]["source_excerpt"], "교사 원문 1")

    def test_pdf_and_pptx_exports_are_parseable(self):
        from pptx import Presentation

        pdf_bytes = render_problem_review_pdf(_sample_report())
        pptx_bytes = render_problem_review_pptx(_sample_report())

        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertTrue(pdf_bytes.rstrip().endswith(b"%%EOF"))
        self.assertGreaterEqual(pdf_bytes.count(b"/Type /Page"), 4)
        deck = Presentation(__import__("io").BytesIO(pptx_bytes))
        self.assertGreaterEqual(len(deck.slides), 8)

    @patch("apps.domains.tools.problem_review.worker._record_progress")
    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage")
    def test_export_worker_writes_only_report_scoped_result(self, upload_file, _progress):
        job = AIJob.new(
            type="problem_review_export",
            tenant_id="tenant-1",
            source_domain="tools_problem_review",
            source_id="report-1",
            payload={
                "tenant_id": "tenant-1",
                "request_user_id": "teacher-1",
                "report_id": "report-1",
                "report_version": 3,
                "output_format": "pptx",
                "report": _sample_report(),
            },
        )

        result = handle_problem_review_export_job(job)

        self.assertEqual(result.status, "DONE")
        key = upload_file.call_args.kwargs["key"]
        self.assertTrue(key.startswith("tenants/tenant-1/tools/problem-review/report-1/"))
        self.assertTrue(upload_file.call_args.kwargs["fileobj"].getvalue().startswith(b"PK"))
        self.assertEqual(result.result["report_version"], 3)

        rejected = handle_problem_review_export_job(AIJob.new(
            type="problem_review_export",
            tenant_id="tenant-2",
            payload={
                "tenant_id": "tenant-1",
                "request_user_id": "teacher-1",
                "report_id": "report-1",
                "output_format": "pdf",
            },
        ))
        self.assertEqual(rejected.status, "FAILED")
        self.assertIn("tenant_id mismatch", rejected.error or "")

    @patch("apps.infrastructure.storage.r2.delete_object_r2_storage")
    def test_terminal_cleanup_accepts_only_exact_problem_review_prefix(self, delete_object):
        from academy.application.use_cases.ai.process_ai_job_from_sqs import PreparedJob
        from academy.framework.workers.ai_sqs_worker import _cleanup_terminal_artifacts

        prepared = PreparedJob(
            job_id="review-job",
            job_type="problem_review_analysis",
            tier="basic",
            payload={
                "report_id": "report-1",
                "source_archive_key": "tenants/tenant-1/tools/problem-review/tmp/report-1/sources.zip",
            },
            receipt_handle="receipt",
            tenant_id="tenant-1",
            source_domain="tools_problem_review",
            source_id="report-1",
        )

        _cleanup_terminal_artifacts(prepared)
        delete_object.assert_called_once_with(
            key="tenants/tenant-1/tools/problem-review/tmp/report-1/sources.zip",
        )

        delete_object.reset_mock()
        mismatched = PreparedJob(
            **{
                **prepared.__dict__,
                "payload": {
                    "report_id": "report-1",
                    "source_archive_key": "tenants/tenant-1/tools/problem-review/tmp/report-2/sources.zip",
                },
            },
        )
        _cleanup_terminal_artifacts(mismatched)
        delete_object.assert_not_called()


class ProblemReviewReportViewTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Review Academy", code="review_academy")
        self.user = get_user_model().objects.create_user(
            username="review_teacher",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="teacher")
        self.factory = APIRequestFactory()

    def _authenticate(self, request, *, user=None, tenant=None):
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.user)
        return request

    @patch(
        "apps.domains.tools.problem_review.views.dispatch_tools_ai_job",
        return_value={"ok": True, "job_id": "review-analysis-job"},
    )
    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage")
    def test_create_dispatches_teacher_owned_analysis_without_exposing_r2_key(
        self,
        upload_file,
        dispatch_job,
    ):
        request = self.factory.post(
            "/api/v1/tools/problem-review/reports/",
            {
                "source_files": SimpleUploadedFile(
                    "teacher-exam.pdf",
                    b"%PDF-1.4\n% teacher fixture\n",
                    content_type="application/pdf",
                ),
                "metadata": '{"title":"직접 만든 시험 리뷰","subject":"통합과학"}',
                "external_ai_confirmed": "true",
            },
            format="multipart",
        )

        response = ProblemReviewReportCollectionView.as_view()(self._authenticate(request))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "analyzing")
        self.assertNotIn("source_archive_key", str(response.data))
        uploaded_key = upload_file.call_args.kwargs["key"]
        self.assertTrue(uploaded_key.startswith(f"tenants/{self.tenant.id}/tools/problem-review/tmp/"))
        dispatched = dispatch_job.call_args.kwargs
        self.assertEqual(dispatched["job_type"], "problem_review_analysis")
        self.assertEqual(dispatched["payload"]["request_user_id"], str(self.user.id))
        self.assertEqual(dispatched["payload"]["source_archive_key"], uploaded_key)

    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage")
    def test_create_requires_external_ai_confirmation_before_upload(self, upload_file):
        request = self.factory.post(
            "/api/v1/tools/problem-review/reports/",
            {
                "source_files": SimpleUploadedFile("exam.pdf", b"%PDF", content_type="application/pdf"),
            },
            format="multipart",
        )

        response = ProblemReviewReportCollectionView.as_view()(self._authenticate(request))

        self.assertEqual(response.status_code, 400)
        upload_file.assert_not_called()

    @patch(
        "apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage",
        side_effect=RuntimeError("fixture upload unavailable"),
    )
    def test_create_marks_report_failed_when_source_upload_cannot_start(self, _upload_file):
        request = self.factory.post(
            "/api/v1/tools/problem-review/reports/",
            {
                "source_files": SimpleUploadedFile(
                    "exam.pdf",
                    b"%PDF",
                    content_type="application/pdf",
                ),
                "external_ai_confirmed": "true",
            },
            format="multipart",
        )

        response = ProblemReviewReportCollectionView.as_view()(self._authenticate(request))

        self.assertEqual(response.status_code, 503)
        report = ProblemReviewReport.objects.get()
        self.assertEqual(report.status, ProblemReviewReport.Status.FAILED)
        self.assertIn("fixture upload unavailable", report.last_error)

    def test_detail_is_teacher_owned_and_save_uses_optimistic_version(self):
        report = ProblemReviewReport.objects.create(
            tenant=self.tenant,
            requested_by=self.user,
            status=ProblemReviewReport.Status.DRAFT,
            title="원본 제목",
            draft=_sample_report(),
        )
        other = get_user_model().objects.create_user(
            username="other_review_teacher",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=other, role="teacher")
        denied_request = self.factory.get(f"/api/v1/tools/problem-review/reports/{report.id}/")

        denied_response = ProblemReviewReportDetailView.as_view()(
            self._authenticate(denied_request, user=other),
            report_id=report.id,
        )

        self.assertEqual(denied_response.status_code, 404)
        stale_request = self.factory.patch(
            f"/api/v1/tools/problem-review/reports/{report.id}/",
            {"version": 0, "draft": _sample_report()},
            format="json",
        )
        stale_response = ProblemReviewReportDetailView.as_view()(
            self._authenticate(stale_request),
            report_id=report.id,
        )
        self.assertEqual(stale_response.status_code, 409)

        updated = _sample_report()
        updated["summary"]["one_line"] = "선생님이 검수해 확정한 한 줄 평입니다."
        updated["questions"] = [{**updated["questions"][0], "number": 7}]
        save_request = self.factory.patch(
            f"/api/v1/tools/problem-review/reports/{report.id}/",
            {"version": 1, "title": "검수 완료 제목", "draft": updated},
            format="json",
        )
        save_response = ProblemReviewReportDetailView.as_view()(
            self._authenticate(save_request),
            report_id=report.id,
        )
        self.assertEqual(save_response.status_code, 200)
        self.assertEqual(save_response.data["version"], 2)
        self.assertEqual(save_response.data["draft"]["summary"]["one_line"], updated["summary"]["one_line"])
        self.assertEqual(len(save_response.data["draft"]["questions"]), 1)
        self.assertEqual(save_response.data["draft"]["questions"][0]["number"], 7)
        self.assertEqual(save_response.data["draft"]["questions"][0]["source_number"], 1)

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import AnswerKey, Exam, ExamAsset, ExamQuestion, Sheet
from apps.domains.exams.views.save_as_template_view import SaveAsTemplateView
from apps.domains.lectures.models import Lecture, Session


User = get_user_model()


class SaveAsTemplateViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Template Save",
            code="templatesave",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="templatesave-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="admin")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Lecture",
            name="Lecture",
            subject="MATH",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1회차")
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="원본 시험",
            description="설명",
            subject="MATH",
            exam_type=Exam.ExamType.REGULAR,
        )
        self.exam.sessions.add(self.session)

    def test_uses_requested_template_title(self):
        request = self.factory.post(
            f"/exams/{self.exam.id}/save-as-template/",
            {"title": "운영 템플릿명"},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SaveAsTemplateView.as_view()(request, exam_id=self.exam.id)

        self.assertEqual(response.status_code, 200)
        self.exam.refresh_from_db()
        self.assertIsNotNone(self.exam.template_exam_id)
        self.assertEqual(self.exam.template_exam.title, "운영 템플릿명")

    def test_copies_regular_exam_structure_before_linking_template(self):
        self.exam.allow_retake = True
        self.exam.max_attempts = 3
        self.exam.pass_score = 72
        self.exam.max_score = 96
        self.exam.answer_visibility = Exam.AnswerVisibility.AFTER_CLOSED
        self.exam.save(
            update_fields=[
                "allow_retake",
                "max_attempts",
                "pass_score",
                "max_score",
                "answer_visibility",
            ]
        )
        source_sheet = Sheet.objects.create(
            exam=self.exam,
            name="MAIN",
            total_questions=2,
            file="exams/sheets/original.pdf",
        )
        source_q1 = ExamQuestion.objects.create(
            sheet=source_sheet,
            number=1,
            score=3.5,
            image_key="questions/q1.png",
            region_meta={"x": 1, "y": 2, "w": 3, "h": 4},
        )
        source_q2 = ExamQuestion.objects.create(
            sheet=source_sheet,
            number=2,
            score=4.5,
            image_key="questions/q2.png",
        )
        AnswerKey.objects.create(
            exam=self.exam,
            answers={
                str(source_q1.id): "A",
                str(source_q2.id): "B",
                "legacy": "preserve",
            },
        )
        ExamAsset.objects.create(
            exam=self.exam,
            asset_type=ExamAsset.AssetType.PROBLEM_PDF,
            file_key="assets/problem.pdf",
            file_type="application/pdf",
            file_size=1234,
        )

        request = self.factory.post(
            f"/exams/{self.exam.id}/save-as-template/",
            {"title": "복제 템플릿"},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)

        response = SaveAsTemplateView.as_view()(request, exam_id=self.exam.id)

        self.assertEqual(response.status_code, 200)
        self.exam.refresh_from_db()
        template = self.exam.template_exam
        self.assertEqual(template.title, "복제 템플릿")
        self.assertTrue(template.allow_retake)
        self.assertEqual(template.max_attempts, 3)
        self.assertEqual(template.pass_score, 72)
        self.assertEqual(template.max_score, 96)
        self.assertEqual(template.answer_visibility, Exam.AnswerVisibility.AFTER_CLOSED)

        template_sheet = template.sheet
        self.assertEqual(template_sheet.total_questions, 2)
        self.assertEqual(template_sheet.file.name, source_sheet.file.name)
        copied_questions = list(template_sheet.questions.order_by("number"))
        self.assertEqual([q.number for q in copied_questions], [1, 2])
        self.assertNotEqual(copied_questions[0].id, source_q1.id)
        self.assertEqual(copied_questions[0].image_key, "questions/q1.png")
        self.assertEqual(copied_questions[0].region_meta["w"], 3)

        copied_answers = template.answer_key.answers
        self.assertEqual(copied_answers[str(copied_questions[0].id)], "A")
        self.assertEqual(copied_answers[str(copied_questions[1].id)], "B")
        self.assertNotIn(str(source_q1.id), copied_answers)
        self.assertEqual(copied_answers["legacy"], "preserve")

        copied_asset = template.assets.get(asset_type=ExamAsset.AssetType.PROBLEM_PDF)
        self.assertEqual(copied_asset.file_key, "assets/problem.pdf")
        self.assertEqual(copied_asset.file_size, 1234)

        self.assertEqual(self.exam.sheet.id, source_sheet.id)

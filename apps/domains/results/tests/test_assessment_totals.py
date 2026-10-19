from types import SimpleNamespace
from django.apps import apps
from django.test import TestCase, SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.domains.results.services.assessment_totals import total_config, selected_total
from apps.support.results.score_total_dependencies import read_total_config, save_total_config, CompositionConflict


class TotalArithmeticTests(SimpleTestCase):
    def test_selection_pending_zero_and_unassigned_are_distinct(self):
        options = [{"exam_id": n, "max_score": 30} for n in range(1, 5)]
        entries = [{"exam_id": 1, "block": {"score": 0}},
                   {"exam_id": 2, "block": {"score": None}},
                   {"exam_id": 3, "block": {"score": 29}}]
        total = selected_total(options, entries, [1, 2, 4], id_key="exam_id")
        self.assertEqual(total, {"score": 0.0, "max_score": 60.0, "selected_count": 2, "scored_count": 1, "complete": False})

    def test_provisional_and_not_submitted_never_become_final_zero(self):
        options = [{"exam_id": n, "max_score": 100} for n in [1, 2]]
        entries = [{"exam_id": 1, "block": {"score": 0, "meta": {"status": "NOT_SUBMITTED"}}},
                   {"exam_id": 2, "block": {"score": 50, "is_provisional": True}}]
        self.assertIsNone(selected_total(options, entries, [1, 2], id_key="exam_id")["score"])

    def test_completion_excluded_and_explicit_empty_stays_empty(self):
        homeworks = [{"homework_id": 1, "grading_mode": "SCORE"}, {"homework_id": 2, "grading_mode": "COMPLETION"}]
        self.assertEqual(total_config(SimpleNamespace(score_totals={}), [], homeworks)["homework_ids"], [1])
        self.assertEqual(total_config(SimpleNamespace(score_totals={"homework_ids": []}), [], homeworks)["homework_ids"], [])


class TotalConfigTests(TestCase):
    def setUp(self):
        self.tenant = apps.get_model("core", "Tenant").objects.create(name="Totals", code="totals")
        self.lecture = apps.get_model("lectures", "Lecture").objects.create(tenant=self.tenant, title="Totals", name="Totals", subject="SCIENCE")
        self.session = apps.get_model("lectures", "Session").objects.create(lecture=self.lecture, order=1, title="1차시")
        self.exam = apps.get_model("exams", "Exam").objects.create(tenant=self.tenant, title="Exam", exam_type="regular")
        self.exam.sessions.add(self.session)
        self.payload = {"expected_version": 0, "exam_ids": [self.exam.id], "homework_ids": []}

    def test_save_reload_and_conflict_preserve_selection(self):
        saved = save_total_config(session_id=self.session.id, tenant=self.tenant, payload=self.payload)
        self.assertEqual(saved["version"], 1)
        self.assertEqual(read_total_config(session_id=self.session.id, tenant=self.tenant), saved)
        with self.assertRaises(CompositionConflict):
            save_total_config(session_id=self.session.id, tenant=self.tenant, payload=self.payload)
        self.assertEqual(read_total_config(session_id=self.session.id, tenant=self.tenant), saved)

    def test_unrelated_assessment_and_duplicate_rejected(self):
        for ids in ([self.exam.id, self.exam.id], [self.exam.id + 1000]):
            with self.assertRaises(ValidationError):
                save_total_config(session_id=self.session.id, tenant=self.tenant, payload={**self.payload, "exam_ids": ids})

    def test_other_tenant_cannot_read_or_write(self):
        from django.http import Http404
        other = apps.get_model("core", "Tenant").objects.create(name="Other", code="totals-other")
        with self.assertRaises(Http404):
            read_total_config(session_id=self.session.id, tenant=other)
        with self.assertRaises(Http404):
            save_total_config(session_id=self.session.id, tenant=other, payload=self.payload)

    def test_composition_reorder_is_atomic_and_preserves_scores(self):
        Exam = apps.get_model("exams", "Exam")
        Homework = apps.get_model("homework_results", "Homework")
        second = Exam.objects.create(tenant=self.tenant, title="Second", exam_type="regular")
        second.sessions.add(self.session)
        scored = Homework.objects.create(tenant=self.tenant, session=self.session, title="Score")
        completion = Homework.objects.create(tenant=self.tenant, session=self.session, title="OX", grading_mode="COMPLETION")
        # A later invalid order must roll back an earlier valid exam reorder.
        with self.assertRaises(ValidationError):
            save_total_config(session_id=self.session.id, tenant=self.tenant, payload={
                **self.payload, "exam_order": [second.id, self.exam.id], "homework_order": [],
            })
        self.exam.refresh_from_db()
        self.session.refresh_from_db()
        self.assertEqual(self.exam.display_order, 0)
        self.assertEqual(self.session.score_totals, {})
        with self.assertRaises(ValidationError):
            save_total_config(session_id=self.session.id, tenant=self.tenant,
                              payload={**self.payload, "homework_ids": [completion.id]})
        save_total_config(session_id=self.session.id, tenant=self.tenant, payload={
            **self.payload, "homework_ids": [scored.id],
            "exam_order": [second.id, self.exam.id], "homework_order": [completion.id, scored.id],
        })
        self.exam.refresh_from_db()
        scored.refresh_from_db()
        self.assertEqual(self.exam.display_order, 0)
        self.assertEqual(scored.display_order, 0)
        local = read_total_config(session_id=self.session.id, tenant=self.tenant)
        self.assertEqual(local["exam_order"], [second.id, self.exam.id])
        self.assertEqual(local["homework_order"], [completion.id, scored.id])
        self.assertEqual(read_total_config(session_id=self.session.id, tenant=self.tenant)["homework_ids"], [scored.id])

    def test_api_staff_write_reload_and_student_denial(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.models import TenantMembership
        from apps.domains.results.views.session_total_config_view import SessionTotalConfigView

        factory = APIRequestFactory()
        staff = get_user_model().objects.create_user(username="totals-staff", tenant=self.tenant, is_staff=True)
        student = get_user_model().objects.create_user(username="totals-student", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=staff, role="admin")
        TenantMembership.ensure_active(tenant=self.tenant, user=student, role="student")
        view = SessionTotalConfigView.as_view()
        for user, method, expected in ((student, "put", 403), (student, "get", 403), (staff, "put", 200), (staff, "get", 200)):
            request = getattr(factory, method)("/", self.payload if method == "put" else {}, format="json")
            request.tenant = self.tenant
            force_authenticate(request, user=user)
            response = view(request, session_id=self.session.id)
            self.assertEqual(response.status_code, expected)
            if expected == 200:
                self.assertEqual(response.data["version"], 1)

    def test_student_summary_uses_selected_initial_scores_and_pending_assignments(self):
        from apps.support.results.score_total_dependencies import student_session_totals

        second = apps.get_model("exams", "Exam").objects.create(
            tenant=self.tenant, title="Pending", exam_type="regular", max_score=50,
        )
        second.sessions.add(self.session)
        homework = apps.get_model("homework_results", "Homework").objects.create(
            tenant=self.tenant, session=self.session, title="Homework", meta={"default_max_score": 20},
        )
        # The caller supplies only authorized first-attempt rows; an assigned
        # exam with no row must still keep the shared total incomplete.
        exams = [{"exam_id": self.exam.id, "enrollment_id": 987,
                  "session_id": self.session.id, "total_score": 80}]
        homeworks = [{"homework_id": homework.id, "enrollment_id": 987,
                      "session_id": self.session.id, "score": 0, "submission_state": "GRADED"}]
        summary = student_session_totals(tenant=self.tenant, exam_rows=exams, homework_rows=homeworks)[0]
        self.assertEqual(summary["exam_total"]["selected_count"], 2)
        self.assertFalse(summary["exam_total"]["complete"])
        self.assertEqual(summary["homework_total"]["score"], 0)
        self.assertTrue(summary["homework_total"]["complete"])
        save_total_config(session_id=self.session.id, tenant=self.tenant, payload={**self.payload, "homework_ids": [homework.id]})
        summary = student_session_totals(tenant=self.tenant, exam_rows=exams, homework_rows=homeworks)[0]
        self.assertEqual(summary["exam_total"]["score"], 80)
        self.assertTrue(summary["exam_total"]["complete"])

    def test_shared_exam_order_is_session_local_and_new_deleted_items_converge(self):
        Exam = apps.get_model("exams", "Exam")
        Session = apps.get_model("lectures", "Session")
        peer_session = Session.objects.create(lecture=self.lecture, order=2, title="Peer")
        second = Exam.objects.create(tenant=self.tenant, title="Second", exam_type="regular", display_order=10)
        self.exam.sessions.add(peer_session)
        second.sessions.add(self.session, peer_session)
        original_peer = read_total_config(session_id=peer_session.id, tenant=self.tenant)
        save_total_config(session_id=self.session.id, tenant=self.tenant, payload={**self.payload, "exam_order": [second.id, self.exam.id], "homework_order": []})
        self.assertEqual(read_total_config(session_id=peer_session.id, tenant=self.tenant), original_peer)
        self.assertEqual(list(Exam.objects.filter(id__in=[self.exam.id, second.id]).order_by("id").values_list("display_order", flat=True)), [0, 10])
        new = Exam.objects.create(tenant=self.tenant, title="New", exam_type="regular", display_order=0)
        new.sessions.add(self.session)
        self.assertEqual(read_total_config(session_id=self.session.id, tenant=self.tenant)["exam_order"], [second.id, self.exam.id, new.id])
        self.exam.sessions.remove(self.session)
        surviving = read_total_config(session_id=self.session.id, tenant=self.tenant)
        self.assertEqual(surviving["exam_order"], [second.id, new.id])
        self.assertEqual(surviving["exam_ids"], [])

    def test_corrupt_homework_tenant_and_template_never_enter_options(self):
        Homework = apps.get_model("homework_results", "Homework")
        other = apps.get_model("core", "Tenant").objects.create(name="Other HW", code="totals-other-hw")
        foreign = Homework.objects.create(tenant=other, session=self.session, title="Foreign")
        template = Homework.objects.create(tenant=self.tenant, homework_type="template", title="Template")
        # Deliberately bypass model validation to simulate a corrupt legacy link.
        Homework.objects.filter(id=template.id).update(session=self.session)
        for homework in (foreign, template):
            self.assertNotIn(homework.id, read_total_config(session_id=self.session.id, tenant=self.tenant)["homework_order"])
            with self.assertRaises(ValidationError):
                save_total_config(session_id=self.session.id, tenant=self.tenant, payload={**self.payload, "homework_ids": [homework.id]})

    def test_session_score_metadata_uses_local_order_and_scoped_homework_options(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.core.models import TenantMembership
        from apps.domains.results.views.session_scores_view import SessionScoresView
        Exam = apps.get_model("exams", "Exam")
        Homework = apps.get_model("homework_results", "Homework")
        second = Exam.objects.create(tenant=self.tenant, title="Second", exam_type="regular", display_order=20)
        second.sessions.add(self.session)
        first_hw = Homework.objects.create(tenant=self.tenant, session=self.session, title="First")
        second_hw = Homework.objects.create(tenant=self.tenant, session=self.session, title="Second")
        other = apps.get_model("core", "Tenant").objects.create(name="Corrupt", code="totals-corrupt")
        Homework.objects.create(tenant=other, session=self.session, title="Foreign")
        template = Homework.objects.create(tenant=self.tenant, homework_type="template", title="Template")
        Homework.objects.filter(id=template.id).update(session=self.session)
        save_total_config(session_id=self.session.id, tenant=self.tenant, payload={
            **self.payload, "homework_ids": [first_hw.id],
            "exam_order": [second.id, self.exam.id], "homework_order": [second_hw.id, first_hw.id],
        })
        staff = get_user_model().objects.create_user(username="local-order-reader", tenant=self.tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=staff, role="admin")
        request = APIRequestFactory().get("/")
        request.tenant = self.tenant
        force_authenticate(request, user=staff)
        response = SessionScoresView.as_view()(request, session_id=self.session.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["exam_id"] for row in response.data["meta"]["exams"]], [second.id, self.exam.id])
        self.assertEqual([row["homework_id"] for row in response.data["meta"]["homeworks"]], [second_hw.id, first_hw.id])
        self.assertEqual([row["display_order"] for row in response.data["meta"]["exams"]], [0, 1])

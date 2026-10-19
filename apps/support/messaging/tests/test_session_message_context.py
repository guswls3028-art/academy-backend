import datetime
from copy import deepcopy
from django.apps import apps
from django.test import TestCase
from apps.core.models import Tenant
from apps.support.messaging.session_message_context import resolve_session_message_data, SessionMessageContextError

Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")
Section = apps.get_model("lectures", "Section")


class SessionMessageContextTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Session blocks", code="sessionblocks")
        self.lecture = Lecture.objects.create(tenant=self.tenant, name="Math", title="Math", subject="MATH")
        self.current = Session.objects.create(lecture=self.lecture, order=1, title="미분 종합", date=datetime.date(2026, 10, 19))
        self.next = Session.objects.create(lecture=self.lecture, order=4, title="도형 복습", date=datetime.date(2026, 10, 26))
        self.later = Session.objects.create(lecture=self.lecture, order=8, title="실전 평가", date=datetime.date(2026, 11, 2))
        self.data = {"session_id": self.current.id, "student_ids": [],
                     "raw_body": "#{현재차시명} #{현재차시날짜} / #{다음차시명} #{다음차시날짜} / #{2차시후명}",
                     "alimtalk_extra_vars": {"다음차시명": "untrusted"},
                     "alimtalk_extra_vars_per_student": {"1": {"_body_subst": "#{다음차시명} #{다음차시날짜}"}}}

    def test_actual_title_order_dates_and_shared_personalized_preview(self):
        original = deepcopy(self.data)
        result = resolve_session_message_data(tenant=self.tenant, data=self.data)
        self.assertEqual(self.data, original)
        self.assertEqual(result["alimtalk_extra_vars"]["다음차시명"], "도형 복습")
        self.assertEqual(result["alimtalk_extra_vars"]["2차시후명"], "실전 평가")
        self.assertEqual(result["alimtalk_extra_vars_per_student"]["1"]["_body_subst"], "도형 복습 2026-10-26")
        self.assertEqual(result["alimtalk_extra_vars"]["강의날짜"], "2026-10-19")

    def test_missing_date_future_and_source_are_explicit_errors(self):
        self.next.date = None
        self.next.save(update_fields=["date"])
        with self.assertRaisesRegex(SessionMessageContextError, "날짜"):
            resolve_session_message_data(tenant=self.tenant, data=self.data)
        with self.assertRaisesRegex(SessionMessageContextError, "차시 뒤"):
            resolve_session_message_data(tenant=self.tenant, data={"session_id": self.current.id, "raw_body": "#{9차시후명}"})
        with self.assertRaisesRegex(SessionMessageContextError, "차시 성적"):
            resolve_session_message_data(tenant=self.tenant, data={"raw_body": "#{다음차시명}"})

    def test_tenant_section_lecture_and_recipient_fail_closed(self):
        other = Tenant.objects.create(name="Other", code="sessionblocksother")
        with self.assertRaises(SessionMessageContextError):
            resolve_session_message_data(tenant=other, data=self.data)
        section = Section.objects.create(tenant=self.tenant, lecture=self.lecture, label="A", day_of_week=0, start_time=datetime.time(17))
        Session.objects.create(lecture=self.lecture, section=section, order=2, title="다른 반")
        second_lecture = Lecture.objects.create(tenant=self.tenant, name="Other", title="Other", subject="MATH")
        Session.objects.create(lecture=second_lecture, order=2, title="다른 강의")
        result = resolve_session_message_data(tenant=self.tenant, data=self.data)
        self.assertEqual(result["alimtalk_extra_vars"]["다음차시명"], "도형 복습")
        with self.assertRaisesRegex(SessionMessageContextError, "등록되지 않은 학생"):
            resolve_session_message_data(tenant=self.tenant, data={**self.data, "student_ids": [999999]})

    def test_send_date_and_class_date_remain_distinct(self):
        from datetime import timezone
        result = resolve_session_message_data(tenant=self.tenant, data={
            **self.data, "scheduled_send_at": datetime.datetime(2026, 10, 20, 6, tzinfo=timezone.utc)})
        self.assertEqual(result["alimtalk_extra_vars"]["날짜"], "2026-10-20")
        self.assertEqual(result["alimtalk_extra_vars"]["강의날짜"], "2026-10-19")

    def test_preflight_reports_recovery_and_send_path_denies_missing_date(self):
        from types import SimpleNamespace
        from apps.domains.messaging.services.preflight import build_send_preflight
        from apps.domains.messaging.views.send_views import SendMessageView
        self.next.date = None
        self.next.save(update_fields=["date"])
        response = build_send_preflight(self.tenant, self.data)
        issue = next(i for i in response["blockers"] if i["code"] == "session_context_missing")
        self.assertIn("날짜", issue["detail"])
        denied = SendMessageView()._send(SimpleNamespace(tenant=self.tenant), self.data, None)
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(denied.data["code"], "session_context_missing")

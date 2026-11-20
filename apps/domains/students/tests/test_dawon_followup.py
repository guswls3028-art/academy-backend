from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import TenantMembership
from apps.domains.clinic.contracts import DAWON_CLINIC_LOCATION as LOCATION, DAWON_CLINIC_MEMO as MEMO
from apps.domains.clinic.models import Session as ClinicSession, SessionParticipant
from apps.domains.students.models import ClassGroup, DawonCounselAppointment
from apps.domains.lectures.models import Lecture
from apps.domains.students.services.dawon_demo_learner import learner_topics
from apps.domains.students.services.dawon_followup import followups, is_sample
from apps.domains.students.views.demo_operations import DawonDemoOperationsView
from apps.domains.students.tests import test_dawon_exam_reports as reports


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DawonFollowupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        reports.DawonExamReportTests.setUpTestData.__func__(cls)
        cls.group = ClassGroup.objects.get(tenant=cls.tenant)
        cls.course = Lecture.objects.get(tenant=cls.tenant, title__contains="HYPER")
        cls.admin = get_user_model().objects.create_user(tenant=cls.tenant, username="t719_dawon-demo-admin")
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.admin, role="admin")

    def call(self, method, data, actor=None, role="admin"):
        request = getattr(APIRequestFactory(), method)("/api/v1/students/demo-operations/", data, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=actor or self.admin, token={"dawon_demo_role": role})
        return DawonDemoOperationsView.as_view()(request)

    def test_full_roster_and_followup_isolation(self):
        roster = list(self.group.students.select_related("user"))
        self.assertEqual(len(roster), 33)
        self.assertTrue(all(is_sample(s) for s in roster))
        ids = {"course":self.course.pk,"student":self.student.pk}
        other = next(s for s in roster if s.pk != self.student.pk)
        day = timezone.localdate()+timedelta(days=3)
        appointment = {**ids,"day":day.isoformat(),"followup":"counsel"}
        self.assertEqual(self.call("patch",appointment,actor=self.user,role="parent").status_code,404)
        for _ in range(2):
            response=self.call("patch",appointment)
            self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(DawonCounselAppointment.objects.count(),1)
        response=self.call("get",ids)
        self.assertEqual(response.status_code,200,response.data)
        self.assertTrue(response.data["exams"])
        self.assertTrue(response.data["homework"])
        self.assertIn(day,response.data["counsel"])
        self.assertTrue(learner_topics(tenant=self.tenant,student=self.student)["counsels"])
        self.assertFalse(learner_topics(tenant=self.tenant,student=other)["counsels"])
        self.assertTrue(followups(self.tenant,[self.course.pk]))
        self.assertFalse(followups(self.tenant,[]))
        slot=ClinicSession.objects.create(tenant=self.tenant,title="수학",memo=MEMO,location=LOCATION,
            date=day,start_time="17:00",max_participants=4)
        for _ in range(2):
            response=self.call("patch",{**ids,"followup":"clinic","session":slot.pk})
            self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(SessionParticipant.objects.filter(session=slot,student=self.student,status="booked").count(),1)
        self.assertTrue(learner_topics(tenant=self.tenant,student=self.student)["clinics"][0]["booked"])
        self.assertFalse(learner_topics(tenant=self.tenant,student=other)["clinics"][0]["booked"])
        self.assertEqual(self.call("get",{"alerts":"1","group":self.group.pk}).status_code,200)

    def test_past_course_keeps_readable_learning_record_and_no_new_followup(self):
        self.course.is_active = False
        self.course.save(update_fields=["is_active"])
        ids = {"course":self.course.pk, "student":self.student.pk}
        record = self.call("get", ids)
        self.assertEqual(record.status_code, 200)
        self.assertTrue(record.data["exams"])
        self.assertFalse(record.data["can_follow"])
        self.assertEqual(self.call("patch", {**ids,"day":timezone.localdate(),"followup":"counsel"}).status_code,404)

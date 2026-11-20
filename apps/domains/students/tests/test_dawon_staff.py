from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.staffs.models import PayrollSnapshot, Staff, WorkMonthLock, WorkRecord
from apps.domains.students.services.dawon_staff import EMPLOYEE, FIXTURES, PREFIX, TEACHER, WORK_MARK
from apps.domains.students.views.dawon_staff import DawonStaffClockView, DawonStaffView


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class DawonStaffFlowTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(id=719, code="dawon", name="다원")
        self.other = Tenant.objects.create(code="other", name="다른 학원")
        self.people = {}
        for role, name in (("admin","체험 관리자"), ("teacher",FIXTURES[TEACHER][0]), ("employee",FIXTURES[EMPLOYEE][0])):
            user = get_user_model().objects.create_user(username=PREFIX + role, tenant=self.tenant, name=name)
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role="staff" if role == "employee" else role)
            self.people[role] = user
        call_command("seed_dawon_staff", stdout=StringIO())

    def request(self, view, role, method="get", data=None, tenant=None, user=None):
        request = getattr(APIRequestFactory(),method)("/",data or {},format="json")
        request.tenant = tenant or self.tenant
        force_authenticate(request,user=user or self.people[role],token={"dawon_demo_role":role})
        return view.as_view()(request)

    def test_seed_and_isolation(self):
        before = WorkRecord.objects.filter(tenant=self.tenant,memo=WORK_MARK).count()
        call_command("seed_dawon_staff", stdout=StringIO())
        self.assertEqual(WorkRecord.objects.filter(tenant=self.tenant,memo=WORK_MARK).count(),before)
        real = Staff.objects.create(tenant=self.tenant,name="별도 직원")
        result = self.request(DawonStaffView,"admin")
        self.assertEqual(result.status_code,200)
        self.assertEqual(len(result.data["staff"]),len(FIXTURES))
        self.assertGreaterEqual(len([key for key in FIXTURES if key.startswith(PREFIX + "instructor-")]), 12)
        from apps.support.lectures.class_group_cycles import assigned_class_group_ids
        from apps.domains.students.models import ClassGroup
        assigned = get_user_model().objects.get(tenant=self.tenant, username=PREFIX + "instructor-09")
        self.assertFalse(assigned.is_active)
        self.assertEqual(list(ClassGroup.objects.filter(pk__in=assigned_class_group_ids(self.tenant, assigned))
                              .values_list("name",flat=True)), ["D10 체험 수학반"])
        self.assertNotIn(real.pk,[s["id"] for s in result.data["staff"]])
        for method, body in (("get",{"staff":real.pk,"month":"2026-11"}),("patch",{"staff":real.pk,"position":"ASSISTANT"})):
            self.assertEqual(self.request(DawonStaffView,"admin",method,body).status_code,404)
        for view, role in ((DawonStaffView,"teacher"),(DawonStaffClockView,"admin")):
            self.assertEqual(self.request(view,role).status_code,404)
        self.assertEqual(self.request(DawonStaffView,"admin",tenant=self.other).status_code,404)
        self.assertEqual(self.request(DawonStaffView,"admin",user=self.people["teacher"]).status_code,404)

    def test_clock_and_payroll_and_position(self):
        staff = Staff.objects.get(user=self.people["teacher"])
        start = datetime(2026,11,20,17,tzinfo=ZoneInfo("Asia/Seoul"))
        with patch("apps.domains.students.views.dawon_staff.timezone.localtime", return_value=start):
            clock = self.request(DawonStaffClockView,"teacher","post",{"action":"start"})
            self.assertEqual(clock.status_code,200,clock.data)
            self.assertEqual(self.request(DawonStaffClockView,"teacher","post",{"action":"start"}).status_code,409)
        pk = clock.data["current"]["id"]
        self.assertEqual(self.request(DawonStaffClockView,"teacher").data["current"]["id"],pk)
        self.assertEqual(self.request(DawonStaffClockView,"teacher","post",{"action":"end","record":pk+1}).status_code,400)
        with patch("apps.domains.students.views.dawon_staff.timezone.localtime",return_value=start + timedelta(hours=2)):
            end = self.request(DawonStaffClockView,"teacher","post",{"action":"end","record":pk})
        self.assertEqual(end.status_code,200,end.data)
        self.assertIsNone(end.data["current"])
        month = self.request(DawonStaffView,"admin",data={"month":"2026-11","staff":staff.pk})
        self.assertEqual(month.status_code,200,month.data)
        row = next(r for r in month.data["records"] if r["id"] == pk)
        self.assertEqual(row["amount"],40000)
        self.assertEqual(month.data["payroll"]["total"],sum(r["amount"] or 0 for r in month.data["records"]))
        employee = Staff.objects.get(user=self.people["employee"])
        self.assertEqual(self.request(DawonStaffView,"admin","patch",{"staff":employee.pk,"position":"ASSISTANT"}).status_code,200)
        employee.refresh_from_db()
        self.assertEqual(employee.position,"ASSISTANT")
        self.assertFalse(employee.is_manager)


    def test_month_lock_snapshot_and_missing_state(self):
        staff = Staff.objects.get(user=self.people["employee"])
        period = {"staff": staff, "tenant": self.tenant, "year": 2026, "month": 1}
        WorkMonthLock.objects.create(**period, is_locked=True)
        query = {"staff": staff.pk, "month": "2026-01"}
        self.assertEqual(self.request(DawonStaffView,"admin",data=query).data["payroll"]["state"],"missing")
        PayrollSnapshot.objects.create(**period,work_hours=5,work_amount=100000,approved_expense_amount=5000,total_amount=105000)
        result = self.request(DawonStaffView,"admin",data=query).data["payroll"]
        self.assertEqual((result["state"], result["total"]),("confirmed",105000))
        self.assertEqual(self.request(DawonStaffView,"admin",data={**query,"month":"2026-13"}).status_code,400)

    def test_admin_instructor_directory_and_delegation(self):
        from django.urls import resolve
        from apps.domains.students.models import ClassGroup

        def request(method,data,actor=None,role="admin",suffix=""):
            path="/api/v1/students/class-groups/"+suffix
            req=getattr(APIRequestFactory(),method)(path,data,format="json")
            req.tenant=self.tenant
            force_authenticate(req,user=actor or self.people["admin"],token={"dawon_demo_role":role})
            view=resolve(path)
            return view.func(req,**view.kwargs)

        options=request("get",{"view":"instructors"})
        self.assertEqual(options.status_code,200,options.data)
        self.assertGreaterEqual(len(options.data),10)
        self.assertTrue(all(set(r)=={"id","name"} for r in options.data))
        chosen=next(r for r in options.data if r["name"]=="체험 수학강사 01")
        person=get_user_model().objects.get(pk=chosen["id"])
        self.assertFalse(person.is_active)
        self.assertFalse(person.has_usable_password())
        form={"name":"강사 인계반","kind":"union","track":"HYPER","assigned_teacher_id":person.pk}
        created=request("post",form)
        self.assertEqual(created.status_code,201,created.data)
        self.assertEqual(created.data["assigned_teacher"],chosen)
        self.assertEqual(request("get",{},suffix=f'{created.data["id"]}/').data["assigned_teacher"],chosen)
        self.assertFalse(get_user_model().objects.get(pk=person.pk).is_active)
        self.assertEqual(ClassGroup.objects.get(pk=created.data["id"]).created_by,self.people["admin"])
        self.assertEqual(request("get",{"view":"instructors"},actor=self.people["teacher"],role="teacher").status_code,403)
        fixture=Staff.objects.get(tenant=self.tenant,user=person)
        for pos,active in [("INSTRUCTOR",False),("ASSISTANT",True)]:
            fixture.position=pos; fixture.is_active=active; fixture.save(update_fields=["position","is_active"])
            self.assertNotIn(chosen,request("get",{"view":"instructors"}).data)
            self.assertEqual(request("post",{**form,"name":"권한 확인"}).status_code,400)
        other=Tenant.objects.create(code="other-placement",name="별도")
        outsider=get_user_model().objects.create_user(tenant=other,username="other-placement-instructor")
        self.assertEqual(request("post",{**form,"name":"별도 지점","assigned_teacher_id":outsider.pk}).status_code,400)

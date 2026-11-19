from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import ClassGroup, Student


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class ClassGroupTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(id=719, code="dawon", name="다원")
        self.other = Tenant.objects.create(code="other-qa", name="테스트")
        self.actor = get_user_model().objects.create_user(username="review-owner", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.actor, role="owner")
        self.factory = APIRequestFactory()
        self.root = "/api/v1/students/class-groups/"

    def call(self, method, suffix="", payload=None, tenant=None, actor=None):
        route = self.root + suffix
        request = getattr(self.factory, method)(route, payload or {}, format="json")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=actor or self.actor)
        view = resolve(route)
        return view.func(request, **view.kwargs)

    def pupil(self, name, school, tenant=None):
        tenant = tenant or self.tenant
        user = get_user_model().objects.create_user(username="pupil-" + name, tenant=tenant)
        TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
        return Student.objects.create(tenant=tenant, user=user, name=name, ps_number=name, omr_code=str(user.pk).zfill(8),
                                      school_type="HIGH", high_school=school)

    def test_school_roster_atomic_and_native_lecture(self):
        one = self.pupil("first", " Ａ고 ")
        wrong = self.pupil("second", "B고")
        foreign = self.pupil("third", "A고", self.other)
        group = self.call("post", payload={"name":"중간반", "kind":"school", "school":"A고"})
        self.assertEqual(group.status_code, 201, group.data)
        base = f"{group.data['id']}/"
        self.assertEqual([v["id"] for v in self.call("get", base+"candidates/").data], [one.pk])
        for bad in [wrong, foreign]:
            response = self.call("patch", base+"members/", {"add":[one.pk, bad.pk]})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.call("get", base).data["student_count"], 0)
        self.assertEqual(self.call("patch", base+"members/", {"add":[one.pk, one.pk]}).status_code, 200)
        fields={"title":"새 수학 강의", "period":"midterm", "start_date":"2027-01-01", "end_date":"2027-02-01"}
        self.assertEqual(self.call("post", base+"lectures/", {**fields,"end_date":"2026-01-01"}).status_code, 400)
        created=self.call("post", base+"lectures/", fields)
        self.assertEqual(created.status_code, 201, created.data)
        self.assertTrue(Enrollment.objects.filter(tenant=self.tenant, lecture_id=created.data["id"], student=one, status="ACTIVE").exists())
        self.assertEqual(self.call("get", base).data["lectures"][0]["student_count"], 1)
        Student.objects.filter(pk=one.pk).update(deleted_at=timezone.now())
        self.assertEqual(self.call("post", base+"lectures/", {**fields,"title":"다음 강의"}).status_code, 400)
        self.assertEqual(self.call("patch", base+"members/", {"remove":[one.pk]}).data["student_count"], 0)

    def test_union_exam_school_snapshot_and_candidate_eligibility(self):
        a = self.pupil("first", " Ａ고 ")
        b = self.pupil("second", "B고")
        group = self.call("post", payload={"name":"연합반", "kind":"union", "track":"SKY"})
        base = f"{group.data['id']}/"
        self.assertEqual([r["id"] for r in self.call("get", base+"candidates/", {"search":"B고"}).data], [b.pk])
        self.call("patch", base+"members/", {"add":[a.pk, b.pk]})
        fields = {"title":"여름", "period":"vacation", "start_date":"2027-01-01", "end_date":"2027-03-01"}
        self.assertEqual(self.call("post", base+"lectures/", fields).data["student_count"], 2)
        for period, student, school in [("midterm", a, "A고"), ("final", b, "B고")]:
            exam = {**fields, "period":period, "title":period}
            for invalid in ["", "C고"]:
                self.assertEqual(self.call("post", base+"lectures/", {**exam, "school":invalid}).status_code, 400)
            new = self.call("post", base+"lectures/", {**exam, "school":school})
            self.assertEqual(new.status_code, 201, new.data)
            self.assertEqual(new.data["school"], school)
            self.assertEqual(list(Enrollment.objects.filter(lecture_id=new.data["id"]).values_list("student_id", flat=True)), [student.pk])
        self.assertEqual({r["school"] for r in self.call("get", base).data["lectures"]}, {"", "A고", "B고"})
        TenantMembership.objects.filter(user=a.user, tenant=self.tenant).update(is_active=False)
        Student.objects.filter(pk=b.pk).update(is_managed=False)
        self.assertTrue(all(not r["eligible"] for r in self.call("get", base).data["students"]))
        self.assertEqual(self.call("patch", base+"members/", {"remove":[a.pk,b.pk]}).status_code, 200)
        inactive = self.pupil("inactive", "A고")
        type(inactive.user).objects.filter(pk=inactive.user_id).update(is_active=False)
        self.assertEqual(self.call("get", base+"candidates/").data, [])
        self.assertEqual(self.call("patch", base+"members/", {"add":[inactive.pk]}).status_code, 400)

    def test_default_closed_and_staff_only(self):
        with override_settings(DAWON_REVIEW_CLASS_GROUPS=False):
            self.assertEqual(self.call("get").status_code, 404)
        outsider=get_user_model().objects.create_user(username="other-owner", tenant=self.other)
        TenantMembership.ensure_active(tenant=self.other, user=outsider, role="owner")
        self.assertEqual(self.call("get", tenant=self.other, actor=outsider).status_code, 404)
        self.assertEqual(self.call("get", actor=outsider).status_code, 403)
        student_user = get_user_model().objects.create_user(username="student-review", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=student_user, role="student")
        self.assertEqual(self.call("get", actor=student_user).status_code, 403)
        with self.subTest("school metadata forbidden for union"):
            self.assertEqual(self.call("post", payload={"name":"반","kind":"union","track":"SKY","school":"A고"}).status_code,400)
        for track in ["1%","HYPER","SKY"]:
            self.assertEqual(self.call("post", payload={"name":track,"kind":"union","track":track}).status_code,201)
        self.assertEqual(ClassGroup.objects.filter(tenant=self.tenant).count(),3)
    def test_late_enrollment_filters_scope_and_preserves_all_prior_statuses(self):
        initial = self.pupil("first", "A고")
        late = self.pupil("second", "A고")
        wrong = self.pupil("third", "B고")
        outsider = self.pupil("fourth", "A고")
        Student.objects.filter(pk=late.pk).update(parent_phone="010-1111-2222", phone="01033334444")
        group = self.call("post", payload={"name": "연합", "kind": "union", "track": "SKY"}).data
        base = f"{group['id']}/"
        query = {"search": late.name, "exact": "true", "parent_phone": "01011112222", "phone": "010-3333-4444"}
        self.assertEqual([s["id"] for s in self.call("get", base+"candidates/", query).data], [late.pk])
        self.call("patch", base+"members/", {"add": [initial.pk]})
        created = self.call("post", base+"lectures/", {"title": "중간", "period": "midterm", "school": "A고",
            "start_date": "2027-01-01", "end_date": "2027-02-01", "name": "강사", "subject": "국어", "description": "설명",
            "schedule_mode": "SINGLE", "schedule_defaults": []})
        self.assertEqual(created.status_code, 201, created.data)
        lecture = Enrollment.objects.get(student=initial).lecture
        self.assertEqual((lecture.name, lecture.subject, lecture.description), ("강사", "국어", "설명"))
        metadata = self.call("get", f"for-lecture/{lecture.pk}/")
        self.assertEqual(metadata.data, {"group_id": group["id"], "name": "연합", "school": "A고"})
        previous = Enrollment.objects.get(lecture=lecture, student=initial)
        previous.status = "INACTIVE"
        previous.save(update_fields=["status"])
        self.call("patch", base+"members/", {"add": [late.pk, wrong.pk]})
        route = base+f"lectures/{lecture.pk}/late-students/"
        self.assertEqual([s["id"] for s in self.call("get", route, query).data], [late.pk])
        self.assertEqual(self.call("get", route, {**query, "offset": 1}).data, [])
        self.assertEqual(self.call("get", route, {**query, "parent_phone": "0"}).data, [])
        for bad in (initial, wrong, outsider):
            self.assertEqual(self.call("post", route, {"student_ids": [late.pk, bad.pk]}).status_code, 400)
            self.assertFalse(Enrollment.objects.filter(lecture=lecture, student=late).exists())
        self.assertEqual(self.call("post", route, {"student_ids": [late.pk]}).data, {"created_count": 1, "student_ids": [late.pk]})
        self.assertEqual(self.call("post", route, {"student_ids": [late.pk]}).status_code, 400)
        previous.refresh_from_db()
        self.assertEqual(previous.status, "INACTIVE")

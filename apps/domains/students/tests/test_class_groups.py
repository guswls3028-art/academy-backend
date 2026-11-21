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

    def call(self, method, suffix="", payload=None, tenant=None, actor=None, token=None):
        route = self.root + suffix
        request = getattr(self.factory, method)(route, payload or {}, format="json")
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=actor or self.actor, token=token)
        view = resolve(route)
        return view.func(request, **view.kwargs)

    def pupil(self, name, school, tenant=None):
        tenant = tenant or self.tenant
        user = get_user_model().objects.create_user(username="pupil-" + name, tenant=tenant)
        TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
        return Student.objects.create(tenant=tenant, user=user, name=name, ps_number=name, omr_code=str(user.pk).zfill(8),
                                      school_type="HIGH", high_school=school)

    def test_director_directory_and_paged_picker_keep_eligible_scope(self):
        admin = get_user_model().objects.create_user(tenant=self.tenant, username="t719_dawon-demo-admin")
        TenantMembership.ensure_active(tenant=self.tenant, user=admin, role="admin")
        token = {"dawon_demo_role": "admin"}
        def get(path="", **query):
            return self.call("get", path, query, actor=admin, token=token)
        students = [self.pupil("학생 "+str(n), "샘플고") for n in (10, 2, 1)]
        Student.objects.filter(pk__in=[s.pk for s in students]).update(grade=2, parent_phone="010-1234-5678")
        group = ClassGroup.objects.create(tenant=self.tenant, name="내신반", kind="school", school="샘플고")
        group.students.add(students[0])
        other = self.pupil("다른 학교", "다른고")
        self.pupil("다른 학원", "샘플고", self.other)
        url = f"{group.pk}/candidates/"
        data = get(url, page_info=1, search="1234-5678", grade=2, offset=1, limit=1).data
        self.assertEqual((data["count"], [r["id"] for r in data["results"]]), (2, [students[1].pk]))
        self.assertEqual(get(url, page_info=1, grade=3).data["count"], 0)
        self.assertIsInstance(get(url).data, list)
        self.assertEqual(get(view="students", search="학생", limit=2).data["count"], 3)
        self.assertEqual(get(view="students", search="학생1").data["count"], 2)
        self.assertEqual(get(view="students", id=students[0].pk).data["results"][0]["groups"],
                         [{"id":group.pk, "name":group.name}])
        self.assertEqual(get(view="students", id=other.pk).data["results"][0]["groups"], [])
        self.assertEqual(self.call("get", "", {"view":"students"}).status_code, 403)
        self.assertEqual(get(url, page_info=1, grade="invalid").status_code, 400)
        from apps.domains.lectures.models import Lecture
        course = Lecture.objects.create(tenant=self.tenant, name="검수", title="미편성 강의")
        Enrollment.objects.create(tenant=self.tenant, lecture=course, student=other, status="ACTIVE")
        Enrollment.objects.create(tenant=self.tenant, lecture=course, student=students[0], status="INACTIVE")
        self.assertEqual([r["id"] for r in get(view="students", lecture=course.pk).data["results"]], [other.pk])
        self.assertEqual(get(view="students", lecture=course.pk, group=group.pk).data["count"], 0)

    def test_director_picker_counts_all_candidates_before_paging(self):
        admin = get_user_model().objects.create_user(tenant=self.tenant, username="t719_dawon-demo-admin")
        TenantMembership.ensure_active(tenant=self.tenant, user=admin, role="admin")
        group = ClassGroup.objects.create(tenant=self.tenant, name="테스트", kind="union", track="SKY")
        path = f"{group.pk}/candidates/"
        for number in reversed(range(1, 54)):
            self.pupil(f"학생 {number}", "학원고")
        params = {"page_info": 1, "search": "학 원 고", "offset": 50}
        self.assertEqual(self.call("get", path, params).status_code, 403)
        response = self.call("get", path, params, actor=admin, token={"dawon_demo_role": "admin"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 53)
        self.assertEqual([row["name"] for row in response.data["results"]], ["학생 51", "학생 52", "학생 53"])

    def test_demo_admin_group_creation_assignment_and_member_course_projection(self):
        from apps.domains.students.models import ClassGroupLecture
        from apps.domains.lectures.models import Lecture
        from apps.core.authentication import _validate_dawon_demo_access
        from rest_framework.exceptions import PermissionDenied

        admin = get_user_model().objects.create_user(tenant=self.tenant, username="t719_dawon-demo-admin")
        teacher = get_user_model().objects.create_user(tenant=self.tenant, username="t719_dawon-demo-teacher")
        TenantMembership.ensure_active(tenant=self.tenant,user=admin,role="admin")
        TenantMembership.ensure_active(tenant=self.tenant,user=teacher,role="teacher")
        token = {"dawon_demo_role":"admin","dawon_demo_until":timezone.now().timestamp()+900}
        def admin_call(method,suffix="",data=None):
            return self.call(method,suffix,data,actor=admin,token=token)
        from apps.domains.staffs.models import Staff
        from apps.domains.students.services.dawon_staff import FIXTURES, TEACHER
        Staff.objects.create(tenant=self.tenant,user=teacher,name=FIXTURES[TEACHER][0],position="INSTRUCTOR")
        form = {"name":"원장 개설반","kind":"union","track":"HYPER","assigned_teacher_id":teacher.pk}
        created = admin_call("post",data=form)
        self.assertEqual(created.status_code,201,created.data)
        group = ClassGroup.objects.get(pk=created.data["id"])
        self.assertEqual((group.created_by,group.assigned_teacher),(admin,teacher))
        self.assertIn(group.pk,[g["id"] for g in self.call("get",actor=teacher,token={"dawon_demo_role":"teacher"}).data])
        self.assertEqual(self.call("get",actor=self.actor,token=token).status_code,403)
        prefix = f"{group.pk}/"
        student = self.pupil("하나","학교")
        peer = self.pupil("둘","학교")
        self.assertEqual(admin_call("get",prefix+"candidates/").status_code,200)
        members = {"add":[student.pk,peer.pk],"remove":[]}
        self.assertEqual(admin_call("patch",prefix+"members/",members).status_code,200)
        course=Lecture.objects.create(tenant=self.tenant,title="내 수업",name="교사",subject="수학",color="#3820ab",chip_label="심화")
        other=Lecture.objects.create(tenant=self.tenant,title="다른 반",name="교사",subject="수학")
        ClassGroupLecture.objects.create(group=group,lecture=course,period="vacation")
        for lecture in (course,other):
            Enrollment.objects.create(tenant=self.tenant,lecture=lecture,student=student,status="ACTIVE")
        own_course = admin_call("get", "", {"view": "students", "id": student.pk}).data["results"][0]["courses"][0]
        group_course = admin_call("get", prefix).data["lectures"][0]
        for item in (own_course, group_course):
            self.assertEqual((item["color"], item["chip_label"]), ("#3820ab", "심화"))
        rows=admin_call("get",prefix).data["students"]
        self.assertEqual({r["id"]:r["course_ids"] for r in rows},{student.pk:[course.pk],peer.pk:[]})
        response=admin_call("patch",prefix+"members/",{"add":[],"remove":[student.pk]})
        self.assertEqual([r["id"] for r in response.data["students"]],[peer.pk])
        self.assertTrue(Enrollment.objects.filter(student=student,lecture=course,status="ACTIVE").exists())
        for method, route, allowed in [("get","",True),("post","",True),("get",prefix,True),
            ("get",prefix+"candidates/",True),("patch",prefix+"members/",True),("delete",prefix,False),
            ("patch",prefix,True),("post",prefix+"lectures/",True),("get","teaching/",False),
            ("post",prefix+"candidates/",False),("get","0/",False)]:
            req=getattr(self.factory,method)(self.root+route)
            req.tenant=self.tenant
            if allowed:
                _validate_dawon_demo_access(req,token)
                req.tenant=self.other
            with self.assertRaises(PermissionDenied):
                _validate_dawon_demo_access(req,token)

    def test_reassignment_preserves_past_and_explicit_instructors_and_new_course_ownership(self):
        from datetime import timedelta
        from apps.domains.lectures.models import Lecture
        from apps.domains.staffs.models import Staff
        from apps.domains.students.services.dawon_staff import FIXTURES, PREFIX, TEACHER
        from apps.domains.students.models import ClassGroupLecture
        admin=get_user_model().objects.create_user(tenant=self.tenant,username=PREFIX+"admin")
        TenantMembership.ensure_active(tenant=self.tenant,user=admin,role="admin")
        people=[]
        for key in (TEACHER,PREFIX+"instructor-01",PREFIX+"instructor-02"):
            user=get_user_model().objects.create_user(tenant=self.tenant,username=key,name=FIXTURES[key][0],is_active=key==TEACHER)
            TenantMembership.ensure_active(tenant=self.tenant,user=user,role="teacher")
            Staff.objects.create(tenant=self.tenant,user=user,name=user.name,position="INSTRUCTOR")
            people.append(user)
        old,new,manual=people
        group=ClassGroup.objects.create(tenant=self.tenant,assigned_teacher=old,name="인계반",kind="union",track="HYPER")
        today=timezone.localdate()
        def course(title,start,end,teacher):
            lec=Lecture.objects.create(tenant=self.tenant,title=title,name=teacher.name,subject="수학",
                    start_date=today+timedelta(days=start),end_date=today+timedelta(days=end))
            return ClassGroupLecture.objects.create(group=group,lecture=lec,period="vacation",reference_teacher=teacher)
        past=course("지난 수업",-45,-20,old)
        current=course("진행 수업",-2,5,old)
        upcoming=course("예정 수업",3,9,old)
        override=course("별도 수업",7,11,manual)
        self.assertEqual(self.call("patch",f"{group.pk}/",{"assigned_teacher_id":new.pk},actor=admin,
                         token={"dawon_demo_role":"admin"}).status_code,200)
        for link,teacher in ((past,old),(current,new),(upcoming,new),(override,new)):
            link.refresh_from_db();self.assertEqual(link.reference_teacher,teacher)
            self.assertEqual(link.lecture.name,teacher.name)
        self.assertEqual(self.call("get",f"{group.pk}/",actor=old,token={"dawon_demo_role":"teacher"}).status_code,404)
        student=self.pupil("인계학생","학교");group.students.add(student)
        payload={"title":"원장 개설","name":"임의 강사","start_date":str(today+timedelta(days=20)),
                 "end_date":str(today+timedelta(days=30)),"period":"vacation"}
        response=self.call("post",f"{group.pk}/lectures/",payload,actor=admin,token={"dawon_demo_role":"admin"})
        self.assertEqual(response.status_code,201,response.data)
        link=ClassGroupLecture.objects.get(lecture_id=response.data["id"])
        self.assertEqual(link.reference_teacher,new);self.assertEqual(link.lecture.name,new.name)
        self.assertTrue(Enrollment.objects.filter(tenant=self.tenant,lecture=link.lecture,student=student,status="ACTIVE").exists())

    def test_teaching_returns_assigned_groups_all_their_courses_and_latest_held_lesson(self):
        from datetime import timedelta
        from apps.domains.lectures.models import Lecture, Session, SessionSchedule
        from apps.domains.students.models import ClassGroupLecture

        teacher = get_user_model().objects.create_user(username="qa-teaching", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=teacher, role="teacher")
        today = timezone.localdate()
        assigned, upcoming, unrelated = [
            ClassGroup.objects.create(tenant=self.tenant, name=name, kind="union", track="SKY")
            for name in ("담당 반", "개강 예정", "다른 반")
        ]

        def course(group, title, reference):
            lecture = Lecture.objects.create(tenant=self.tenant, title=title)
            ClassGroupLecture.objects.create(group=group, lecture=lecture, period="vacation", reference_teacher=reference)
            return lecture

        lead = course(assigned, "담당 강의", teacher)
        shared = course(assigned, "같은 반의 다른 강의", self.actor)
        future = course(upcoming, "개강 예정 강의", teacher)
        other = course(unrelated, "다른 반 강의", self.actor)
        Session.objects.create(lecture=lead, order=1, date=today - timedelta(days=1))
        recent = Session.objects.create(lecture=shared, order=1, title="오늘 수업", date=today)
        for lecture in (lead, future):
            Session.objects.create(lecture=lecture, order=2, date=today + timedelta(days=1))
        Session.objects.create(lecture=other, order=1, title="다른 반 오늘 수업", date=today)

        response = self.call("get", "teaching/", actor=teacher)
        self.assertEqual(response.status_code, 200, response.data)
        rows = {row["id"]: row for row in response.data}
        self.assertEqual(set(rows), {assigned.pk, upcoming.pk})
        self.assertCountEqual(rows[assigned.pk]["courses"], [lead.pk, shared.pk])
        self.assertEqual(rows[assigned.pk]["recent"], {
            "id": recent.pk, "lecture_id": shared.pk, "title": "오늘 수업", "date": today, "schedule_id": None,
        })
        self.assertEqual(rows[upcoming.pk]["courses"], [future.pk])
        self.assertIsNone(rows[upcoming.pk]["recent"])

        def day_sessions(group):
            path = "/api/v1/lectures/sessions/"
            request = self.factory.get(path, {"date": today.isoformat(), "class_group": getattr(group, "pk", group)})
            request.tenant = self.tenant
            force_authenticate(request, user=teacher)
            result = resolve(path).func(request)
            self.assertEqual(result.status_code, 200, result.data)
            return [row["id"] for row in result.data]

        self.assertEqual(day_sessions(assigned), [recent.pk])
        self.assertEqual(day_sessions(unrelated), [])
        foreign = ClassGroup.objects.create(tenant=self.other, name="다른 학원 반", kind="union", track="SKY")
        self.assertEqual(day_sessions(foreign), [])
        SessionSchedule.objects.create(session=recent, label="다음 주", date=today + timedelta(days=7))
        actual = Session.objects.create(lecture=shared, order=2, title="분반 수업", date=today + timedelta(days=3))
        held = SessionSchedule.objects.create(session=actual, label="A조", date=today)
        SessionSchedule.objects.create(session=actual, label="B조", date=today + timedelta(days=1))
        self.assertEqual(day_sessions(assigned), [actual.pk])
        additional = Session.objects.create(lecture=future, order=3, date=today)
        self.assertCountEqual(day_sessions("assigned"), [actual.pk, additional.pk])
        rows = {row["id"]: row for row in self.call("get", "teaching/", actor=teacher).data}
        self.assertEqual(rows[assigned.pk]["recent"], {
            "id": actual.pk, "lecture_id": shared.pk, "title": actual.title, "date": today, "schedule_id": held.pk,
        })

    def test_teacher_directory_and_mutation_only_reach_owned_groups(self):
        teacher = get_user_model().objects.create_user(username="directory-teacher", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=teacher, role="teacher")
        role = {"actor": teacher, "token": {"dawon_demo_role": "teacher"}}
        payload = {"name": "직접 개설한 반", "kind": "union", "track": "SKY"}
        self.assertEqual(self.call("post",payload=payload,**role).status_code,403)
        mine = ClassGroup.objects.create(tenant=self.tenant,assigned_teacher=teacher,name=payload["name"],kind="union",track="SKY").pk
        other = self.call("post", payload=dict(payload, name="다른 선생님 반")).data["id"]
        self.assertEqual([row["id"] for row in self.call("get", **role).data], [mine])
        self.assertEqual(self.call("get", f"{mine}/", **role).status_code, 200)
        self.assertEqual(self.call("patch", f"{mine}/members/", {"add": []}, **role).status_code,403)
        self.assertEqual(self.call("get",f"{other}/",**role).status_code,404)
        self.assertEqual({row["id"] for row in self.call("get").data}, {mine, other})

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

    def test_course_cycles_follow_start_dates_per_group_and_school(self):
        from datetime import date
        from unittest.mock import patch

        members = [self.pupil("alpha", "A고"), self.pupil("beta", "B고")]
        group = self.call("post", payload={"name": "상설반", "kind": "union", "track": "SKY"}).data
        base = f"{group['id']}/"
        self.call("patch", base + "members/", {"add": [s.pk for s in members]})
        periods = [("winter", "vacation", "", 1), ("A mid", "midterm", "A고", 2),
                   ("B mid", "midterm", "B고", 3), ("A final", "final", "A고", 4),
                   ("summer", "vacation", "", 5)]
        ids = {}
        for title, period, school, month in reversed(periods):
            payload = {"title": title, "period": period, "school": school,
                       "start_date": f"2027-{month:02d}-01", "end_date": "2027-06-30"}
            response = self.call("post", base + "lectures/", payload)
            self.assertEqual(response.status_code, 201, response.data)
            ids[title] = response.data["id"]
            duplicate = self.call("post", base + "lectures/", {**payload, "title": title + " copy"})
            self.assertEqual(duplicate.status_code, 400)
        for month, current in [(1, {"winter"}), (2, {"A mid"}), (3, {"A mid", "B mid"}),
                               (4, {"A final", "B mid"}), (5, {"summer"}), (7, set())]:
            with patch("django.utils.timezone.localdate", return_value=date(2027, month, 1)):
                rows = self.call("get", base).data["lectures"]
                self.assertEqual({r["title"] for r in rows if r["cycle_status"] == "current"}, current)
                self.assertEqual({r["title"] for r in rows if r["cycle_status"] == "upcoming"},
                                 {title for title, _, _, start in periods if start > month})
                route = "/api/v1/lectures/lectures/"
                req = self.factory.get(route)
                req.tenant = self.tenant
                force_authenticate(req, user=self.actor)
                view = resolve(route)
                listing = view.func(req, **view.kwargs)
                self.assertEqual({r["id"]: r["cycle_status"] for r in listing.data},
                                 {r["id"]: r["cycle_status"] for r in rows})
        self.assertEqual(Enrollment.objects.filter(tenant=self.tenant, lecture_id__in=ids.values(),
                                                    status="ACTIVE").count(), 7)

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
        self.assertEqual(metadata.data, {"group_id": group["id"], "name": "연합", "school": "A고", "top_reference_percent": 30, "can_edit_top_reference": True})
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

    def test_top_reference_persists_and_matches_student_normalized_exam_cohort(self):
        from types import SimpleNamespace
        from django.apps import apps
        from apps.domains.results.models import Result
        from apps.domains.results.services.exam_analysis_metrics import load_exam_analysis
        from apps.domains.results.services.student_result_service import get_my_exam_result_data
        from apps.domains.results.serializers.admin_exam_analysis_insights import AdminExamAnalysisInsightsSerializer

        teacher = get_user_model().objects.create_user(username="responsible", tenant=self.tenant, name="담당")
        TenantMembership.ensure_active(tenant=self.tenant, user=teacher, role="teacher")
        other = get_user_model().objects.create_user(username="other", tenant=self.tenant, name="별도")
        TenantMembership.ensure_active(tenant=self.tenant, user=other, role="teacher")
        students = [self.pupil("case" + str(i), "A고") for i in range(4)]
        group = self.call("post", payload={"kind": "union", "track": "1%", "name": "A"}).data
        base = str(group["id"]) + "/"
        self.call("patch", base + "members/", {"add": [s.pk for s in students]})
        result = self.call("post", base + "lectures/", {"period": "midterm", "school": "A고", "title": "기준",
            "name": teacher.name, "start_date": "2026-10-01", "end_date": "2026-11-01"})
        lecture = apps.get_model("lectures", "Lecture").objects.get(pk=result.data["id"])
        endpoint = f"for-lecture/{lecture.pk}/"
        self.assertEqual(self.call("get", endpoint).data["top_reference_percent"], 30)
        self.assertEqual(self.call("patch", endpoint, {"top_reference_percent": 100}, actor=other).status_code, 404)
        self.assertEqual(self.call("patch", endpoint, {"top_reference_percent": 101}, actor=teacher).status_code, 400)
        self.assertEqual(self.call("patch", endpoint, {"top_reference_percent": 100}, actor=teacher).status_code, 200)
        exam = apps.get_model("exams", "Exam").objects.create(tenant=self.tenant, title="시험", max_score=100)
        exam.sessions.add(apps.get_model("lectures", "Session").objects.create(lecture=lecture, order=1, title="시험"))
        for student, score in zip(students, [20, 10, 0, None]):
            enrollment = Enrollment.objects.get(lecture=lecture, student=student)
            apps.get_model("exams", "ExamEnrollment").objects.create(exam=exam, enrollment=enrollment)
            if score is not None:
                Result.objects.create(target_type="exam", target_id=exam.pk, enrollment=enrollment, total_score=score, max_score=20)
        other_lecture = apps.get_model("lectures", "Lecture").objects.create(tenant=self.tenant, title="다른 학교 강의",
                                                                            name="B고", subject="수학")
        peer = self.pupil("other-school", "B고")
        other_enrollment = Enrollment.objects.create(tenant=self.tenant, lecture=other_lecture, student=peer, status="ACTIVE")
        exam.sessions.add(apps.get_model("lectures", "Session").objects.create(lecture=other_lecture, order=1, title="B고"))
        apps.get_model("exams", "ExamEnrollment").objects.create(exam=exam, enrollment=other_enrollment)
        Result.objects.create(target_type="exam", target_id=exam.pk, enrollment=other_enrollment, total_score=20, max_score=20)
        view = lambda: AdminExamAnalysisInsightsSerializer(load_exam_analysis(exam=exam, tenant=self.tenant,
            lecture_id=lecture.pk).insights).data["score_summary"]
        summary = view()
        self.assertEqual((summary["top_reference_percent"], summary["top_reference_average"], summary["top_reference_count"]), (100, 50, 3))
        request = SimpleNamespace(user=students[0].user, tenant=self.tenant)
        student_result = get_my_exam_result_data(request, exam.pk, self.tenant)
        self.assertEqual((student_result["top_reference_average"], student_result["top_reference_ratio"]), (50, 200))
        self.call("patch", endpoint, {"top_reference_percent": 34}, actor=teacher)
        self.assertEqual(view()["top_reference_average"], 75)

        Result.objects.filter(target_id=exam.pk, target_type="exam", enrollment__lecture=lecture).update(total_score=0)
        zero = get_my_exam_result_data(request, exam.pk, self.tenant)
        self.assertEqual(zero["top_reference_average"], 0)
        self.assertIsNone(zero["top_reference_ratio"])

    def test_previous_release_lecture_link_insert_uses_database_default(self):
        from django.apps import apps
        from django.db import connection

        group = ClassGroup.objects.create(tenant=self.tenant, kind="union", track="SKY", name="호환성")
        lecture = apps.get_model("lectures", "Lecture").objects.create(tenant=self.tenant, title="개설", name="강사", subject="수학")
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO students_classgrouplecture (group_id, lecture_id, period, school) "
                           "VALUES (%s, %s, %s, %s) RETURNING top_reference_percent, reference_teacher_id",
                           [group.pk, lecture.pk, "vacation", ""])
            self.assertEqual(cursor.fetchone(), (30, None))

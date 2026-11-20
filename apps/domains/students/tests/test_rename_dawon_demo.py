import hashlib
import json
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.management.color import no_style
from django.db import connection
from django.test import TestCase, override_settings

from apps.core.models import Tenant, TenantMembership
from apps.core.dawon_preview_identity import ORIGINAL_IDENTITIES
from apps.domains.lectures.models import Lecture
from apps.domains.parents.models import Parent
from apps.domains.parents.services import parent_account_username
from apps.domains.students.models import ClassGroup, ClassGroupLecture, Student
from apps.domains.students.services.dawon_staff import LEGACY_FIXTURES, PREFIX
from apps.domains.teachers.models import Teacher


@override_settings(DAWON_REVIEW_CLASS_GROUPS=True)
class RenameDawonTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(pk=719, code="dawon", name="QA")
        users = get_user_model().objects
        for role, old in (("admin", "다원 체험 원장"), ("teacher", "다원 체험 선생님"), ("employee", "다원 체험 강사")):
            user = users.create_user(tenant=self.tenant, username=PREFIX+role, password=None, name=old)
            TenantMembership.ensure_active(tenant=self.tenant, user=user, role="staff" if role == "employee" else role)
        Teacher.objects.create(pk=1, tenant=self.tenant, name=LEGACY_FIXTURES[PREFIX+"teacher"][0], subject="수학")
        pins = {}
        for n in (1, 2):
            phone = f"0100000990{n}"
            name = f"다원 수업 검수 학생 {n}"
            guardian = name + " 학부모"
            parent_user = users.create_user(tenant=self.tenant, username=parent_account_username(tenant=self.tenant, parent_phone=phone), name=guardian)
            parent = Parent.objects.create(pk=n, tenant=self.tenant, user=parent_user, name=guardian, phone=phone)
            user = users.create_user(tenant=self.tenant, username=f"qa-learner-{n}", name=name)
            ps, omr = f"qa-demo-{n}", f"{7000+n:08}"
            Student.objects.create(pk=n, tenant=self.tenant, user=user, name=name, parent=parent,
                                   parent_phone=phone, ps_number=ps, omr_code=omr)
            pins[n] = tuple(hashlib.sha256(v.encode()).hexdigest() for v in (user.username, ps, omr))
        # Only identity values change in the isolated test; the full selection/rename path runs.
        patched = patch.dict(ORIGINAL_IDENTITIES, pins)
        patched.start()
        self.addCleanup(patched.stop)
        for pk, old, kind, track, school, cid, title in [
            (1, "고1 HYPER 연합반", "union", "HYPER", "", 2, "HYPER 방학 수학Ⅱ"),
            (2, "고1 예시고 내신반", "school", "", "예시고", 4, "예시고 기말 수학Ⅱ"),
        ]:
            g = ClassGroup.objects.create(pk=pk, tenant=self.tenant, name=old, kind=kind, track=track, school=school)
            course = Lecture.objects.create(pk=cid, tenant=self.tenant, name=title, title=title)
            ClassGroupLecture.objects.create(group=g, lecture=course, period="vacation")
        with connection.cursor() as cursor:
            for sql in connection.ops.sequence_reset_sql(no_style(), [ClassGroup, Lecture, Student]):
                cursor.execute(sql)
        call_command("seed_dawon_staff", stdout=StringIO())
        old_names = ("가온", "나래", "다빈", "라온", "서우", "도윤", "예린", "은솔")
        for n in range(32):
            name = ("김", "윤", "서", "정")[n // 8] + old_names[n % 8]
            user = users.create_user(tenant=self.tenant, username=f"t719_demo-report-student-{n:02}", name=name)
            Student.objects.create(tenant=self.tenant, user=user, name=name, ps_number=f"DW-R-{n:02}", omr_code=f"709900{n:02}")

    def run_plan(self, **options):
        out = StringIO()
        call_command("rename_dawon_demo", stdout=out, **options)
        return json.loads(out.getvalue())

    def test_digest_checked_rename_seed_replay_and_safe_reverse(self):
        first = self.run_plan()
        self.assertEqual(len(first["classes"]), 7)
        before = list(Student.objects.order_by("pk").values_list("pk", "user_id", "user__password"))
        with self.assertRaisesMessage(CommandError, "Plan changed"):
            self.run_plan(apply="invalid")
        self.assertFalse(ClassGroup.objects.filter(name="1%반 1").exists())
        self.run_plan(apply=first["sha256"])
        self.assertEqual(self.run_plan(apply=first["sha256"])["count"], 0)
        self.assertEqual(self.run_plan()["classes"], [])
        self.assertEqual(list(Student.objects.order_by("pk").values_list("pk", "user_id", "user__password")), before)
        added = ClassGroup.objects.get(tenant=self.tenant, name="1%반 1")
        self.assertEqual((added.track, added.assigned_teacher.name), ("1%", "김선생 1"))
        pupil = Student.objects.get(name="김학생 3")
        added.students.add(pupil)
        with self.assertRaisesMessage(CommandError, "preserve"):
            self.run_plan(legacy=True)
        added.students.remove(pupil)
        back = self.run_plan(legacy=True)
        self.run_plan(legacy=True, apply=back["sha256"])
        self.assertFalse(ClassGroup.objects.filter(pk=added.pk).exists())
        self.assertEqual(self.run_plan(legacy=True)["count"], 0)
        self.assertEqual(Student.objects.get(pk=pupil.pk).name, "김가온")

    def test_label_collision_foreign_tenant_and_edited_name(self):
        ClassGroup.objects.create(tenant=self.tenant, name="내신반 4", kind="union", track="SKY")
        with self.assertRaisesMessage(CommandError, "occupied"):
            self.run_plan()
        pupil = Student.objects.get(ps_number="DW-R-13")
        pupil.name = "작성자 변경"
        pupil.save(update_fields=["name"])
        with self.assertRaisesMessage(CommandError, "Unexpected display identity"):
            self.run_plan()
        with self.settings(DAWON_REVIEW_CLASS_GROUPS=False):
            with self.assertRaisesMessage(CommandError, "Dedicated"):
                self.run_plan()

    def test_new_label_alone_cannot_select_original_learner(self):
        from apps.core.dawon_preview_identity import matches_original
        unrelated = Student.objects.get(pk=2)
        unrelated.name = "김학생 2"
        unrelated.ps_number = "other"
        self.assertFalse(matches_original(unrelated))

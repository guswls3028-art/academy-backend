"""Rename only the enumerated, linked Dawon preview fixtures in one transaction."""
import hashlib
import json
import re

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.dawon_preview_identity import ORIGINAL_IDENTITIES, matches_original
from apps.core.models import Tenant, TenantMembership
from apps.domains.clinic.contracts import is_demo_tenant
from apps.domains.students.models import ClassGroup, ClassGroupLecture, Student
from apps.domains.students.services.dawon_staff import EMPLOYEE, FIXTURES, LEGACY_FIXTURES, PREFIX, TEACHER, WORK_MARK, sample_staff


def require(ok, message):
    if not ok:
        raise CommandError(message)


class RenamePlan:
    def __init__(self, tenant, reverse):
        self.tenant = tenant
        self.reverse = reverse
        self.rows = []
        self.class_actions = []

    def add(self, obj, old, new):
        require(obj.tenant_id == self.tenant.pk and obj.name in (old, new),
                f"Unexpected display identity on {obj._meta.label} pk={obj.pk}")
        target = old if self.reverse else new
        if obj.name != target:
            self.rows.append((obj, obj.name, target))

    def account(self, username, *, passwordless=True):
        user = get_user_model()._base_manager.select_for_update().get(tenant=self.tenant, username=username)
        require(not user.is_staff and not user.is_superuser and (not passwordless or not user.has_usable_password()),
                f"Unexpected account attributes for {username}")
        return user

    def group(self, group, old, new):
        require(not ClassGroup.objects.filter(tenant=self.tenant, name__in=(old, new)).exclude(pk=group.pk).exists(),
                f"Class label collision on pk={group.pk}")
        self.add(group, old, new)

    def reports(self):
        for n in range(32):
            suffix = f"{n:02}"
            u = self.account("t719_demo-report-student-" + suffix)
            s = Student._base_manager.select_for_update().get(
                tenant=self.tenant, user=u, ps_number="DW-R-" + suffix, omr_code="709900" + suffix,
                deleted_at__isnull=True)
            old = ("김", "윤", "서", "정")[n // 8] + ("가온", "나래", "다빈", "라온", "서우", "도윤", "예린", "은솔")[n % 8]
            self.add(u, old, f"김학생 {n+3}")
            self.add(s, old, f"김학생 {n+3}")

    def staff(self):
        from apps.domains.teachers.contracts import active_teachers
        teacher = active_teachers(self.tenant).select_for_update().get(pk=1, subject="수학")
        require(not (teacher.phone or teacher.email or teacher.note), "Unexpected instructor directory fields")
        self.add(teacher, LEGACY_FIXTURES[TEACHER][0], FIXTURES[TEACHER][0])
        admin = self.account(PREFIX + "admin")
        require(admin.is_active and TenantMembership.objects.filter(tenant=self.tenant, user=admin,
                is_active=True, role="admin").exists(), "Unexpected demo administrator")
        self.add(admin, "다원 체험 원장", "김원장 1")
        for username, (new, _) in FIXTURES.items():
            old = LEGACY_FIXTURES[username][0]
            user = self.account(username)
            require(user.is_active == (username in (TEACHER, EMPLOYEE)), "Unexpected demo account activation")
            profile = sample_staff(self.tenant).select_for_update().get(tenant=self.tenant, user=user,
                                                                  phone="", is_manager=False)
            self.add(user, old, new)
            self.add(profile, old, new)
            if username.startswith(PREFIX + "instructor-"):
                n = int(username.rsplit("-", 1)[1]) + 1
                link = ClassGroupLecture.objects.get(reference_teacher=user, group__tenant=self.tenant,
                    lecture__tenant=self.tenant, lecture__description=WORK_MARK, lecture__title=f"D{n:02} 수학 수업")
                lecture = type(link.lecture)._base_manager.select_for_update().get(pk=link.lecture_id, tenant=self.tenant)
                group = ClassGroup.objects.select_for_update().get(pk=link.group_id, tenant=self.tenant,
                                                                   kind="union", track="SKY")
                self.group(group, f"D{n:02} 체험 수학반", f"스카이반 {n-1}")
                self.add(lecture, old, new)

    def extra_classes(self):
        # This account is an inactive staff fixture and cannot create classes through the UI.
        owner = self.account(PREFIX + "branch-director")
        require(not owner.is_active, "Unexpected synthetic creator")
        teacher = self.account(TEACHER)
        for name, kind, track, school in [
            *((f"1%반 {i}", "union", "1%", "") for i in range(1, 5)),
            *((f"내신반 {i}", "school", "", "예시고") for i in range(2, 5)),
        ]:
            group = ClassGroup.objects.select_for_update().filter(tenant=self.tenant, name=name).first()
            attrs = dict(tenant=self.tenant, name=name, kind=kind, track=track, school=school,
                         created_by=owner, assigned_teacher=teacher)
            if group is None:
                if not self.reverse:
                    self.class_actions.append(("create", None, attrs))
                continue
            require(group.created_by_id == owner.pk and (group.kind, group.track, group.school) == (kind, track, school),
                    f"Reserved class label occupied: {name}")
            if self.reverse:
                require(group.assigned_teacher_id == teacher.pk and not group.students.exists()
                        and not group.course_links.exists(), f"Class {group.pk} changed; preserve its contents.")
                self.class_actions.append(("remove", group, attrs))

    def guardian(self, student, n):
        require(student.parent_id and student.user_id, "Original learner links missing")
        parent = type(student.parent)._base_manager.select_for_update().get(pk=student.parent_id, tenant=self.tenant)
        require(parent.user_id and parent.phone == student.parent_phone and
                not Student._base_manager.filter(parent=parent).exclude(pk=student.pk).exists(),
                "Unexpected guardian linkage")
        user = self.account(parent.user.username, passwordless=False)
        from apps.domains.parents.services import parent_account_username
        require(parent.pk == n and user.pk == parent.user_id and user.username == parent_account_username(tenant=self.tenant, parent_phone=parent.phone),
                "Unexpected guardian account")
        old = f"다원 수업 검수 학생 {n} 학부모"
        self.add(parent, old, f"보호자 {n}")
        self.add(user, old, f"보호자 {n}")

    def primary(self):
        for n in ORIGINAL_IDENTITIES:
            student = Student._base_manager.select_for_update().get(pk=n, tenant=self.tenant,
                                                                    is_managed=True, deleted_at__isnull=True)
            user = self.account(student.user.username, passwordless=False)
            require(user.pk == student.user_id and matches_original(student), "Unexpected original learner")
            old = f"다원 수업 검수 학생 {n}"
            self.add(student, old, f"김학생 {n}")
            self.add(user, old, f"김학생 {n}")
            self.guardian(student, n)
        for pk, old, new, kind, track, course, title in [
            (1, "고1 HYPER 연합반", "하이퍼1", "union", "HYPER", 2,
             "HYPER 방학 수학Ⅱ"),
            (2, "고1 예시고 내신반", "내신반 1", "school", "", 4,
             "예시고 기말 수학Ⅱ"),
        ]:
            group = ClassGroup.objects.select_for_update().get(tenant=self.tenant, pk=pk, kind=kind, track=track)
            require(group.school == ("예시고" if kind == "school" else ""), "Unexpected original class school")
            link = ClassGroupLecture.objects.get(group=group, lecture_id=course, lecture__tenant=self.tenant)
            require(link.lecture.title == title, "Unexpected original course")
            self.group(group, old, new)


class Command(BaseCommand):
    help = "Preview the exact Dawon fixture name changes; apply only with the reviewed digest."

    def add_arguments(self, parser):
        parser.add_argument("--apply", metavar="PLAN_SHA256")
        parser.add_argument("--legacy", action="store_true", help="Prepare or apply the inverse name mapping.")

    @transaction.atomic
    def handle(self, *args, **options):
        tenant = Tenant._base_manager.select_for_update().filter(pk=719, code="dawon").first()
        require(is_demo_tenant(tenant), "Dedicated Dawon review environment required.")
        plan = RenamePlan(tenant, options["legacy"])
        plan.staff()
        plan.reports()
        plan.primary()
        plan.extra_classes()
        rows = sorted((obj._meta.label, obj.pk, before, after) for obj, before, after in plan.rows)
        classes = [(action, group.pk if group else None, attrs["name"], attrs["kind"], attrs["track"],
                    attrs["school"], attrs["created_by"].pk, attrs["assigned_teacher"].pk)
                   for action, group, attrs in plan.class_actions]
        digest = hashlib.sha256(json.dumps([719, options["legacy"], rows, classes], ensure_ascii=False).encode()).hexdigest()
        if options["apply"]:
            require(re.fullmatch(r"[a-f0-9]{64}", options["apply"]) and
                    (options["apply"] == digest or not (rows or classes)), "Plan changed; inspect a new dry run.")
            for obj, before, after in plan.rows:
                count = type(obj)._base_manager.filter(pk=obj.pk, tenant=tenant, name=before).update(name=after)
                require(count == 1, "A planned label changed; rolled back.")
            for action, group, attrs in plan.class_actions:
                if action == "create":
                    ClassGroup.objects.create(**attrs)
                else:
                    removed, _ = group.delete()
                    require(removed == 1, "Unexpected dependencies; rolled back.")
        self.stdout.write(json.dumps({"tenant":719, "applied":bool(options["apply"]), "sha256":digest,
                                     "count":len(rows), "rows":rows, "classes":classes}, ensure_ascii=False))

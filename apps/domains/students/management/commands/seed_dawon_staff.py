from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture
from apps.domains.students.models import ClassGroup, ClassGroupLecture
from apps.domains.clinic.contracts import is_demo_tenant
from apps.domains.staffs.models import Staff, StaffWorkType, WorkMonthLock, WorkRecord, WorkType
from apps.domains.students.services.dawon_staff import EMPLOYEE, FIXTURES, LEGACY_FIXTURES, TEACHER, WORK_MARK, WORK_TYPE


def seed_secondary_class(tenant, user):
    number = int(user.username.rsplit("-", 1)[1]) + 1
    cohort_name = f"스카이반 {number-1}"
    legacy_name = f"D{number:02} 체험 수학반"
    title = f"D{number:02} 수학 수업"
    member, _ = TenantMembership.objects.get_or_create(tenant=tenant, user=user,
                                                       defaults={"role": "teacher", "is_active": True})
    if member.role != "teacher" or not member.is_active:
        raise CommandError("Unexpected synthetic teacher membership.")
    matches = list(ClassGroup.objects.filter(tenant=tenant, name__in=(legacy_name, cohort_name))[:2])
    if len(matches) > 1:
        raise CommandError("Class label collision.")
    new = not matches
    group = (ClassGroup.objects.create(tenant=tenant, name=cohort_name, kind="union", track="SKY")
             if new else matches[0])
    if not new:
        linked = ClassGroupLecture.objects.filter(group=group, lecture__tenant=tenant, lecture__description=WORK_MARK,
                                                   lecture__title=title, reference_teacher=user)
        if (group.kind, group.track, group.school) != ("union", "SKY", "") or not linked.exists():
            raise CommandError("Existing class does not match the synthetic fixture.")
        return
    if Lecture.objects.filter(tenant=tenant, title=title).exists():
        raise CommandError("Existing lecture does not match the synthetic fixture.")
    lecture = Lecture.objects.create(tenant=tenant, title=title, subject="수학", name=user.name, description=WORK_MARK)
    ClassGroupLecture.objects.create(group=group, lecture=lecture, period="vacation", reference_teacher=user)


class Command(BaseCommand):
    help = "Prepare synthetic staff and sample attendance in the isolated Dawon room."

    @transaction.atomic
    def handle(self, *args, **options):
        tenant = Tenant.objects.select_for_update().filter(pk=719, code="dawon").first()
        if not is_demo_tenant(tenant):
            raise CommandError("Dedicated Dawon preview required.")
        work, _ = WorkType.objects.get_or_create(
            tenant=tenant, name=WORK_TYPE, defaults={"description": WORK_MARK, "base_hourly_wage": 20000})
        if work.description != WORK_MARK or not work.is_active:
            raise CommandError("Unexpected work type identity.")
        today = timezone.localdate()
        previous = today.replace(day=1) - timedelta(days=1)
        days = [previous.replace(day=8), previous.replace(day=15)]
        days.extend(date(today.year, today.month, n) for n in (3, 8, 12) if n < today.day)
        for index, (username, (name, position)) in enumerate(FIXTURES.items()):
            accounts = get_user_model().objects.filter(tenant=tenant, username=username)
            user = accounts.first()
            if username in (TEACHER, EMPLOYEE) and user is None:
                raise CommandError("Seed the original preview roles first.")
            if user is None:
                user = get_user_model().objects.create_user(
                    tenant=tenant, username=username, name=name, password=None, is_active=False)
            if user.name not in (name, LEGACY_FIXTURES[username][0]) or user.has_usable_password() or user.is_staff or user.is_superuser or (
                username not in (TEACHER, EMPLOYEE) and user.is_active
            ):
                raise CommandError("Unexpected staff fixture identity.")
            staff, _ = Staff.objects.get_or_create(tenant=tenant, user=user,
                defaults={"name": user.name, "phone": "", "position": position})
            if staff.name != user.name or staff.phone or not staff.is_active or staff.is_manager:
                raise CommandError("Unexpected staff fixture profile.")
            if username == TEACHER and user.is_active and staff.position == "ASSISTANT":
                teacher_member = TenantMembership.objects.filter(tenant=tenant,user=user,role="teacher",is_active=True).exists()
                teaching = ClassGroupLecture.objects.filter(group__tenant=tenant,lecture__tenant=tenant,
                    lecture__is_active=True,reference_teacher=user).exists() or ClassGroup.objects.filter(
                        tenant=tenant,assigned_teacher=user).exists()
                if teacher_member and teaching:
                    staff.position = "INSTRUCTOR"
                    staff.save(update_fields=["position"])
            if username.startswith("t719_dawon-demo-instructor-"):
                seed_secondary_class(tenant, user)
            StaffWorkType.objects.get_or_create(tenant=tenant, staff=staff, work_type=work)
            for day in days:
                if WorkMonthLock.objects.filter(tenant=tenant, staff=staff, year=day.year, month=day.month, is_locked=True).exists():
                    continue
                if WorkRecord.objects.filter(tenant=tenant, staff=staff, date=day).exists():
                    continue
                WorkRecord.objects.create(tenant=tenant, staff=staff, work_type=work, date=day,
                    start_time=time(13), end_date=day, end_time=time(16 + index % 3), memo=WORK_MARK)
        self.stdout.write(f"Prepared {len(FIXTURES)} synthetic staff profiles.")

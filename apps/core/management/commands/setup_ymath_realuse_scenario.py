from __future__ import annotations

import json
import os
from datetime import date, timedelta

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import Program, Tenant, TenantMembership
from apps.core.models.user import user_display_username, user_internal_username
from apps.core.services.password import change_password
from apps.domains.parents.services import ensure_parent_account_for_student
from apps.core.services.student_grade_report_layout import (
    STUDENT_GRADE_REPORT_LAYOUT_KEY,
    ymath_student_grade_report_layout,
)
from apps.core.management.commands.setup_three_tenants import (
    DEFAULT_FEATURE_FLAGS,
    YMATH_FEATURE_FLAGS,
)


SCENARIO_CODE_PREFIX = "qa-ymath-realuse-"
DEFAULT_SCENARIO_CODE = "qa-ymath-realuse-20260805"
PASSWORD_ENV = "YMATH_REALUSE_SCENARIO_PASSWORD"
LOGIN_UAT_ACCOUNT_COUNT_PER_ROLE = 10


def assert_isolated_runtime() -> None:
    database = settings.DATABASES.get("default", {})
    database_name = str(database.get("NAME") or "")
    database_engine = str(database.get("ENGINE") or "")
    buckets = {
        str(getattr(settings, name, "") or "")
        for name in (
            "R2_AI_BUCKET",
            "R2_STORAGE_BUCKET",
            "R2_EXCEL_BUCKET",
            "R2_ADMIN_BUCKET",
        )
    }
    development_runtime = (
        database_name.startswith("academy_api_development")
        and buckets
        and all(name.startswith("academy-development-") for name in buckets)
    )
    test_runtime = (
        (database_engine.endswith("sqlite3") or database_name == ":memory:" or "test" in database_name.lower())
        and buckets
        and all(name.startswith("test-") for name in buckets)
    )
    if not (development_runtime or test_runtime):
        raise CommandError(
            "Ymath real-use scenario is allowed only in the isolated development "
            "or test database with matching non-production R2 buckets."
        )


def _ymath_program_contract() -> tuple[dict, dict]:
    source = Program.objects.filter(tenant__code="ymath", is_active=True).order_by("id").first()
    feature_flags = dict(DEFAULT_FEATURE_FLAGS)
    feature_flags.update(YMATH_FEATURE_FLAGS)
    ui_config = {
        "login_title": "Ymath 실사용 검증",
        STUDENT_GRADE_REPORT_LAYOUT_KEY: ymath_student_grade_report_layout(),
    }
    if source:
        if isinstance(source.feature_flags, dict):
            feature_flags.update(source.feature_flags)
        if isinstance(source.ui_config, dict):
            ui_config.update(source.ui_config)
    feature_flags.update(YMATH_FEATURE_FLAGS)
    return feature_flags, ui_config


class Command(BaseCommand):
    help = (
        "Create an isolated Ymath-shaped teacher, roster, lectures, and sessions "
        "for real source-file product verification."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", default=DEFAULT_SCENARIO_CODE)
        parser.add_argument("--teacher-username", default="ymath-qa-teacher")
        parser.add_argument("--student-count", type=int, default=6)
        parser.add_argument("--session-count", type=int, default=24)
        parser.add_argument(
            "--login-uat",
            action="store_true",
            help="Create a secret-free 10 student + 10 parent + 10 staff login manifest.",
        )
        lifecycle = parser.add_mutually_exclusive_group()
        lifecycle.add_argument("--reset", action="store_true")
        lifecycle.add_argument("--destroy", action="store_true")

    def handle(self, *args, **options):
        tenant_code = str(options["tenant_code"] or "").strip().lower()
        if not tenant_code.startswith(SCENARIO_CODE_PREFIX):
            raise CommandError(f"tenant-code must start with {SCENARIO_CODE_PREFIX!r}.")
        assert_isolated_runtime()
        existing = Tenant.objects.filter(code=tenant_code).first()
        if options["destroy"]:
            if existing is None:
                self.stdout.write(
                    json.dumps(
                        {
                            "status": "YMATH_REALUSE_SCENARIO_ABSENT",
                            "tenant_code": tenant_code,
                            "remaining": {"tenants": 0, "users": 0},
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return

            tenant_id = existing.id
            counts = self._tenant_counts(existing)
            user_count = existing.users.count()
            with transaction.atomic():
                existing.delete()
            remaining = {
                "tenants": Tenant.objects.filter(id=tenant_id).count(),
                "users": get_user_model().objects.filter(tenant_id=tenant_id).count(),
            }
            if any(remaining.values()):
                raise CommandError(
                    "Isolated scenario cleanup left database residue: "
                    + json.dumps(remaining, ensure_ascii=False, sort_keys=True)
                )
            self.stdout.write(
                json.dumps(
                    {
                        "status": "YMATH_REALUSE_SCENARIO_DESTROYED",
                        "tenant_code": tenant_code,
                        "tenant_id": tenant_id,
                        "deleted": {**counts, "users": user_count},
                        "remaining": remaining,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return

        login_uat = bool(options["login_uat"])
        student_count = LOGIN_UAT_ACCOUNT_COUNT_PER_ROLE if login_uat else int(options["student_count"])
        session_count = int(options["session_count"])
        if not 1 <= student_count <= 30:
            raise CommandError("student-count must be between 1 and 30.")
        if not 1 <= session_count <= 80:
            raise CommandError("session-count must be between 1 and 80.")

        password = str(os.environ.get(PASSWORD_ENV) or "")
        if not password:
            raise CommandError(f"{PASSWORD_ENV} must be set.")

        if existing and options["reset"]:
            counts = self._tenant_counts(existing)
            existing.delete()
            self.stdout.write(
                self.style.WARNING(
                    "Deleted isolated scenario tenant before rebuild: "
                    + json.dumps(counts, ensure_ascii=False, sort_keys=True)
                )
            )

        with transaction.atomic():
            tenant, _ = Tenant.objects.get_or_create(
                code=tenant_code,
                defaults={
                    "name": "Ymath 실사용 복제 검증",
                    "is_active": True,
                },
            )
            tenant.name = "Ymath 실사용 복제 검증"
            tenant.is_active = True
            tenant.save(update_fields=["name", "is_active"])

            feature_flags, ui_config = _ymath_program_contract()
            program, _ = Program.objects.get_or_create(tenant=tenant)
            program.display_name = "Ymath 실사용 검증"
            program.brand_key = "ymath"
            program.login_variant = Program.LoginVariant.HAKWONPLUS
            program.plan = Program.Plan.ALL
            scenario_started_at = timezone.localdate()
            program.subscription_status = Program.SubscriptionStatus.ACTIVE
            program.subscription_started_at = scenario_started_at
            program.subscription_expires_at = scenario_started_at + timedelta(days=365)
            program.cancel_at_period_end = False
            program.feature_flags = feature_flags
            program.ui_config = ui_config
            program.is_active = True
            program.save()

            teacher = self._ensure_user(
                tenant=tenant,
                login_username=str(options["teacher_username"]),
                password=password,
                name="Ymath QA 선생님",
                is_staff=True,
            )
            TenantMembership.ensure_active(
                tenant=tenant,
                user=teacher,
                role="admin",
            )

            Lecture = apps.get_model("lectures", "Lecture")
            Session = apps.get_model("lectures", "Session")
            Enrollment = apps.get_model("enrollment", "Enrollment")
            SessionEnrollment = apps.get_model("enrollment", "SessionEnrollment")
            Student = apps.get_model("students", "Student")
            Staff = apps.get_model("staffs", "Staff")

            lecture_specs = (
                ("공통수학2 정규반", "공수", "#3158d4"),
                ("대수 심화반", "대수", "#7c3aed"),
            )
            lectures = []
            sessions = []
            start = date(2026, 8, 3)
            for lecture_index, (title, chip, color) in enumerate(lecture_specs):
                lecture, _ = Lecture.objects.update_or_create(
                    tenant=tenant,
                    title=title,
                    defaults={
                        "name": title,
                        "subject": "MATH",
                        "description": "실제 Ymath 원본 전수 검증용 격리 강의",
                        "lecture_time": "월·수 18:00" if lecture_index == 0 else "화·목 20:00",
                        "color": color,
                        "chip_label": chip,
                        "is_active": True,
                    },
                )
                lectures.append(lecture)
                for order in range(1, session_count + 1):
                    session, _ = Session.objects.update_or_create(
                        lecture=lecture,
                        section=None,
                        order=order,
                        defaults={
                            "session_type": Session.SessionType.REGULAR,
                            "regular_order": order,
                            "title": f"{order}차시 실사용 자료",
                            "date": start + timedelta(days=(order - 1) * 3 + lecture_index),
                        },
                    )
                    sessions.append(session)

            students = []
            parents = []
            for index in range(1, student_count + 1):
                student_user = self._ensure_user(
                    tenant=tenant,
                    login_username=f"ymath-qa-student-{index:02d}",
                    password=password,
                    name=f"검증학생 {index:02d}",
                    is_staff=False,
                )
                TenantMembership.ensure_active(
                    tenant=tenant,
                    user=student_user,
                    role="student",
                )
                parent = None
                if login_uat:
                    parent_result = ensure_parent_account_for_student(
                        tenant=tenant,
                        parent_phone=f"01099{index:06d}",
                        student_name=f"검증학생 {index:02d}",
                    )
                    parent = parent_result.parent
                    change_password(parent.user, password)
                    parents.append(parent)
                student_defaults = {
                    "name": f"검증학생 {index:02d}",
                    "ps_number": f"QA-{index:04d}",
                    "omr_code": f"98{index:06d}",
                    "phone": f"01098{index:06d}",
                    "parent_phone": f"01099{index:06d}",
                    "grade": 1 + ((index - 1) % 3),
                    "school_type": "HIGH",
                    "high_school": "검증고등학교",
                    "memo": "격리 개발환경 실사용 시나리오 학생",
                }
                if parent is not None:
                    student_defaults["parent"] = parent
                student, _ = Student.objects.update_or_create(
                    tenant=tenant,
                    user=student_user,
                    defaults=student_defaults,
                )
                students.append(student)
                for lecture in lectures:
                    enrollment, _ = Enrollment.objects.update_or_create(
                        tenant=tenant,
                        student=student,
                        lecture=lecture,
                        defaults={"status": "ACTIVE"},
                    )
                    lecture_sessions = [item for item in sessions if item.lecture_id == lecture.id]
                    for session in lecture_sessions:
                        SessionEnrollment.objects.get_or_create(
                            tenant=tenant,
                            enrollment=enrollment,
                            session=session,
                        )

            login_staff = []
            if login_uat:
                for index in range(1, LOGIN_UAT_ACCOUNT_COUNT_PER_ROLE + 1):
                    staff_user = self._ensure_user(
                        tenant=tenant,
                        login_username=f"ymath-qa-staff-{index:02d}",
                        password=password,
                        name=f"로그인 검증 직원 {index:02d}",
                        is_staff=True,
                    )
                    TenantMembership.ensure_active(
                        tenant=tenant,
                        user=staff_user,
                        role="staff",
                    )
                    staff, _ = Staff.objects.update_or_create(
                        tenant=tenant,
                        user=staff_user,
                        defaults={
                            "name": f"로그인 검증 직원 {index:02d}",
                            "phone": f"01097{index:06d}",
                            "is_active": True,
                        },
                    )
                    login_staff.append(staff)

        payload = {
            "status": "YMATH_REALUSE_SCENARIO_READY",
            "tenant_code": tenant.code,
            "tenant_id": tenant.id,
            "teacher_username": str(options["teacher_username"]),
            "teacher_user_id": teacher.id,
            "subscription_expires_at": program.subscription_expires_at.isoformat(),
            "student_ids": [student.id for student in students],
            "lecture_ids": [lecture.id for lecture in lectures],
            "session_ids": [session.id for session in sessions],
            "counts": self._tenant_counts(tenant),
        }
        if login_uat:
            accounts = [
                {
                    "role": "student",
                    "username": user_display_username(student.user),
                    "landing_path": "/student",
                }
                for student in students
            ]
            accounts.extend(
                {
                    "role": "parent",
                    "username": user_display_username(parent.user),
                    "landing_path": "/student",
                }
                for parent in parents
            )
            accounts.extend(
                {
                    "role": "staff",
                    "username": user_display_username(staff.user),
                    "landing_path": "/workspace/mobile",
                }
                for staff in login_staff
            )
            payload["login_manifest"] = {
                "schema_version": 1,
                "tenant_code": tenant.code,
                "account_count": len(accounts),
                "accounts": accounts,
            }
        self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _ensure_user(*, tenant, login_username, password, name, is_staff):
        User = get_user_model()
        internal_username = user_internal_username(tenant, login_username)
        user, _ = User.objects.get_or_create(
            username=internal_username,
            defaults={
                "tenant": tenant,
                "name": name,
                "is_active": True,
                "is_staff": is_staff,
            },
        )
        user.tenant = tenant
        user.name = name
        user.is_active = True
        user.is_staff = is_staff
        user.set_password(password)
        user.save()
        return user

    @staticmethod
    def _tenant_counts(tenant) -> dict[str, int]:
        model_names = {
            "students": ("students", "Student"),
            "parents": ("parents", "Parent"),
            "staffs": ("staffs", "Staff"),
            "lectures": ("lectures", "Lecture"),
            "sessions": ("lectures", "Session"),
            "enrollments": ("enrollment", "Enrollment"),
            "exams": ("exams", "Exam"),
            "homeworks": ("homework_results", "Homework"),
        }
        counts = {}
        for label, (app_label, model_name) in model_names.items():
            model = apps.get_model(app_label, model_name)
            if label == "sessions":
                counts[label] = model.objects.filter(lecture__tenant=tenant).count()
            else:
                counts[label] = model.objects.filter(tenant=tenant).count()
        return counts

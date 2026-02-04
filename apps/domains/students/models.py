# PATH: apps/domains/students/models.py

from django.db import models
from django.conf import settings

from apps.api.common.models import TimestampModel
from apps.core.models import Tenant
from apps.core.db import TenantQuerySet  # ✅ 추가


class Student(TimestampModel):
    # 🔐 tenant-safe manager (실수 방지)
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="students",
        help_text="소속 학원 (Tenant)",
    )

    # ✅ 봉인: Student는 User 없이 존재 불가 / User 삭제되면 Student도 같이 삭제
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=False,
        blank=False,
        related_name="student_profile",
        help_text="학생 로그인 계정 (필수)",
    )

    # ✅ NEW: PS 번호 (학원 공식 학생 ID)
    ps_number = models.CharField(
        max_length=20,
        null=False,
        blank=False,
        help_text="PS 번호 (학원 학생 ID)",
    )

    # ✅ NEW: OMR 식별자 (전화번호 뒤 8자리)
    omr_code = models.CharField(
        max_length=8,
        null=False,
        blank=False,
        help_text="OMR 자동채점 식별자 (전화번호 뒤 8자리)",
    )

    name = models.CharField(max_length=50)

    gender = models.CharField(
        max_length=1,
        choices=[("M", "남"), ("F", "여")],
        null=True,
        blank=True,
    )

    grade = models.PositiveSmallIntegerField(
        choices=[(1, "1"), (2, "2"), (3, "3")],
        null=True,
        blank=True,
    )

    SCHOOL_TYPE_CHOICES = (
        ("MIDDLE", "중등"),
        ("HIGH", "고등"),
    )

    school_type = models.CharField(
        max_length=10,
        choices=SCHOOL_TYPE_CHOICES,
        default="HIGH",
    )

    # ✅ 봉인: phone/parent_phone 운영 필수 (NULL/BLANK 차단)
    phone = models.CharField(max_length=20, null=False, blank=False)
    parent_phone = models.CharField(max_length=20, null=False, blank=False)

    parent = models.ForeignKey(
        "parents.Parent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )

    high_school = models.CharField(max_length=100, null=True, blank=True)
    high_school_class = models.CharField(max_length=100, null=True, blank=True)
    major = models.CharField(max_length=50, null=True, blank=True)
    middle_school = models.CharField(max_length=100, null=True, blank=True)

    memo = models.TextField(null=True, blank=True)
    is_managed = models.BooleanField(default=True)

    tags = models.ManyToManyField(
        "Tag",
        through="StudentTag",
        related_name="students",
        blank=True,
    )

    class Meta:
        ordering = ["-id"]
        constraints = [
            # ✅ tenant 단위 User 유일 (기존 유지, 단 user는 이제 null 불가)
            models.UniqueConstraint(
                fields=["tenant", "user"],
                name="uniq_student_user_per_tenant",
            ),
            # ✅ NEW: tenant 단위 PS 번호 유일
            models.UniqueConstraint(
                fields=["tenant", "ps_number"],
                name="uniq_student_ps_number_per_tenant",
            ),
            # ✅ NEW: tenant 단위 OMR 코드 유일
            models.UniqueConstraint(
                fields=["tenant", "omr_code"],
                name="uniq_student_omr_code_per_tenant",
            ),
        ]

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=20, default="#000000")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                name="uniq_tag_name",
            )
        ]

    def __str__(self):
        return self.name


class StudentTag(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="student_tags",
    )
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "tag"],
                name="uniq_student_tag",
            )
        ]

    def __str__(self):
        return f"{self.student.name} - {self.tag.name}"

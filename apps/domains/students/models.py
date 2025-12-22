from django.db import models
from django.conf import settings

from apps.api.common.models import TimestampModel


class Student(TimestampModel):
    # =========================
    # 🔐 로그인 사용자 연결 (신규)
    # =========================
    # - 상용 SaaS에서 재생 권한/로그인을 증명하려면 User ↔ Student 매핑이 필수
    # - 기존 데이터/운영 고려: null 허용
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_profile",
        help_text="학생이 로그인 계정을 가지는 경우 연결",
    )

    # =========================
    # 기본 정보
    # =========================
    name = models.CharField(max_length=50)

    gender = models.CharField(
        max_length=1,
        choices=[("M", "남"), ("F", "여")],
        null=True,
        blank=True,
    )

    # 중/고 공통 학년 (1~3)
    grade = models.PositiveSmallIntegerField(
        choices=[(1, "1"), (2, "2"), (3, "3")],
        null=True,
        blank=True,
    )

    # 🔴 중학생 / 고등학생 구분
    SCHOOL_TYPE_CHOICES = (
        ("MIDDLE", "중등"),
        ("HIGH", "고등"),
    )

    school_type = models.CharField(
        max_length=10,
        choices=SCHOOL_TYPE_CHOICES,
        default="HIGH",
    )

    phone = models.CharField(max_length=20, null=True, blank=True)

    # legacy 유지 (학생 기준 빠른 조회용)
    parent_phone = models.CharField(max_length=20, null=True, blank=True)

    # =========================
    # 보호자 (1:N 구조)
    # =========================
    parent = models.ForeignKey(
        "parents.Parent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )

    # =========================
    # 학교 정보
    # =========================
    # 고등학생용
    high_school = models.CharField(max_length=100, null=True, blank=True)
    high_school_class = models.CharField(max_length=100, null=True, blank=True)
    major = models.CharField(max_length=50, null=True, blank=True)

    # 중학생용
    middle_school = models.CharField(max_length=100, null=True, blank=True)

    # =========================
    # 기타
    # =========================
    memo = models.TextField(null=True, blank=True)
    is_managed = models.BooleanField(default=True)

    # =========================
    # 태그 (주의학생 등)
    # =========================
    tags = models.ManyToManyField(
        "Tag",
        through="StudentTag",
        related_name="students",
        blank=True,
    )

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                name="uniq_student_user",
                condition=models.Q(user__isnull=False),
            )
        ]

    def __str__(self):
        return self.name


# =========================
# Tag
# =========================
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


# =========================
# Student - Tag 연결
# =========================
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

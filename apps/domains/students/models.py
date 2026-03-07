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
        db_index=True,  # ✅ tenant_id 인덱스 추가
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

    # ✅ NEW: OMR 식별자 (학생 전화번호 또는 부모 전화번호 뒤 8자리)
    omr_code = models.CharField(
        max_length=8,
        null=False,
        blank=False,
        help_text="OMR 자동채점 식별자 (학생 전화번호 또는 부모 전화번호 뒤 8자리)",
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

    # 학생 전화번호 (선택사항, 없으면 null)
    phone = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="정규화된 전화번호 (하이픈 제거, 예: 01012345678)",
    )
    # 부모 전화번호 (필수)
    parent_phone = models.CharField(
        max_length=20,
        null=False,
        blank=False,
        help_text="정규화된 전화번호 (하이픈 제거, 예: 01012345678)",
    )

    uses_identifier = models.BooleanField(
        default=False,
        help_text="True면 학생 전화 없음, 식별자(010+8자리)로 가입. 표시 시 '식별자 XXXX-XXXX'",
    )

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
    address = models.CharField(max_length=255, null=True, blank=True, help_text="주소 (선택)")
    is_managed = models.BooleanField(default=True)

    # 학생이 학생앱에서만 설정 (관리자 편집 불가)
    profile_photo = models.ImageField(
        upload_to="student_profile/%Y/%m/",
        null=True,
        blank=True,
        help_text="학생이 학생앱에서 업로드한 프로필 사진",
    )

    deleted_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="삭제일시. 설정 시 30일 보관 후 자동 삭제",
    )

    tags = models.ManyToManyField(
        "Tag",
        through="StudentTag",
        related_name="students",
        blank=True,
    )

    class Meta:
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),  # ✅ 복합 인덱스 추가
        ]
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
            # OMR 코드는 unique 제거 (쌍둥이 등 중복 허용, 자동 채점 후 수동 매칭)
        ]

    def save(self, *args, **kwargs):
        if self.pk and self.user_id:
            try:
                old = Student.objects.only("ps_number").get(pk=self.pk)
                if old.ps_number != self.ps_number:
                    from apps.core.models.user import user_internal_username
                    new_username = user_internal_username(self.tenant, self.ps_number)
                    if self.user.username != new_username:
                        self.user.username = new_username
                        self.user.save(update_fields=["username"])
            except Student.DoesNotExist:
                pass
        super().save(*args, **kwargs)

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


class StudentRegistrationRequest(TimestampModel):
    """
    학생 회원가입 신청 (로그인 페이지 셀프 등록).
    선생이 승인하면 Student + User + TenantMembership 생성 후 status=approved.
    """
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STATUS_CHOICES = [(PENDING, "대기"), (APPROVED, "승인됨"), (REJECTED, "거절")]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="student_registration_requests",
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
        db_index=True,
    )

    name = models.CharField(max_length=50)
    initial_password = models.CharField(max_length=128)  # 저장 후 승인 시 사용
    parent_phone = models.CharField(max_length=20)
    phone = models.CharField(max_length=20, null=True, blank=True)

    school_type = models.CharField(max_length=10, default="HIGH")
    high_school = models.CharField(max_length=100, null=True, blank=True)
    middle_school = models.CharField(max_length=100, null=True, blank=True)
    high_school_class = models.CharField(max_length=100, null=True, blank=True)
    major = models.CharField(max_length=50, null=True, blank=True)
    grade = models.PositiveSmallIntegerField(null=True, blank=True)
    gender = models.CharField(max_length=1, null=True, blank=True)
    memo = models.TextField(null=True, blank=True)
    address = models.CharField(max_length=255, null=True, blank=True, help_text="주소 (선택)")
    origin_middle_school = models.CharField(
        max_length=100, null=True, blank=True,
        help_text="출신중학교 (고등학생 선택 입력)",
    )

    # 승인 시 생성된 학생 (승인 후에만 설정)
    student = models.OneToOneField(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registration_request",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "status"])]

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

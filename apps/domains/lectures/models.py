# PATH: apps/domains/lectures/models.py

from django.db import models
from apps.api.common.models import TimestampModel
from apps.core.models import Tenant
from apps.core.db import TenantQuerySet  # ✅ 추가


class Lecture(TimestampModel):
    """
    강의 (Course / Lecture)

    - 학원(Tenant) 단위로 완전 분리
    - 여러 Session(차시)을 가진다
    """

    # 🔐 tenant-safe manager
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="lectures",
        db_index=True,  # ✅ tenant_id 인덱스 추가
    )

    title = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    subject = models.CharField(max_length=50)
    description = models.TextField(blank=True)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    lecture_time = models.CharField(max_length=100, blank=True, help_text="강의 시간 (예: 토 12:00 ~ 13:00)")

    color = models.CharField(max_length=20, default="#3b82f6", help_text="아이콘/라벨 색상")
    chip_label = models.CharField(
        max_length=2,
        blank=True,
        default="",
        help_text="강의딱지 2글자 (미입력 시 제목 앞 2자 사용)",
    )

    is_active = models.BooleanField(default=True)

    is_system = models.BooleanField(
        default=False,
        db_index=True,
        help_text="시스템용 강의 (전체공개영상 컨테이너 등). 강의 목록·성적 등에서 자동 제외.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "created_at"]),  # ✅ 복합 인덱스 추가
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "title"],
                name="uniq_lecture_title_per_tenant",
            ),
            # 테넌트당 시스템 강의는 최대 1개만 허용 (공개 영상 컨테이너)
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_system=True),
                name="uniq_system_lecture_per_tenant",
            ),
        ]

    def __str__(self):
        return self.title

    @classmethod
    def get_or_create_system_lecture(cls, tenant):
        """
        테넌트의 시스템 강의(공개 영상 컨테이너)를 안전하게 가져오거나 생성.
        - is_system=True로 먼저 조회
        - 없으면 기존 title='전체공개영상' 강의를 is_system=True로 업그레이드
        - 그래도 없으면 새로 생성
        - IntegrityError(race condition) 시 재조회
        """
        from django.db import IntegrityError

        # 1. is_system=True인 강의 조회
        lecture = cls.objects.filter(tenant=tenant, is_system=True).first()
        if lecture:
            return lecture

        # 2. 레거시: title='전체공개영상'인 강의가 있으면 is_system=True로 업그레이드
        lecture = cls.objects.filter(tenant=tenant, title="전체공개영상").first()
        if lecture:
            if not lecture.is_system:
                lecture.is_system = True
                lecture.save(update_fields=["is_system", "updated_at"])
            return lecture

        # 3. 신규 생성 (race condition 방어)
        try:
            lecture = cls.objects.create(
                tenant=tenant,
                title="전체공개영상",
                name="전체공개영상",
                subject="공개",
                description="프로그램에 등록된 모든 학생이 시청할 수 있는 영상입니다.",
                is_active=True,
                is_system=True,
            )
            return lecture
        except IntegrityError:
            # race condition: 다른 요청이 먼저 생성함 → 재조회
            lecture = cls.objects.filter(tenant=tenant, is_system=True).first()
            if lecture:
                return lecture
            # title 충돌인 경우
            lecture = cls.objects.filter(tenant=tenant, title="전체공개영상").first()
            if lecture:
                if not lecture.is_system:
                    lecture.is_system = True
                    lecture.save(update_fields=["is_system", "updated_at"])
                return lecture
            raise  # 예상 못한 에러는 전파


class Section(TimestampModel):
    """
    반 (Section) — 강의 내 반 편성 단위.

    section_mode=true인 학원에서만 사용.
    같은 강의의 같은 내용을 다른 요일/시간에 제공하는 단위.
    예) 수학 A반(수요일 17시), 수학 B반(목요일 19시)
    """

    SECTION_TYPE_CHOICES = [
        ("CLASS", "수업"),
        ("CLINIC", "클리닉"),
    ]

    DAY_OF_WEEK_CHOICES = [
        (0, "월"),
        (1, "화"),
        (2, "수"),
        (3, "목"),
        (4, "금"),
        (5, "토"),
        (6, "일"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="sections",
        db_index=True,
    )
    lecture = models.ForeignKey(
        "Lecture",
        on_delete=models.CASCADE,
        related_name="sections",
    )

    label = models.CharField(max_length=10, help_text="반 이름 (A, B, C...)")
    section_type = models.CharField(
        max_length=10,
        choices=SECTION_TYPE_CHOICES,
        default="CLASS",
    )
    day_of_week = models.IntegerField(
        choices=DAY_OF_WEEK_CHOICES,
        help_text="요일 (0=월 ~ 6=일)",
    )
    start_time = models.TimeField(help_text="시작 시간")
    end_time = models.TimeField(null=True, blank=True, help_text="종료 시간")
    location = models.CharField(max_length=100, blank=True, default="")
    max_capacity = models.PositiveIntegerField(null=True, blank=True, help_text="최대 수용 인원")
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "lecture"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "lecture", "label", "section_type"],
                name="uniq_section_per_lecture_type",
            ),
        ]
        ordering = ["section_type", "label"]

    def __str__(self):
        return f"{self.lecture.title} - {self.get_section_type_display()} {self.label}반"


class SectionAssignment(TimestampModel):
    """
    정규편성 — 학생(Enrollment)의 정규 반 배정.

    enrollment당 1개. class_section(수업 반) + clinic_section(클리닉 반, optional).
    학생은 교차 출석 가능하므로 실제 출석 반은 Attendance.attended_section에 기록.
    """

    SOURCE_CHOICES = [
        ("SELF", "자발적 등록"),
        ("AUTO", "자동 배정"),
        ("MANUAL", "수동 배정"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="section_assignments",
        db_index=True,
    )
    enrollment = models.OneToOneField(
        "enrollment.Enrollment",
        on_delete=models.CASCADE,
        related_name="section_assignment",
    )
    class_section = models.ForeignKey(
        Section,
        on_delete=models.PROTECT,
        related_name="class_assignments",
        help_text="수업 정규반",
    )
    clinic_section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        related_name="clinic_assignments",
        null=True,
        blank=True,
        help_text="클리닉 정규반 (clinic_mode=regular일 때)",
    )
    source = models.CharField(
        max_length=10,
        choices=SOURCE_CHOICES,
        default="MANUAL",
    )

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "class_section"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "enrollment"],
                name="uniq_section_assignment_per_enrollment",
            ),
        ]

    def __str__(self):
        return f"{self.enrollment} → {self.class_section.label}반"


class Session(TimestampModel):
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.SET_NULL,
        related_name="sessions",
        null=True,
        blank=True,
        help_text="반별 차시 (section_mode=true). null이면 반 무관 공통 차시.",
    )

    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["lecture", "section", "order"],
                condition=models.Q(section__isnull=False),
                name="uniq_session_order_per_lecture_section",
            ),
        ]

    def __str__(self):
        section_label = f" ({self.section.label}반)" if self.section else ""
        return f"{self.lecture.title} - {self.order}차시{section_label}"

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


class Session(TimestampModel):
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="sessions",
    )

    order = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.lecture.title} - {self.order}차시"

# PATH: apps/domains/clinic/serializers.py

from datetime import datetime, timedelta
from rest_framework import serializers
from .models import Session, SessionParticipant, Test, Submission
from apps.domains.lectures.models import Lecture
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import Student


class ClinicSessionSerializer(serializers.ModelSerializer):
    participant_count = serializers.SerializerMethodField()
    booked_count = serializers.SerializerMethodField()

    tenant = serializers.PrimaryKeyRelatedField(read_only=True)
    created_by = serializers.PrimaryKeyRelatedField(read_only=True)

    # section FK: 쓰기 시 section_id, 읽기 시 id+label
    section_label = serializers.CharField(source="section.label", read_only=True, default=None)
    section_type = serializers.CharField(source="section.section_type", read_only=True, default=None)

    # 대상 강의: 쓰기 시 id 배열, 읽기 시 id+title
    target_lecture_ids = serializers.PrimaryKeyRelatedField(
        source="target_lectures",
        queryset=Lecture.objects.none(),  # __init__에서 tenant 필터 적용
        many=True,
        required=False,
    )
    target_lecture_names = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and hasattr(request, "tenant") and request.tenant:
            self.fields["target_lecture_ids"].child_relation.queryset = (
                Lecture.objects.filter(tenant=request.tenant)
            )
            if "section" in self.fields:
                from apps.domains.lectures.models import Section
                self.fields["section"].queryset = Section.objects.filter(tenant=request.tenant)

    # ✅ 파생 필드: 종료 시간 (저장 X)
    end_time = serializers.SerializerMethodField()

    # ✅ [ADD] 운영 판단 필드
    available_slots = serializers.SerializerMethodField()
    is_full = serializers.SerializerMethodField()

    # ✅ [ADD] 상태/소스 요약
    status_summary = serializers.SerializerMethodField()
    source_summary = serializers.SerializerMethodField()
    has_auto_targets = serializers.SerializerMethodField()

    class Meta:
        model = Session
        exclude = ("target_lectures",)

    def get_participant_count(self, obj: Session):
        return getattr(obj, "participant_count", 0)

    def get_booked_count(self, obj: Session):
        return getattr(obj, "booked_count", 0)

    def get_end_time(self, obj: Session):
        if not obj.start_time or not obj.duration_minutes:
            return None
        dt = datetime.combine(obj.date, obj.start_time)
        return (dt + timedelta(minutes=obj.duration_minutes)).time()

    def get_available_slots(self, obj):
        cnt = getattr(obj, "booked_count", None)
        if obj.max_participants is None or cnt is None:
            return None
        return max(obj.max_participants - cnt, 0)

    def get_is_full(self, obj):
        cnt = getattr(obj, "booked_count", None)
        if obj.max_participants is None or cnt is None:
            return False
        return cnt >= obj.max_participants

    def get_status_summary(self, obj):
        return {
            "booked": getattr(obj, "booked_count", 0),
            "attended": getattr(obj, "attended_count", 0),
            "no_show": getattr(obj, "no_show_count", 0),
            "cancelled": getattr(obj, "cancelled_count", 0),
        }

    def get_source_summary(self, obj):
        return {
            "auto": getattr(obj, "auto_count", 0),
            "manual": getattr(obj, "manual_count", 0),
        }

    def get_has_auto_targets(self, obj):
        return getattr(obj, "auto_count", 0) > 0

    def get_target_lecture_names(self, obj):
        lectures = obj.target_lectures.all()
        if not lectures:
            return []
        return [{"id": lec.id, "title": lec.title} for lec in lectures]


class ClinicSessionParticipantSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.name", read_only=True)
    session_date = serializers.SerializerMethodField()  # ✅ session이 없을 수 있으므로 SerializerMethodField 사용
    session_start_time = serializers.SerializerMethodField()
    session_location = serializers.SerializerMethodField()

    # ✅ 파생 노출
    session_duration_minutes = serializers.SerializerMethodField()
    session_end_time = serializers.SerializerMethodField()

    # ✅ 학생 SSOT 표시용: 강의 딱지 + 클리닉 하이라이트 + 아바타
    lecture_title = serializers.SerializerMethodField()
    lecture_color = serializers.SerializerMethodField()
    lecture_chip_label = serializers.SerializerMethodField()
    name_highlight_clinic_target = serializers.SerializerMethodField()
    profile_photo_url = serializers.SerializerMethodField()

    # ✅ [ADD] 변경자 이름 노출
    status_changed_by_name = serializers.CharField(
        source="status_changed_by.username",
        read_only=True,
        default=None,
    )

    # FK 전환 후 API 호환성: enrollment → enrollment_id로 노출
    enrollment_id = serializers.PrimaryKeyRelatedField(
        source="enrollment", read_only=True,
    )

    completed_by_name = serializers.CharField(
        source="completed_by.username",
        read_only=True,
        default=None,
    )

    class Meta:
        model = SessionParticipant
        fields = "__all__"
        extra_kwargs = {
            "enrollment": {"write_only": True, "required": False},
        }

    def get_session_date(self, obj):
        """session이 있으면 session.date, 없으면 requested_date"""
        return obj.session.date if obj.session else obj.requested_date
    
    def get_session_start_time(self, obj):
        """session이 있으면 session.start_time, 없으면 requested_start_time"""
        return obj.session.start_time if obj.session else obj.requested_start_time
    
    def get_session_location(self, obj):
        """session이 있으면 session.location, 없으면 None"""
        return obj.session.location if obj.session else None
    
    def get_session_duration_minutes(self, obj):
        """session이 있으면 duration_minutes, 없으면 None"""
        return obj.session.duration_minutes if obj.session else None
    
    def get_session_end_time(self, obj):
        if not obj.session or not obj.session.start_time or not obj.session.duration_minutes:
            return None
        dt = datetime.combine(obj.session.date, obj.session.start_time)
        return (dt + timedelta(minutes=obj.session.duration_minutes)).time()

    def get_lecture_title(self, obj):
        enrollment = getattr(obj, "enrollment", None)
        lecture = getattr(enrollment, "lecture", None) if enrollment else None
        return getattr(lecture, "title", None) if lecture else None

    def get_lecture_color(self, obj):
        enrollment = getattr(obj, "enrollment", None)
        lecture = getattr(enrollment, "lecture", None) if enrollment else None
        return getattr(lecture, "color", None) if lecture else None

    def get_lecture_chip_label(self, obj):
        enrollment = getattr(obj, "enrollment", None)
        lecture = getattr(enrollment, "lecture", None) if enrollment else None
        return getattr(lecture, "chip_label", None) if lecture else None

    def _get_unresolved_clinic_enrollment_ids(self) -> set:
        """list 직렬화 시 미해결 자동 ClinicLink가 있는 enrollment_id 집합을 1회 prefetch.

        get_name_highlight_clinic_target N+1 회피용. tenant 격리는 ClinicLink.tenant FK로 직접 필터.
        """
        ctx = self.context
        if "_unresolved_clinic_eids" in ctx:
            return ctx["_unresolved_clinic_eids"]

        request = ctx.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if not tenant:
            ctx["_unresolved_clinic_eids"] = set()
            return set()

        # parent의 instance(list)에서 enrollment_id 수집
        enrollment_ids = set()
        instances = getattr(self.parent, "instance", None) if self.parent else None
        if instances is not None and hasattr(instances, "__iter__"):
            for p in instances:
                eid = getattr(p, "enrollment_id", None)
                if eid:
                    enrollment_ids.add(int(eid))
        elif hasattr(self, "instance") and self.instance:
            eid = getattr(self.instance, "enrollment_id", None)
            if eid:
                enrollment_ids.add(int(eid))

        if not enrollment_ids:
            ctx["_unresolved_clinic_eids"] = set()
            return set()

        from apps.domains.progress.models import ClinicLink
        unresolved = set(
            ClinicLink.objects
            .filter(
                tenant=tenant,
                is_auto=True,
                resolved_at__isnull=True,
                enrollment_id__in=enrollment_ids,
            )
            .values_list("enrollment_id", flat=True)
            .distinct()
        )
        ctx["_unresolved_clinic_eids"] = unresolved
        return unresolved

    def get_name_highlight_clinic_target(self, obj):
        """클리닉 대상(미해결) + 미출석이면 True"""
        # 이미 출석(ATTENDED)이면 False
        if getattr(obj, "status", None) == "attended":
            return False
        eid = getattr(obj, "enrollment_id", None)
        if not eid:
            return False
        # bulk prefetch한 set에서 O(1) 룩업으로 N+1 회피.
        return int(eid) in self._get_unresolved_clinic_enrollment_ids()

    def get_profile_photo_url(self, obj):
        """학생 프로필 사진 R2 presigned URL"""
        student = getattr(obj, "student", None)
        if not student:
            return None
        r2_key = getattr(student, "profile_photo_r2_key", None) or ""
        if not r2_key:
            return None
        try:
            from django.conf import settings
            from libs.r2_client.presign import create_presigned_get_url
            return create_presigned_get_url(r2_key, expires_in=3600, bucket=settings.R2_STORAGE_BUCKET)
        except Exception:
            return None


class ClinicSessionParticipantCreateSerializer(serializers.ModelSerializer):
    """
    ✅ 예약 등록(생성) 전용
    - 선생: student, enrollment_id 직접 지정, session 필수
    - 학생: student 생략 가능 (자동 설정), source="student_request", status="pending"
    - 학생 신청 시: session 또는 (requested_date + requested_start_time) 필수
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and hasattr(request, "tenant") and request.tenant:
            tenant = request.tenant
            self.fields["session"].queryset = Session.objects.filter(tenant=tenant)
            self.fields["enrollment_id"].queryset = Enrollment.objects.filter(tenant=tenant)

    # FK 전환 호환: 프론트가 enrollment_id로 보내면 enrollment FK로 매핑
    enrollment_id = serializers.PrimaryKeyRelatedField(
        source="enrollment",
        queryset=Enrollment.objects.none(),  # __init__에서 tenant-scoped로 교체
        required=False,
        allow_null=True,
    )

    class Meta:
        model = SessionParticipant
        fields = [
            "session",
            "requested_date",  # ✅ 학생 신청 시 날짜
            "requested_start_time",  # ✅ 학생 신청 시 시간
            "student",
            "status",
            "memo",
            "source",
            "enrollment_id",
            "clinic_reason",
            "participant_role",
        ]
        extra_kwargs = {
            "student": {"required": False},  # 학생 신청 시 생략 가능
            "session": {"required": False},  # 학생 신청 시 세션이 없을 수 있음
            "requested_date": {"required": False},
            "requested_start_time": {"required": False},
        }
    
    def validate(self, attrs):
        """session 또는 (requested_date + requested_start_time) 중 하나는 필수"""
        session = attrs.get("session")
        requested_date = attrs.get("requested_date")
        requested_start_time = attrs.get("requested_start_time")
        
        if not session and not (requested_date and requested_start_time):
            raise serializers.ValidationError(
                "session 또는 (requested_date + requested_start_time) 중 하나는 필수입니다."
            )
        
        if session and (requested_date or requested_start_time):
            raise serializers.ValidationError(
                "session과 requested_date/requested_start_time을 동시에 사용할 수 없습니다."
            )
        
        return attrs


class ClinicSessionBulkCreateSerializer(serializers.Serializer):
    """
    POST /clinic/sessions/bulk-create/ 전용 직렬화기
    - dates 배열 (최대 20일) + 공통 세션 필드
    """
    title = serializers.CharField(required=False, allow_blank=True, default="")
    start_time = serializers.TimeField()
    duration_minutes = serializers.IntegerField(min_value=1)
    location = serializers.CharField(max_length=200)
    max_participants = serializers.IntegerField(min_value=1, default=20)
    target_grade = serializers.IntegerField(required=False, allow_null=True, default=None)
    target_school_type = serializers.CharField(required=False, allow_null=True, default=None)
    section_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    target_lecture_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=[]
    )
    dates = serializers.ListField(
        child=serializers.DateField(), min_length=1, max_length=20
    )

    def validate_target_grade(self, value):
        if value is not None and value not in range(1, 7):  # 1~6 (초등 포함)
            raise serializers.ValidationError("학년은 1~6 중 하나여야 합니다.")
        return value

    def validate_target_school_type(self, value):
        if value is not None and value not in ("ELEMENTARY", "MIDDLE", "HIGH"):
            raise serializers.ValidationError("학교 유형은 ELEMENTARY, MIDDLE, HIGH 중 하나여야 합니다.")
        return value


class ClinicTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Test
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and hasattr(request, "tenant") and request.tenant:
            tenant = request.tenant
            if "session" in self.fields:
                self.fields["session"].queryset = Session.objects.filter(tenant=tenant)


class ClinicSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Submission
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request and hasattr(request, "tenant") and request.tenant:
            tenant = request.tenant
            if "test" in self.fields:
                self.fields["test"].queryset = Test.objects.filter(tenant=tenant)
            if "student" in self.fields:
                self.fields["student"].queryset = Student.objects.filter(tenant=tenant)

    def validate_score(self, value):
        if value is None:
            return value
        if float(value) < 0:
            raise serializers.ValidationError("score는 0 이상이어야 합니다.")
        return value

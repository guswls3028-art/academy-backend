# apps/domains/attendance/serializers.py

from rest_framework import serializers
from .models import Attendance
from apps.support.attendance.serializer_dependencies import (
    clinic_highlight_map_for_attendance,
    enrollment_queryset_for_attendance_serializer,
    session_queryset_for_attendance_serializer,
)


class AttendanceSerializer(serializers.ModelSerializer):
    session = serializers.PrimaryKeyRelatedField(
        queryset=session_queryset_for_attendance_serializer(),
    )
    enrollment_id = serializers.PrimaryKeyRelatedField(
        source="enrollment",
        queryset=enrollment_queryset_for_attendance_serializer(),
    )
    student_id = serializers.IntegerField(
        source="enrollment.student_id",
        read_only=True,
    )
    name = serializers.CharField(
        source="enrollment.student.name",
        read_only=True,
    )
    parent_phone = serializers.CharField(
        source="enrollment.student.parent_phone",
        read_only=True,
    )
    phone = serializers.CharField(
        source="enrollment.student.phone",
        read_only=True,
        allow_null=True,
    )
    lecture_title = serializers.CharField(
        source="session.lecture.title",
        read_only=True,
    )
    lecture_color = serializers.CharField(
        source="session.lecture.color",
        read_only=True,
        default="#3b82f6",
    )
    profile_photo_url = serializers.SerializerMethodField()
    name_highlight_clinic_target = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if tenant:
            self.fields["session"].queryset = session_queryset_for_attendance_serializer(tenant)
            self.fields["enrollment_id"].queryset = enrollment_queryset_for_attendance_serializer(tenant)

    class Meta:
        model = Attendance
        fields = [
            "id",
            "session",
            "enrollment_id",
            "student_id",
            "status",
            "memo",
            "name",
            "parent_phone",
            "phone",
            "lecture_title",
            "lecture_color",
            "profile_photo_url",
            "name_highlight_clinic_target",
        ]

    def validate(self, attrs):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        instance = self.instance

        session = attrs.get("session", getattr(instance, "session", None))
        enrollment = attrs.get("enrollment", getattr(instance, "enrollment", None))

        if instance is not None:
            if "session" in attrs and session and session.id != instance.session_id:
                raise serializers.ValidationError(
                    {"session": "출결의 차시는 단건 수정으로 변경할 수 없습니다."}
                )
            if "enrollment" in attrs and enrollment and enrollment.id != instance.enrollment_id:
                raise serializers.ValidationError(
                    {"enrollment_id": "출결의 수강 등록은 단건 수정으로 변경할 수 없습니다."}
                )

        if tenant is not None:
            if session is not None and session.lecture.tenant_id != tenant.id:
                raise serializers.ValidationError(
                    {"session": "현재 학원의 차시만 사용할 수 있습니다."}
                )
            if enrollment is not None and enrollment.tenant_id != tenant.id:
                raise serializers.ValidationError(
                    {"enrollment_id": "현재 학원의 수강 등록만 사용할 수 있습니다."}
                )

        if session is not None and enrollment is not None:
            if enrollment.lecture_id != session.lecture_id:
                raise serializers.ValidationError(
                    {"enrollment_id": "해당 차시의 강의에 등록된 수강생만 출결에 추가할 수 있습니다."}
                )

        return attrs

    def get_profile_photo_url(self, obj):
        student = getattr(getattr(obj, "enrollment", None), "student", None)
        if not student:
            return None
        r2_key = getattr(student, "profile_photo_r2_key", None) or ""
        if not r2_key:
            return None
        try:
            from django.conf import settings
            from academy.adapters.storage.r2_presign import create_presigned_get_url
            return create_presigned_get_url(r2_key, expires_in=3600, bucket=settings.R2_STORAGE_BUCKET)
        except Exception:
            return None

    def _get_clinic_highlight_map(self):
        ctx = self.context
        if "_clinic_highlight_map" in ctx:
            return ctx["_clinic_highlight_map"]

        request = ctx.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        if not tenant:
            ctx["_clinic_highlight_map"] = {}
            return {}

        instances = getattr(self.parent, "instance", None) if self.parent else None
        enrollment_ids = set()
        if instances is not None and hasattr(instances, "__iter__"):
            for att in instances:
                eid = getattr(att, "enrollment_id", None)
                if eid:
                    enrollment_ids.add(int(eid))

        if not enrollment_ids:
            ctx["_clinic_highlight_map"] = {}
            return {}

        highlight_map = clinic_highlight_map_for_attendance(
            tenant=tenant,
            enrollment_ids=enrollment_ids,
        )
        ctx["_clinic_highlight_map"] = highlight_map
        return highlight_map

    def get_name_highlight_clinic_target(self, obj):
        highlight_map = self._get_clinic_highlight_map()
        eid = getattr(obj, "enrollment_id", None)
        return highlight_map.get(int(eid), False) if eid else False


class AttendanceMatrixStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    name = serializers.CharField()
    phone = serializers.CharField(allow_null=True)
    parent_phone = serializers.CharField(allow_null=True)
    profile_photo_url = serializers.CharField(allow_null=True, required=False)
    name_highlight_clinic_target = serializers.BooleanField(default=False, required=False)
    attendance = serializers.DictField()

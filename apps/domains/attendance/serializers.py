# apps/domains/attendance/serializers.py

from rest_framework import serializers
from .models import Attendance


class AttendanceSerializer(serializers.ModelSerializer):
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

    def get_profile_photo_url(self, obj):
        student = getattr(getattr(obj, "enrollment", None), "student", None)
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

        from apps.domains.results.utils.clinic_highlight import compute_clinic_highlight_map
        highlight_map = compute_clinic_highlight_map(tenant=tenant, enrollment_ids=enrollment_ids)
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

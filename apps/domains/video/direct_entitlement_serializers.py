from rest_framework import serializers

from apps.domains.video.models import DirectVideoEntitlement
from apps.domains.video.services.direct_entitlements import (
    get_active_direct_video_entitlement,
)


class DirectVideoEntitlementSerializer(serializers.ModelSerializer):
    student_id = serializers.IntegerField(read_only=True)
    student_name = serializers.CharField(source="student.name", read_only=True)
    student_school = serializers.SerializerMethodField()
    student_grade = serializers.IntegerField(source="student.grade", read_only=True)
    video_id = serializers.IntegerField(read_only=True)
    video_title = serializers.CharField(source="video.title", read_only=True)
    lecture_title = serializers.CharField(
        source="video.session.lecture.title",
        read_only=True,
    )
    state = serializers.SerializerMethodField()

    class Meta:
        model = DirectVideoEntitlement
        fields = (
            "id",
            "student_id",
            "student_name",
            "student_school",
            "student_grade",
            "video_id",
            "video_title",
            "lecture_title",
            "state",
            "reason",
            "granted_at",
            "revoked_at",
            "revoke_reason",
        )
        read_only_fields = fields

    def get_student_school(self, obj) -> str:
        student = obj.student
        return str(
            student.high_school
            or student.middle_school
            or student.elementary_school
            or ""
        )

    def get_state(self, obj) -> str:
        if obj.revoked_at is not None:
            return "REVOKED"
        active = get_active_direct_video_entitlement(
            tenant=obj.tenant,
            student=obj.student,
            video=obj.video,
        )
        return "ACTIVE" if active is not None and active.id == obj.id else "INELIGIBLE"


class DirectVideoEntitlementGrantSerializer(serializers.Serializer):
    student_id = serializers.IntegerField(min_value=1)
    video_id = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(min_length=1, max_length=2000, trim_whitespace=True)
    confirmed_regrant = serializers.BooleanField(default=False)


class DirectVideoEntitlementRevokeSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=1, max_length=2000, trim_whitespace=True)


class DirectVideoEntitlementErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()


class DirectVideoEntitlementMutationSerializer(serializers.Serializer):
    entitlement = DirectVideoEntitlementSerializer()
    created = serializers.BooleanField()
    changed = serializers.BooleanField()

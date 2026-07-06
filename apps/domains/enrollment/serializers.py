from rest_framework import serializers

from .models import Enrollment, SessionEnrollment
from apps.support.enrollment.serializer_dependencies import (
    lecture_queryset,
    session_queryset,
)


class StudentShortSerializer(serializers.Serializer):
    """강의/수강 맥락에서 노출하는 학생 최소 정보. Student 모델 스펙과 정합성 유지."""

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(read_only=True)
    grade = serializers.IntegerField(read_only=True, allow_null=True)
    school_type = serializers.CharField(read_only=True, allow_null=True)
    elementary_school = serializers.CharField(read_only=True, allow_null=True)
    high_school = serializers.CharField(read_only=True, allow_null=True)
    middle_school = serializers.CharField(read_only=True, allow_null=True)
    high_school_class = serializers.CharField(read_only=True, allow_null=True)
    major = serializers.CharField(read_only=True, allow_null=True)
    origin_middle_school = serializers.CharField(read_only=True, allow_null=True)
    phone = serializers.CharField(read_only=True, allow_null=True)
    parent_phone = serializers.CharField(read_only=True, allow_null=True)


class EnrollmentSerializer(serializers.ModelSerializer):
    student = StudentShortSerializer(read_only=True)
    tenant = serializers.PrimaryKeyRelatedField(read_only=True)
    lecture = serializers.PrimaryKeyRelatedField(
        queryset=lecture_queryset(),
    )

    class Meta:
        model = Enrollment
        fields = [
            "id", "tenant", "student", "lecture", "status",
            "enrolled_at", "created_at", "updated_at",
        ]

    def validate(self, attrs):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        instance = self.instance
        lecture = attrs.get("lecture", getattr(instance, "lecture", None))

        if instance is not None and "lecture" in attrs and lecture.id != instance.lecture_id:
            raise serializers.ValidationError(
                {"lecture": "수강 등록의 강의는 단건 수정으로 변경할 수 없습니다."}
            )

        if tenant is not None and lecture is not None and lecture.tenant_id != tenant.id:
            raise serializers.ValidationError(
                {"lecture": "현재 학원의 강의만 사용할 수 있습니다."}
            )

        return attrs


class SessionEnrollmentSerializer(serializers.ModelSerializer):
    tenant = serializers.PrimaryKeyRelatedField(read_only=True)
    session = serializers.PrimaryKeyRelatedField(
        queryset=session_queryset(),
    )
    enrollment = serializers.PrimaryKeyRelatedField(
        queryset=Enrollment.objects.select_related("lecture", "student").all(),
    )
    student_name = serializers.CharField(
        source="enrollment.student.name", read_only=True
    )
    student_id = serializers.IntegerField(
        source="enrollment.student_id", read_only=True
    )
    # 학원장이 "직전 차시 불러오기" 명단에서 동명이인 식별할 수 있도록 학교·학년 노출.
    # school은 school_type 기반으로 high/middle/elementary 중 하나를 합성 (frontend mapStudent와 동일 규칙).
    student_grade = serializers.IntegerField(
        source="enrollment.student.grade", read_only=True, allow_null=True
    )
    student_school = serializers.SerializerMethodField()

    class Meta:
        model = SessionEnrollment
        fields = [
            "id", "tenant", "session", "enrollment",
            "student_name", "student_id", "student_school", "student_grade",
            "created_at",
        ]

    def validate(self, attrs):
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None) if request else None
        instance = self.instance

        session = attrs.get("session", getattr(instance, "session", None))
        enrollment = attrs.get("enrollment", getattr(instance, "enrollment", None))

        if instance is not None:
            if "session" in attrs and session.id != instance.session_id:
                raise serializers.ValidationError(
                    {"session": "차시 수강의 차시는 단건 수정으로 변경할 수 없습니다."}
                )
            if "enrollment" in attrs and enrollment.id != instance.enrollment_id:
                raise serializers.ValidationError(
                    {"enrollment": "차시 수강의 수강 등록은 단건 수정으로 변경할 수 없습니다."}
                )

        if tenant is not None:
            if session is not None and session.lecture.tenant_id != tenant.id:
                raise serializers.ValidationError(
                    {"session": "현재 학원의 차시만 사용할 수 있습니다."}
                )
            if enrollment is not None and enrollment.tenant_id != tenant.id:
                raise serializers.ValidationError(
                    {"enrollment": "현재 학원의 수강 등록만 사용할 수 있습니다."}
                )

        if session is not None and enrollment is not None:
            if enrollment.lecture_id != session.lecture_id:
                raise serializers.ValidationError(
                    {"enrollment": "해당 차시의 강의에 등록된 수강생만 추가할 수 있습니다."}
                )

        return attrs

    def get_student_school(self, obj):
        """
        school_type SSOT 우선. school_type 이 가리키는 필드 1개만 노출.
        과거 데이터가 high_school·middle_school 모두에 남아있어도
        현재 학교급에 맞는 학교명을 정확히 표시.
        """
        student = getattr(getattr(obj, "enrollment", None), "student", None)
        if student is None:
            return None
        stype = (getattr(student, "school_type", "") or "").upper()
        if stype == "HIGH":
            return getattr(student, "high_school", None) or None
        if stype == "MIDDLE":
            return getattr(student, "middle_school", None) or None
        if stype == "ELEMENTARY":
            return getattr(student, "elementary_school", None) or None
        # school_type 미설정 데이터 fallback (일관성 위해 학교급 순서대로)
        return (
            getattr(student, "high_school", None)
            or getattr(student, "middle_school", None)
            or getattr(student, "elementary_school", None)
            or None
        )

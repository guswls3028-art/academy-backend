# PATH: apps/domains/lectures/serializers.py

from rest_framework import serializers
from .models import Lecture, Session, Section, SectionAssignment


class LectureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lecture
        fields = [
            "id", "tenant", "title", "name", "subject", "description",
            "start_date", "end_date", "lecture_time",
            "color", "chip_label", "is_active", "is_system",
            "created_at", "updated_at",
        ]
        read_only_fields = ["tenant"]
        ref_name = "Lecture"

    def validate(self, attrs):
        start = attrs.get("start_date") or (self.instance and self.instance.start_date)
        end = attrs.get("end_date") or (self.instance and self.instance.end_date)
        if start and end and start > end:
            raise serializers.ValidationError(
                {"end_date": "종료일은 시작일보다 같거나 이후여야 합니다."}
            )
        return attrs


class SessionSerializer(serializers.ModelSerializer):
    order = serializers.IntegerField(required=False, allow_null=True)
    section_label = serializers.CharField(source="section.label", read_only=True, default=None)
    section_type = serializers.CharField(source="section.section_type", read_only=True, default=None)

    class Meta:
        model = Session
        fields = [
            "id", "lecture", "section", "order", "title", "date",
            "section_label", "section_type",
            "created_at", "updated_at",
        ]
        ref_name = "LectureSession"
        # UniqueConstraint validators를 비활성화 — order auto-assign과 충돌 방지
        # 커스텀 validate() + DB 제약에서 중복 검증
        validators = []

    def validate(self, attrs):
        """
        section이 제공된 경우, 해당 section이 같은 tenant + 같은 lecture에 속하는지 검증.
        날짜가 강의 기간 범위를 벗어나면 경고 (차단하지는 않음 — 보강 차시 등 운영 패턴 존재).
        order 중복 시 명확한 에러 메시지.
        """
        section = attrs.get("section")
        lecture = attrs.get("lecture")

        if section and lecture:
            if section.lecture_id != lecture.id:
                raise serializers.ValidationError(
                    {"section": "반은 해당 강의에 속한 반이어야 합니다."}
                )
            if section.tenant_id != lecture.tenant_id:
                raise serializers.ValidationError(
                    {"section": "반과 강의의 학원이 일치하지 않습니다."}
                )

        # order 중복 검증 (DB 제약과 별도로 명확한 에러 메시지 제공)
        order = attrs.get("order")
        if order is not None and lecture:
            qs = Session.objects.filter(lecture=lecture, order=order, section=section)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                section_label = f" ({section.label}반)" if section else ""
                raise serializers.ValidationError(
                    {"order": f"이 강의{section_label}에 이미 {order}차시가 존재합니다."}
                )

        # 날짜 범위 경고 (ValidationError가 아닌 non_field_errors 수준 경고)
        date = attrs.get("date")
        if date and lecture:
            if lecture.start_date and date < lecture.start_date:
                # 경고만 — 차단하지 않음 (보강 차시 등 정상 운영 패턴)
                pass
            if lecture.end_date and date > lecture.end_date:
                pass

        return attrs


class SectionSerializer(serializers.ModelSerializer):
    day_of_week_display = serializers.CharField(source="get_day_of_week_display", read_only=True)
    section_type_display = serializers.CharField(source="get_section_type_display", read_only=True)
    assignment_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Section
        fields = [
            "id", "tenant", "lecture", "label", "section_type",
            "day_of_week", "day_of_week_display", "section_type_display",
            "start_time", "end_time", "location", "max_capacity",
            "is_active", "assignment_count",
            "created_at", "updated_at",
        ]
        read_only_fields = ["tenant"]
        ref_name = "LectureSection"


class SectionAssignmentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="enrollment.student.name", read_only=True)
    student_id = serializers.IntegerField(source="enrollment.student_id", read_only=True)
    lecture_id = serializers.IntegerField(source="enrollment.lecture_id", read_only=True)
    class_section_label = serializers.CharField(source="class_section.label", read_only=True)
    clinic_section_label = serializers.CharField(
        source="clinic_section.label", read_only=True, default=None,
    )
    source_display = serializers.CharField(source="get_source_display", read_only=True)

    class Meta:
        model = SectionAssignment
        fields = [
            "id", "tenant", "enrollment", "class_section", "clinic_section", "source",
            "student_name", "student_id", "lecture_id",
            "class_section_label", "clinic_section_label", "source_display",
            "created_at", "updated_at",
        ]
        read_only_fields = ["tenant"]
        ref_name = "SectionAssignment"

    def validate(self, attrs):
        class_section = attrs.get("class_section")
        if class_section and class_section.section_type != "CLASS":
            raise serializers.ValidationError(
                {"class_section": "수업 반에는 CLASS 타입의 반만 지정할 수 있습니다."}
            )
        clinic_section = attrs.get("clinic_section")
        if clinic_section and clinic_section.section_type != "CLINIC":
            raise serializers.ValidationError(
                {"clinic_section": "클리닉 반에는 CLINIC 타입의 반만 지정할 수 있습니다."}
            )
        return attrs

# PATH: apps/domains/lectures/serializers.py

from rest_framework import serializers
from .models import Lecture, Session, Section, SectionAssignment


class LectureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lecture
        fields = "__all__"
        read_only_fields = ["tenant"]
        ref_name = "Lecture"


class SessionSerializer(serializers.ModelSerializer):
    order = serializers.IntegerField(required=False, allow_null=True)
    section_label = serializers.CharField(source="section.label", read_only=True, default=None)
    section_type = serializers.CharField(source="section.section_type", read_only=True, default=None)

    class Meta:
        model = Session
        fields = "__all__"
        ref_name = "LectureSession"


class SectionSerializer(serializers.ModelSerializer):
    day_of_week_display = serializers.CharField(source="get_day_of_week_display", read_only=True)
    section_type_display = serializers.CharField(source="get_section_type_display", read_only=True)
    assignment_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Section
        fields = "__all__"
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
        fields = "__all__"
        read_only_fields = ["tenant"]
        ref_name = "SectionAssignment"

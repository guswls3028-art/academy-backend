# apps/domains/results/serializers/exam_attempt.py (신규)

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.domains.results.models import ExamAttempt
from apps.domains.results.services.attempt_service import ExamAttemptService


class ExamAttemptSerializer(serializers.ModelSerializer):
    tenant_related_fields = ("exam", "enrollment", "clinic_link")
    submission_id = serializers.IntegerField(min_value=1, required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None)

        for field_name in self.tenant_related_fields:
            field = self.fields[field_name]
            queryset = getattr(field, "queryset", None)
            if queryset is None:
                continue
            if not tenant:
                field.queryset = queryset.none()
                continue
            if field_name == "exam":
                field.queryset = queryset.filter(
                    tenant=tenant,
                    exam_type="regular",
                    is_active=True,
                    sessions__lecture__tenant=tenant,
                ).distinct()
            else:
                field.queryset = queryset.filter(tenant=tenant)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError({"detail": "학원 정보가 필요합니다."})

        related_objects = {
            field_name: attrs.get(
                field_name,
                getattr(self.instance, field_name, None) if self.instance else None,
            )
            for field_name in self.tenant_related_fields
        }
        errors = {}
        for field_name, related in related_objects.items():
            if related is not None and related.tenant_id != tenant.id:
                errors[field_name] = "다른 학원의 항목은 사용할 수 없습니다."

        enrollment = related_objects["enrollment"]
        clinic_link = related_objects["clinic_link"]
        exam = related_objects["exam"]
        if (
            exam is not None
            and enrollment is not None
            and not exam.sessions.filter(
                lecture_id=enrollment.lecture_id,
                lecture__tenant=tenant,
            ).exists()
        ):
            errors["enrollment"] = "시험이 연결된 강의의 수강 등록만 사용할 수 있습니다."
        if (
            clinic_link is not None
            and enrollment is not None
            and clinic_link.enrollment_id != enrollment.id
        ):
            errors["clinic_link"] = "응시 수강 등록과 같은 클리닉 항목만 사용할 수 있습니다."
        if (
            clinic_link is not None
            and exam is not None
            and (
                clinic_link.source_type != "exam"
                or clinic_link.source_id != exam.id
                or clinic_link.resolved_at is not None
                or not exam.sessions.filter(id=clinic_link.session_id).exists()
            )
        ):
            errors["clinic_link"] = "해당 시험의 미해소 클리닉 항목만 사용할 수 있습니다."

        submission_id = attrs.get(
            "submission_id",
            self.instance.submission_id if self.instance else None,
        )
        if submission_id is not None and exam is not None and enrollment is not None:
            Submission = django_apps.get_model("submissions", "Submission")
            if not Submission.objects.filter(
                id=submission_id,
                tenant=tenant,
                target_type="exam",
                target_id=exam.id,
                enrollment_id=enrollment.id,
            ).exists():
                errors["submission_id"] = (
                    "해당 학원·시험·수강 등록에 속한 제출만 사용할 수 있습니다."
                )

        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def create(self, validated_data):
        exam = validated_data["exam"]
        enrollment = validated_data["enrollment"]
        clinic_link = validated_data.get("clinic_link")
        try:
            return ExamAttemptService.create_for_submission(
                exam_id=exam.id,
                enrollment_id=enrollment.id,
                submission_id=validated_data["submission_id"],
                clinic_link_id=clinic_link.id if clinic_link else None,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"detail": exc.messages}) from exc

    class Meta:
        model = ExamAttempt
        fields = "__all__"
        read_only_fields = (
            "attempt_index",
            "is_retake",
            "is_representative",
            "status",
            "meta",
        )

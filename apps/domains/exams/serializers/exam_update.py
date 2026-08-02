from rest_framework import serializers
from apps.domains.exams.models import Exam


class ExamUpdateSerializer(serializers.ModelSerializer):
    """
    수정 전용 serializer
    - exam_type, subject 직접 변경 ❌
    - template_exam_id: regular 시험에서 시험 설정으로 템플릿 지정 가능 (한 번만)
    - 2026-05-13 학원장 결정: 시험 단위 status(OPEN/CLOSED) 폐기.
      학생별 Achievement SSOT 가 단일 진실. status 필드는 PATCH 대상에서 제외.
    """

    class Meta:
        model = Exam
        fields = [
            "title",
            "description",
            "is_active",
            # "status",  # 폐기 (2026-05-13). 학생별 Achievement SSOT.
            "template_exam_id",
            "subject",
            "allow_retake",
            "max_attempts",
            "pass_score",
            "max_score",
            "grading_mode",
            "manual_grading_method",
            "choice_question_count",
            "display_order",
            "open_at",
            "close_at",
            "answer_visibility",
        ]

    def validate(self, attrs):
        exam: Exam = self.instance

        # P1-5: 시험 유효성 검증
        max_attempts = attrs.get("max_attempts", exam.max_attempts)
        pass_score = attrs.get("pass_score", exam.pass_score)
        max_score = attrs.get("max_score", exam.max_score)
        open_at = attrs.get("open_at", exam.open_at)
        close_at = attrs.get("close_at", exam.close_at)

        errors = {}
        if max_attempts is not None and max_attempts < 1:
            errors["max_attempts"] = "1 이상이어야 합니다."
        if pass_score is not None and max_score is not None and pass_score > max_score:
            errors["pass_score"] = f"합격 점수({pass_score})가 만점({max_score})을 초과할 수 없습니다."
        if open_at and close_at and open_at >= close_at:
            errors["close_at"] = "마감 시각이 시작 시각 이후여야 합니다."
        if errors:
            raise serializers.ValidationError(errors)

        grading_mode = attrs.get("grading_mode", exam.grading_mode)
        choice_question_count = attrs.get(
            "choice_question_count",
            exam.choice_question_count,
        )
        if (
            grading_mode == Exam.GradingMode.MIXED
            and int(choice_question_count or 0) < 1
        ):
            raise serializers.ValidationError(
                {
                    "choice_question_count": (
                        "혼합형 시험은 앞쪽 선택형 문항 수를 1개 이상 입력해 주세요."
                    )
                }
            )

        choice_boundary_changed = (
            "choice_question_count" in attrs
            and attrs["choice_question_count"] != exam.choice_question_count
        )
        sheet = getattr(exam, "sheet", None)
        sheet_choice_count = int(getattr(sheet, "choice_count", 0) or 0)
        sheet_total_questions = int(getattr(sheet, "total_questions", 0) or 0)
        repairs_legacy_boundary = (
            choice_boundary_changed
            and int(exam.choice_question_count or 0) == 0
            and int(attrs["choice_question_count"] or 0) == sheet_choice_count
            and 0 < sheet_choice_count < sheet_total_questions
        )
        if (
            choice_boundary_changed
            and sheet is not None
            and not repairs_legacy_boundary
        ):
            raise serializers.ValidationError(
                {
                    "choice_question_count": (
                        "문항이 생성된 시험의 선택형 문항 경계는 변경할 수 없습니다. "
                        "빈 시험을 새로 만들거나 문항 생성 전에 변경해 주세요."
                    )
                }
            )

        if exam.exam_type == Exam.ExamType.TEMPLATE:
            return attrs

        tid = attrs.get("template_exam_id")
        if tid is not None:
            # cross-tenant 차단: 자기 테넌트의 template 만 허용.
            request = self.context.get("request") if hasattr(self, "context") else None
            tenant = getattr(request, "tenant", None) or getattr(exam, "tenant", None)
            qs = Exam.objects.all()
            if tenant is not None:
                qs = qs.filter(tenant=tenant)
            try:
                t = qs.get(id=int(tid))
            except (TypeError, ValueError, Exam.DoesNotExist):
                raise serializers.ValidationError({"template_exam_id": "invalid"})
            if t.exam_type != Exam.ExamType.TEMPLATE:
                raise serializers.ValidationError({"template_exam_id": "must be template exam"})
            attrs["template_exam"] = t
            attrs["subject"] = t.subject

        return attrs

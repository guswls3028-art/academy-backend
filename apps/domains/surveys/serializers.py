import re

from rest_framework import serializers

from .models import Survey


class SurveyWriteSerializer(serializers.ModelSerializer):
    expected_version = serializers.IntegerField(min_value=0, write_only=True, required=False)

    class Meta:
        model = Survey
        fields = ["title", "description", "kind", "questions", "audience", "score_defaults", "allow_attachments", "opens_at", "closes_at", "response_limit", "expected_version", "creation_key"]
        extra_kwargs = {"description": {"max_length": 5000}, "response_limit": {"min_value": 1, "max_value": 10000}}

    def validate_questions(self, questions):
        if not isinstance(questions, list) or len(questions) > 30:
            raise serializers.ValidationError("질문은 최대 30개까지 만들 수 있습니다.")
        seen = set()
        result = []
        for question in questions:
            if not isinstance(question, dict):
                raise serializers.ValidationError("질문 형식을 확인해 주세요.")
            key, label, kind = question.get("id"), question.get("label"), question.get("type")
            if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", key) or key in seen or key in {"__proto__", "prototype", "constructor"}:
                raise serializers.ValidationError("질문 식별자가 없거나 중복되었습니다.")
            if not isinstance(label, str) or not label.strip() or len(label) > 300:
                raise serializers.ValidationError("질문 제목은 1~300자로 입력해 주세요.")
            if kind not in {"text", "textarea", "number", "date", "single_choice", "multiple_choice"}:
                raise serializers.ValidationError("지원하지 않는 질문 유형입니다.")
            required = question.get("required", False)
            if not isinstance(required, bool):
                raise serializers.ValidationError("필수 응답 여부를 확인해 주세요.")
            options = question.get("options", [])
            if kind in {"single_choice", "multiple_choice"}:
                if not isinstance(options, list) or not 2 <= len(options) <= 30 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in options):
                    raise serializers.ValidationError("선택지는 2~30개, 각각 200자 이내로 입력해 주세요.")
                options = [v.strip() for v in options]
                if len(set(options)) != len(options):
                    raise serializers.ValidationError("같은 선택지를 중복해서 넣을 수 없습니다.")
            else:
                options = []
            seen.add(key)
            result.append({"id": key, "label": label.strip(), "type": kind, "required": required, "options": options})
        return result

    def validate_audience(self, audience):
        if not isinstance(audience, dict) or audience.get("mode") not in {"all", "selected", "lecture"}:
            raise serializers.ValidationError("응답 대상을 선택해 주세요.")
        mode = audience["mode"]
        ids = audience.get("student_ids", [])
        if not isinstance(ids, list) or len(ids) > 10000 or any(type(v) is not int or v <= 0 for v in ids):
            raise serializers.ValidationError("학생 목록을 확인해 주세요.")
        if mode == "selected" and not ids:
            raise serializers.ValidationError("학생을 한 명 이상 선택해 주세요.")
        lecture = audience.get("lecture_id")
        if mode == "lecture" and (type(lecture) is not int or lecture <= 0):
            raise serializers.ValidationError("강의를 선택해 주세요.")
        return {"mode": mode, "student_ids": sorted(set(ids)) if mode == "selected" else [], "lecture_id": lecture if mode == "lecture" else None}

    def validate_score_defaults(self, values):
        if not isinstance(values, dict):
            raise serializers.ValidationError("시험 정보를 확인해 주세요.")
        result = {}
        for key, value in values.items():
            if key in {"academic_year", "semester", "exam_month"}:
                bounds = {"academic_year": (2000, 2200), "semester": (1, 2), "exam_month": (1, 12)}[key]
                if type(value) is not int or not bounds[0] <= value <= bounds[1]:
                    raise serializers.ValidationError("학년도·학기·시행 월을 확인해 주세요.")
            elif key == "source":
                if value not in {"school_exam", "national_mock", "kice_mock"}:
                    raise serializers.ValidationError("시험 종류를 확인해 주세요.")
            elif key == "exam_round":
                if value not in {"first", "second", "performance", "other"}:
                    raise serializers.ValidationError("시험 시기를 확인해 주세요.")
            elif key in {"subject", "exam_name"}:
                if not isinstance(value, str) or not value.strip() or len(value) > (50 if key == "subject" else 80):
                    raise serializers.ValidationError("과목·시험명을 확인해 주세요.")
                value = value.strip()
            else:
                raise serializers.ValidationError("지원하지 않는 시험 설정입니다.")
            result[key] = value
        return result

    def validate(self, attrs):
        current = self.instance
        start = attrs.get("opens_at", getattr(current, "opens_at", None))
        end = attrs.get("closes_at", getattr(current, "closes_at", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"closes_at": "마감은 시작보다 늦어야 합니다."})
        defaults = attrs.get("score_defaults", getattr(current, "score_defaults", {}))
        source = defaults.get("source")
        school_fields = {"semester", "exam_round", "exam_name"}
        if (source != "school_exam" and school_fields.intersection(defaults)) or (source not in {"national_mock", "kice_mock"} and "exam_month" in defaults):
            raise serializers.ValidationError({"score_defaults": "시험 종류에 맞는 학기·시험 시기·시행 월만 설정해 주세요."})
        return attrs


class SurveyActionSerializer(serializers.Serializer):
    expected_version = serializers.IntegerField(min_value=1)


class SurveyResponseWriteSerializer(serializers.Serializer):
    answers = serializers.DictField(default=dict)
    attachment_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=10, default=list)
    score_submission_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=20, default=list)
    expected_version = serializers.IntegerField(min_value=0)


class SurveySelectedStudentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class SurveyReadSerializer(serializers.ModelSerializer):
    response_count = serializers.IntegerField()
    target_count = serializers.IntegerField(required=False)
    can_respond = serializers.BooleanField()
    answered = serializers.BooleanField()
    response_unavailable_reason = serializers.CharField(allow_blank=True)
    selected_students = SurveySelectedStudentSerializer(many=True, required=False)

    class Meta:
        model = Survey
        fields = ["id", "title", "description", "kind", "status", "questions", "score_defaults", "allow_attachments", "opens_at", "closes_at", "response_limit", "published_at", "created_at", "updated_at", "version", "response_count", "target_count", "can_respond", "answered", "response_unavailable_reason", "audience", "selected_students"]
        extra_kwargs = {"audience": {"required": False}}


class SurveyPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = SurveyReadSerializer(many=True)


class SurveyAttachmentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    size_bytes = serializers.IntegerField()
    content_type = serializers.CharField()
    r2_key = serializers.CharField()


class SurveyResponseReadSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    survey_id = serializers.IntegerField()
    survey_title = serializers.CharField()
    kind = serializers.ChoiceField(choices=Survey.Kind.choices)
    questions = serializers.JSONField()
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    student_ps = serializers.CharField()
    answers = serializers.JSONField()
    version = serializers.IntegerField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    attachments = SurveyAttachmentSerializer(many=True)
    score_submissions = serializers.ListField(child=serializers.DictField())


class StudentSurveyDetailSerializer(serializers.Serializer):
    survey = SurveyReadSerializer()
    response = SurveyResponseReadSerializer(allow_null=True)


class SurveyRecipientReadSerializer(serializers.Serializer):
    student_id = serializers.IntegerField()
    student_name = serializers.CharField()
    answered = serializers.BooleanField()
    response = SurveyResponseReadSerializer(allow_null=True)


class SurveyRecipientsPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    survey = SurveyReadSerializer()
    results = SurveyRecipientReadSerializer(many=True)


class SurveyHistoryPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    results = SurveyResponseReadSerializer(many=True)

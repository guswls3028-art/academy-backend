import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.support.surveys import audience_students, lock_response_student, response_materials
from .models import Survey, SurveyRecipient, SurveyResponse


class SurveyConflict(APIException):
    status_code = 409
    default_detail = "다른 곳에서 변경된 내용이 있습니다. 최신 내용을 확인한 뒤 다시 시도해 주세요."
    default_code = "survey_conflict"


def response_unavailable_reason(survey, response_count, has_response=False):
    now = timezone.now()
    if survey.status != Survey.Status.OPEN:
        return "응답이 마감되었습니다."
    if survey.opens_at and now < survey.opens_at:
        return "아직 응답 시작 전입니다."
    if survey.closes_at and now >= survey.closes_at:
        return "응답 기간이 끝났습니다."
    if not has_response and survey.response_limit and response_count >= survey.response_limit:
        return "신청 인원이 모두 찼습니다."
    return ""


def validate_answers(survey, answers):
    if len(json.dumps(answers, ensure_ascii=False).encode("utf-8")) > 40000:
        raise ValidationError("응답 내용이 너무 깁니다. 내용을 줄여 주세요.")
    known = {question["id"] for question in survey.questions}
    if set(answers) - known:
        raise ValidationError("설문 질문이 변경되었습니다. 입력을 복사해 보관한 뒤 새로고침해 주세요.")
    clean = {}
    for question in survey.questions:
        key, label, kind = question["id"], question["label"], question["type"]
        value = answers.get(key)
        missing = value is None or value == "" or value == [] or (isinstance(value, str) and not value.strip())
        if missing:
            if question["required"]:
                raise ValidationError({key: f"{label}: 필수 질문에 답해 주세요."})
            continue
        if kind in {"text", "textarea"}:
            limit = 5000 if kind == "textarea" else 500
            if not isinstance(value, str) or len(value) > limit:
                raise ValidationError({key: f"{label}: {limit}자 이내로 입력해 주세요."})
            value = value.strip()
        elif kind == "number":
            try:
                number = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise ValidationError({key: f"{label}: 숫자를 입력해 주세요."}) from None
            if isinstance(value, bool) or not number.is_finite() or abs(number) > 1000000000:
                raise ValidationError({key: f"{label}: 유효한 숫자를 입력해 주세요."})
            value = float(number)
        elif kind == "date":
            try:
                if not isinstance(value, str) or len(value) != 10:
                    raise ValueError
                value = date.fromisoformat(value).isoformat()
            except ValueError:
                raise ValidationError({key: f"{label}: 날짜를 확인해 주세요."}) from None
        elif kind == "single_choice":
            if not isinstance(value, str) or value not in question["options"]:
                raise ValidationError({key: f"{label}: 선택지에서 골라 주세요."})
        elif kind == "multiple_choice":
            if not isinstance(value, list) or len(value) > len(question["options"]) or any(not isinstance(v, str) or v not in question["options"] for v in value):
                raise ValidationError({key: f"{label}: 선택지에서 골라 주세요."})
            value = [v for v in question["options"] if v in value]
        clean[key] = value
    return clean


@transaction.atomic
def update_survey(*, tenant, survey_id, values):
    survey = Survey.objects.select_for_update().filter(tenant=tenant, id=survey_id).first()
    if not survey:
        raise NotFound()
    expected = values.pop("expected_version", None)
    if expected != survey.version:
        raise SurveyConflict()
    if "creation_key" in values:
        raise ValidationError("생성 요청 키는 수정할 수 없습니다.")
    frozen = {"kind", "questions", "audience", "score_defaults", "allow_attachments"}
    if survey.published_at and any(key in values and values[key] != getattr(survey, key) for key in frozen):
        raise ValidationError("배포한 설문의 질문·시험·대상·첨부 설정은 바꿀 수 없습니다. 새 설문으로 만들어 주세요.")
    limit = values.get("response_limit", survey.response_limit)
    if limit and limit < SurveyResponse.objects.filter(tenant=tenant, survey=survey).count():
        raise ValidationError("정원은 이미 응답한 인원보다 적게 설정할 수 없습니다.")
    for key, value in values.items():
        setattr(survey, key, value)
    survey.version += 1
    survey.save()
    return survey


@transaction.atomic
def publish_survey(*, tenant, survey_id, expected_version):
    survey = Survey.objects.select_for_update().filter(tenant=tenant, id=survey_id).first()
    if not survey:
        raise NotFound()
    if survey.version != expected_version:
        raise SurveyConflict()
    if survey.status == Survey.Status.OPEN:
        return survey
    if not survey.questions and survey.kind != Survey.Kind.SCHOOL_SCORE:
        raise ValidationError("질문을 한 개 이상 만들어 주세요.")
    if survey.closes_at and survey.closes_at <= timezone.now():
        raise ValidationError("마감일을 현재 이후로 바꾼 뒤 응답을 시작해 주세요.")
    if not survey.published_at:
        student_ids = list(audience_students(tenant=tenant, audience=survey.audience).values_list("id", flat=True)[:10001])
        if not student_ids:
            raise ValidationError("응답할 학생이 없습니다. 대상 학생·강의를 다시 확인해 주세요.")
        if len(student_ids) > 10000:
            raise ValidationError("한 설문에는 최대 10,000명을 지정할 수 있습니다. 대상을 나눠 주세요.")
        SurveyRecipient.objects.bulk_create([SurveyRecipient(tenant=tenant, survey=survey, student_id=student_id) for student_id in student_ids], batch_size=500)
        survey.published_at = timezone.now()
    survey.status = Survey.Status.OPEN
    survey.version += 1
    survey.save()
    return survey


@transaction.atomic
def close_survey(*, tenant, survey_id, expected_version):
    survey = Survey.objects.select_for_update().filter(tenant=tenant, id=survey_id).first()
    if not survey:
        raise NotFound()
    if survey.version != expected_version:
        raise SurveyConflict()
    if survey.status == Survey.Status.DRAFT:
        raise ValidationError("작성 중인 설문입니다. 먼저 응답을 시작해 주세요.")
    survey.status = Survey.Status.CLOSED
    survey.version += 1
    survey.save(update_fields=["status", "version", "updated_at"])
    return survey


@transaction.atomic
def save_response(*, tenant, student, user, survey_id, values):
    student = lock_response_student(tenant=tenant, student=student)
    if not student:
        raise PermissionDenied("학생 정보를 다시 확인해 주세요.")
    survey = Survey.objects.select_for_update().filter(tenant=tenant, id=survey_id).exclude(status=Survey.Status.DRAFT).first()
    if not survey or not SurveyRecipient.objects.filter(tenant=tenant, survey=survey, student=student).exists():
        raise NotFound("응답할 수 있는 설문을 찾을 수 없습니다.")
    answers = validate_answers(survey, values["answers"])
    files, scores = response_materials(tenant=tenant, student=student, survey=survey, file_ids=values["attachment_ids"], score_ids=values["score_submission_ids"])
    response = SurveyResponse.objects.filter(tenant=tenant, survey=survey, student=student).first()
    # A retried identical request remains successful even after the deadline.
    if response and response.answers == answers and sorted(response.attachments.values_list("id", flat=True)) == [file.id for file in files] and sorted(response.score_submissions.values_list("id", flat=True)) == [score.id for score in scores]:
        return response
    if values["expected_version"] != (response.version if response else 0):
        raise SurveyConflict("다른 화면에서 응답이 변경되었습니다. 입력은 유지됩니다. 저장된 응답을 확인해 주세요.")
    reason = response_unavailable_reason(survey, SurveyResponse.objects.filter(tenant=tenant, survey=survey).count(), bool(response))
    if reason:
        raise SurveyConflict(reason)
    if response:
        response.answers = answers
        response.submitted_by = user
        response.version += 1
        response.save(update_fields=["answers", "submitted_by", "version", "updated_at"])
    else:
        response = SurveyResponse.objects.create(tenant=tenant, survey=survey, student=student, submitted_by=user, answers=answers)
    response.attachments.set(files)
    response.score_submissions.set(scores)
    return response

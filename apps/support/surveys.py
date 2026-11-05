"""Survey boundaries to students, inventory and the existing reported-score owner."""
from rest_framework.exceptions import ValidationError

from apps.domains.enrollment.models import Enrollment
from apps.domains.inventory.models import InventoryFile
from apps.domains.results.models.student_reported_score import StudentReportedScore
from apps.domains.students.models import Student
from apps.support.results.student_reported_scores import serialize_reported_score


def audience_students(*, tenant, audience):
    students = Student.objects.filter(tenant=tenant, deleted_at__isnull=True)
    mode = audience.get("mode")
    if mode == "selected":
        ids = audience["student_ids"]
        students = students.filter(id__in=ids)
        if students.count() != len(ids):
            raise ValidationError("응답 대상에 선택할 수 없는 학생이 있습니다.")
    elif mode == "lecture":
        students = students.filter(id__in=Enrollment.objects.filter(tenant=tenant, lecture_id=audience["lecture_id"], lecture__tenant=tenant, status="ACTIVE").values("student_id"))
    elif mode != "all":
        raise ValidationError("응답 대상을 선택해 주세요.")
    return students.order_by("id")


def active_student(*, tenant, student_id):
    return Student.objects.filter(tenant=tenant, id=student_id, deleted_at__isnull=True).first()


def student_choices(*, tenant, search):
    rows = Student.objects.filter(tenant=tenant, deleted_at__isnull=True)
    if search:
        rows = rows.filter(name__icontains=search)
    return [{"id": row.id, "name": row.name, "school": row.high_school or row.middle_school or row.elementary_school or ""} for row in rows.order_by("name", "id")[:100]]


def selected_student_names(*, tenant, student_ids):
    return list(Student.objects.filter(tenant=tenant, id__in=student_ids, deleted_at__isnull=True).order_by("name", "id").values("id", "name"))


def resolve_response_student(request):
    from apps.domains.student_app.permissions import get_request_student
    return get_request_student(request)


def lock_response_student(*, tenant, student):
    from apps.domains.inventory.services import inventory_move_lock_token
    from apps.support.students.namespace_lock import lock_student_creation_tenant_reference, lock_student_ps_namespaces
    lock_student_creation_tenant_reference(tenant_id=tenant.id)
    lock_student_ps_namespaces(tenant_id=tenant.id, ps_numbers=(inventory_move_lock_token(scope="student", student_ps=student.ps_number),))
    return Student.objects.select_for_update().filter(tenant=tenant, id=student.id, deleted_at__isnull=True, ps_number=student.ps_number).first()


def response_materials(*, tenant, student, survey, file_ids, score_ids):
    ids = sorted(set(file_ids))
    score_ids = sorted(set(score_ids))
    scores = list(StudentReportedScore.objects.select_for_update().filter(tenant=tenant, student=student, id__in=score_ids).order_by("id"))
    if len(scores) != len(score_ids) or any(row.status not in {"pending", "verified"} for row in scores):
        raise ValidationError("성적표 제출 기록을 확인한 뒤 다시 선택해 주세요.")
    if survey.kind == "school_score":
        if not scores:
            raise ValidationError("성적표 원본과 점수를 제출한 뒤 설문을 완료해 주세요.")
        for score in scores:
            if any(getattr(score, key, None) != value for key, value in survey.score_defaults.items()):
                raise ValidationError("이 설문에서 요청한 시험·과목의 성적표를 선택해 주세요.")
        ids = sorted(set(ids + [row.evidence_file_id for row in scores if row.evidence_file_id]))
    elif scores:
        raise ValidationError("성적 취합 설문에서만 성적표를 연결할 수 있습니다.")
    if ids and not (survey.allow_attachments or survey.kind == "school_score"):
        raise ValidationError("이 설문은 첨부파일을 받지 않습니다.")
    files = list(InventoryFile.objects.select_for_update().filter(tenant=tenant, scope="student", student_ps=student.ps_number, id__in=ids).order_by("id"))
    if len(files) != len(ids) or len(files) > 10:
        raise ValidationError("본인의 첨부파일을 최대 10개까지 선택해 주세요.")
    if any(file.content_type not in {"image/png", "image/jpeg", "image/jpg", "application/pdf"} or file.size_bytes > 20 * 1024 * 1024 for file in files):
        raise ValidationError("첨부파일은 20MB 이하의 JPG·PNG·PDF만 사용할 수 있습니다.")
    return files, scores


def serialize_response_materials(response):
    files = [file for file in response.attachments.all() if file.tenant_id == response.tenant_id and file.scope == "student" and file.student_ps == response.student.ps_number]
    scores = [score for score in response.score_submissions.all() if score.tenant_id == response.tenant_id and score.student_id == response.student_id]
    return {
        "attachments": [{"id": file.id, "name": file.display_name, "size_bytes": file.size_bytes, "content_type": file.content_type, "r2_key": file.r2_key} for file in files],
        "score_submissions": [serialize_reported_score(score) for score in scores],
    }


def inventory_files_have_survey_response(*, tenant, file_ids):
    from apps.domains.surveys.models import SurveyResponse
    return SurveyResponse.objects.filter(tenant=tenant, attachments__id__in=file_ids).exists()


def survey_attachment_protection(*, tenant, file_ids):
    if inventory_files_have_survey_response(tenant=tenant, file_ids=file_ids):
        return {"ok": False, "status": 409, "code": "survey_attachment_protected", "detail": "제출한 설문에 연결된 자료입니다. 응답을 수정해 첨부를 해제한 뒤 다시 시도해 주세요."}
    return None

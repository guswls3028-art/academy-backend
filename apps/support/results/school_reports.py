"""Central school exam collection, backed by the existing reported scores."""

from dataclasses import asdict, dataclass

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.models import TenantMembership
from apps.core.services.tenant_access import get_active_membership_role
from apps.domains.community.models import CommunityNotification
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture
from apps.domains.results.models import StudentReportedScore
from apps.domains.students.models import Student
from apps.support.results.student_reported_scores import (
    lock_student_score_submission_references,
    serialize_reported_score,
    validate_student_score_submission,
)
from apps.support.students.namespace_lock import (
    lock_student_creation_tenant_reference,
    lock_student_creation_user_reference,
)

OPTIONAL_ENTRY_FIELDS = (
    "grade_rank", "grade_scale", "achievement_level", "subject_average",
    "standard_deviation", "cohort_size", "exam_date",
)

# Kept outside legacy review statuses so old readers cannot review these rows
# or accept them as survey evidence during an overlapping rollout.
DIRECT_ENTRY_STATUS = StudentReportedScore.Status.SELF_ENTERED
DIRECT_REMOVED_STATUS = StudentReportedScore.Status.SELF_REMOVED


@dataclass(frozen=True)
class SchoolReportTerm:
    academic_year: int
    semester: int
    exam_round: str

    def as_dict(self):
        return asdict(self)


def _active_students(tenant):
    return Student.objects.filter(tenant=tenant, deleted_at__isnull=True)


def own_student(tenant, user):
    if get_active_membership_role(user, tenant) != "student":
        raise PermissionDenied("학생 본인만 자기 학교 성적을 확인할 수 있습니다.")
    student = _active_students(tenant).filter(user=user).first()
    if student is None:
        raise PermissionDenied("학생 본인만 자기 학교 성적을 확인할 수 있습니다.")
    return student


def staff_student(tenant, student_id):
    student = _active_students(tenant).filter(pk=student_id).first()
    if student is None:
        raise NotFound("학생을 찾을 수 없습니다.")
    return student


def _account_access(tenant):
    return TenantMembership.objects.filter(
        tenant=tenant, role="student", is_active=True, user__is_active=True,
    )


def _term_scores(tenant, term):
    return StudentReportedScore.objects.filter(
        tenant=tenant,
        source=StudentReportedScore.Source.SCHOOL_EXAM,
        status__in=(StudentReportedScore.Status.PENDING, StudentReportedScore.Status.VERIFIED, DIRECT_ENTRY_STATUS),
        **term.as_dict(),
    ).filter(
        Q(direct_entry=True, evidence_file__isnull=True)
        | Q(evidence_file__tenant=tenant, evidence_file__scope="student",
            evidence_file__student_ps=F("student__ps_number"))
    )


def _term_requests(tenant, term):
    return CommunityNotification.objects.filter(
        tenant=tenant,
        kind=CommunityNotification.KIND_SCHOOL_REPORT_REQUEST,
        **{f"payload__{key}": value for key, value in term.as_dict().items()},
    )


def _current_scores(rows):
    # A direct correction changes its existing row; evidence-backed submissions
    # keep their upload date even when later reviewed.
    latest = {}
    for row in rows:
        key = row.subject.casefold()
        timestamp = row.updated_at if row.direct_entry else row.created_at
        current = latest.get(key)
        if current is None or (timestamp, row.id) > (current[0], current[1].id):
            latest[key] = (timestamp, row)
    return [row for _timestamp, row in sorted(latest.values(), key=lambda item: item[1].subject.casefold())]


def _submitted_at(rows):
    latest = max((row.updated_at if row.direct_entry else row.created_at for row in rows), default=None)
    return latest.isoformat() if latest else None


def _school_name(student):
    current = {
        "ELEMENTARY": student.elementary_school,
        "MIDDLE": student.middle_school,
        "HIGH": student.high_school,
    }.get(student.school_type)
    return current or student.high_school or student.middle_school or student.elementary_school or ""


def _serialize_score(row):
    score = serialize_reported_score(row)
    if row.status == DIRECT_ENTRY_STATUS:
        score["status"] = StudentReportedScore.Status.PENDING
    return score


def term_detail(tenant, student, term):
    rows = list(_term_scores(tenant, term).filter(student=student).select_related("evidence_file"))
    requested_at = (
        _term_requests(tenant, term).filter(recipient_id=student.user_id)
        .order_by("-created_at").values_list("created_at", flat=True).first()
    )
    return {
        **term.as_dict(),
        "student_id": student.pk,
        "student_name": student.name,
        "school": _school_name(student),
        "grade": student.grade,
        "status": "submitted" if rows else "missing",
        "submitted_at": _submitted_at(rows),
        "requested_at": requested_at.isoformat() if requested_at else None,
        "scores": [_serialize_score(row) for row in _current_scores(rows)],
        "direct_scores": [
            _serialize_score(row)
            for row in sorted(rows, key=lambda row: row.id)
            if row.direct_entry and row.status == DIRECT_ENTRY_STATUS
        ],
    }


@transaction.atomic
def save_direct_scores(tenant, student, user, term, subjects):
    keys = {item["subject"].casefold() for item in subjects}
    if len(keys) != len(subjects):
        raise ValidationError({"subjects": "동일한 과목을 두 번 입력할 수 없습니다."})
    try:
        locked = lock_student_score_submission_references(validated_rows=[
            {"tenant": tenant, "student": student, "submitted_by": user},
        ])
    except ValueError as exc:
        raise PermissionDenied("학생 정보를 다시 확인해 주세요.") from exc
    if not _account_access(tenant).filter(user_id=user.id).exists():
        raise PermissionDenied("학생 계정이 활성 상태가 아닙니다.")
    direct = StudentReportedScore.objects.filter(
        tenant=tenant, student=locked, direct_entry=True, source=StudentReportedScore.Source.SCHOOL_EXAM,
        status=DIRECT_ENTRY_STATUS, **term.as_dict(),
    )
    existing = {row.subject.casefold(): row for row in direct}
    for index, subject in enumerate(subjects):
        match = existing.pop(subject["subject"].casefold(), None)
        previous = {field: getattr(match, field) for field in OPTIONAL_ENTRY_FIELDS} if match else {}
        try:
            values = validate_student_score_submission(
                tenant=tenant,
                student=locked,
                user=user,
                student_ps=locked.ps_number,
                payload={
                    **term.as_dict(), **previous, **subject,
                    "score_source": StudentReportedScore.Source.SCHOOL_EXAM,
                },
            )
        except ValueError as exc:
            raise ValidationError({"subjects": {index: str(exc)}}) from exc
        if match is None:
            StudentReportedScore.objects.create(**values, direct_entry=True, status=DIRECT_ENTRY_STATUS)
            continue
        changes = [
            key for key in ("subject", "score", "max_score", *OPTIONAL_ENTRY_FIELDS)
            if getattr(match, key) != values[key]
        ]
        if changes:
            for key in changes:
                setattr(match, key, values[key])
            match.save(update_fields=[*changes, "updated_at"])
    for removed in existing.values():
        removed.status = DIRECT_REMOVED_STATUS
        removed.save(update_fields=["status", "updated_at"])
    return term_detail(tenant, locked, term)


def roster_query(tenant, term, lecture_id=None):
    roster = _active_students(tenant)
    if lecture_id is not None:
        if not Lecture.objects.filter(tenant=tenant, pk=lecture_id, is_system=False).exists():
            raise NotFound("강의를 찾을 수 없습니다.")
        roster = roster.filter(Exists(Enrollment.objects.filter(
            tenant=tenant, lecture_id=lecture_id, student_id=OuterRef("pk"), status="ACTIVE",
        )))
    roster = roster.annotate(
        report_submitted=Exists(_term_scores(tenant, term).filter(student_id=OuterRef("pk"))),
    )
    counts = roster.aggregate(
        total_count=Count("pk"), submitted_count=Count("pk", filter=Q(report_submitted=True)),
    )
    counts["missing_count"] = counts["total_count"] - counts["submitted_count"]
    return roster, counts


def roster_page(tenant, term, roster, search=None, status=None):
    if search:
        roster = roster.filter(
            Q(name__icontains=search) | Q(elementary_school__icontains=search)
            | Q(middle_school__icontains=search) | Q(high_school__icontains=search)
        )
    if status:
        roster = roster.filter(report_submitted=status == "submitted")
    return roster.annotate(
        request_account_active=Exists(_account_access(tenant).filter(user_id=OuterRef("user_id"))),
        requested_at=Subquery(
            _term_requests(tenant, term).filter(recipient_id=OuterRef("user_id"))
            .order_by("-created_at").values("created_at")[:1]
        ),
    ).only(
        "id", "name", "school_type", "elementary_school", "middle_school", "high_school", "grade", "phone",
    ).order_by("name", "id")


def roster_entries(tenant, term, students, *, alimtalk_available=False):
    grouped = {student.pk: [] for student in students}
    if grouped:
        for row in _term_scores(tenant, term).filter(student_id__in=grouped).only(
            "id", "student_id", "subject", "direct_entry", "created_at", "updated_at",
        ):
            grouped[row.student_id].append(row)
    return [
        {
            "student_id": student.pk,
            "name": student.name,
            "school": _school_name(student),
            "grade": student.grade,
            "status": "submitted" if student.report_submitted else "missing",
            "score_count": len(_current_scores(grouped[student.pk])),
            "submitted_at": _submitted_at(grouped[student.pk]),
            "requested_at": student.requested_at.isoformat() if student.requested_at else None,
            "can_request": student.request_account_active and not student.report_submitted and student.requested_at is None,
            "can_request_alimtalk": (
                alimtalk_available and student.request_account_active and not student.report_submitted
                and len((student.phone or "").replace("-", "").strip()) >= 10
            ),
        }
        for student in students
    ]


def _request_targets(tenant, student_ids, *, lock):
    targets = _active_students(tenant).filter(pk__in=student_ids)
    original = dict(targets.values_list("id", "user_id"))
    if len(original) != len(student_ids):
        raise ValidationError({"student_ids": "요청 대상 학생을 다시 선택해 주세요."})
    if not lock:
        return original
    # Match score writes and student lifecycle: tenant/User FK gates precede
    # Student row locks. Concurrent submission and request then serialize.
    try:
        lock_student_creation_tenant_reference(tenant_id=tenant.id)
        for user_id in sorted(original.values()):
            lock_student_creation_user_reference(user_id=user_id)
    except ObjectDoesNotExist as exc:
        raise ValidationError({"student_ids": "요청 대상 학생을 다시 선택해 주세요."}) from exc
    locked = dict(targets.select_for_update().order_by("id").values_list("id", "user_id"))
    if locked != original:
        raise ValidationError({"student_ids": "요청 대상 학생을 다시 선택해 주세요."})
    return locked


def missing_student_accounts(tenant, term, student_ids, *, lock=False):
    targets = _request_targets(tenant, student_ids, lock=lock)
    submitted = set(_term_scores(tenant, term).filter(student_id__in=targets).values_list("student_id", flat=True))
    active_accounts = set(
        _account_access(tenant).filter(user_id__in=targets.values()).values_list("user_id", flat=True)
    )
    return {
        student_id: user_id
        for student_id, user_id in targets.items()
        if student_id not in submitted and user_id in active_accounts
    }


@transaction.atomic
def request_missing_scores(tenant, term, student_ids):
    missing = missing_student_accounts(tenant, term, student_ids, lock=True)
    already_requested = set(
        _term_requests(tenant, term).filter(recipient_id__in=missing.values()).values_list("recipient_id", flat=True)
    )
    notifications = [
        CommunityNotification(
            tenant=tenant,
            recipient_id=user_id,
            kind=CommunityNotification.KIND_SCHOOL_REPORT_REQUEST,
            payload=term.as_dict(),
        )
        for user_id in missing.values()
        if user_id not in already_requested
    ]
    CommunityNotification.objects.bulk_create(notifications)
    return {"requested": len(notifications), "skipped": len(student_ids) - len(notifications)}

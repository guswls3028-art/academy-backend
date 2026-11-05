"""Tenant-scoped schedule projections for attendance, results and student screens."""
from django.db.models import Case, Count, DateField, Exists, F, IntegerField, OuterRef, Subquery, Value, When
from django.db.models.functions import Coalesce
from apps.domains.lectures.models import SessionSchedule, SessionScheduleMembership
from apps.domains.lectures.services.schedules import schedule_enrollment_ids
from apps.support.lectures.schedule_dependencies import Attendance, SessionEnrollment


def student_schedule_annotations(*, tenant, student, session_ref="pk", date_ref="date"):
    schedule = SessionScheduleMembership.objects.filter(
        schedule__session_id=OuterRef(session_ref), schedule__session__lecture__tenant=tenant,
        session_enrollment__session_id=OuterRef(session_ref), session_enrollment__tenant=tenant,
        session_enrollment__enrollment__tenant=tenant,
        session_enrollment__enrollment__student=student,
    ).order_by("pk")
    configured = SessionSchedule.objects.filter(session_id=OuterRef(session_ref), session__lecture__tenant=tenant)
    return {
        "schedule_configured": Exists(configured),
        "effective_date": Case(
            When(Exists(configured), then=Subquery(schedule.values("schedule__date")[:1])),
            default=F(date_ref), output_field=DateField(),
        ),
        "schedule_label": Subquery(schedule.values("schedule__label")[:1]),
        "schedule_start_time": Subquery(schedule.values("schedule__start_time")[:1]),
    }


def staff_schedule_progress_annotations(*, tenant, day):
    """A teacher's Today card counts only students assigned to that actual day."""
    roster = SessionEnrollment.objects.filter(
        tenant=tenant, session_id=OuterRef("pk"), enrollment__tenant=tenant,
        enrollment__status="ACTIVE", enrollment__student__deleted_at__isnull=True,
        schedule_membership__schedule__date=day,
    ).order_by().values("session_id").annotate(total=Count("pk")).values("total")[:1]
    filled = Attendance.objects.filter(
        tenant=tenant, session_id=OuterRef("pk"), enrollment__tenant=tenant,
        enrollment__status="ACTIVE", enrollment__student__deleted_at__isnull=True,
        enrollment__session_enrollments__tenant=tenant,
        enrollment__session_enrollments__session_id=OuterRef("pk"),
        enrollment__session_enrollments__schedule_membership__schedule__date=day,
    ).order_by().values("session_id").annotate(total=Count("pk", distinct=True)).values("total")[:1]
    configured = Exists(SessionSchedule.objects.filter(session_id=OuterRef("pk"), session__lecture__tenant=tenant))
    return {
        field: Case(
            When(configured, then=Coalesce(Subquery(query), Value(0))),
            default=F(field), output_field=IntegerField(),
        )
        for field, query in (("attendance_total", roster), ("attendance_filled", filled))
    }


__all__ = ["schedule_enrollment_ids", "student_schedule_annotations", "staff_schedule_progress_annotations"]

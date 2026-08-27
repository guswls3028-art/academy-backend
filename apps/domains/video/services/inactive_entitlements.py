from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import models, transaction
from django.utils import timezone

from apps.core.models import TenantMembership
from apps.core.permissions import is_effective_staff
from apps.domains.video.models import (
    AccessMode,
    InactiveVideoEntitlement,
    Video,
    VideoAccess,
    VideoProgress,
)
from apps.support.video.inactive_entitlement_dependencies import (
    get_inactive_entitlement_scope_models,
)


Enrollment, SessionEnrollment, Lecture, Session, Student = (
    get_inactive_entitlement_scope_models()
)


logger = logging.getLogger(__name__)


class InactiveVideoEntitlementError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class InactiveVideoEntitlementMutation:
    entitlement: InactiveVideoEntitlement
    created: bool
    changed: bool


@dataclass(frozen=True)
class LockedInactiveVideoWriteAccess:
    entitlement: InactiveVideoEntitlement
    enrollment: Enrollment
    video: Video


def _required_text(value: Any, *, field: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise InactiveVideoEntitlementError(f"{field}_required", f"{field} is required")
    if len(normalized) > max_length:
        raise InactiveVideoEntitlementError(f"{field}_too_long", f"{field} is too long")
    return normalized


def _account_is_active(*, tenant_id: int, student: Student) -> bool:
    return bool(
        student.deleted_at is None
        and student.user_id
        and getattr(student.user, "is_active", False)
        and TenantMembership.objects.filter(
            tenant_id=tenant_id,
            user_id=student.user_id,
            role="student",
            is_active=True,
        ).exists()
    )


def _validate_exact_scope(*, entitlement: InactiveVideoEntitlement, now) -> bool:
    enrollment = entitlement.enrollment
    video = entitlement.video
    session = video.session
    lecture = session.lecture if session else None
    student = entitlement.student

    if entitlement.source != InactiveVideoEntitlement.Source.STAFF_AUTHORIZATION:
        return False
    if entitlement.revoked_at is not None:
        return False
    if entitlement.expires_at is not None and entitlement.expires_at <= now:
        return False
    if enrollment.status != "INACTIVE":
        return False
    if not _account_is_active(tenant_id=entitlement.tenant_id, student=student):
        return False
    if not session or not lecture or not lecture.is_active:
        return False
    if video.status != Video.Status.READY:
        return False
    if video.deleted_at is not None:
        return False
    if video.source_type == Video.SourceType.YOUTUBE:
        return False
    if VideoAccess.objects.filter(
        video_id=video.id,
        enrollment_id=enrollment.id,
    ).filter(
        models.Q(access_mode=AccessMode.BLOCKED) | models.Q(rule="blocked")
    ).exists():
        return False
    if not (
        entitlement.student_id == enrollment.student_id
        and entitlement.tenant_id == enrollment.tenant_id
        and entitlement.tenant_id == video.tenant_id
        and entitlement.tenant_id == student.tenant_id
        and enrollment.lecture_id == lecture.id
    ):
        return False
    return SessionEnrollment.objects.filter(
        tenant_id=entitlement.tenant_id,
        enrollment_id=enrollment.id,
        session_id=session.id,
    ).exists()


def get_active_inactive_video_entitlement(
    *,
    video: Video,
    enrollment: Enrollment,
    now=None,
) -> InactiveVideoEntitlement | None:
    """Return only a fully valid exact entitlement; every mismatch fails closed."""
    if enrollment.status != "INACTIVE":
        return None
    now = now or timezone.now()
    entitlement = (
        InactiveVideoEntitlement.objects
        .select_related(
            "student__user",
            "enrollment",
            "video__session__lecture",
        )
        .filter(
            tenant_id=enrollment.tenant_id,
            student_id=enrollment.student_id,
            enrollment_id=enrollment.id,
            video_id=video.id,
            video__deleted_at__isnull=True,
            revoked_at__isnull=True,
        )
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        .first()
    )
    if entitlement is None or not _validate_exact_scope(entitlement=entitlement, now=now):
        return None
    return entitlement


def active_entitlements_for_student(*, tenant, student, now=None):
    """Prefilter candidate rows; callers still use exact validation per row."""
    now = now or timezone.now()
    return (
        InactiveVideoEntitlement.objects
        .filter(
            tenant=tenant,
            student=student,
            enrollment__status="INACTIVE",
            source=InactiveVideoEntitlement.Source.STAFF_AUTHORIZATION,
            video__deleted_at__isnull=True,
            revoked_at__isnull=True,
        )
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        .select_related(
            "student__user",
            "enrollment__lecture",
            "video__session__lecture",
        )
        .order_by("video__session__order", "video__order", "video_id")
    )


def lock_and_revalidate_inactive_video_write_access(
    *,
    tenant_id: int,
    enrollment_id: int,
    video_id: int,
    expected_policy_version: int,
) -> LockedInactiveVideoWriteAccess:
    """Lock and revalidate an inactive entitlement immediately before a write."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("inactive video write access requires an atomic transaction")

    video_ref = (
        Video.objects.filter(
            id=video_id,
            tenant_id=tenant_id,
            deleted_at__isnull=True,
        )
        .values("id", "session_id")
        .first()
    )
    session_ref = None
    if video_ref is not None and video_ref["session_id"] is not None:
        session_ref = (
            Session.objects.filter(
                id=video_ref["session_id"],
                lecture__tenant_id=tenant_id,
            )
            .values("id", "lecture_id")
            .first()
        )
    if session_ref is None:
        raise InactiveVideoEntitlementError(
            "inactive_entitlement_changed",
            "inactive video entitlement is no longer valid",
        )

    lecture = (
        Lecture.objects.select_for_update()
        .filter(
            id=session_ref["lecture_id"],
            tenant_id=tenant_id,
            is_active=True,
        )
        .first()
    )
    if lecture is None:
        raise InactiveVideoEntitlementError(
            "inactive_entitlement_changed",
            "inactive video entitlement is no longer valid",
        )
    session = (
        Session.objects.select_for_update()
        .filter(id=session_ref["id"], lecture=lecture)
        .first()
    )
    enrollment = (
        Enrollment.objects.select_for_update(of=("self",))
        .filter(
            id=enrollment_id,
            tenant_id=tenant_id,
            lecture=lecture,
            status="INACTIVE",
        )
        .first()
    )
    if session is None or enrollment is None:
        raise InactiveVideoEntitlementError(
            "inactive_entitlement_changed",
            "inactive video entitlement is no longer valid",
        )
    video = (
        Video.objects.select_for_update(of=("self",))
        .filter(
            id=video_id,
            tenant_id=tenant_id,
            session=session,
            status=Video.Status.READY,
            deleted_at__isnull=True,
        )
        .first()
    )
    if (
        video is None
        or int(video.policy_version or 1) != int(expected_policy_version or 1)
    ):
        raise InactiveVideoEntitlementError(
            "inactive_entitlement_changed",
            "inactive video entitlement policy changed",
        )

    entitlement = (
        InactiveVideoEntitlement.objects.select_for_update(of=("self",))
        .filter(
            tenant_id=tenant_id,
            student_id=enrollment.student_id,
            enrollment_id=enrollment.id,
            video_id=video.id,
            revoked_at__isnull=True,
        )
        .first()
    )
    now = timezone.now()
    if entitlement is None or not _validate_exact_scope(
        entitlement=entitlement,
        now=now,
    ):
        raise InactiveVideoEntitlementError(
            "inactive_entitlement_changed",
            "inactive video entitlement is no longer valid",
        )
    return LockedInactiveVideoWriteAccess(
        entitlement=entitlement,
        enrollment=enrollment,
        video=video,
    )


@transaction.atomic
def update_inactive_entitled_video_progress(
    *,
    tenant_id: int,
    enrollment_id: int,
    video_id: int,
    expected_policy_version: int,
    defaults: dict,
):
    locked = lock_and_revalidate_inactive_video_write_access(
        tenant_id=tenant_id,
        enrollment_id=enrollment_id,
        video_id=video_id,
        expected_policy_version=expected_policy_version,
    )
    return VideoProgress.objects.update_or_create(
        video=locked.video,
        enrollment=locked.enrollment,
        defaults=defaults,
    )


def _validate_actor(*, tenant, actor, actor_reference: str) -> str:
    reference = _required_text(
        actor_reference,
        field="actor_reference",
        max_length=128,
    )
    if actor is not None and not is_effective_staff(actor, tenant):
        raise InactiveVideoEntitlementError("actor_forbidden", "staff actor required")
    return reference


@transaction.atomic
def grant_inactive_video_entitlement(
    *,
    tenant,
    student_id: int,
    enrollment_id: int,
    video_id: int,
    access_mode: str,
    source: str,
    source_reference: str,
    reason: str,
    actor=None,
    actor_reference: str,
    expires_at=None,
) -> InactiveVideoEntitlementMutation:
    actor_reference = _validate_actor(
        tenant=tenant,
        actor=actor,
        actor_reference=actor_reference,
    )
    source_reference = _required_text(
        source_reference,
        field="source_reference",
        max_length=128,
    )
    reason = _required_text(reason, field="reason", max_length=2000)
    if source != InactiveVideoEntitlement.Source.STAFF_AUTHORIZATION:
        raise InactiveVideoEntitlementError("source_invalid", "unsupported entitlement source")
    if access_mode not in (AccessMode.FREE_REVIEW, AccessMode.PROCTORED_CLASS):
        raise InactiveVideoEntitlementError("access_mode_invalid", "invalid access mode")

    now = timezone.now()
    if expires_at is not None and expires_at <= now:
        raise InactiveVideoEntitlementError("expires_at_invalid", "expiry must be in the future")

    # Resolve nullable relations without FOR UPDATE first, then lock the graph in
    # the same lecture -> enrollment -> video direction as playback. PostgreSQL
    # rejects FOR UPDATE across the nullable Video.session outer join.
    video_ref = (
        Video.objects.filter(
            id=video_id,
            tenant=tenant,
            deleted_at__isnull=True,
        )
        .values("id", "session_id")
        .first()
    )
    session_ref = None
    if video_ref is not None and video_ref["session_id"] is not None:
        session_ref = (
            Session.objects.filter(
                id=video_ref["session_id"],
                lecture__tenant=tenant,
            )
            .values("id", "lecture_id")
            .first()
        )
    if session_ref is None:
        raise InactiveVideoEntitlementError(
            "video_scope_mismatch",
            "video does not belong to the selected enrollment lecture",
        )

    lecture = (
        Lecture.objects.select_for_update()
        .filter(id=session_ref["lecture_id"], tenant=tenant, is_active=True)
        .first()
    )
    if lecture is None:
        raise InactiveVideoEntitlementError(
            "video_scope_mismatch",
            "video does not belong to the selected enrollment lecture",
        )
    session = (
        Session.objects.select_for_update()
        .filter(id=session_ref["id"], lecture=lecture)
        .first()
    )
    if session is None:
        raise InactiveVideoEntitlementError(
            "video_scope_mismatch",
            "video does not belong to the selected enrollment lecture",
        )

    student = (
        Student.objects.select_for_update(of=("self",))
        .filter(id=student_id, tenant=tenant, deleted_at__isnull=True)
        .first()
    )
    if student is None:
        raise InactiveVideoEntitlementError("student_not_found", "active student not found")
    if not _account_is_active(tenant_id=tenant.id, student=student):
        raise InactiveVideoEntitlementError("account_inactive", "student account is inactive")

    enrollment = (
        Enrollment.objects.select_for_update()
        .filter(
            id=enrollment_id,
            tenant=tenant,
            student=student,
            status__in=("ACTIVE", "INACTIVE"),
        )
        .first()
    )
    if enrollment is None:
        raise InactiveVideoEntitlementError(
            "enrollment_unavailable",
            "exact active or inactive enrollment not found",
        )
    if enrollment.lecture_id != lecture.id:
        raise InactiveVideoEntitlementError(
            "video_scope_mismatch",
            "video does not belong to the selected enrollment lecture",
        )

    video = (
        Video.objects.select_for_update(of=("self",))
        .filter(
            id=video_id,
            tenant=tenant,
            session=session,
            status=Video.Status.READY,
            deleted_at__isnull=True,
        )
        .first()
    )
    if video is None:
        raise InactiveVideoEntitlementError(
            "video_scope_mismatch",
            "video does not belong to the selected enrollment lecture",
        )
    if video.source_type == Video.SourceType.YOUTUBE:
        raise InactiveVideoEntitlementError(
            "video_source_unsupported",
            "inactive enrollment entitlements do not support YouTube videos",
        )
    if not SessionEnrollment.objects.filter(
        tenant=tenant,
        enrollment=enrollment,
        session=session,
    ).exists():
        raise InactiveVideoEntitlementError(
            "session_scope_missing",
            "selected enrollment has no exact session scope",
        )

    entitlement = (
        InactiveVideoEntitlement.objects.select_for_update()
        .filter(
            tenant=tenant,
            enrollment=enrollment,
            video=video,
            revoked_at__isnull=True,
        )
        .order_by("-id")
        .first()
    )
    if (
        entitlement is not None
        and entitlement.expires_at is not None
        and entitlement.expires_at <= now
    ):
        entitlement.revoked_at = now
        entitlement.revoked_by = actor
        entitlement.revoked_by_reference = actor_reference
        entitlement.revoke_reason = "Superseded expired entitlement during a new grant"
        entitlement.save(
            update_fields=[
                "revoked_at",
                "revoked_by",
                "revoked_by_reference",
                "revoke_reason",
                "updated_at",
            ]
        )
        entitlement = None

    desired = {
        "student": student,
        "access_mode": access_mode,
        "source": source,
        "source_reference": source_reference,
        "reason": reason,
        "granted_by": actor,
        "granted_by_reference": actor_reference,
        "expires_at": expires_at,
    }
    if entitlement is not None and any(
        getattr(entitlement, field) != value
        for field, value in desired.items()
    ):
        entitlement.revoked_at = now
        entitlement.revoked_by = actor
        entitlement.revoked_by_reference = actor_reference
        entitlement.revoke_reason = "Superseded by updated grant"
        entitlement.save(
            update_fields=[
                "revoked_at",
                "revoked_by",
                "revoked_by_reference",
                "revoke_reason",
                "updated_at",
            ]
        )
        entitlement = None

    created = entitlement is None
    if entitlement is None:
        entitlement = InactiveVideoEntitlement.objects.create(
            tenant=tenant,
            student=student,
            enrollment=enrollment,
            video=video,
            access_mode=access_mode,
            source=source,
            source_reference=source_reference,
            reason=reason,
            granted_by=actor,
            granted_by_reference=actor_reference,
            granted_at=now,
            expires_at=expires_at,
        )
    changed = created

    logger.info(
        "INACTIVE_VIDEO_ENTITLEMENT_GRANTED tenant_id=%s student_id=%s enrollment_id=%s "
        "video_id=%s entitlement_id=%s created=%s changed=%s actor=%s",
        tenant.id,
        student.id,
        enrollment.id,
        video.id,
        entitlement.id,
        created,
        changed,
        actor_reference,
    )
    return InactiveVideoEntitlementMutation(
        entitlement=entitlement,
        created=created,
        changed=changed,
    )


@transaction.atomic
def revoke_inactive_video_entitlement(
    *,
    tenant,
    entitlement_id: int,
    reason: str,
    actor=None,
    actor_reference: str,
) -> InactiveVideoEntitlementMutation:
    actor_reference = _validate_actor(
        tenant=tenant,
        actor=actor,
        actor_reference=actor_reference,
    )
    reason = _required_text(reason, field="reason", max_length=2000)
    entitlement_ref = (
        InactiveVideoEntitlement.objects
        .filter(id=entitlement_id, tenant=tenant)
        .values("id", "video_id")
        .first()
    )
    if entitlement_ref is None:
        raise InactiveVideoEntitlementError("entitlement_not_found", "entitlement not found")

    Video.objects.select_for_update().get(id=entitlement_ref["video_id"], tenant=tenant)
    entitlement = (
        InactiveVideoEntitlement.objects.select_for_update()
        .select_related("video")
        .get(id=entitlement_id, tenant=tenant)
    )
    if entitlement.revoked_at is not None:
        return InactiveVideoEntitlementMutation(
            entitlement=entitlement,
            created=False,
            changed=False,
        )

    entitlement.revoked_at = timezone.now()
    entitlement.revoked_by = actor
    entitlement.revoked_by_reference = actor_reference
    entitlement.revoke_reason = reason
    entitlement.save(
        update_fields=[
            "revoked_at",
            "revoked_by",
            "revoked_by_reference",
            "revoke_reason",
            "updated_at",
        ]
    )
    logger.info(
        "INACTIVE_VIDEO_ENTITLEMENT_REVOKED tenant_id=%s entitlement_id=%s "
        "video_id=%s actor=%s",
        tenant.id,
        entitlement.id,
        entitlement.video_id,
        actor_reference,
    )
    return InactiveVideoEntitlementMutation(
        entitlement=entitlement,
        created=False,
        changed=True,
    )

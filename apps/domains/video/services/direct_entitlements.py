from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from academy.adapters.db.django import repositories_video as video_repo
from apps.core.models import TenantMembership
from apps.core.permissions import is_effective_staff
from apps.domains.video.models import DirectVideoEntitlement, Video


logger = logging.getLogger(__name__)


class DirectVideoEntitlementError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class DirectVideoEntitlementMutation:
    entitlement: DirectVideoEntitlement
    created: bool
    changed: bool


@dataclass(frozen=True)
class LockedDirectVideoAccess:
    entitlement: DirectVideoEntitlement
    student: Any
    video: Video


def _required_text(value: Any, *, field: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise DirectVideoEntitlementError(f"{field}_required", f"{field} is required")
    if len(normalized) > max_length:
        raise DirectVideoEntitlementError(f"{field}_too_long", f"{field} is too long")
    return normalized


def _account_is_active(*, tenant_id: int, student) -> bool:
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


def _has_any_lecture_enrollment(*, entitlement: DirectVideoEntitlement) -> bool:
    session = entitlement.video.session
    return bool(
        session
        and video_repo.any_enrollment_exists_for_student_lecture(
            tenant_id=entitlement.tenant_id,
            student_id=entitlement.student_id,
            lecture_id=session.lecture_id,
        )
    )


def _validate_exact_scope(*, entitlement: DirectVideoEntitlement) -> bool:
    student = entitlement.student
    video = entitlement.video
    session = video.session
    lecture = session.lecture if session else None
    if entitlement.source != DirectVideoEntitlement.Source.STAFF_AUTHORIZATION:
        return False
    if entitlement.revoked_at is not None:
        return False
    if not _account_is_active(tenant_id=entitlement.tenant_id, student=student):
        return False
    if not session or not lecture or not lecture.is_active:
        return False
    if video.status != Video.Status.READY or video.deleted_at is not None:
        return False
    if video.visibility != Video.Visibility.ENROLLED:
        return False
    if video.source_type == Video.SourceType.YOUTUBE:
        return False
    if not (
        entitlement.tenant_id == student.tenant_id
        and entitlement.tenant_id == video.tenant_id
        and entitlement.tenant_id == lecture.tenant_id
    ):
        return False
    return not _has_any_lecture_enrollment(entitlement=entitlement)


def get_active_direct_video_entitlement(
    *,
    tenant,
    student,
    video: Video,
) -> DirectVideoEntitlement | None:
    entitlement = (
        DirectVideoEntitlement.objects
        .select_related("student__user", "video__session__lecture")
        .filter(
            tenant=tenant,
            student=student,
            video=video,
            revoked_at__isnull=True,
        )
        .first()
    )
    if entitlement is None or not _validate_exact_scope(entitlement=entitlement):
        return None
    return entitlement


def active_direct_video_entitlements_for_student(*, tenant, student):
    candidates = (
        DirectVideoEntitlement.objects
        .filter(
            tenant=tenant,
            student=student,
            source=DirectVideoEntitlement.Source.STAFF_AUTHORIZATION,
            revoked_at__isnull=True,
            video__deleted_at__isnull=True,
        )
        .select_related("student__user", "video__session__lecture")
        .order_by("video__session__order", "video__order", "video_id")
    )
    return [
        entitlement
        for entitlement in candidates
        if _validate_exact_scope(entitlement=entitlement)
    ]


def _validate_actor(*, tenant, actor, actor_reference: str) -> str:
    reference = _required_text(
        actor_reference,
        field="actor_reference",
        max_length=128,
    )
    if actor is None or not is_effective_staff(actor, tenant):
        raise DirectVideoEntitlementError("actor_forbidden", "staff actor required")
    return reference


def _locked_scope(*, tenant, student_id: int, video_id: int):
    video_ref = (
        Video.objects.filter(id=video_id, tenant=tenant, deleted_at__isnull=True)
        .values("id", "session_id")
        .first()
    )
    if video_ref is None or video_ref["session_id"] is None:
        raise DirectVideoEntitlementError("video_not_found", "exact video not found")

    # Canonical enrollment creation locks Student before inserting Enrollment.
    # Match that order so the two operations serialize without a lecture/student
    # lock inversion, then re-check every exact scope row under the same transaction.
    student = video_repo.lock_direct_video_student(
        tenant=tenant,
        student_id=student_id,
    )
    if student is None:
        raise DirectVideoEntitlementError("student_not_found", "active student not found")
    if not _account_is_active(tenant_id=tenant.id, student=student):
        raise DirectVideoEntitlementError("account_inactive", "student account is inactive")
    lecture, session = video_repo.lock_direct_video_session_scope(
        tenant=tenant,
        session_id=video_ref["session_id"],
    )
    if lecture is None or session is None:
        raise DirectVideoEntitlementError("video_not_found", "exact video not found")
    if not lecture.is_active:
        raise DirectVideoEntitlementError("lecture_inactive", "active lecture required")
    if video_repo.any_enrollment_exists_for_student_lecture(
        tenant_id=tenant.id,
        student_id=student.id,
        lecture_id=lecture.id,
    ):
        raise DirectVideoEntitlementError(
            "enrollment_exists",
            "an enrollment already exists for this lecture",
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
        raise DirectVideoEntitlementError("video_not_ready", "READY video required")
    if video.visibility == Video.Visibility.PUBLIC:
        raise DirectVideoEntitlementError(
            "video_already_public",
            "public video does not need a direct entitlement",
        )
    if video.source_type == Video.SourceType.YOUTUBE:
        raise DirectVideoEntitlementError(
            "video_source_unsupported",
            "direct entitlements do not support YouTube videos",
        )
    return student, video


@transaction.atomic
def lock_and_revalidate_direct_video_access(
    *,
    tenant,
    student_id: int,
    video_id: int,
    entitlement_id: int,
) -> LockedDirectVideoAccess:
    student, video = _locked_scope(
        tenant=tenant,
        student_id=student_id,
        video_id=video_id,
    )
    entitlement = (
        DirectVideoEntitlement.objects.select_for_update(of=("self",))
        .select_related("student__user", "video__session__lecture")
        .filter(
            id=entitlement_id,
            tenant=tenant,
            student=student,
            video=video,
            revoked_at__isnull=True,
        )
        .first()
    )
    if entitlement is None or not _validate_exact_scope(entitlement=entitlement):
        raise DirectVideoEntitlementError(
            "direct_entitlement_changed",
            "direct video entitlement is no longer valid",
        )
    return LockedDirectVideoAccess(
        entitlement=entitlement,
        student=student,
        video=video,
    )


@transaction.atomic
def grant_direct_video_entitlement(
    *,
    tenant,
    student_id: int,
    video_id: int,
    reason: str,
    actor=None,
    actor_reference: str,
    source_reference: str,
    confirmed_regrant: bool,
) -> DirectVideoEntitlementMutation:
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
    student, video = _locked_scope(
        tenant=tenant,
        student_id=student_id,
        video_id=video_id,
    )
    current = (
        DirectVideoEntitlement.objects.select_for_update(of=("self",))
        .filter(
            tenant=tenant,
            student=student,
            video=video,
            revoked_at__isnull=True,
        )
        .first()
    )
    if current is not None:
        return DirectVideoEntitlementMutation(
            entitlement=current,
            created=False,
            changed=False,
        )
    if (
        DirectVideoEntitlement.objects.filter(
            tenant=tenant,
            student=student,
            video=video,
            revoked_at__isnull=False,
        ).exists()
        and not confirmed_regrant
    ):
        raise DirectVideoEntitlementError(
            "regrant_confirmation_required",
            "an explicit confirmation is required to grant access again",
        )
    now = timezone.now()
    entitlement = DirectVideoEntitlement.objects.create(
        tenant=tenant,
        student=student,
        video=video,
        source=DirectVideoEntitlement.Source.STAFF_AUTHORIZATION,
        source_reference=source_reference,
        reason=reason,
        granted_by=actor,
        granted_by_reference=actor_reference,
        granted_at=now,
    )
    logger.info(
        "DIRECT_VIDEO_ENTITLEMENT_GRANTED tenant_id=%s student_id=%s video_id=%s "
        "entitlement_id=%s actor=%s",
        tenant.id,
        student.id,
        video.id,
        entitlement.id,
        actor_reference,
    )
    return DirectVideoEntitlementMutation(
        entitlement=entitlement,
        created=True,
        changed=True,
    )


@transaction.atomic
def revoke_direct_video_entitlement(
    *,
    tenant,
    entitlement_id: int,
    reason: str,
    actor=None,
    actor_reference: str,
) -> DirectVideoEntitlementMutation:
    actor_reference = _validate_actor(
        tenant=tenant,
        actor=actor,
        actor_reference=actor_reference,
    )
    reason = _required_text(reason, field="reason", max_length=2000)
    entitlement_ref = (
        DirectVideoEntitlement.objects
        .filter(id=entitlement_id, tenant=tenant)
        .values("id", "student_id", "video_id")
        .first()
    )
    if entitlement_ref is None:
        raise DirectVideoEntitlementError("entitlement_not_found", "entitlement not found")
    student = video_repo.lock_direct_video_student(
        tenant=tenant,
        student_id=entitlement_ref["student_id"],
        include_deleted=True,
    )
    if student is None:
        raise DirectVideoEntitlementError("entitlement_not_found", "entitlement not found")
    Video.all_with_deleted.select_for_update(of=("self",)).get(
        id=entitlement_ref["video_id"],
        tenant=tenant,
    )
    entitlement = DirectVideoEntitlement.objects.select_for_update(of=("self",)).get(
        id=entitlement_id,
        tenant=tenant,
    )
    if entitlement.revoked_at is not None:
        return DirectVideoEntitlementMutation(
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
        "DIRECT_VIDEO_ENTITLEMENT_REVOKED tenant_id=%s entitlement_id=%s "
        "video_id=%s actor=%s",
        tenant.id,
        entitlement.id,
        entitlement.video_id,
        actor_reference,
    )
    return DirectVideoEntitlementMutation(
        entitlement=entitlement,
        created=False,
        changed=True,
    )

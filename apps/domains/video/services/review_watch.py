"""Wall-clock review usage. Lock the student's ledger before changing any stream."""

from datetime import datetime, timezone as dt_timezone
from uuid import uuid4

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.domains.video.models import VideoReviewWatchStream, VideoReviewWatchUsage

# A closed tab cannot keep consuming a student's allowance indefinitely.
SIGNAL_TIMEOUT_MS = 20_000
PREFETCH_REQUEST_COST_MS = 2_000
PREFETCH_MAX_MS = 96_000


def threshold_ms(video):
    return int(video.duration or 0) * 1400


def elapsed_ms(stream, now):
    if stream.active_since is None:
        return 0
    elapsed = int((now - stream.active_since).total_seconds() * 1000)
    return max(0, min(SIGNAL_TIMEOUT_MS, elapsed))


def chargeable_ms(video, used_ms, elapsed):
    if video.review_watch_limit_enabled:
        return min(elapsed, max(0, threshold_ms(video) - used_ms))
    return elapsed


def settle(video, usage, stream, now):
    elapsed = chargeable_ms(video, usage.used_ms, elapsed_ms(stream, now))
    usage.used_ms += elapsed
    usage.prefetch_credit_ms = min(PREFETCH_MAX_MS, usage.prefetch_credit_ms + elapsed)
    stream.active_since = None


def snapshot(video, usage, stream):
    limit = threshold_ms(video) if video.review_watch_limit_enabled else None
    return {
        "stream": str(stream.key),
        "used_ms": usage.used_ms,
        "remaining_ms": max(0, limit - usage.used_ms) if limit is not None else None,
    }


@transaction.atomic
def open_review_stream(video, student, *, user_id, expires_at, context):
    if not student or student.tenant_id != video.tenant_id:
        raise PermissionDenied("학생 권한을 확인할 수 없습니다.")
    usage, _ = VideoReviewWatchUsage.objects.select_for_update().get_or_create(video=video, student=student)
    now = timezone.now()
    # Reopening a capped video takes over the same allowance. A previous
    # browser cannot continue spending it or request further protected media.
    for previous in usage.streams.all():
        stale = previous.active_since and elapsed_ms(previous, now) == SIGNAL_TIMEOUT_MS
        if video.review_watch_limit_enabled or stale or previous.access_until <= now:
            settle(video, usage, previous, now)
            previous.delete()
    usage.save(update_fields=["used_ms", "prefetch_credit_ms"])
    if video.review_watch_limit_enabled and usage.used_ms >= threshold_ms(video):
        return None
    stream = usage.streams.create(
        key=uuid4(),
        user_id=user_id,
        policy_version=video.policy_version,
        enrollment_id=getattr(context.enrollment, "pk", None),
        entitlement_id=getattr(context.direct_entitlement or context.inactive_entitlement, "pk", None),
        access_until=datetime.fromtimestamp(expires_at, tz=dt_timezone.utc),
    )
    return snapshot(video, usage, stream)


@transaction.atomic
def admit_fragment(stream, now):
    """Allow bounded prefetch across reloads. Media requests never start a viewing clock."""
    usage = VideoReviewWatchUsage.objects.select_for_update().get(pk=stream.usage_id)
    current = usage.streams.filter(pk=stream.pk, closed=False).first()
    if not current:
        return 403
    elapsed = elapsed_ms(current, now)
    if usage.used_ms + elapsed >= threshold_ms(stream.usage.video):
        return 403
    if current.active_since is not None and elapsed < SIGNAL_TIMEOUT_MS:
        return 200
    if usage.prefetch_credit_ms < PREFETCH_REQUEST_COST_MS:
        return 429
    usage.prefetch_credit_ms -= PREFETCH_REQUEST_COST_MS
    usage.save(update_fields=["prefetch_credit_ms"])
    return 200


@transaction.atomic
def record_review_state(*, video, student, stream_key, sequence, playing, prepare=False):
    usage = VideoReviewWatchUsage.objects.select_for_update().filter(video=video, student=student).first()
    stream = usage and usage.streams.filter(key=stream_key, closed=False).first()
    if (
        not stream
        or stream.policy_version != video.policy_version
        or video.review_watch_limit_enabled
        and stream.access_until <= timezone.now()
    ):
        raise PermissionDenied("다른 기기에서 영상을 열었거나 재생이 만료되었습니다. 다시 열어 주세요.")
    if prepare:
        # Cold-start recovery needs the first packets before the first playing event.
        # Authenticate the play gesture separately from the bearer HLS URL, keep the
        # allowance per student across devices, and never start or extend a clock here.
        if usage.prefetch_credit_ms < PREFETCH_REQUEST_COST_MS * 4:
            now = timezone.now()
            since = (now - usage.last_prepare_at).total_seconds() if usage.last_prepare_at else 30
            if since < 30:
                return {**snapshot(video, usage, stream), "retry_after_ms": max(1, int((30 - since) * 1000))}
            usage.prefetch_credit_ms = PREFETCH_REQUEST_COST_MS * 4
            usage.last_prepare_at = now
            usage.save(update_fields=["prefetch_credit_ms", "last_prepare_at"])
        return snapshot(video, usage, stream)
    if sequence > stream.sequence:
        now = timezone.now()
        settle(video, usage, stream, now)
        exhausted = video.review_watch_limit_enabled and usage.used_ms >= threshold_ms(video)
        if exhausted:
            # Do not reduce historical usage if the teacher enabled a limit later.
            stream.closed = True
        elif playing:
            stream.active_since = now
        stream.sequence = sequence
        stream.save(update_fields=["sequence", "active_since", "closed"])
        usage.save(update_fields=["used_ms", "prefetch_credit_ms"])
    return snapshot(video, usage, stream)

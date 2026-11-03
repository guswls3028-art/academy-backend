"""Wall-clock review usage. Lock the student's ledger before changing any stream."""

from datetime import datetime, timedelta, timezone as dt_timezone
from uuid import uuid4

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.domains.video.models import VideoReviewWatchStream, VideoReviewWatchUsage

# A closed tab cannot keep consuming a student's allowance indefinitely.
SIGNAL_TIMEOUT_MS = 20_000
PREFETCH_WINDOW_MS = 16_000
PLAYER_BUFFER_MS = 120_000  # HLS player maximum forward buffer, before accounting for elapsed playback.
PREFETCH_TTL = timedelta(seconds=30)
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


def snapshot(video, usage, stream, now):
    limit = threshold_ms(video) if video.review_watch_limit_enabled else None
    used = usage.used_ms + chargeable_ms(video, usage.used_ms, elapsed_ms(stream, now))
    return {
        "stream": str(stream.key),
        "used_ms": used,
        "remaining_ms": max(0, limit - used) if limit is not None else None,
    }


@transaction.atomic
def open_review_stream(video, student, *, user_id, expires_at, context):
    if not student or student.tenant_id != video.tenant_id:
        raise PermissionDenied("학생 권한을 확인할 수 없습니다.")
    if video.review_watch_limit_enabled and not video.duration:
        raise ValidationError({"detail": "영상 길이를 확인해야 복습할 수 있습니다. 선생님께 영상 확인을 요청해 주세요."})
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
    return snapshot(video, usage, stream, now)


def observed_position(stream, reported, now):
    # A new position window requires an authenticated prepare. Heartbeats alone may only
    # advance by the elapsed time at the highest supported playback rate.
    if stream.prepared_until and stream.prepared_until > now and stream.prepared_position_ms is not None and abs(reported - stream.prepared_position_ms) <= PREFETCH_WINDOW_MS:
        return reported
    if stream.playback_position_ms is None and stream.active_since is not None:
        return reported  # First signal from a stream admitted by the preceding release.
    anchor = stream.playback_position_ms or 0
    travel = elapsed_ms(stream, now) * 16
    return max(anchor - travel, min(reported, anchor + travel))


def prefetch_allows(stream, now, span):
    """Repeated GET, HEAD and Range reads of the same window are interchangeable."""
    elapsed = elapsed_ms(stream, now)
    playing = stream.active_since is not None and elapsed < SIGNAL_TIMEOUT_MS
    prepared = stream.prepared_until is not None and stream.prepared_until > now
    if not (playing or prepared):
        return False
    if span is None:
        return True
    begin, end = span
    # Native players may need the first short media segment to discover track metadata before seeking.
    if begin == 0 and end <= PREFETCH_WINDOW_MS:
        return True
    if playing and stream.playback_position_ms is None:
        # One in-flight interval issued by the old server; the next signal records its position.
        return True
    for anchor, ahead in (
        (stream.playback_position_ms, PLAYER_BUFFER_MS + elapsed * 16 if playing else PREFETCH_WINDOW_MS),
        (stream.prepared_position_ms if prepared else None, PREFETCH_WINDOW_MS),
    ):
        if anchor is not None and end > anchor - 3000 and begin < anchor + ahead:
            return True
    return False


@transaction.atomic
def record_review_state(*, video, student, user_id, context, stream_key, sequence, playing, prepare=False, position_ms=0):
    usage = VideoReviewWatchUsage.objects.select_for_update().filter(video=video, student=student).first()
    entitlement = context.direct_entitlement or context.inactive_entitlement
    stream = usage and usage.streams.filter(
        key=stream_key, user_id=user_id,
        enrollment_id=getattr(context.enrollment, "pk", None),
        entitlement_id=getattr(entitlement, "pk", None),
    ).first()
    if (
        not stream
        or stream.policy_version != video.policy_version
        or (video.review_watch_limit_enabled and stream.access_until <= timezone.now())
        or (stream.closed and not (video.review_watch_limit_enabled and usage.used_ms >= threshold_ms(video)))
    ):
        raise PermissionDenied("다른 기기에서 영상을 열었거나 재생이 만료되었습니다. 다시 열어 주세요.")
    now = timezone.now()
    if stream.closed:
        return snapshot(video, usage, stream, now)
    position_ms = min(position_ms, int(video.duration or 0) * 1000)
    if prepare:
        if stream.prepared_until and stream.prepared_until > now and stream.prepared_position_ms == position_ms:
            return snapshot(video, usage, stream, now)
        if video.review_watch_limit_enabled:
            if usage.prefetch_credit_ms < PREFETCH_WINDOW_MS:
                if usage.last_prepare_at and usage.last_prepare_at + PREFETCH_TTL > now:
                    wait = (usage.last_prepare_at + PREFETCH_TTL - now).total_seconds()
                    return {**snapshot(video, usage, stream, now), "retry_after_ms": max(1, int(wait * 1000))}
                usage.last_prepare_at = now
            usage.prefetch_credit_ms = max(0, usage.prefetch_credit_ms - PREFETCH_WINDOW_MS)
            usage.save(update_fields=["prefetch_credit_ms", "last_prepare_at"])
        stream.prepared_position_ms = position_ms
        stream.prepared_until = now + PREFETCH_TTL
        stream.save(update_fields=["prepared_position_ms", "prepared_until"])
        return snapshot(video, usage, stream, now)
    if sequence > stream.sequence:
        was_playing = stream.active_since is not None and elapsed_ms(stream, now) < SIGNAL_TIMEOUT_MS
        position_ms = observed_position(stream, position_ms, now)
        settle(video, usage, stream, now)
        exhausted = video.review_watch_limit_enabled and usage.used_ms >= threshold_ms(video)
        if exhausted:
            # Do not reduce historical usage if the teacher enabled a limit later.
            stream.closed = True
        elif playing:
            stream.playback_position_ms = position_ms
            stream.active_since = now
        elif was_playing:
            # A stalled player needs the current segment to resume sending playing signals.
            stream.playback_position_ms = position_ms
            stream.prepared_until = now + PREFETCH_TTL
        stream.sequence = sequence
        stream.save(update_fields=["sequence", "active_since", "closed", "playback_position_ms", "prepared_until"])
        usage.save(update_fields=["used_ms", "prefetch_credit_ms"])
    return snapshot(video, usage, stream, now)

# Direct video access without enrollment

## Purpose and entry point

Staff can grant one existing student access to one Academy-hosted video without
creating a lecture enrollment. The entry point is the video's **시청 권한 관리**
modal under **수강 등록 없이 영상만**. This is an exception for a customer who
must watch one exact video but must not join the lecture roster.

The ordinary path remains enrollment first. An active or inactive `Enrollment`
row for the video's lecture makes a direct entitlement ineligible. Staff must use
the existing enrollment or inactive-enrollment contract instead.

## Data and authorization

`DirectVideoEntitlement` owns the exception. One current row is unique for the
same tenant, student, and video; revoked rows remain as audit history. A grant
records a nonblank reason, the authenticated staff actor, source reference, and
grant time. Revoke records its actor, reason, and time and is idempotent. Granting
the same exact target after revoke creates a new audit row and requires a new
explicit confirmation.

The migration is additive: it creates only the entitlement table, indexes, and
constraints. An older API instance does not query this table, so overlapping old
and new runtimes keep all existing enrolled, public, and inactive-enrollment
playback paths unchanged during rollout.

Grant and runtime resolution fail closed unless all of these remain true:

- request, staff membership, student, video, session, and lecture share one
  resolved tenant;
- the student account and student membership are active and the student is not
  soft-deleted;
- the lecture is active and the video is `READY`, not soft-deleted,
  `ENROLLED`, and Academy-hosted;
- no `Enrollment` row of any status exists for the student and exact lecture.

`PUBLIC` and YouTube videos are rejected. `PUBLIC` would broaden access to the
tenant, and YouTube embeds cannot be bounded by the Academy CDN revocation
contract.

## Student visibility and playback

Student video home projects the exact entitled lecture and session with
`enrollment_id=null`. The session endpoint returns only entitled videos; sibling
videos remain denied. Parent requests retain the existing selected-child
boundary, and an ambiguous or unrelated child fails closed.

Direct access is always `FREE_REVIEW`. It creates no enrollment, session roster,
attendance, `VideoAccess`, progress, forward-skip budget, monitored playback
session, student activity/history, social write, view-count write, fee, result,
or notification. The student player keeps resume position in that browser only.

Every access check, playback bootstrap, token refresh, heartbeat, and event/CDN
validation revalidates the current tenant, student, video, and entitlement. The
bootstrap token has the separate `student-video-direct` audience and
`DIRECT_VIDEO_ENTITLEMENT` source. Tokens, HLS URLs, and thumbnails expire within
600 seconds even if the ordinary playback TTL is configured higher. Direct
heartbeat and event calls do not create monitoring state or event rows. Revoke
blocks the next validation or bootstrap; an already issued CDN URL can remain
usable only until that bounded expiry.

## Staff API

- `GET /api/v1/media/direct-video-entitlements/?video_id={id}` returns the
  same-tenant audit history for one exact video. `video_id` is required.
- `POST /api/v1/media/direct-video-entitlements/` grants one exact student and
  video with a nonblank reason and explicit regrant flag.
- `POST /api/v1/media/direct-video-entitlements/{id}/revoke/` revokes the row
  idempotently with a nonblank reason.

The API is staff-only and tenant-resolved. It has no bulk operation and does not
send a message.

## Failure and verification

Invalid tenant, actor, student, video, account, lecture, source, visibility, or
enrollment state creates no row. A later enrollment makes a current direct row
ineligible immediately and it does not reactivate when that enrollment becomes
inactive.

Focused verification is owned by
`tests/test_direct_video_entitlement.py`. It covers grant,
idempotent revoke, explicit regrant, enrollment precedence, tenant and parent
isolation, exact sibling denial, playback TTL, OpenAPI, migration rollback, and
zero side effects across enrollment, attendance, progress, history, messaging,
and playback-session state.

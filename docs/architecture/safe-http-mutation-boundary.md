# Safe HTTP mutation boundary

## Purpose

Academy treats `GET`, `HEAD`, and `OPTIONS` as strictly read-only. Opening a
screen, retrying a query, a browser prefetch, or a crawler request must never
create configuration, enqueue work, send a message, increment a counter, or
change tenant-owned data. A state change requires an explicit mutating request
whose permission, tenant, target, idempotency, and failure behavior can be
tested independently.

This boundary prevents a whole class of surprise triggers: a newly exposed GET
route cannot revive an old automatic action merely because a default row was
created or a callback ran while data was being read.

## Executable contract

`apps/core/middleware/safe_method_write.py` wraps the application database for
every safe-method request. SQL whose operation is `INSERT`, `UPDATE`, `DELETE`,
DDL, or a data-changing CTE is rejected before it reaches the database. The
request fails closed and the normal exception pipeline records the defect; the
write is not silently tolerated. The guard wraps database-backed session
response handling as well as the view, so a safe request cannot defer a write
until middleware unwinds or a streaming iterator begins producing content.

`scripts/lint/check_safe_method_writes.py` rejects direct ORM writes and
`transaction.on_commit()` from `get`, `head`, `options`, `list`, and `retrieve`
handlers. Both the static check and its unit tests run in the backend quality
gate and the production build workflow. The runtime guard remains authoritative
for writes hidden behind helpers or repository calls.

Product messaging has an additional boundary: every product producer first
persists a `ScheduledNotification` outbox row. Because a safe request cannot
create that row, it cannot send or schedule an Alimtalk through the supported
product path. Direct provider/SQS dispatch remains prohibited by
`docs/ssot/messaging-policy.md`.

## Read defaults and explicit creation

Missing optional configuration is represented in memory and serialized with
its model/domain default; a GET does not persist it. The first PATCH/POST is the
creation boundary. Current examples are landing admin settings, teacher push
preferences, homework policy, and messaging auto-send settings.

The same rule applies to operational resources and telemetry:

- missing matchup hit-report drafts return 404 on GET and are initialized by
  POST;
- public board/showcase detail GETs are read-only and the client records a
  successful open through `POST .../{id}/view/`;
- student video home does not create the system lecture/session; the teacher
  upload path owns that container and an absent public library is `null`;
- `GET .../playback/?access_check=true` is read-only; issuing media access,
  activity evidence, view count, and any monitored playback session uses
  `POST .../playback/`;
- exam-result GET is read-only; a successful student open is recorded through
  `POST /api/v1/students/me/activity/exam-result-open/` after the exact result
  access check. A linked parent's read remains valid but is never presented as
  the student's own activity;
- staff activity-timeline GET is read-only; its access audit is an explicit
  `POST /api/v1/students/{student_id}/activities/view/` that revalidates the
  staff and tenant-scoped student before recording evidence.

Existing rows and counters are preserved. This boundary performs no backfill,
deletion, or reinterpretation of historical data.

## Failure and verification

A safe-method database write is a release-blocking defect, not a recoverable
warning. Feature code must move the mutation to an explicit POST/PATCH/DELETE
contract and add a failure-first regression that asserts both the response and
zero database change on the read path.

Focused verification:

```powershell
python scripts/lint/check_safe_method_writes.py
python -m unittest scripts.lint.test_check_safe_method_writes
python -m pytest apps/core/tests/test_safe_method_write_guard.py -q
python -m pytest apps/domains/landing_public/tests/test_public_race_guards.py apps/domains/teacher_app/tests/test_push_config_safe_get.py -q
python -m pytest apps/domains/student_app/tests/test_parent_exam_child_selection.py tests/test_student_video_progress_enrollment_resolution.py -q
pwsh scripts/v1/test-workflow-governance-contract.ps1
```

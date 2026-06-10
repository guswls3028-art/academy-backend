# OMR 3-Column Worker Canary

**Time:** 2026-06-10 12:47 KST
**Scope:** Production OMR 60-question, 3-column end-to-end worker path
**Result:** PASS

## Target

Validate the remaining OMR 3-column risk that had not been exercised in
production:

API dispatch -> SQS -> AI worker scale-out -> OMR recognition -> callback ->
student match -> answer persistence -> grading.

## Canary Data

- Temporary tag: `[E2E-OMR3COL-1781062368]`
- Tenant: `11` / `e2eomr3c062368`
- Exam: `424`
- Sheet: `140`
- Submission: `354`
- Enrollment: `1084`
- AI job: `003ec75f-c96f-47b9-8206-6a553e4382d3`
- Uploaded file: `tenants/e2e/omr3col/e2eomr3c062368.png`
- OMR shape: 60 objective questions, no essay
- Layout: 3 columns, `1-20`, `21-40`, `41-60`

## Evidence

- API dispatch changed submission `354` from `submitted` to `dispatched`.
- SQS publish succeeded to `academy-v1-ai-queue`.
- API started AI worker ASG: desired `0 -> 1`.
- AI worker consumed the queue message; queue returned to:
  - visible `0`
  - in-flight `0`
  - delayed `0`
- AI job completed:
  - `job_type`: `omr_grading`
  - `status`: `DONE`
  - `error`: empty
- Submission completed:
  - `status`: `done`
  - `error`: empty
  - `enrollment_id`: `1084`
- Recognition run:
  - `status`: `DONE`
  - `aligned`: `true`
  - `alignment_method`: `marker_homography`
  - `answer_count`: `60`
  - `answer_status_counts`: `{"ok": 60}`
  - `identifier`: `98765432`
  - `identifier_status`: `ok`
  - `worker_version`: `v15`
- Persisted answers:
  - `SubmissionAnswer`: `60`
  - `OMRDetectedAnswer`: `60`
  - first questions: q1=`1`, q2=`2`, q3=`3`
  - last questions: q58=`3`, q59=`4`, q60=`5`
- Student match:
  - `status`: `confirmed`
  - `identifier_status`: `matched`
  - `method`: `auto_identifier`
  - `enrollment_id`: `1084`
- Result:
  - `total_score`: `60.0`
  - `max_score`: `60.0`
  - `ResultItem`: `60`
- Manual review:
  - `required`: `false`
  - `reasons`: `[]`

## Cleanup

Cleanup completed after verification:

- Deleted R2 keys:
  - `tenants/e2e/omr3col/e2eomr3c062368.png`
  - `tenants/11/ai/submissions/354/aligned/003ec75f-c96f-47b9-8206-6a553e4382d3.jpg`
- Deleted AI job rows for `003ec75f-c96f-47b9-8206-6a553e4382d3`.
- Deleted temporary tenant `11`; cascade removed canary exam/submission/student graph.
- Verified remaining records:
  - tenant: `false`
  - submission: `false`
  - job: `false`
- Verified AI queue after cleanup:
  - visible `0`
  - in-flight `0`
  - delayed `0`
- Verified AI worker ASG after cleanup:
  - desired `0`
  - instances `[]`

## Residual Risk After Canary

The production worker path for 60-question, 3-column OMR is verified.

Remaining risk is limited to real-world scan quality and device-specific
capture artifacts. Poorly cropped, blurred, shadowed, or low-resolution scans
can still require manual review, but the 1/2/3-column layout and worker callback
path are no longer unverified.

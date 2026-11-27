# API schema and generated-client contract

**Status:** Active
**Owners:** Backend API and frontend API consumers
**Last reviewed:** 2026-11-28

## Purpose and ownership

The backend owns the committed OpenAPI 3 contract at `schema/openapi.json`.
It is generated from the complete `/api/v1/` URL tree with
`apps.api.config.settings.schema`, an isolated SQLite configuration that never
changes the production runtime or its live drf-yasg documentation surface.

Frontend generated types consume this committed artifact. Product-specific
authorization, tenant selection, and failure rules remain owned by their
domain documents and implementation; the schema describes transport shape and
does not relax those rules.

## Generation and failure contract

Run from the backend repository root:

```powershell
python scripts/generate_openapi_schema.py
python scripts/generate_openapi_schema.py --check
```

Generation performs OpenAPI-spec validation and writes deterministic,
key-sorted JSON. The command always selects `apps.api.config.settings.schema`
even when the operator shell already defines `DJANGO_SETTINGS_MODULE`; it does
not inherit test, development, worker, or production settings. Check mode fails when:

- the committed schema differs from current routes and serializers;
- generator errors or warnings exceed `schema/generation-baseline.json`;
- the number of documented paths or schemas falls below the reviewed baseline;
- the schema is not valid OpenAPI 3.

The current reviewed baseline covers 687 paths and 699 schema components.
Legacy APIView and serializer inference gaps are recorded as 1,311 generator
errors (261 unique) and 299 warnings (134 unique). They are an explicit
no-regression ceiling, not a claim of complete endpoint typing. When an
endpoint is touched, add its serializer/schema metadata and lower the baseline
after regenerating; never raise the baseline merely to make CI pass.

Lecture roster attendance uses the `LectureAttendance` component, distinct from
employee `Attendance`. Its memo projections include `lecture_memo`,
`lecture_memo_updated_at`, and read-only `student_memo`. The dedicated enrollment
memo PATCH returns `LectureMemoResult` and requires `X-Expected-Updated-At`;
data ownership and role boundaries are in [student-core.md](../domain/student-core.md).

Generic `ExamAttempt` and derived progress resources publish read-only GET
operations. Question and Sheet creation use dedicated request components so
`Question.sheet` and `Sheet.exam` remain required writable IDs on POST while
ordinary PATCH components cannot advertise parent re-assignment.

Academy JWT and tenant-aware session authentication are represented through
`apps.api.schema_extensions`. Schema generation contains no credentials and
does not connect to tenant or production data.

## List pagination contract

Endpoints using the default DRF paginator accept `page` and `page_size`; the
default is 20 and `page_size` is capped at 500. Domain-specific paginators may
set a smaller or larger documented cap, and endpoints that require a complete
bounded collection may explicitly disable pagination. Clients still consume
the standard `count/next/previous/results` shape unless the endpoint documents
a flat-list response.

This contract is required by the teacher/admin exam, homework, result, and
selection APIs. Silently ignoring `page_size` can hide records beyond the
first 20 and is treated as a transport regression.

## Staff submission review responses

The staff submission inbox and results dashboard publish these GET responses under
`/api/v1/`. Each requires an authenticated staff role in the resolved tenant.

| Endpoint | Default response | Selected response |
| --- | --- | --- |
| `submissions/submissions/pending/` | Up to 200 exam submission rows | `page` or `page_size` returns a page with counts and a failure summary. |
| `results/admin/landing-stats/` | Counts and up to five rows each in `pending_top` and `recent_done_top` | `session_choices=1` adds session evidence for resolved exam targets. |
| `submissions/submissions/exams/{exam_id}/` | Up to 200 exam submission rows, including each submission's saved score when available | `focus_id` selects that submission in this exam. `review_issues=1` returns `items`, `total`, and `next_cursor`. |
| `submissions/submissions/homework/{homework_id}/` | Up to 200 homework submission rows with file and review details | `enrollment_id` selects current submissions for the matching enrollment. `page` or `page_size` returns `count`, `next`, `previous`, and `results`. |

Both the pending inbox and dashboard preserve the older response when session
choices are omitted. With the option enabled, a recorded entry session, eligible
session choices, the original grading lecture, and read-only history have separate
fields. An empty accessible collection remains a successful response. Invalid
selection or pagination inputs are documented on the relevant operation, including
the homework list's out-of-range page response. The projections and their
authorization are covered by the [exam grading](../domain/exam-grading.md) and
[homework grading](../domain/homework-grading.md) contracts.

## Scalar query parsing contract

Touched DRF endpoints parse integer and boolean query parameters through
`apps.api.common.query_params`. Missing or blank values may use the documented
default, but malformed values never silently become that default. Integer
identifiers and bounded values fail with field-keyed `400` validation; boolean
parameters accept only `true/false` and `1/0`, so a typo such as `flase` cannot
change a filter or permission-sensitive view to false. Endpoints with an
established upper-cap contract still cap an oversized positive `page_size`;
the parser distinction is between a valid integer and malformed input.

Mutation bodies that are not already owned by a DRF serializer parse boolean
fields through `apps.core.parsing.parse_bool`. JSON booleans and the documented
boolean string/integer forms retain their value; Python truthiness is never
used on request values because `bool("false")` is true. Ambiguous values fail
with field-keyed `400` validation before a database, queue, publication, or
permission-sensitive mutation. This applies to maintenance and landing
publication flags, score-edit leases, public-board moderation, worker controls,
payroll reruns, and Problem Studio confirmation/options.

## Compatibility and verification

Existing URLs, responses, drf-yasg pages, and production settings are
unchanged. Backend CI runs `python scripts/generate_openapi_schema.py --check`.
The frontend pins the backend schema source revision and regenerates its type
artifact, so a transport change is reviewed on both sides instead of silently
drifting.

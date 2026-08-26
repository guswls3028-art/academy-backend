# API schema and generated-client contract

**Status:** Active
**Owners:** Backend API and frontend API consumers
**Last reviewed:** 2026-08-27

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

The current reviewed baseline covers 578 paths and 382 schema components.
Legacy APIView and serializer inference gaps are recorded as 1,372 generator
errors (277 unique) and 348 warnings (138 unique). They are an explicit
no-regression ceiling, not a claim of complete endpoint typing. When an
endpoint is touched, add its serializer/schema metadata and lower the baseline
after regenerating; never raise the baseline merely to make CI pass.

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

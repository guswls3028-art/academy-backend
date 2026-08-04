# API schema and generated-client contract

**Status:** Active
**Owners:** Backend API and frontend API consumers
**Last reviewed:** 2026-08-05

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
key-sorted JSON. Check mode fails when:

- the committed schema differs from current routes and serializers;
- generator errors or warnings exceed `schema/generation-baseline.json`;
- the number of documented paths or schemas falls below the reviewed baseline;
- the schema is not valid OpenAPI 3.

The initial adoption covers 540 paths and 307 schema components. Legacy
APIView and serializer inference gaps are recorded as 1,384 generator errors
(280 unique) and 357 warnings (139 unique). They are an explicit
no-regression ceiling, not a claim of complete endpoint typing. When an
endpoint is touched, add its serializer/schema metadata and lower the baseline
after regenerating; never raise the baseline merely to make CI pass.

Academy JWT and tenant-aware session authentication are represented through
`apps.api.schema_extensions`. Schema generation contains no credentials and
does not connect to tenant or production data.

## Compatibility and verification

Existing URLs, responses, drf-yasg pages, and production settings are
unchanged. Backend CI runs `python scripts/generate_openapi_schema.py --check`.
The frontend pins the backend schema source revision and regenerates its type
artifact, so a transport change is reviewed on both sides instead of silently
drifting.

# Problem Studio HWPX Verification — 2026-07-27

## Scope

Verify the production repair for the Hancom Hangul `파일이 손상되었습니다`
dialog reported against `03_자체양식_문제검수본.hwpx`.

## Root Cause

The previous writer hand-assembled a partial HWPX ZIP/XML package. It omitted
or malformed strict package details including the header section count,
required namespaces, the application-settings root, and the header spine
reference. The previous regression test asserted ZIP members but did not
reopen or schema-validate the package.

## Automated Verification

- Implementation commit:
  `12e853193813d707790d07ec4c0bf2a31e86f3a3`
- Runtime dependency closure:
  `e672865e00a0408156a374e51ccb09fe4a41dd58`
- Successful deployment:
  [30269167091](https://github.com/guswls3028-art/academy-backend/actions/runs/30269167091)
- Regression suites: 37 passed, with 8 additional subtests passed.
- Django system check, migration dry-run, relevant Ruff, dependency
  consistency, and diff checks: pass.
- Post-deploy canary: 30 PASS, 0 WARN, 0 FAIL.
- Read-only deployment verification: PASS / GO.

## Production Artifact Check

At approximately 23:06 KST, a controlled tenant-1 E2E administrator sent a
non-persisting synchronous transfer request to the production API. The request
used a one-pixel generated image and did not create an AI job, database row, or
stored source object.

| Check | Result |
|-------|--------|
| HTTP response | 200 |
| Result ZIP size | 17,224 bytes |
| Inner HWPX size | 8,763 bytes |
| HWPX SHA-256 | `fc808c08d3b0df8917b6f76b6e7b3d012816689291592889c32412824923aefd` |
| First ZIP member is uncompressed `mimetype` | PASS |
| Header `secCnt` and required namespaces | PASS |
| `HWPApplicationSetting` root | PASS |
| Header spine reference | PASS |
| Preview/section title synchronization | PASS |
| `HwpxDocument.open()` and schema validation | PASS |
| Package validator | PASS with the skeleton's version-path fallback warning |

The version-path warning is non-fatal: `version.xml` exists at the standard
fallback path and both schema and package validators returned exit code 0.

## Remaining Licensed-Editor Check

This verification host does not have licensed Hancom Hangul installed.
Generate a new result after deployment and open the HWPX on the reporting
device. Record:

- exact Hangul product/build;
- generated job or output identifier;
- absence or presence of the damaged-file dialog;
- visible text/layout result;
- whether insertion and new-document fallback both behave as expected.

Until that check is recorded, the licensed-editor item remains
`needs-manual-validation`; automated package compatibility is
`production-confirmed`.

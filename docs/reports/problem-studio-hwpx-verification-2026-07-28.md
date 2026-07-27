# Problem Studio HWPX Verification — 2026-07-28

## Scope

Close the remaining automatic compatibility warning after the production
repair for the Hancom Hangul `파일이 손상되었습니다` incident.

## Residual Warning and Repair

The editor-compatible skeleton stored `version.xml` at the HWPX package root,
but `Contents/content.hpf` did not explicitly reference it. The package was
valid and schema-safe, but `python-hwpx` had to discover the standard fallback
path and reported a non-fatal warning.

The writer now adds the standard OPF manifest item before saving:

- id: `version`
- href: `../version.xml`
- media type: `application/xml`

Existing references are preserved, so the repair is idempotent if the
underlying skeleton later includes the item itself.

## Automated Verification

- Implementation commit:
  `c228fc52546ea8e081f94c6453d1599eaab73417`
- Successful deployment:
  [30286385427](https://github.com/guswls3028-art/academy-backend/actions/runs/30286385427)
- Problem Studio regression suite: 28 passed.
- Relevant Ruff checks, Django system check, migration dry-run, dependency
  consistency, and diff checks: pass.
- Local `hwpx-validate` and `hwpx-validate-package`: pass with zero package
  warnings.
- Post-deploy production canary: 30 PASS, 0 WARN, 0 FAIL.
- Read-only deployment verification: PASS / GO.
- Runtime image verification: PASS; the live API instance digest matches the
  promoted release manifest.

## Production Artifact Check

At approximately 02:15 KST, a controlled tenant-1 E2E administrator sent a
non-persisting synchronous transfer request to the production API. The request
used a generated one-pixel PNG and did not create an AI job, database row, or
stored source object.

| Check | Result |
|-------|--------|
| HTTP response | 200 |
| Result ZIP size | 17,255 bytes |
| Result ZIP SHA-256 | `e26cae274cc1e134a167287ff7dbb5d268bdd64c9c3aaaacdfad81c678152d22` |
| Inner HWPX size | 8,772 bytes |
| HWPX SHA-256 | `cb0a1497f96a575598ba98d5e7b7a8a0de31da0f566d20ce4657a1f8945e6bf6` |
| First ZIP member is uncompressed `mimetype` | PASS |
| Header/settings/content references | PASS |
| Explicit `version.xml` manifest id and href | PASS |
| Preview/section title synchronization | PASS |
| `HwpxDocument.open()` and `version_path()` | PASS |
| XML schema validation | PASS |
| Package validation | PASS, 0 warnings |

The generated files are retained under
`C:\academy\_artifacts\problem-studio-hwpx-production-20260728` for the
licensed-editor handoff.

## Production Image Evidence

- Promoted image tag:
  `sha-c228fc52546ea8e081f94c6453d1599eaab73417-run-30286385427-1`
- API digest:
  `sha256:0cd3c007d8fc2ee6da8a2b3e877fbcac3a350fad53c23d8be62685546725dd4c`
- AI CPU worker digest:
  `sha256:4d56c636a30ccb513471065b319a97ceb35aa5e8afad411db8d064d4fc107efd`
- Tools worker digest:
  `sha256:22172866e88d63c7ed0fd214ebce5ef982106b552389f894963450511f06bc38`

## Remaining Licensed-Editor Check

This verification host does not have licensed Hancom Hangul installed. Open
the newly generated 2026-07-28 artifact on the reporting device and record:

- exact Hangul product/build;
- absence or presence of the damaged-file dialog;
- visible text/layout result;
- whether insertion and new-document fallback both behave as expected.

Until that device-owned check is recorded, the licensed-editor item remains
`needs-manual-validation`; automatic package compatibility is
`production-confirmed`.

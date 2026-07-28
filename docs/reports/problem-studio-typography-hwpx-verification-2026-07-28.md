# Problem Studio Typography and Native HWPX Equation Verification — 2026-07-28

## Scope

Verify that teacher-selected typography survives the production Problem Studio
transfer path and that chemical subscript/superscript text is emitted as an
editable Hancom Hangul equation rather than visual-only Unicode.

## Released Runtime

- Backend implementation: `2c27f8a7be4cd41030bb84dece6ec4b28ddbb6e2`
- Backend deployment:
  [30365901188](https://github.com/guswls3028-art/academy-backend/actions/runs/30365901188)
- Frontend implementation: `39e222fe2bd65a22646eecfe9b791ba7420119ec`
- Frontend deployment:
  [30365955995](https://github.com/guswls3028-art/academy-frontend/actions/runs/30365955995)
- Windows companion: `1.1.0`
- Companion size: `67,679,859` bytes
- Companion SHA-256:
  `af9538f4aa3685384f0e638609348160dbdf04d61cc82a1d4a3f415bcf043bfd`

## Licensed Editor Check

The generated local fixture was inserted into an already-open disposable
integration-test document without saving, closing, or quitting Hangul.

| Item | Evidence |
|------|----------|
| Product | Hancom Hangul, `Hwp.exe` product/file version `12.0.0.535` |
| Open result | No damaged-file or compatibility warning |
| Title typography | `함초롬돋움`, `22.0 pt` |
| Body typography | `함초롬바탕`, `11.0 pt` |
| Chemical formula | `H₂O` rendered as a Hangul equation object |
| Editability | Double-click opened the native equation editor with script `H _ {2} O` |
| Other formulas | `SO₄²⁻` and the fraction sample rendered as equation objects |
| User-document lifecycle | No `Save`, `Close`, or `Quit` action |

This closes the native-equation/editability check for the installed Hangul
build. Exact rendering for every teacher-owned font, Hangul version, table, and
shape combination remains a device-specific review item.

## Production API and Artifact Check

An authenticated tenant-1 administrator performed read-only metadata requests
and one non-persisting synchronous transfer using the generated HWPX fixture.
No AI job, source object, font asset, or saved-style mutation was created.

| Check | Result |
|-------|--------|
| `GET /problem-studio/fonts/` | HTTP 200; 6 built-in fonts |
| `GET /problem-studio/document-style/` | HTTP 200; default reusable style |
| `GET /problem-studio/hangul-companion/` | HTTP 200; version/size/SHA exact |
| `POST /problem-studio/transfer-document/` | HTTP 200 |
| Transfer warnings | 0 |
| Result ZIP | 18,902 bytes; SHA-256 `9507cec88af457bf9cc4d0b481a68e14c4aa2cdaff7eda0b8ff88abebfd05b3d` |
| Inner HWPX | 10,344 bytes; SHA-256 `1f2757e6a194c25df175971e2b067c62a6af02d7c9592449037cfedb2fbe87ec` |
| Package validation | PASS; 0 warnings; package reopen PASS |
| Native equations | 2; `H _ {2} O`, `S O _ {4} ^ {2 -}` |
| Typography | title/body families and `22/11 pt`, `165%`, `14 pt` spacing exact |
| Font embedding | `personal_fonts_embedded=false` |

The retained evidence is under:

`C:\academy\_artifacts\problem-studio-typography-hwpx-20260728`

## Deployment Closure

- Production workflow: success.
- Post-deploy canary: 30 PASS, 0 WARN, 0 FAIL.
- Runtime image digest: exact match with the promoted immutable manifest.
- Read-only deployment verification: `CONDITIONAL GO` only because the local
  verifier could not run `wrangler r2 bucket list`; app/API/CDN cache checks and
  runtime infrastructure passed.
- The companion object was independently verified through remote R2 HEAD
  metadata, so the generic local Wrangler warning does not block this release.

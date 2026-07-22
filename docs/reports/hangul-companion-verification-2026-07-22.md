# Hangul Companion Verification Report

- Verification date: 2026-07-22 KST
- Frontend implementation commit: `aa9abeb5`
- Windows COM gate commit: `d3cb4dea87de4fdb543137225d009f570416feb9`
- GitHub Actions run: [29910478788](https://github.com/guswls3028-art/academy-frontend/actions/runs/29910478788)

## Conclusion

The Windows companion Release build, COM insertion contract, separate-process ROT/COM path, sealed installer integrity, repeated-launch stability, and production deployment path passed. The test device did not have a licensed Hancom Hangul editing application, HWP COM ProgID, or `.hwpx` editor association, so live insertion into Hancom Hangul 2024 remains `needs-manual-validation`.

This feature is the Academy Problem Studio tool-tab action plus a Windows companion. It is not a native Hancom `한애드온즈` package.

## Result Matrix

| Scope | Status | Result |
|-------|--------|--------|
| Release build | `repo-confirmed` | Companion and integration-test projects built with 0 warnings and 0 errors. |
| COM insertion contract | `repo-confirmed` | Visible normal-edit selection, one `InsertFile`, controlled parameters, security-module hook, rejection fallback, and hidden/read-only exclusion passed. |
| Non-destructive document lifecycle | `repo-confirmed` | `Save`, `Close`, and `Quit` calls remained 0 in direct and cross-process scenarios. |
| Separate-process ROT/COM | `repo-confirmed` | A mock Hangul COM object in one Windows process was discovered and invoked by a separate Release companion process. |
| Production delivery | `repo-confirmed` | Windows gate, frontend quality gate, Cloudflare deployment, production canary, tenant availability, and production round-trip E2E passed in run `29910478788`. |
| Repeated stability | `repo-confirmed` | 100 consecutive Release launches and 500 contract scenarios completed with 0 failures, 0 residual processes, and 0 matching Windows Application crash events. |
| Licensed Hancom Hangul 2024 | `needs-manual-validation` | Live cursor insertion, security prompt/module behavior, fallback opening, and visual fidelity must be checked on an approved licensed device. |
| Exact native `.hwp` fidelity | `intentionally-unchanged` | The supported product result is an editable teacher-review draft, not lossless native HWP reproduction. |

## Contract Evidence

The automated Windows test covered:

- `Visible=true` and `EditMode=1` as the only eligible document state.
- Exactly one `CreateAction("InsertFile")`, `CreateSet`, `GetDefault`, and `Execute` sequence.
- `FileName`, `KeepSection`, `KeepCharshape`, `KeepParashape`, and `KeepStyle` parameter values.
- Optional `RegisterModule("FilePathCheckDLL", moduleName)` when `ACADEMY_HWP_FILE_PATH_MODULE` is configured.
- Safe fallback when Hangul rejects insertion.
- No mutation of hidden or read-only documents.
- Zero `Save`, `Close`, and `Quit` calls.
- Cross-process ROT discovery and insertion from the sealed Release process.

The frontend workflow makes this test a blocking `windows-latest` dependency of production deployment.

## Repeated Stability Evidence

The sealed Release executable was launched 100 consecutive times. The run executed 500 contract scenarios:

- 300 successful insertion flows.
- 100 Hangul-side insertion rejections.
- 100 hidden/read-only exclusions.

Observed totals were 0 failed runs, 0 residual companion processes after completion, and 0 matching Windows Application crash events in the post-run event-log window.

## Installer Integrity

The installed executable matched the sealed final binary:

```text
SHA-256 61032235c3c813a0139df22b22b24068340192fb7a49b5e40f05ade2bf265cc0
```

The current-user `academy-hangul://` protocol registration remained present after the stability run.

## Remaining Licensed-Device Validation

Complete `backend/docs/operations/runbooks/problem-studio-source-transfer-uat.md` on a Windows device with:

1. A licensed Hancom Hangul 2024 editing application, not the viewer.
2. Hancom Automation approval, the approved file-path security module, and trusted code signing for commercial distribution.
3. A visible normal-edit document with a known cursor marker.
4. Representative formulas, tables, shapes, images, choices, answers, and explanations for visual comparison.

Do not mark this item passed from a viewer, mock COM server, CI result, or handoff diagnostic alone.

## Official Product Constraints

- [Hancom Office Viewer](https://download.hancom.com/product/office/officeViewer) is view-only, and business/organization use requires separate approval.
- The official [HWP SDK](https://download.hancom.com/product/sdk/hwpSdk) is a paid product.
- Hancom documents [version-independent late binding for `HwpObject`](https://forum.developer.hancom.com/t/c/2825).
- Hancom documents [Automation security-module registration through `RegisterModule`](https://forum.developer.hancom.com/t/2024-registermodule/1655).

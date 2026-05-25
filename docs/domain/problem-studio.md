# Problem Studio Domain Note

Last verified: 2026-05-25

## Product Philosophy

Problem Studio is currently a teacher assistive transcription and review tool, not an autonomous LLM problem writer.

The primary user promise is:

1. Teacher uploads a problem source file or image.
2. The system moves the source into an editable Hangul-compatible review document.
3. The teacher edits, verifies, and finalizes the document manually.

This matters because the early teacher need is not "make perfect new questions". It is "do not make me retype EBS/private workbook/scanned material by hand". A rough editable Hangul file is already useful if the teacher can open it and fix it.

Do not describe the current product as "rewriting other textbooks" or "automatically making derivative questions". Safer product language is "source transfer", "teacher review draft", "academy template", and, for future stages, "teacher-approved similar-type candidates from permitted source/context".

## Current User-Facing Shape

- Route: frontend `/admin/tools/problem-studio`
- Tool tab label: `문제 제작`
- Main screen is intentionally simple:
  - upload source
  - set title/class/subject
  - save original as Hangul-compatible file
- Secondary functions are collapsed as options:
  - text correction and question editor
  - PDF/print preview
  - planning memo for later generation stages

Keep this hierarchy. If the page starts feeling like a full authoring studio again, it is probably regressing for the current use case.

## Current Implementation Truth

Frontend:

- Main page: `frontend/src/app_admin/domains/tools/problem-studio/pages/ProblemStudioPage.tsx`
- API client: `frontend/src/app_admin/domains/tools/problem-studio/api/problemStudio.api.ts`
- Hangul-compatible document writer: `frontend/src/app_admin/domains/tools/problem-studio/utils/worksheetDocument.ts`
- PDF/preview writer: `frontend/src/app_admin/domains/tools/problem-studio/utils/worksheetPdf.ts`
- Accepted source extensions in UI: `.pdf`, `.hwp`, `.hwpx`, `.doc`, `.docx`, `.png`, `.jpg`, `.jpeg`, `.webp`
- Current primary action sends:
  - `variant_mode: "copy"`
  - `variant_count: 1`
  - `use_ai: false`
  - `transfer_only: true`
- The primary downloaded file is `.doc` HTML compatible with Word/Hangul, not native `.hwp` or `.hwpx`.
- Answers and explanations are placed in an endnote-like section using Office/Hangul-compatible HTML markers.

Backend:

- Routes: `backend/apps/domains/tools/urls.py`
  - `POST /api/v1/tools/problem-studio/jobs/`
  - `GET /api/v1/tools/problem-studio/jobs/<job_id>/`
  - legacy/sync path also exists: `POST /api/v1/tools/problem-studio/generate/`
- Views: `backend/apps/domains/tools/problem_studio/views.py`
- Service: `backend/apps/domains/tools/problem_studio/services.py`
- Worker entry: `backend/apps/domains/tools/problem_studio/worker.py`
- AI job type: `problem_studio_package`
- Worker payload stores extracted source text and source metadata, so the async worker does not depend on request file lifetime.
- Transfer-only output uses `generation_engine: "source_transfer"` when extracted text exists.

Source extraction support:

- PDF: server attempts text extraction with PyMuPDF.
- HWPX: server reads `Preview/PrvText.txt` first, then XML content files.
- DOCX: server reads `word/document.xml`.
- HWP binary: metadata/warning only.
- DOC binary: metadata/warning only.
- Image files: kept on the frontend as visual attachments in the draft; there is no OCR in the current MVP.

## Known Limitations

- No OCR. Scanned images and image-only PDFs are preserved as images unless they have extractable text elsewhere.
- No native HWP/HWPX writer yet. The current `.doc` is intentionally a compatibility draft.
- No automatic LLM variation in the production UI. AI generation/fallback code paths exist, but the UI currently disables them.
- Template understanding is shallow. "매치업 기존 양식" and uploaded template names are recorded, but the system does not yet learn precise spacing/style rules from a template file.
- The generated answer/explanation fields are review aids, not authoritative. Teacher verification remains required.

## Future Direction

Keep future work staged in this order unless product policy changes:

1. Better source transfer
   - OCR for scanned images/image PDFs.
   - More reliable problem splitting.
   - Native HWPX output with real endnote objects.
2. Template fidelity
   - Read a sample academy format and map title/class/header/question spacing rules.
   - Reuse Matchup assets where possible.
3. Teacher-controlled generation
   - Similar-type candidates only after source transfer is stable.
   - Candidate selection/approval before download.
   - Short textbook-concept explanations.
   - Explicit handling of trap/false-friend explanations.
4. Operational guardrails
   - Source/rights logging.
   - Tenant isolation checks.
   - Answer/explanation validation before any AI-generated package is treated as ready.

## Handoff Checklist

Before changing this feature:

- Re-measure current code first. Do not assume this note is fresher than code.
- Preserve the current MVP promise: upload source -> editable Hangul-compatible review file.
- Do not enable `use_ai: true` in the primary UI without a product/policy decision.
- Keep advanced controls collapsed unless teachers ask for them in the default workflow.
- Run at least:
  - frontend `pnpm typecheck`
  - frontend changed-file ESLint
  - frontend `pnpm build`
  - backend problem studio service tests if backend extraction/worker code changes

Last deployed simplification:

- Frontend commit: `df19b06d` (`Simplify problem studio workflow UI`)
- GitHub quality/deploy/E2E run: `26385421989`
- Production route verified: `https://hakwonplus.com/admin/tools/problem-studio`

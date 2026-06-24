# Problem Studio Domain Note

Last verified: 2026-06-24

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
  - beta rewrite candidates for teacher review
  - text correction and question editor
  - PDF/print preview

Keep this hierarchy. If the page starts feeling like a full authoring studio again, it is probably regressing for the current use case.

## Current Implementation Truth

Frontend:

- Main page: `frontend/src/app_admin/domains/tools/problem-studio/pages/ProblemStudioPage.tsx`
- API client: `frontend/src/app_admin/domains/tools/problem-studio/api/problemStudio.api.ts`
- Hangul-compatible document writer: `frontend/src/app_admin/domains/tools/problem-studio/utils/worksheetDocument.ts`
- PDF/preview writer: `frontend/src/app_admin/domains/tools/problem-studio/utils/worksheetPdf.ts`
- Accepted source extensions in UI: `.pdf`, `.hwp`, `.hwpx`, `.doc`, `.docx`, `.zip`, `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`
- Current primary action sends:
  - `variant_mode: "copy"`
  - `variant_count: 1`
  - `use_ai: false`
  - `transfer_only: true`
- Beta rewrite action is intentionally secondary/collapsed and sends:
  - `variant_mode: "same-type" | "trap" | "concept"`
  - `variant_count: 1..10`
  - `use_ai: true`
  - `transfer_only: false`
- The primary downloaded file is `.doc` HTML compatible with Word/Hangul. The transfer ZIP also includes a text-focused `.hwpx` companion review workbook. Binary native `.hwp` writing is still not promised.
- Answers and explanations are placed in an endnote-like section using Office/Hangul-compatible HTML markers.

Backend:

- Routes: `backend/apps/domains/tools/urls.py`
  - `POST /api/v1/tools/problem-studio/jobs/`
  - `GET /api/v1/tools/problem-studio/jobs/<job_id>/`
  - `POST /api/v1/tools/problem-studio/transfer-document/`
- Views: `backend/apps/domains/tools/problem_studio/views.py`
- Service: `backend/apps/domains/tools/problem_studio/services.py`
- Transfer package builder: `backend/apps/domains/tools/problem_studio/transfer_documents.py`
- Source text extraction SSOT: `backend/apps/domains/tools/problem_studio/extractors.py`
- Source/question structure analyzer: `backend/apps/domains/tools/problem_studio/structure.py`
- Worker entry: `backend/apps/domains/tools/problem_studio/worker.py`
- AI job type: `problem_studio_package`
- Worker payload stores extracted source text and source metadata, so the async worker does not depend on request file lifetime.
- Transfer-only output uses `generation_engine: "source_transfer"` when extracted text exists.
- Beta rewrite uses the async job path only. If the AI adapter or quota fails, the service returns a rule-based teacher-review candidate and warning instead of blocking the base transfer feature.
- Large source-transfer downloads bypass the AI worker and JSON result payload. The transfer-document endpoint returns a ZIP package containing Hangul/Word-compatible `.doc` HTML drafts plus:
  - `00_먼저열기_검수체크리스트.doc`
  - `00_변환리포트.html`
  - `00_manifest.json`
  - `00_파일목록.csv`
  - `01_자체양식_문제검수본.doc`
  - `02_OCR_연결후보.csv`
  - `03_자체양식_문제검수본.hwpx`
- `00_manifest.json` uses `problem-studio-transfer-manifest/v2` and records:
  - `structured_item_count`
  - `structured_problem_count`
  - `ocr_candidate_count`
  - `quality_level`
  - `structure.review_actions`
  - `template_outputs`
- The response also exposes these headers:
  - `X-Problem-Studio-Structured-Item-Count`
  - `X-Problem-Studio-OCR-Candidate-Count`
  - `X-Problem-Studio-Quality-Level`

Source extraction and structure support:

- PDF: server attempts text extraction with PyMuPDF and records per-part text-layer results for structure analysis.
- Transfer package PDF: server renders every source page to embedded page images, split into 60-page `.doc` parts. If the PDF has no text layer, the part is marked as an OCR candidate rather than treated as editable text.
- Bounded OCR: scanned image/PDF pages without a text layer are attempted through local Tesseract OCR up to `PROBLEM_STUDIO_OCR_MAX_UNITS` per transfer package (default 8). Successful OCR text feeds the same structure analyzer; failed, skipped, or over-limit units remain in `02_OCR_연결후보.csv`.
- HWPX: server reads `Preview/PrvText.txt` first, then XML content files.
- DOCX: server reads `word/document.xml`.
- HWP binary: transfer package extracts BodyText paragraph text and every image-like `BinData` stream into a Hangul/Word-compatible `.doc`; compressed BinData image bytes are inflated and normalized to browser/Office-safe PNG/JPEG data URLs. The editable text and image gallery are preserved, but exact original HWP layout is not yet guaranteed.
- HWP binary beta rewrite also reuses the BodyText paragraph extractor for source text, but image/table layout still belongs to the base transfer package.
- DOC binary: metadata/warning only.
- ZIP: transfer package expands supported nested sources (`.pdf`, `.hwp`, `.hwpx`, `.docx`, `.doc`, image files) within safety limits and writes one or more `.doc` drafts per nested file. Beta rewrite also reads supported nested text documents within the same safety posture.
- Image files: embedded as visual pages in the transfer package, then passed through bounded local OCR. Remaining unreadable/over-limit images are marked as OCR candidates for later text editability.
- Structure analyzer: extracted PDF/HWP/HWPX/DOCX text is split into teacher-review problem/concept candidates. The result is a review aid, not an authoritative segmentation engine.
- Fixture verification script: `backend/scripts/problem_studio_transfer_fixtures.py` converts a local source folder into the same transfer ZIP and JSON summary for regression checks.
- UAT runbook: `backend/docs/operations/runbooks/problem-studio-source-transfer-uat.md`

## Product Review

- Commercial default: sell this as "source transfer to editable teacher-review drafts", not as fully autonomous problem generation.
- Value moment: teacher uploads the files they already receive and gets a Hangul/Word-compatible package without retyping. The first visible CTA must stay `원본 한글로 저장`.
- The downloaded ZIP is itself part of the product. The teacher should open `00_먼저열기_검수체크리스트.doc` first, then use `01_자체양식_문제검수본.doc`, `03_자체양식_문제검수본.hwpx`, `02_OCR_연결후보.csv`, `00_변환리포트.html`, and `00_파일목록.csv` to inspect structure candidates, OCR work queue, warnings, missing files, and source-to-output mapping.
- Beta positioning: rewrite candidates are optional, visibly marked Beta, and output to a review draft. A teacher should never need to touch beta controls to complete the base workflow.
- Business risk: OCR now improves editable text for bounded scanned/image-only units, but teacher review is still required for Korean, formulas, tables, and dense diagrams. The product should preserve source images and avoid promising exact native HWP layout.
- Next conversion lever: template fidelity and OCR coverage improve willingness to pay more than more generation modes.

## Known Limitations

- OCR in the transfer endpoint is bounded, local, and best-effort. Large scanned PDFs can still leave pages in `02_OCR_연결후보.csv` when they exceed `PROBLEM_STUDIO_OCR_MAX_UNITS`, the engine is unavailable, or OCR returns no text.
- No binary native `.hwp` writer yet. The current `.doc` is intentionally a compatibility draft, and `03_자체양식_문제검수본.hwpx` is a text-focused companion workbook rather than exact source layout reconstruction.
- HWP transfer preserves extracted text and embedded images, not exact object ordering or native HWP layout. It is a teacher review draft, not a final typeset workbook.
- Problem/concept structure is heuristic. The system now produces a `01_자체양식_문제검수본.doc`, but teachers must still verify split boundaries, choices, answers, and explanations.
- Automatic rewrite is available only as a collapsed Beta workflow. It is not the primary CTA and every result remains a teacher-review draft.
- Template understanding is shallow. "매치업 기존 양식" and uploaded template names are recorded, but the system does not yet learn precise spacing/style rules from a template file.
- The generated answer/explanation fields are review aids, not authoritative. Teacher verification remains required.

## Future Direction

Keep future work staged in this order unless product policy changes:

1. Better source transfer
   - Async OCR worker connection for scanned images/image PDFs that exceed the bounded synchronous OCR unit budget, using the manifest OCR candidate contract and `02_OCR_연결후보.csv` queue fields.
   - More reliable HWP text/image ordering and table/choice grouping.
   - Richer HWPX output with images, tables, and real endnote objects.
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
- Do not move Beta rewrite into the primary CTA without a product/policy decision.
- Keep advanced controls collapsed unless teachers ask for them in the default workflow.
- Run at least:
  - frontend `pnpm typecheck`
  - frontend changed-file ESLint
  - frontend `pnpm build`
  - backend problem studio service tests if backend extraction/worker code changes
  - real fixture transfer with `backend/scripts/problem_studio_transfer_fixtures.py` when package output changes
  - visual QA over the generated ZIP when HTML/document layout changes

Last deployed simplification:

- Frontend commit: `df19b06d` (`Simplify problem studio workflow UI`)
- GitHub quality/deploy/E2E run: `26385421989`
- Production route verified: `https://hakwonplus.com/admin/tools/problem-studio`

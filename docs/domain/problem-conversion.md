# Persistent exam conversion

## Scope and ownership

The staff-only conversion workspace replaces the primary Problem Studio screen.
Old APIs/jobs, personal fonts/styles and browser drafts remain on the legacy route.
Frontend owner: [PROBLEM-CONVERSION.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/PROBLEM-CONVERSION.md).
Legacy owner: [problem-studio.md](problem-studio.md).

`ProblemStudioConversion` stores tenant, teacher, source archive, options, page
transcription checkpoints, questions, review decisions, AI proposals and versioned
HWPX artifacts. Missing tenant, foreign teacher and foreign tenant fail closed.
The additive migration does not modify existing user data.

## API and processing

- `GET|POST /api/v1/tools/problem-studio/conversions/`: history/upload.
- `GET|PATCH .../conversions/<uuid>/`: snapshot, version-checked edits, review,
  proposal application/discard.
- `POST .../conversions/<uuid>/actions/`: resume, rebuild, correct, reanalyze, redraw.

Creation requires explicit external-AI consent and bounded PDF/PNG/JPEG/WebP
uploads. Sources remain under the exact tenant/conversion R2 prefix for resume.
They are persistent user documents, unlike legacy temporary archives. No automatic
error-report uploads to external support are added.
The source archive participates in the shared storage-reference registry, so an
unrelated cleanup cannot remove a still-referenced original. Disposable development
tenant cleanup removes the exact conversion prefix after worker writes have ended.

The existing AI CPU `problem_studio_transcription` job carries scoped conversion
identity and a committed operation nonce. Version/nonce checks fence superseded
workers. Dispatch occurs after commit; rejection leaves a retryable failed row.
Terminal job failure is reflected on read without a database write; an explicit
resume/edit persists recovery from worker crashes.

Extraction reuses the source renderer/structural analyzer. Successful image
transcriptions are checkpointed by SHA-256. After question extraction, retry reuses
saved questions and completed per-question analysis. Missing OCR coverage or
structural truncation fails rather than presenting partial output as complete.
Unreadable/no-question sources retain the original and show safe guidance to start
a new conversion with a clearer/smaller source. Provider/internal failures expose
only the generic resume guidance, never raw provider diagnostics.
Questions-only transcribes without solving; explanations adds review-draft solutions;
thorough requests deeper reasoning. Same-exam copies select the highest structural
confidence by question number and keep source pages for comparison. Different PDF
exams become separate frontend-created conversions.

Configured OpenAI/Bedrock routes and AI quotas remain. Existing full-book Beta
reservations/completed/free-run accounting remains on its legacy endpoint.
Teacher-requested AI changes are validated proposals and reopen question review.
Applying a proposal preserves both existing and newly reported review flags.
Accepting current content (one/all) explicitly discards the corresponding proposals
without applying them. Figure redraw returns bounded
line/rectangle/ellipse/text primitives, rendered locally with Korean fonts; arbitrary
code, SVG or external URLs are not executed. Original images remain available.
Reconstructed diagrams require teacher comparison; scientific equivalence is not promised.

## Editing and output

PATCH requires the current version; 409 does not write. Teacher changes reset review
and invalidate old artifacts. Edited questions are marked handled so resume cannot
overwrite them with initial AI analysis. Crop coordinates must be finite and inside
the selected page. Asset reads validate tenant/conversion prefix, byte count and
SHA-256. Responses omit storage keys and issue short-lived URLs only for current,
review-eligible artifacts. The original source ZIP has an owner-scoped fresh download
URL. Reanalysis reads the full source page when known; figure redraw uses the selected
crop/figure. Export preserves the actual image aspect ratio.
Preserved pages and figures keep their actual PNG/JPEG bytes and MIME; newly saved
JPEG assets use `.jpg` filenames. Existing JPEG assets with `.png` names remain
readable by their recorded MIME and signed key; no saved sources are rewritten.

The existing HWPX writer produces manuscript, four-question groups and portrait A3
two-column documents. Requested solutions begin on a new page. Long questions flow
without clipping; arbitrary content is not guaranteed to fit four questions on one
page. Native equations/image manifests remain, and teacher fonts/styles are reused.
All three artifacts are uploaded before publishing their shared source version.
Optional second review requires every question to be accepted before generation.
Otherwise output is explicitly a review draft. No destructive history/source API is
introduced; retention must be included in future user/tenant deletion procedures.

## Verification

`test_conversions` covers synthetic PDF→conversion→teacher edit→HWPX rebuild→reload,
tenant/owner isolation, stale versions, proposal-only AI, failed export resume,
required review and storage-key isolation. Existing Problem Studio tests cover the
underlying source/HWPX engine. Run model/migration checks, lint and owning CI gates.
These checks do not establish actual AI fidelity, licensed Hancom layout, or deployed
runtime identity. Preserve existing production continuity and paired release gates.
The isolated real-use gate invokes the existing Nova 2 Lite route using the development
host's sealed transcription grant; see [development runtime](../operations/persistent-development-runtime.md).

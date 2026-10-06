# Public report board and inline reader

Godmin and the shared homepage menu link to `/landing/resources`. Each resolved
tenant has an isolated, public-facing publication board for matchup and analysis
reports. Anonymous students, parents and outsiders open an article and read its
body immediately. Original downloads remain a secondary, collapsed option. This
replaces the download-first board because its purpose is also public explanation
and promotion. It does not replace landing configuration, publish a homepage draft
or change the older family community and generated-report boards.

## Publishing and isolation

`PublicResourceBoardAccess` names two distinct exact user IDs for one tenant.
Missing/incomplete configuration denies publishing. Each publisher must remain
an active user with an active owner/admin/teacher membership in that exact tenant;
global staff/superuser flags and preferred tenant pointers grant no extra access.
Configuration is an operator-only management command, with a read-only default:
`configure_public_resource_publishers --tenant-code godmin --publisher-ids 6249 3935`
and explicit `--apply` for the validated production configuration. These are the
confirmed godmin owner/admin accounts; similarly named student accounts are not
publishers. Read back configuration after applying. Other tenants require their
own exact publisher configuration; there is no godmin fallback.

The writer supplies a title, category, optional authored body and 0–5 originals.
A text-only article requires a nonempty body. An article with only auxiliary
formats also requires a body; a ZIP or unknown file must not masquerade as a
readable report. Every supported report attachment must finish preparation before
publication. The writer sees the same reader privately before publishing and can
reorder reports. File order persists through save, reload and either publisher's
subsequent edit.

API boundary: `/landing-public/resources/` list/create, `/:id/` detail/patch/delete,
`/capabilities/` writer capability; `/uploads/resource/` authenticated multipart
upload; `/resource-files/:uuid/` public short download link or uploader cleanup;
`/resource-files/:uuid/reader/` GET reader data and POST prepare/retry. Resolved
tenant is required everywhere. Public metadata excludes storage keys, login
identifiers, drafts and deleted posts. Reader GET never mutates or starts work.
Published readers are public; a pending original's preview is visible only to
its active designated uploader. POST rechecks publisher authorization under the
same user → membership → board locks used by publication/revocation.

Only ready, nonremoved files attach. New attachments must belong to the current
uploader; either publisher can edit a post retaining the other's attachments.
Files cannot move to another post. Duplicate creates reuse a request UUID only
when author, category, title, body and **ordered** file IDs match. PATCH uses
`expected_updated_at` under publisher/post locks. A changed revision returns 409
without changing the post or restoring files removed by another publisher. An
identical successful replay returns the current post without touching its
revision. The revision field remains optional for already-open older clients
during rolling release; those retain last-write behavior until refreshed.

If a create response is lost and the same request UUID is retried with changed
input, 409 identifies only that author's already-published post, revision and
attached IDs. The editor retains input, stops treating published originals as
pending cleanup, and continues as a versioned edit. The response also supplies
the published title, body and filenames for comparison. A subsequent concurrent
edit still receives the ordinary revision conflict.

## Originals and readable reports

Each nonempty original is limited to 30 MiB, without an extension allowlist.
PDF/HWP/HWPX retain bounded integrity checks; resource PDFs must be unencrypted
and at most 100 pages. HWP uses the HWP5 FileHeader/DocInfo/body container; HWPX
requires safe bounded ZIP entries, the Hancom mimetype and header/section members.
Original Unicode names are bounded; path/control characters are rejected.
Download disposition uses RFC 5987 and a safe ASCII fallback. All originals use
attachment disposition, including HTML/SVG and unknown formats. Other originals
use `application/octet-stream`, independent of client MIME. Storage keys contain
only the tenant and UUID; original names remain metadata and bytes never change.

Reader formats and results:

- PDF opens immediately through the established PDFJS canvas reader, with
  accessible extracted text, lazy visible-page rendering and zoom.
- HWP/HWPX are converted by the checksum-pinned official rhwp 0.8.7 executable.
  Plain text, table cells, formulas and raster images form the responsive article.
  An original-page PDF remains available. Unknown/complex DocLang structures
  select complete page mode; partial extraction is never called a complete report.
- DOCX/XLSX/PPTX use the pinned official LibreOffice 26.8.1 Writer/Calc/Impress
  components to preserve report pages. Macro providers, desktop integration and
  updater packages are not installed. The vendor packages preserve the existing
  final-runtime Perl removal boundary; Debian's ucf-dependent variant is not used.
- PNG/JPEG/WebP/GIF are shown inline. Still images apply EXIF orientation, retain
  transparency and are bounded/reencoded; bounded raster animations retain frames.
  UTF-8 TXT/Markdown are readable plain text. Document HTML is never executed.
- Other formats remain auxiliary originals alongside an authored readable body.

## Conversion, failure and recovery

Conversion belongs to the deterministic Tools worker and its existing queue,
using job type `public_resource_reader`. No AI generation or external document
service is involved. Upload stores the private original first, then enqueues work
after commit. A queue failure marks the reader and pending job failed. API work
does not run native conversion or hold an upload request open for it.

Migration `0010_publicresourcefile_position_and_more` adds ordering and reader
state/token/timestamp/data/object-key fields; it does not rewrite original bytes
or delete existing posts. Old clients ignore the additive metadata. Existing
PDFs remain immediately readable; old convertible originals without preparation
show an explicit preparation state and can be prepared by their publisher. Before
rollout completion, inspect existing published originals and prepare any affected
documents through their authorized publisher flow; do not claim an empty board
as evidence that existing documents converted.

The worker claims pending work once and validates tenant, source UUID, job ID,
generation token and canonical original key. Retry tokens fence stale deliveries;
duplicates cannot overwrite or delete a successful sibling's output. The child
receives a private temporary HOME and no application/cloud credentials. Linux
resource limits bound memory/CPU/output; the parent kills the whole process group
after 150 seconds. Seccomp blocks IPv4/IPv6 sockets and io_uring in native children.
Office packages reject macros, active objects, linked external inputs, file fields,
unsafe ZIP/XML and excessive expansion before rendering. No document text/native
stderr enters logs.

Preparation permits at most 100 pages, 10,000 blocks, 2 MiB reader JSON and 60 MiB
derived output. Images and nested structures have separate bounds. Failure keeps
the original and supplies a visible retry/PDF-export recovery message. Pending
work older than 10 minutes becomes retryable; publishing never silently drops
an unreadable supported report.

Derived objects use the exact prefix
`landing-public/resources/{tenant_id}/{file_uuid}/reader/{generation_uuid}/`.
Record cleanup targets before writing; row locks serialize bounded writes with
retry/deletion. Reader responses contain plain structured data and freshly signed
five-minute image/PDF URLs, never document HTML, scripts or remote assets. Reader
and download links use `Cache-Control: no-store`. Deleted posts/removed files issue
no new links; an issued URL remains usable until its five-minute expiry. Public
lists defer heavy reader JSON and never embed signed URLs.

Pending-file cleanup commits an unready tombstone before deleting the exact
recorded derivatives and original. Failure remains retryable and cannot attach
a missing object. Detached manual files and deleted posts remain private and are
not swept automatically. There is no background deletion of customer documents.
QA explicitly purges only its disposable rows and complete generation prefixes.

## Verification and operational ownership

Focused tests cover publishers, anonymous reading, private previews, revocation,
tenant separation, text-only publication, unsupported-only body requirements,
pending/failed preparation, ordered replay, token fencing, duplicate delivery,
cleanup, malformed packages, network denial and image fidelity. Native smoke uses
synthetic Korean HWP/HWPX/DOCX/XLSX/PPTX with paragraphs, tables, image and formula;
no customer documents enter fixtures. Browser evidence covers write → private
preview → publish → anonymous reload/read → second-publisher edit → delete and
cleanup at desktop and 390px, plus visible failure/retry and original-byte download.

The isolated non-login-UAT scenario uses its own admin and dedicated
`ymath-qa-resource-publisher` teacher. Its accounts/allowlist never touch godmin or
customer tenants. Cleanup includes original and derivative objects under the
exact QA tenant prefix and proves tenant/user/storage zero. Production inspection
is observational. Existing backend immutable-image/development/preproduction/
rolling-release gates and frontend same-artifact canary remain mandatory.

Runtime owners: [deployment modes](../operations/deployment-modes.md),
[persistent development](../operations/persistent-development-runtime.md),
[container security](../operations/container-image-security.md).
Frontend interactions: academy-frontend `docs/PUBLIC-RESOURCE-BOARD.md`.

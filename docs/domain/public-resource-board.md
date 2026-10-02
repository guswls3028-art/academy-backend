# Public teaching resource board

The existing godmin public homepage links to `/landing/resources`. The board has
two categories, matchup and analysis, for teacher-prepared PDF, HWP and HWPX
documents. Anonymous students, parents and outsiders can list, read, preview PDF
and download original documents. It does not replace the existing landing config
or publish a homepage draft. The older family community and generated report
boards retain their existing policies.

## Publishing and isolation

`PublicResourceBoardAccess` names two distinct exact user IDs for one tenant.
Missing/incomplete configuration denies publishing. Each publisher must remain
an active user with an active owner/admin/teacher membership in that exact tenant;
global staff/superuser flags and preferred tenant pointers grant no extra access.
Configuration is an operator-only management command, with a read-only default:
`configure_public_resource_publishers --tenant-code godmin --publisher-ids 6249 3935`
and explicit `--apply` for the validated production configuration. These are the
confirmed godmin owner/admin accounts; similarly named student accounts are not
publishers. Configuration must be read back after applying.

API boundary: `/landing-public/resources/` list/create, `/:id/` detail/patch/delete,
`/capabilities/` current writer capability; `/uploads/resource/` authenticated
multipart upload; `/resource-files/:uuid/` public short download link or publisher
cleanup of their own pending upload. Resolved tenant is required everywhere.
Public metadata excludes storage keys, login identifiers, drafts and deleted posts.

Files remain private while pending. Only successfully uploaded ready files can
attach. Cleanup marks a pending file unready in a committed transaction before
R2 deletion; storage or DB cleanup failure cannot publish a missing object, and
the uploader can retry cleanup. Creating a post with the explicit publish
action attaches 1–5 validated files atomically. New attachments must belong to
the current uploader; either publisher can edit a post retaining the other's
existing attachments. A file cannot move to another post. Duplicate create
retries reuse a request UUID only when the author, content and file set match.

## Files, failure and recovery

Each nonempty file is limited to 30 MiB. Case-insensitive PDF/HWP/HWPX extensions
and content validation are mandatory; MIME alone grants no acceptance. HWP uses
the HWP5 FileHeader/DocInfo/body container. HWPX requires bounded safe ZIP entries,
the Hancom mimetype and document/header/section members. Original Unicode names
are bounded and path/control characters are rejected; download disposition uses
RFC 5987 encoding. Private object keys are generated from tenant and UUID.

Public download links are generated after a fresh published-post and file check,
expire in 300 seconds, and use `Cache-Control: no-store`. No signed URL is stored
on a post. A removed attachment or deleted post yields no new links; a URL already
issued can remain usable until its five-minute expiry. GET requests never mutate
or perform cleanup. PDF previews use the established PDFJS renderer; Hangul files
are downloaded for opening with compatible software.

Upload failure attempts exact-key cleanup; if storage cleanup fails, a private
pending row retains the recovery key. Cancelled pending files have an authorized
uploader-only cleanup endpoint. Deleted posts and detached manual documents are
retained, remain private and are not swept automatically. QA must explicitly purge
its own disposable rows and storage keys and prove zero residue without touching
manual/customer content. Ordinary publisher accounts cannot change the allowlist.

## Verification

Focused contract tests cover both publishers' success, anonymous reads/links,
membership revocation, nonpublisher and superuser denial, tenant isolation,
pending and attached-file ownership, idempotent retry, deletion, invalid category,
format/size/name validation and storage failure recovery. Required backend gates
and exact-artifact isolated persistent development and preproduction gates apply.
Browser real-use verification covers publication → anonymous reload → actual
download bytes/PDF canvas, both categories, retained attachments, errors and retry,
desktop and 390px. Production verification is observational and uses no synthetic
student or customer fixture. Frontend interaction owner: academy-frontend
`docs/PUBLIC-RESOURCE-BOARD.md`.

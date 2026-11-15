# Developer agent session board

The developer can inspect Codex chats, Claude Code sessions and their recorded
subagents at `/dev/agent-sessions`. This is a metadata viewer, not a controller:
there are no model calls, session messages, start/stop actions or canonical task
updates. The frontend interaction owner is
[`frontend/docs/AGENT-SESSIONS.md`](../../../frontend/docs/AGENT-SESSIONS.md).

## Identity and data boundary

`GET /api/v1/core/dev/agent-sessions/` requires authentication, the existing
`IsPlatformAdmin` permission, tenant ID **1**, and the active account whose stored
username is `admin97` or `t1_admin97`. Other owners/superusers, a similarly named
account in another tenant, impersonation and support-preview tokens are denied
before reading storage. Only GET/HEAD/OPTIONS are supported. Responses use
`Cache-Control: no-store`; upstream diagnostics are never returned or logged.

The API reads exactly `_platform/agent-sessions/admin97/latest.json` in the
existing **private** `academy-storage` R2 bucket. This prefix is outside tenant
file namespaces and is not signed or exposed by this endpoint. Managed public
access and custom domains were checked disabled/absent on 2026-11-15; preserve
this private-storage requirement. No database migration or tenant business rows
are introduced. The snapshot is replaced in place; it is not a conversation
archive. The reader accepts at most 1 MiB and 600 nodes, rejects duplicate IDs,
cycles and future collector timestamps, and returns only allowlisted fields.

## Source and state meaning

`scripts/operations/agent_session_snapshot.py` reads the local Codex SQLite index
in read-only mode and only Academy rollout files. It selects at most 100 recent
root chats and their recorded `thread_spawn_edges` descendants. Extended Windows
paths are normalized; sibling repositories, `dnfm/`, `dnfm-group/` and rollout
paths outside the configured Codex sessions directories are excluded. It exports
the explicit display name/nickname, never first-user-message, preview, prompt,
tool arguments/results or credentials. Only lifecycle enums are retained from
bounded log tails. Unrecognized/new formats become `unknown`, not success.

Claude project history is limited to the Academy project directory. The current
CLI may also appear as `프로젝트 미지정` when launched at the drive root. Its
registry identity is checked against the Windows process creation time, so a
reused PID or exited process cannot masquerade as live. No messaging socket or
`.key` file is opened. Agent files retain their actual parent session; unrelated
Codex/Claude chats are not connected by inferred responsibility.

`completed` means **last response finished**, not that a feature, deployment or
user goal succeeded. `waiting` represents an observed idle/input-waiting runtime.
Interrupted/archived sessions and unknown states remain distinct. A running log
with no changes for 30 minutes becomes unknown. The finite log tail cannot prove
the state of every old or unusually long session; the UI exposes the evidence
basis and latest activity. Missing source data is reported separately from zero
sessions. A snapshot older than 90 seconds remains readable but is marked stale.

## Publisher and recovery

Use the existing Windows owner context and existing Python environment; no
conversation/model service is started. `--once` inspects without uploading;
`--publish` writes only the fixed private key. The publisher reads the approved
`/academy/api/env` SSM value in memory, retains only R2 connection fields, verifies
the AWS account and exact R2 endpoint/bucket, and never persists credentials.
Do not copy the API environment into files, command arguments, agents or logs.

The deployed local publisher must use a pinned copy of the reviewed script,
one owned process and a 30-second interval. Preserve its runtime/evidence outside
disposable worktree scratch. Install/start and exact live identity readback are
part of delivery; code publication alone does not prove the feed is running.
If the PC sleeps, network/auth fails or publishing stops, the board must show
the last collection time and stale/unavailable state. Restore the configured
publisher/authentication and use the ordinary refresh; do not fabricate nodes,
restart unrelated agents or silently change application credentials.

Windows installation uses `scripts/operations/install-agent-session-publisher.ps1`
with `-Action Install -Revision <full committed backend SHA>`. It copies only that
committed collector to `C:\academy\_runtime\agent-sessions`, records its SHA-256,
and registers `Academy-AgentSessions-admin97` for the current owner's interactive
logon. The task runs hidden through the existing `pythonw.exe`, starts immediately,
and restarts after a process failure. It requires no stored Windows password.
Task single-instance settings and an OS mutex prevent duplicate publishers.
The atomic `state.json` receipt contains only health/counts/timestamps, never
session names or credentials. `-Action Inspect` reads task/installation/health
evidence. Stop this exact scheduled task to disable publishing; after 90 seconds
the viewer becomes stale. Resume with `Start-ScheduledTask` for the same task.
Signing out or shutting down the PC stops collection; the next owner logon resumes it.

## Verification

`apps/core/tests/test_dev_agent_sessions.py` covers exact-account/tenant denial,
support/impersonation denial, no-store, read-only methods, safe source failures,
metadata whitelisting, hierarchy, stale/future and malformed snapshots.
`tests/test_agent_session_collector.py` covers workspace/path exclusion, actual
parent edges, prompt exclusion, partial logs, cached stale activity and peer
identity. Real-use acceptance additionally requires the exact deployed GET,
fresh publisher readback, visible stale/recovery and the frontend PC/390px flow.

# Score Edit Draft Autosave — Design & Integration

## 1. Current score edit flow (analysis)

### Where state lives
- **Server (source of truth)**: `GET /results/admin/sessions/<session_id>/scores/` → `meta` + `rows` (SessionScoreRow[]). Real scores live in `Result`, `ResultItem`, `HomeworkScore`.
- **Local edit buffer**: `ScoresTable` holds `pendingRef` (Map<string, PendingChange>) and `dirtyKeysRef` (Set<string>). Each cell blur/Enter adds to pending; no API call until "편집 종료".
- **Final save**: User clicks "편집 종료" → parent calls `panelRef.current.flushPendingChanges()` → `ScoresTable.flushPendingChanges()` runs: for each pending item calls existing patch APIs (`patchExamTotalScoreQuick`, `patchExamObjectiveScoreQuick`, `patchExamSubjectiveScoreQuick`, `patchHomeworkQuick`), then `invalidateQueries(sessionScores(sessionId))`.

### How changed cells are tracked
- Key format: `examTotal:enrollmentId:examId`, `examObjective:...`, `examSubjective:...`, `homework:enrollmentId:homeworkId`.
- `PendingChange` type: `examTotal` | `examObjective` | `examSubjective` | `homework` with corresponding ids and score (or metaStatus for homework 미제출).

### Keys
- Session: `session_id` (integer).
- Rows: `enrollment_id` (Enrollment = 수강생 1명).
- Exam: `exam_id`; sub: `total` | `objective` | `subjective`.
- Homework: `homework_id`; score number | null; metaStatus `NOT_SUBMITTED` optional.

### Existing draft/session concept
- **None.** Pending is in-memory only; refresh/crash loses all.

---

## 2. Where each concern lives (target)

| Concern | Lives in |
|--------|----------|
| **A. Local edit buffer** | Unchanged: `ScoresTable` `pendingRef` + `dirtyKeysRef`. Edits still only in memory until flush. |
| **B. Draft autosave** | Backend: `ScoreEditDraft` model (payload JSON). Frontend: `useScoreEditDraft(sessionId)` calls GET/PUT draft API when threshold (12 cells) or interval (40s). |
| **C. Final commit** | Unchanged: "편집 종료" → `flushPendingChanges()` (existing patch APIs) + new: POST draft commit to mark draft cleared. |

---

## 3. Backend design

- **Model**: `ScoreEditDraft` (one row per session per user).
  - `session_id` (int), `tenant_id` (int), `editor_user_id` (int), `payload` (JSONField list of changes), `updated_at`.
  - Unique on (session_id, editor_user_id).
- **Endpoints**:
  - `GET /results/admin/sessions/<session_id>/score-draft/` → 200 { changes: PendingChange[] } or 404.
  - `PUT /results/admin/sessions/<session_id>/score-draft/` → save body `{ changes: PendingChange[] }`, return 200.
  - `POST /results/admin/sessions/<session_id>/score-draft/commit/` → delete draft (or mark committed); 204. No write to Result/HomeworkScore here (frontend does that via existing patches).

---

## 4. Frontend design

- **ScoresTable** (ref handle):
  - `getPendingSnapshot(): PendingChange[]` — serialize current pending for autosave.
  - `applyDraftPatch(changes: PendingChange[])` — restore: clear pending, apply each to pendingRef+dirtyKeysRef, then sync DOM (or force re-fetch and merge).
- **useScoreEditDraft(sessionId)**:
  - On mount when entering edit mode: GET draft; if present show "이전에 임시저장된 편집 내용이 있습니다. 복원할까요?" (restore / discard).
  - While editing: count dirty cells from table ref; when count >= 12 or 40s since last autosave, PUT draft (getPendingSnapshot()).
  - Status: `idle` | `saving` | `saved` | `error`; show "저장 안 됨" | "임시저장 중..." | "임시저장됨 · n초 전" | "임시저장 실패".
  - beforeunload: if dirty and no recent successful autosave, warn.
- **편집 종료**: existing flush (patch APIs) + POST draft/commit; then clear local pending.

---

## 5. Files changed (summary)

- Backend: new model `ScoreEditDraft`, migration, new view + urls for score-draft GET/PUT/commit.
- Frontend: `ScoresTable` ref + getPendingSnapshot/applyDraftPatch; `useScoreEditDraft` hook; draft API client; SessionScoresPanel/SessionScoresTab recovery modal + status strip + beforeunload; 편집 종료 calls commit API.

---

## 6. Edge cases

- **Concurrent edit**: Optional: GET draft could return `editor_user_id`/`last_updated`; if different user or very old, show warning. Minimal: same user overwrites own draft.
- **Autosave failure**: Show "임시저장 실패", retry on next threshold or manual "다시 시도". Optional: keep last patch in sessionStorage as backup.
- **Restore then discard**: Discard = DELETE draft (or POST commit with discard flag) and reload scores.

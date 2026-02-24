# FORENSIC AUDIT: Why does academy-video-worker image load debug_toolbar?

**Scope:** Root cause of `ModuleNotFoundError: debug_toolbar` in Batch netprobe job.  
**Rule:** Factual trace only. No fixes. No suggestions.

---

## TASK A — Docker Image Content Verification

### 1. Dockerfile used to build academy-video-worker image

**File:** `docker/video-worker/Dockerfile`

```dockerfile
ARG BASE_IMAGE=academy-base:latest
FROM ${BASE_IMAGE} AS base

USER root
COPY src ./src
COPY academy ./academy
COPY apps ./apps
COPY libs ./libs
COPY manage.py ./
RUN chown -R appuser:appuser /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 ...
RUN mkdir -p /tmp/video-worker /tmp/video-worker-locks ...

USER appuser
COPY requirements/requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

ENTRYPOINT ["python", "-m", "apps.worker.video_worker.batch_entrypoint"]
CMD ["python", "-m", "apps.worker.video_worker.batch_main"]
```

### 2. Build context path used in docker build command

- **CI (GitHub Actions):** `context: .`, `file: docker/video-worker/Dockerfile` (`.github/workflows/video_batch_deploy.yml` lines 67–69). So context = repository root.
- **Doc/scripts:** `docker build -f docker/video-worker/Dockerfile -t ... .` (e.g. `PRODUCTION_CLOSURE_EXECUTION_PLAN.md`, `build_and_push_ecr.ps1`). Context = repository root.

### 3. Directory tree of image (inferred from Dockerfile COPY)

From COPY instructions (context = repo root):

- `/app/manage.py` — exists (copied)
- `/app/src/` — exists
- `/app/academy/` — exists
- `/app/apps/` — exists (includes `apps/api/config/settings/`, `apps/worker/video_worker/`, `apps/support/video/management/commands/`)
- `/app/libs/` — exists

There is no `/app/settings.py`. Settings live under `/app/apps/api/config/settings/` (e.g. `base.py`, `dev.py`, `worker.py`).

### 4. Confirmations

| Question | Answer |
|----------|--------|
| Does manage.py exist? | YES. Copied to `/app/manage.py`. |
| Does settings.py exist? | No top-level `settings.py`. Settings modules exist at `apps/api/config/settings/*.py` (base.py, dev.py, worker.py, etc.). |
| Where is debug_toolbar referenced? | Only in **source code**: `apps/api/config/settings/dev.py` lines 9 and 12 (`INSTALLED_APPS += ["debug_toolbar"]`, `MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")`). Not in worker.py. Not in requirements (see below). |

**Requirements in image:** `requirements/requirements.txt` is used (Dockerfile line 31). That file does **not** list `django-debug-toolbar` or `debug_toolbar`. So the package is **not** installed in the image.

---

## TASK B — ECR Image History

**Cannot be verified from repo alone.** Requires AWS CLI or console.

Commands to run (replace region/account as needed):

```bash
# 1) All tags for academy-video-worker
aws ecr list-images --repository-name academy-video-worker --region ap-northeast-2

# 2) Image digest and details for :latest
aws ecr describe-images --repository-name academy-video-worker --image-ids imageTag=latest --region ap-northeast-2

# 3) Push timestamp is in describe-images output (imagePushedAt)
```

| Question | Answer (from repo) |
|----------|--------------------|
| When was :latest pushed? | CANNOT VERIFY FROM REPO. Use `aws ecr describe-images ...` → `imagePushedAt`. |
| Was it pushed from CI or local? | CANNOT VERIFY FROM REPO. CI can push on push to main (paths include `docker/video-worker/**`) or workflow_dispatch (`.github/workflows/video_batch_deploy.yml`). |

---

## TASK C — Source Code Inspection

### "debug_toolbar"

| File | Line | Content |
|------|------|---------|
| `apps/api/config/settings/dev.py` | 9 | `"debug_toolbar",` (inside INSTALLED_APPS += []) |
| `apps/api/config/settings/dev.py` | 12 | `MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")` |
| `scripts/infra/ssm_bootstrap_video_worker.ps1` | 144 | Comment only: "# Batch/ops jobs always use worker settings (no debug_toolbar, ...)" |

### "INSTALLED_APPS"

| File | Line | Content |
|------|------|---------|
| `apps/api/config/settings/base.py` | 111 | `INSTALLED_APPS = [` |
| `apps/api/config/settings/worker.py` | 33–35 | Comment and `INSTALLED_APPS = [` (no debug_toolbar) |
| `apps/api/config/settings/dev.py` | 8–10 | `INSTALLED_APPS += [` and `"debug_toolbar",` |
| `supporting/solapi-python-main/.../settings.py` | 34 | Unrelated project |

### "manage.py"

| File | Line | Content |
|------|------|---------|
| `manage.py` | 24–26 | `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.dev")` |

So the **default** settings module when `DJANGO_SETTINGS_MODULE` is unset is **dev**.

### "DJANGO_SETTINGS_MODULE"

| File | Line | Content |
|------|------|---------|
| `manage.py` | 24–26 | `setdefault(..., "apps.api.config.settings.dev")` |
| `scripts/infra/ssm_bootstrap_video_worker.ps1` | 145 | Sets SSM payload key `DJANGO_SETTINGS_MODULE` = `apps.api.config.settings.worker` (when building JSON for SSM). |
| `apps/worker/video_worker/batch_main.py` | 18 | `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")` (used when entrypoint execs batch_main; not used when job command is `manage.py netprobe`). |
| `.env.example` | 57 | `DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod` |
| (Other files) | — | Various docs/scripts setting worker for other workers or tests. |

---

## TASK D — Worker Entry Flow

### 1. batch_entrypoint (Python, not shell)

**File:** `apps/worker/video_worker/batch_entrypoint.py`

- Fetches SSM parameter `/academy/workers/env` (or `BATCH_SSM_ENV`), decrypts.
- Parses `content = r["Parameter"]["Value"]` **line by line** with regex `^([A-Za-z_][A-Za-z0-9_]*)=(.*)$` (KEY=VAL) and sets `os.environ[key] = val`.
- Then: `argv = sys.argv[1:]` if len > 1 else default `["python", "-m", "apps.worker.video_worker.batch_main"]`; then `os.execvp(argv[0], argv)`.

**Fact:** SSM bootstrap writes the parameter value as **one line of JSON** (`ConvertTo-Json -Compress` in `ssm_bootstrap_video_worker.ps1`). A single JSON line does not match the KEY=VAL regex, so **no env vars are set from SSM** in the entrypoint (including `DJANGO_SETTINGS_MODULE`).

### 2. Command netprobe actually runs

- Job definition (`scripts/infra/batch/video_ops_job_definition_netprobe.json`): `"command": ["python", "manage.py", "netprobe"]`, `"environment": []`.
- Container run: ENTRYPOINT `python -m apps.worker.video_worker.batch_entrypoint` + command `python manage.py netprobe` → so the process is started as:
  - `python -m apps.worker.video_worker.batch_entrypoint python manage.py netprobe`
- So `sys.argv` = `[<path to batch_entrypoint>, "python", "manage.py", "netprobe"]`, hence `argv[1:]` = `["python", "manage.py", "netprobe"]`.
- Entrypoint then does `os.execvp("python", ["python", "manage.py", "netprobe"])`, so **manage.py** runs with argv `["manage.py", "netprobe"]`. So the netprobe job runs **manage.py**, which loads Django and runs the `netprobe` management command.

### 3. Whether DJANGO_SETTINGS_MODULE is set anywhere for netprobe

- **Job definition:** `"environment": []` — empty. So not set by Batch.
- **batch_entrypoint:** Sets only env vars parsed from SSM as KEY=VAL lines; SSM value is one JSON line, so **no env vars are set**, including **no** `DJANGO_SETTINGS_MODULE`.
- **manage.py:** Uses `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.dev")`. So when the variable is unset (as for netprobe), it becomes **dev**.

So for the netprobe job, **DJANGO_SETTINGS_MODULE is not set** by Batch or entrypoint, and **manage.py defaults to dev**.

---

## TASK E — Final Determination

| # | Question | Answer |
|---|----------|--------|
| 1 | Is academy-video-worker actually API-based image? | **YES.** Same repo root, same `manage.py`, same `apps/`, same `apps/api/config/settings/` (including dev.py and worker.py), same `requirements/requirements.txt` as API. It is the same Django application image used for API and video worker; entrypoint and command differ. |
| 2 | Is debug_toolbar present in worker settings? | **NO.** `apps/api/config/settings/worker.py` does not reference debug_toolbar. It is present only in **dev** settings (`apps/api/config/settings/dev.py`). |
| 3 | Is latest tag pointing to wrong build? | **UNKNOWN.** Cannot be determined from repo. ECR image digest and push time must be checked via AWS CLI/console. |
| 4 | Root cause in one sentence. | **manage.py defaults to `apps.api.config.settings.dev` when `DJANGO_SETTINGS_MODULE` is unset; the netprobe job is run as `python manage.py netprobe` with no env set from SSM (SSM value is JSON, entrypoint parses only KEY=VAL lines), so Django loads dev settings, which add `debug_toolbar` to INSTALLED_APPS, and the image does not install the `debug_toolbar` package.** |

---

**End of forensic audit. No fixes or suggestions.**

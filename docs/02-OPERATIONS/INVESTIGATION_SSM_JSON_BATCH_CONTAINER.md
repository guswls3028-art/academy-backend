# INVESTIGATION: SSM JSON parsing not effective in running Batch container

**Rule:** Factual trace only. No fixes. No suggestions.

---

## TASK A — Confirm batch_entrypoint.py in image (repo & build)

### 1. Current batch_entrypoint.py source in repo

**Path:** `apps/worker/video_worker/batch_entrypoint.py`

- **json.loads:** Present (line 44: `data = json.loads(content)`).
- **Fallback KEY=VAL:** Present (`_parse_key_val_lines`, lines 17–30; used in `_load_env_from_ssm_value` when JSON fails, lines 55–63).
- **DJANGO_SETTINGS_MODULE:** Set/defaulted (lines 92–98: check then `os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.worker")`; log line 99).
- **Log "Loaded SSM JSON with X keys":** Present (line 84).

### 2. Git commit hash used in build

**Cannot be determined from repo.** The build runs on a remote EC2. The remote runs:

- `cd /home/ec2-user/build && (test -d academy/.git && (cd academy && git fetch && git reset --hard origin/main && git pull)) || (rm -rf academy && git clone <GitRepoUrl> academy && cd academy)`

So the code is **origin/main** of `GitRepoUrl` (default: `https://github.com/guswls3028-art/academy-backend.git`) at the time of that pull. The commit hash is not logged by the script. **Operator must check on build server:** after a build, `cd /home/ec2-user/build/academy && git rev-parse HEAD` (or inspect SSM command output if logged).

### 3. Docker build context path

- **Remote build:** `cd /home/ec2-user/build/academy` then `docker build ... .` → context = **repository root** (`academy` directory on the build server).

### 4. Exact docker build command used in build_and_push_ecr_remote.ps1 (VideoWorkerOnly)

On the remote host the script runs (from `build_and_push_ecr_on_ec2.sh` with `VIDEO_WORKER_ONLY=1`):

```bash
cd /home/ec2-user/build/academy
export VIDEO_WORKER_ONLY=1
./scripts/build_and_push_ecr_on_ec2.sh
```

Inside that script (when `VIDEO_WORKER_ONLY` is set):

```bash
docker build $DOCKER_EXTRA -f docker/video-worker/Dockerfile -t academy-video-worker:latest .
```

- **Context:** `.` = current directory = `/home/ec2-user/build/academy` (repo root).
- **File:** `docker/video-worker/Dockerfile`.
- **NoCache:** Only if `-NoCache` was passed to the PowerShell script (`NO_CACHE=1` → `DOCKER_EXTRA="--no-cache"`).

### 5. Does Dockerfile copy updated batch_entrypoint.py?

**Yes.** Dockerfile contains:

```dockerfile
COPY apps ./apps
```

So `apps/worker/video_worker/batch_entrypoint.py` from the build context is copied into the image as `/app/apps/worker/video_worker/batch_entrypoint.py`. There is no separate copy step; it is included in `COPY apps ./apps`. **Whether the “updated” file is in the image depends on whether that file in the build context (at build time) was the version with JSON parsing.** That is determined by the commit on the build server at build time (see 2).

---

## TASK B — Inspect running container content

**Requires operator to submit a Batch job with overridden command.** Use job definition `academy-video-ops-netprobe` (or any ops def using the same image) and override the command.

**Option 1 — cat file (correct path in image):**

- Path in image: `/app/apps/worker/video_worker/batch_entrypoint.py` (not `/app/batch_entrypoint.py`).

Submit job with command:

```json
["cat", "/app/apps/worker/video_worker/batch_entrypoint.py"]
```

Example (PowerShell):

```powershell
aws batch submit-job --job-name debug-cat-entrypoint --job-queue academy-video-batch-queue --job-definition academy-video-ops-netprobe --container-overrides '{"command":["cat","/app/apps/worker/video_worker/batch_entrypoint.py"]}' --region ap-northeast-2
```

Then fetch log stream for that job and read stdout.

**Option 2 — Python inspect (module path):**

- Module name at runtime: `apps.worker.video_worker.batch_entrypoint` (working dir `/app`, PYTHONPATH includes `/app` in base image).

Submit job with command:

```json
["python", "-c", "import inspect, apps.worker.video_worker.batch_entrypoint as m; print(inspect.getsource(m))"]
```

**Verification:**

- Does the output contain `json.loads`?
- Does it contain logic that falls back to KEY=VAL parsing (e.g. `_parse_key_val_lines` or equivalent)?
- Does it set or assert `DJANGO_SETTINGS_MODULE`?

---

## TASK C — Confirm DJANGO_SETTINGS_MODULE at runtime

**Requires operator to submit a Batch job.** Override command to print the env var (entrypoint still runs first and loads SSM into env, then this command runs if we override the whole command — **note:** with override, the container runs the override command instead of the default; the ENTRYPOINT is still `python -m apps.worker.video_worker.batch_entrypoint`, and the override is passed as CMD. So the process is: entrypoint runs, loads SSM into env, then execvp(override_cmd). So the override command is run **after** entrypoint; but the default is `python manage.py netprobe`. So when we override to `python -c "print(...)"`, we replace the CMD, so entrypoint runs with argv = ["python", "-c", "print(...)"]. So entrypoint loads env then execvp("python", ["python", "-c", "print(...)"]). So the child process **does** see the env set by the entrypoint. So this is valid.

Submit job with command:

```json
["python", "-c", "import os; print(os.environ.get('DJANGO_SETTINGS_MODULE', 'NOT_SET'))"]
```

Example:

```powershell
aws batch submit-job --job-name debug-django-settings --job-queue academy-video-batch-queue --job-definition academy-video-ops-netprobe --container-overrides '{"command":["python","-c","import os; print(os.environ.get(\"DJANGO_SETTINGS_MODULE\", \"NOT_SET\"))"]}' --region ap-northeast-2
```

Check CloudWatch Logs for that job: stdout should show the value or `NOT_SET`.

---

## TASK D — Inspect SSM value exactly as container sees it

**Requires operator to submit a Batch job.** Override command to fetch SSM and print the raw value (job role must have `ssm:GetParameter`).

Submit job with command:

```json
["python", "-c", "import boto3, json; c=boto3.client('ssm', region_name='ap-northeast-2'); v=c.get_parameter(Name='/academy/workers/env', WithDecryption=True)['Parameter']['Value']; print('RAW_LEN:', len(v)); print('RAW_VALUE:'); print(v[:2000]); print('---'); obj=json.loads(v); print('KEYS:', list(obj.keys())); print('DJANGO_SETTINGS_MODULE in JSON:', obj.get('DJANGO_SETTINGS_MODULE', 'MISSING'))"]
```

(Trim if command length is an issue; at minimum print `v` and whether `json.loads(v)` succeeds and whether `DJANGO_SETTINGS_MODULE` is in the parsed object.)

**Verification:**

- Is the printed value valid JSON?
- Does the parsed object contain key `DJANGO_SETTINGS_MODULE`?

---

## TASK E — Final determination

**1. Is updated entrypoint inside image?**  
**Cannot be determined from repo alone.** Repo contains the updated entrypoint. The image contains whatever was in `apps/` at build time on the remote server. Confirm only by TASK B (inspect file or source in container).

**2. Is JSON parsing logic present at runtime?**  
**Cannot be determined without TASK B.** If the container’s `batch_entrypoint.py` (or its source) includes `json.loads` and KEY=VAL fallback, yes; otherwise no.

**3. Is DJANGO_SETTINGS_MODULE loaded before manage.py?**  
**Cannot be determined without TASK C.** If the debug job in TASK C prints `apps.api.config.settings.worker` (or another value), it is loaded by the entrypoint before the child runs. If it prints `NOT_SET`, it is not set when the override command runs (entrypoint did not set it or failed before exec).

**4. Root cause in one sentence.**  
**Cannot be stated without runtime results from B/C/D.** Possible causes: (a) image was built from a commit that does not contain the JSON entrypoint changes; (b) entrypoint runs but SSM value is not valid JSON or is not what the script expects; (c) entrypoint fails before setting env and the override command is never run; (d) job definition or command override prevents the entrypoint from running. Run the debug jobs above and use their output to choose among these.

---

**End of investigation document. Run TASK B, C, D and then answer E from the results.**

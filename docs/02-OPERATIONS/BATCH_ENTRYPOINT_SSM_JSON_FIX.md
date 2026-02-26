# Batch Entrypoint SSM JSON Fix — Deliverable

## 1. Full updated batch_entrypoint.py

Path: `apps/worker/video_worker/batch_entrypoint.py` (see repo).

---

## 2. Diff against previous version

```diff
--- a/apps/worker/video_worker/batch_entrypoint.py
+++ b/apps/worker/video_worker/batch_entrypoint.py
@@ -1,50 +1,110 @@
 #!/usr/bin/env python3
 """
-Batch entrypoint: fetch /academy/workers/env from SSM, set os.environ, then exec batch_main.
+Batch entrypoint: fetch /academy/workers/env from SSM, set os.environ, then exec.
+Supports SSM value as JSON (production) or legacy KEY=VALUE lines.
 Job role must have ssm:GetParameter for academy/*.
 """
 from __future__ import annotations

+import json
 import os
 import re
-import subprocess
 import sys

 REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-2"
 SSM_NAME = os.environ.get("BATCH_SSM_ENV", "/academy/workers/env")


+def _parse_key_val_lines(content: str) -> dict[str, str]:
+    """Legacy: parse KEY=VALUE lines. Returns dict of key -> value."""
+    out: dict[str, str] = {}
+    for line in content.splitlines():
+        line = line.strip()
+        if not line or line.startswith("#"):
+            continue
+        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
+        if m:
+            key, val = m.group(1), m.group(2).strip()
+            if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
+                val = val[1:-1].replace("\\'", "'").replace('\\"', '"')
+            out[key] = val
+    return out
+
+
+def _load_env_from_ssm_value(content: str) -> tuple[int, bool]:
+    """
+    Load environment from SSM value. Prefer JSON; fallback to KEY=VALUE lines.
+    Returns (number of keys set, True if JSON was used).
+    """
+    content = (content or "").strip()
+    if not content:
+        raise RuntimeError("SSM parameter value is empty")
+
+    # 1) Try JSON
+    try:
+        data = json.loads(content)
+        if not isinstance(data, dict):
+            raise ValueError("SSM JSON must be a JSON object")
+        for k, v in data.items():
+            if not isinstance(k, str):
+                continue
+            os.environ[k] = str(v) if v is not None else ""
+        return len(data), True
+    except json.JSONDecodeError:
+        pass
+
+    # 2) Fallback: KEY=VALUE lines
+    parsed = _parse_key_val_lines(content)
+    if not parsed:
+        raise RuntimeError(
+            "SSM value is neither valid JSON nor KEY=VALUE lines; no env loaded"
+        )
+    for k, v in parsed.items():
+        os.environ[k] = v
+    return len(parsed), False
+
+
 def main() -> int:
     try:
         import boto3
+    except ImportError as e:
+        print(f"batch_entrypoint: boto3 required: {e}", file=sys.stderr)
+        return 1

+    try:
         client = boto3.client("ssm", region_name=REGION)
         r = client.get_parameter(Name=SSM_NAME, WithDecryption=True)
         content = r["Parameter"]["Value"]
-        for line in content.splitlines():
-            line = line.strip()
-            if not line or line.startswith("#"):
-                continue
-            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
-            if m:
-                key, val = m.group(1), m.group(2).strip()
-                if val.startswith("'") and val.endswith("'") or val.startswith('"') and val.endswith('"'):
-                    val = val[1:-1].replace("\\'", "'").replace('\\"', '"')
-                os.environ[key] = val
     except Exception as e:
         print(f"batch_entrypoint: SSM fetch failed: {e}", file=sys.stderr)
-        # Continue - maybe env is injected another way
+        return 1
+
+    try:
+        n, from_json = _load_env_from_ssm_value(content)
+        if from_json:
+            print(f"Loaded SSM JSON with {n} keys", file=sys.stderr)
+        else:
+            print(f"Loaded SSM env with {n} keys (legacy)", file=sys.stderr)
+    except (RuntimeError, ValueError) as e:
+        print(f"batch_entrypoint: {e}", file=sys.stderr)
+        return 1
+
+    # Assert DJANGO_SETTINGS_MODULE
+    dsm = os.environ.get("DJANGO_SETTINGS_MODULE", "").strip()
+    if not dsm:
+        os.environ.setdefault(
+            "DJANGO_SETTINGS_MODULE",
+            "apps.api.config.settings.worker",
+        )
+        dsm = os.environ["DJANGO_SETTINGS_MODULE"]
+    print(f"DJANGO_SETTINGS_MODULE = {dsm}", file=sys.stderr)

-    # CMD from Batch is passed as args: python -m batch_main <job_id>
+    # CMD from Batch is passed as args
     argv = sys.argv[1:] if len(sys.argv) > 1 else ["python", "-m", "apps.worker.video_worker.batch_main"]
     if argv[0] == "python" or argv[0].endswith("python"):
         os.execvp(argv[0], argv)
```

---

## 3. Example log output when SSM JSON loads correctly

When `/academy/workers/env` contains valid JSON (e.g. from `ssm_bootstrap_video_worker.ps1` with `-Overwrite`), CloudWatch Logs for the netprobe (or any Batch job using this entrypoint) will show:

```
Loaded SSM JSON with 25 keys
DJANGO_SETTINGS_MODULE = apps.api.config.settings.worker
```

Then the actual command output (e.g. netprobe) follows. No secret values are printed.

---

## 4. Final rebuild + redeploy command sequence

From repository root, with AWS credentials and ECR login configured:

```powershell
# 1) Build base image (if not already built)
docker build -f docker/Dockerfile.base -t academy-base:latest .

# 2) Build video-worker image
docker build -f docker/video-worker/Dockerfile -t academy-video-worker:latest .

# 3) Tag for ECR (replace 809466760795 and ap-northeast-2 if different)
$ECR_URI = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
docker tag academy-video-worker:latest $ECR_URI

# 4) Login and push
aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com
docker push $ECR_URI

# 5) Ensure SSM has JSON with DJANGO_SETTINGS_MODULE (ssm_bootstrap already sets worker)
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite

# 6) Run netprobe (no infra change; job def uses same image:latest)
.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName academy-video-batch-queue

# 7) Production done check
.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
```

One-liner build+push (after base exists):

```powershell
docker build -f docker/video-worker/Dockerfile -t 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest . && aws ecr get-login-password --region ap-northeast-2 | docker login --username AWS --password-stdin 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com && docker push 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest
```

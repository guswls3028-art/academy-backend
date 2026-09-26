"""Fixed, read-only SSM probe for the isolated candidate QA instance.

The controller embeds this exact source in AWS-RunShellScript. It accepts no
caller-supplied commands or lease claims and emits only allowlisted metadata.
It never reads or prints container secrets or message bodies.
"""
from __future__ import annotations

import json
from http.client import HTTPConnection
from pathlib import Path
import stat
import subprocess
import time


CONTAINERS = {
    "api": "academy-api",
    "ai": "academy-ai-development",
    "tools": "academy-tools-development",
    "messaging": "academy-messaging-development",
}
ENV_KEYS = (
    "ACADEMY_RUNTIME_ENV", "ACADEMY_QA_MODE", "CANDIDATE_LEASE_REQUIRED",
    "ACADEMY_QA_LEASE_ID", "ACADEMY_QA_BINDING_SHA256",
    "ACADEMY_QA_MESSAGE_KEY_VERSION", "ACADEMY_QA_ACTIVITY_DIR", "GUNICORN_WORKERS",
    "AI_SQS_QUEUE_NAME_LITE", "AI_SQS_QUEUE_NAME_BASIC",
    "AI_SQS_QUEUE_NAME_PREMIUM", "TOOLS_SQS_QUEUE_NAME",
    "MESSAGING_SQS_QUEUE_NAME", "SOLAPI_MOCK",
)


class ProbeError(RuntimeError):
    pass


def _run(*argv):
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=15, check=False)
    if completed.returncode or completed.stderr.strip() or len(completed.stdout) > 120_000:
        raise ProbeError("QA read-only Docker probe failed")
    return completed.stdout


INSIDE_CODE = r'''
import json, os, re, stat, time
from pathlib import Path

processes = []
boot = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
            if line.startswith("btime "))
ticks = os.sysconf("SC_CLK_TCK")
for path in Path("/proc").iterdir():
    if not path.name.isdecimal() or int(path.name) == os.getpid():
        continue
    try:
        raw = (path / "stat").read_text(encoding="utf-8")
        after = raw.rsplit(") ", 1)[1].split()
        argv = (path / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        processes.append({"pid": int(path.name), "ppid": int(after[1]),
                          "started_at": int(boot + int(after[19]) / ticks),
                          "gunicorn": "gunicorn" in argv,
                          "command": "api" if "gunicorn" in argv else
                                     "ai" if "apps.worker.ai_worker.sqs_main_cpu" in argv else
                                     "tools" if "apps.worker.tools_worker.sqs_main" in argv else
                                     "messaging" if "apps.worker.messaging_worker.sqs_main" in argv else "other"})
    except (OSError, ValueError, IndexError):
        continue

directory = Path("/tmp/academy-qa-activity")
if not directory.is_dir() or directory.is_symlink():
    raise SystemExit("QA activity directory unavailable")
snapshots = []
for path in directory.iterdir():
    if not re.fullmatch(r"(?:api|ai|tools|messaging)-[1-9][0-9]*-[0-9a-f]{32}\.json", path.name):
        raise SystemExit("Unexpected QA activity entry")
    if path.is_symlink():
        raise SystemExit("QA activity symlink")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 20000 or metadata.st_mode & 0o077:
        raise SystemExit("QA activity file unsafe")
    row = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(row, dict):
        raise SystemExit("QA activity file invalid")
    allowed = ("enabled", "kind", "pid", "process_id", "observed_at", "alive",
               "lease_id", "binding_sha256", "revision", "enforcement_expires_at",
               "lease_verified", "control_hold", "admission", "inflight", "holds")
    safe = {key: row.get(key) for key in allowed}
    if isinstance(safe["inflight"], list):
        safe["inflight"] = [
            {key: item.get(key) for key in ("operation_sha256", "lease_revision", "started_at", "message_id")}
            if isinstance(item, dict) else None for item in safe["inflight"]
        ]
    if isinstance(safe["holds"], list):
        safe["holds"] = [True for _ in safe["holds"]]
    snapshots.append({"filename": path.name, "mtime": int(metadata.st_mtime), "data": safe})
print(json.dumps({"observed_at": int(time.time()), "processes": processes,
                  "snapshots": snapshots}, sort_keys=True, separators=(",", ":")))
'''


def collect():
    required_env = Path("/opt/academy-qa/required.env")
    marker = Path("/opt/academy-qa/inert-ready")
    if required_env.is_symlink() or not required_env.is_file():
        raise ProbeError("QA required launch marker missing")
    required_stat = required_env.stat()
    if required_stat.st_uid != 0 or stat.S_IMODE(required_stat.st_mode) != 0o600 or required_env.read_text(encoding="utf-8") != (
        "ACADEMY_QA_MODE=isolated-qa\nCANDIDATE_LEASE_REQUIRED=true\n"
    ):
        raise ProbeError("QA required launch marker differs")
    names = _run("docker", "ps", "-a", "--format", "{{.Names}}").splitlines()
    if len(names) != len(set(names)):
        raise ProbeError("Duplicate Docker container name")
    containers = {}
    for kind, name in CONTAINERS.items():
        if name not in names:
            continue
        inspected = json.loads(_run("docker", "inspect", name))
        if not isinstance(inspected, list) or len(inspected) != 1:
            raise ProbeError("Incomplete Docker inspection")
        item = inspected[0]
        image = json.loads(_run("docker", "image", "inspect", item["Image"]))
        if not isinstance(image, list) or len(image) != 1:
            raise ProbeError("Incomplete immutable image inspection")
        raw_env = item["Config"]["Env"]
        env = dict(entry.split("=", 1) for entry in raw_env if "=" in entry and entry.split("=", 1)[0] in ENV_KEYS)
        inside = json.loads(_run("docker", "exec", name, "python", "-c", INSIDE_CODE))
        containers[kind] = {
            "name": name, "status": item["State"]["Status"],
            "health": item["State"].get("Health", {}).get("Status"),
            "image_ref": item["Config"]["Image"], "image_id": item["Image"],
            "actual_image_id": image[0]["Id"], "repo_digests": image[0]["RepoDigests"],
            "cmd": item["Config"]["Cmd"], "image_cmd": image[0]["Config"]["Cmd"],
            "entrypoint": item["Config"]["Entrypoint"],
            "image_entrypoint": image[0]["Config"]["Entrypoint"],
            "network_mode": item["HostConfig"]["NetworkMode"],
            "ports": item["NetworkSettings"]["Ports"],
            "env": env, "inside": inside,
        }
    api_health = False
    if CONTAINERS["api"] in names:
        try:
            connection = HTTPConnection("127.0.0.1", 8000, timeout=2)
            try:
                connection.request("GET", "/healthz")
                response = connection.getresponse()
                api_health = response.status == 200
                response.read(1024)
            finally:
                connection.close()
        except Exception:
            api_health = False
    return {"schema_version": 1, "observed_at": int(time.time()),
            "required_env_exact": True, "inert_ready": marker.is_file() and not marker.is_symlink(),
            "container_names": names, "containers": containers, "api_health": api_health}


if __name__ == "__main__":
    try:
        print(json.dumps(collect(), sort_keys=True, separators=(",", ":")))
    except Exception:
        # Never expose Docker stderr, environment values or exception text.
        raise SystemExit("candidate QA read-only probe unavailable") from None

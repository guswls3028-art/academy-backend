#!/usr/bin/env python3
"""Production student-score submit→verify→chart→void→evidence-delete roundtrip.

Required environment: E2E_STUDENT_USER/PASS and E2E_ADMIN_USER/PASS.
Use ``--cleanup-remote`` from the backend repo to remove the final detached
``[E2E-*]`` audit rows through the existing digest-pinned SSM management path.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path


API_URL = os.environ.get("E2E_API_URL", "https://api.hakwonplus.com").rstrip("/")
TENANT_CODE = os.environ.get("E2E_TENANT_CODE", "hakwonplus")
TIMEOUT = 60


class SmokeFail(SystemExit):
    def __init__(self, message: str):
        sys.stderr.write(f"\nREPORTED SCORE SMOKE FAIL: {message}\n")
        super().__init__(1)


def request(method: str, path: str, *, token: str | None = None, body: bytes | None = None, content_type: str = "application/json") -> tuple[int, dict]:
    headers = {"X-Tenant-Code": TENANT_CODE, "Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API_URL}/api/v1{path}", method=method, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return exc.code, {"_raw": raw[:400].decode("utf-8", errors="replace")}


def json_request(method: str, path: str, payload: dict, *, token: str | None = None) -> tuple[int, dict]:
    return request(method, path, token=token, body=json.dumps(payload).encode("utf-8"))


def login(role: str) -> str:
    username = os.environ.get(f"E2E_{role.upper()}_USER", "").strip()
    password = os.environ.get(f"E2E_{role.upper()}_PASS", "").strip()
    if not username or not password:
        raise SmokeFail(f"E2E_{role.upper()}_USER/PASS are required")
    status, payload = json_request("POST", "/token/", {
        "username": username,
        "password": password,
        "tenant_code": TENANT_CODE,
    })
    if status != 200 or not payload.get("access"):
        raise SmokeFail(f"{role} login {status}: {payload}")
    return str(payload["access"])


def multipart(fields: dict[str, str], *, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----academy-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(),
            value.encode("utf-8"),
            b"\r\n",
        ])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: image/jpeg\r\n\r\n",
        content,
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def assert_status(status: int, expected: int, step: str, payload: dict) -> None:
    if status != expected:
        raise SmokeFail(f"{step} expected={expected} actual={status}: {payload}")


def remote_cleanup(marker: str, *, recover_active: bool = False) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    command = (
        "cleanup_reported_score_canary "
        f"--tenant-code {TENANT_CODE} --marker '{marker}' --confirm '{marker}'"
        f"{' --recover-active' if recover_active else ''}"
    )
    result = subprocess.run(
        [
            "pwsh",
            "-File",
            str(repo_root / "scripts" / "v1" / "run-api-management-remote.ps1"),
            "-Command",
            command,
        ],
        cwd=repo_root,
        check=False,
    )
    return result.returncode


def run(cleanup_remote: bool) -> None:
    marker = f"[E2E-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}]"
    student_token = login("student")
    admin_token = login("admin")
    status, profile = request("GET", "/student/me/", token=student_token)
    assert_status(status, 200, "student profile", profile)
    student_ps = str(profile.get("ps_number") or "")
    student_id = profile.get("id")
    if not student_ps or not student_id:
        raise SmokeFail(f"student profile missing id/ps_number: {profile}")

    cleanup_state: dict[str, object] = {
        "stage": "pre-upload",
        "finished": False,
        "score_id": None,
        "file_id": None,
    }

    def emergency_cleanup() -> None:
        if cleanup_state["finished"]:
            return
        score_id = cleanup_state.get("score_id")
        file_id = cleanup_state.get("file_id")
        try:
            if score_id and cleanup_state["stage"] == "pending":
                cleanup_status, _ = json_request(
                    "PATCH",
                    f"/results/admin/reported-scores/{score_id}/review/",
                    {"action": "reject", "review_all_evidence": True, "review_note": f"실패한 운영 검증 정리 {marker}"},
                    token=admin_token,
                )
                if cleanup_status == 200:
                    cleanup_state["stage"] = "terminal"
            elif score_id and cleanup_state["stage"] == "verified":
                cleanup_status, _ = json_request(
                    "PATCH",
                    f"/results/admin/reported-scores/{score_id}/review/",
                    {"action": "void", "review_all_evidence": True, "review_note": f"실패한 운영 검증 정리 {marker}"},
                    token=admin_token,
                )
                if cleanup_status == 200:
                    cleanup_state["stage"] = "terminal"
            if file_id and cleanup_state["stage"] in {"terminal", "voided"}:
                delete_query = urllib.parse.urlencode({"scope": "student", "student_ps": student_ps})
                delete_status, _ = request(
                    "DELETE",
                    f"/storage/inventory/files/{file_id}/?{delete_query}",
                    token=student_token,
                )
                if delete_status == 204:
                    cleanup_state["stage"] = "deleted"
            if cleanup_remote and remote_cleanup(marker, recover_active=True) == 0:
                cleanup_state["finished"] = True
            else:
                sys.stderr.write(
                    f"CANARY CLEANUP REQUIRED: cleanup_reported_score_canary "
                    f"--tenant-code {TENANT_CODE} --marker '{marker}' --confirm '{marker}' --recover-active\n"
                )
        except Exception as exc:
            sys.stderr.write(f"EMERGENCY CLEANUP FAILED marker={marker}: {exc}\n")

    # Register before upload: a committed upload whose response times out still has an
    # exact UUID marker and a remote recovery path.
    atexit.register(emergency_cleanup)

    now = datetime.now()
    score_items = [
        {"subject": marker, "score": 87, "max_score": 100},
        {"subject": f"{marker}-2", "score": 91, "max_score": 100},
    ]
    fields = {
        "scope": "student",
        "student_ps": student_ps,
        "score_submission": "true",
        "score_source": "school_exam",
        "academic_year": str(now.year),
        "semester": "1" if now.month <= 7 else "2",
        "exam_round": "other",
        "exam_name": marker,
        "exam_date": now.date().isoformat(),
        "subject": marker,
        "score": "87",
        "max_score": "100",
        "score_items": json.dumps(score_items, ensure_ascii=False),
        "display_name": f"{marker}.jpg",
        "description": f"성적표 운영 왕복 검증 {marker}",
        "icon": "file-text",
    }
    upload_body, upload_type = multipart(
        fields,
        filename=f"{marker}.jpg",
        content=b"\xff\xd8\xff\xe0academy-score-canary",
    )
    status, uploaded = request(
        "POST",
        "/storage/inventory/upload/",
        token=student_token,
        body=upload_body,
        content_type=upload_type,
    )
    assert_status(status, 200, "multipart upload", uploaded)
    scores = uploaded.get("scoreSubmissions") or []
    if len(scores) != 2 or any(row.get("status") != "pending" for row in scores):
        raise SmokeFail(f"upload response does not contain two pending scores: {uploaded}")
    score_id = int(scores[0]["id"])
    file_id = int(uploaded["id"])
    cleanup_state.update({"stage": "pending", "score_id": score_id, "file_id": file_id})
    print(f"[1/5] submit OK marker={marker} file={file_id} scores=2")

    status, reviewed = json_request("PATCH", f"/results/admin/reported-scores/{score_id}/review/", {
        "action": "verify",
        "review_all_evidence": True,
    }, token=admin_token)
    if status == 200:
        cleanup_state["stage"] = "verified"
    assert_status(status, 200, "group verify", reviewed)
    if len(reviewed.get("score_submissions") or []) != 2:
        raise SmokeFail(f"group verify did not return two rows: {reviewed}")
    print("[2/5] staff group verify OK")

    query = urllib.parse.urlencode({
        "days": "all",
        "student_id": student_id,
        "source": "school",
        "subject": marker,
        "page_size": 5,
    })
    status, console = request("GET", f"/results/admin/student-performance/?{query}", token=admin_token)
    assert_status(status, 200, "performance console", console)
    students = console.get("students") or []
    if len(students) != 1 or students[0]["subject_summaries"]["school"][marker]["scored_count"] != 1:
        raise SmokeFail(f"verified score not projected into chart summary: {console}")
    print("[3/5] console chart projection OK")

    status, voided = json_request("PATCH", f"/results/admin/reported-scores/{score_id}/review/", {
        "action": "void",
        "review_all_evidence": True,
        "review_note": f"운영 왕복 검증 종료 {marker}",
    }, token=admin_token)
    if status == 200:
        cleanup_state["stage"] = "voided"
    assert_status(status, 200, "group void", voided)
    if any(row.get("status") != "voided" for row in voided.get("score_submissions") or []):
        raise SmokeFail(f"group void failed: {voided}")
    print("[4/5] group void OK")

    delete_query = urllib.parse.urlencode({"scope": "student", "student_ps": student_ps})
    status, deleted = request("DELETE", f"/storage/inventory/files/{file_id}/?{delete_query}", token=student_token)
    if status == 204:
        cleanup_state["stage"] = "deleted"
    assert_status(status, 204, "evidence delete", deleted)
    print("[5/5] detached evidence delete OK")

    cleanup_command = (
        "cleanup_reported_score_canary "
        f"--tenant-code {TENANT_CODE} --marker '{marker}' --confirm '{marker}'"
    )
    if cleanup_remote:
        if remote_cleanup(marker) != 0:
            raise SmokeFail(f"remote audit cleanup failed; run manually: {cleanup_command}")
        print("[cleanup] detached canary audit rows removed")
    else:
        print(f"CLEANUP REQUIRED: pwsh scripts/v1/run-api-management-remote.ps1 -Command \"{cleanup_command}\"")
    cleanup_state["finished"] = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup-remote", action="store_true")
    args = parser.parse_args()
    run(cleanup_remote=args.cleanup_remote)

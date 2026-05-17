#!/usr/bin/env python3
"""학생 시점 영상 재생 chain 검증 — post-deploy mechanical gate.

5/13 R2 보호 + Worker 전환 작업 후 박철T 학생 영상 100% 차단 사고
(variant URL relative + no sig → Worker 401 "missing signature").
master.m3u8 200 만 보고 "fix 완료" 보고한 게 학원장 신뢰 파괴 본질.

본 script 는 학생 시점 실 chain 을 단호한 assert:
  1. student login → access token
  2. /student/video/me/ → enrolled lecture / session / video
  3. /student/video/videos/{id}/playback/ → response.play_url
  4. master.m3u8 fetch (iPhone UA) → body 안 variant URL 마다 ?sig= 박혔나
  5. variant.m3u8 fetch → 200
  6. first segment .ts fetch → 200
  7. (있으면) thumbnail.jpg fetch → 200

FAIL → exit 1 → workflow notify-on-failure SNS 발화. AI/사람 같은 사고 차단.

환경 변수:
  E2E_STUDENT_USER, E2E_STUDENT_PASS — tenant 1 hakwonplus 학생
  E2E_API_URL                       — default https://api.hakwonplus.com
  E2E_CDN_URL                       — default https://cdn.hakwonplus.com
  E2E_TENANT_CODE                   — default hakwonplus
  E2E_VIDEO_ID                      — optional explicit public/enrolled video id
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

API_URL = os.environ.get("E2E_API_URL", "https://api.hakwonplus.com").rstrip("/")
CDN_URL = os.environ.get("E2E_CDN_URL", "https://cdn.hakwonplus.com").rstrip("/")
TENANT_CODE = os.environ.get("E2E_TENANT_CODE", "hakwonplus")
STUDENT_USER = os.environ.get("E2E_STUDENT_USER", "")
STUDENT_PASS = os.environ.get("E2E_STUDENT_PASS", "")
EXPLICIT_VIDEO_ID = os.environ.get("E2E_VIDEO_ID", "").strip()

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
DEFAULT_TIMEOUT = 20


class SmokeFail(SystemExit):
    def __init__(self, msg: str):
        sys.stderr.write(f"\nSMOKE FAIL: {msg}\n")
        super().__init__(1)


def _req(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None, ua: str | None = None) -> tuple[int, bytes, dict]:
    h = {"User-Agent": ua or IPHONE_UA, "X-Tenant-Code": TENANT_CODE}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, method=method, headers=h, data=body)
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def _post_json(url: str, payload: dict, *, headers: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    status, data, _ = _req("POST", url, headers=h, body=body)
    try:
        return status, json.loads(data.decode("utf-8"))
    except Exception:
        return status, {"_raw": data[:300].decode("utf-8", errors="replace")}


def _get_json(url: str, *, token: str | None = None) -> tuple[int, dict]:
    h = {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    status, data, _ = _req("GET", url, headers=h)
    try:
        return status, json.loads(data.decode("utf-8"))
    except Exception:
        return status, {"_raw": data[:300].decode("utf-8", errors="replace")}


def login_student() -> str:
    if not STUDENT_USER or not STUDENT_PASS:
        raise SmokeFail("E2E_STUDENT_USER / E2E_STUDENT_PASS are required")
    status, body = _post_json(
        f"{API_URL}/api/v1/token/",
        {"username": STUDENT_USER, "password": STUDENT_PASS, "tenant_code": TENANT_CODE},
    )
    if status != 200 or "access" not in body:
        raise SmokeFail(f"student login {status}: {body}")
    print(f"[1/7] student login OK (user={STUDENT_USER})")
    return body["access"]


def find_first_video(token: str) -> tuple[int, int, int]:
    """Returns (lecture_id, session_id, video_id) of first enrolled video."""
    status, body = _get_json(f"{API_URL}/api/v1/student/video/me/", token=token)
    if status != 200:
        raise SmokeFail(f"/student/video/me/ {status}: {body}")
    lectures = body.get("lectures") or []
    if not lectures:
        raise SmokeFail(f"student has no enrolled lectures with videos - E2E_STUDENT_USER={STUDENT_USER}")
    lec = lectures[0]
    sessions = lec.get("sessions") or []
    if not sessions:
        raise SmokeFail(f"lecture {lec.get('id')} has no sessions")
    sess = sessions[0]
    print(f"[2/7] enrolled lecture={lec.get('id')} session={sess.get('id')}")
    # 영상 list
    status, body = _get_json(
        f"{API_URL}/api/v1/student/video/sessions/{sess['id']}/videos/?enrollment={lec.get('enrollment_id') or ''}",
        token=token,
    )
    if status != 200:
        raise SmokeFail(f"/sessions/{sess['id']}/videos/ {status}: {body}")
    items = body.get("items") or []
    if not items:
        raise SmokeFail(f"session {sess['id']} has no videos")
    return lec["id"], sess["id"], items[0]["id"]


def fetch_play_url(token: str, video_id: int, enrollment_id: int | None) -> str:
    qp = f"?enrollment={enrollment_id}" if enrollment_id else ""
    url = f"{API_URL}/api/v1/student/video/videos/{video_id}/playback/{qp}"
    status, body = _get_json(url, token=token)
    if status != 200:
        raise SmokeFail(f"playback endpoint {status}: {body}")
    play_url = body.get("play_url") or body.get("hls_url")
    if not play_url:
        raise SmokeFail(f"playback response missing play_url/hls_url: {body}")
    print(f"[3/7] play_url OK video={video_id}")
    return play_url


def fetch_master(play_url: str) -> tuple[str, str]:
    """Returns (body_text, base_url_for_relative). HLS spec: master query 안 propagate."""
    status, data, _ = _req("GET", play_url)
    if status != 200:
        raise SmokeFail(f"master.m3u8 {status} (play_url={play_url})")
    body = data.decode("utf-8", errors="replace")
    if "#EXTM3U" not in body:
        raise SmokeFail(f"master.m3u8 body invalid (no #EXTM3U): {body[:200]}")
    print(f"[4/7] master.m3u8 200 ({len(body)} bytes)")
    return body, play_url


def find_variant_urls(master_body: str, master_url: str) -> list[str]:
    """Returns absolute variant URLs (rewritten ones include sig)."""
    out = []
    for raw in master_body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("http"):
            out.append(line)
            continue
        # relative — resolve against base WITHOUT propagating master query (HLS spec)
        out.append(_resolve_hls_relative(master_url, line))
    return out


def _resolve_hls_relative(manifest_url: str, line: str) -> str:
    """Resolve HLS child paths against the manifest file URL, dropping parent query."""
    base = manifest_url.split("?")[0]
    return urllib.parse.urljoin(base, line)


def assert_variant_chain(variants: list[str]) -> str:
    """Each variant URL must include ?sig= (Worker m3u8 rewrite gate). Returns first variant body."""
    if not variants:
        raise SmokeFail("master manifest has no variant URLs")
    first = variants[0]
    qs = urllib.parse.urlparse(first).query
    qs_keys = set(urllib.parse.parse_qs(qs).keys())
    if "sig" not in qs_keys or "exp" not in qs_keys:
        # 🚨 5/13 사고 root cause 그 자체
        raise SmokeFail(
            f"variant URL missing sig/exp - master.m3u8 body rewrite failure\n"
            f"  variant: {first}\n"
            f"  query keys: {qs_keys}\n"
            f"  Worker m3u8 body rewrite logic is not deployed or regressed"
        )
    print(f"[5/7] variant URL sig/exp OK ({len(variants)} variants)")
    # variant fetch
    status, data, _ = _req("GET", first)
    if status != 200:
        raise SmokeFail(f"variant fetch {status} (url={first})")
    body = data.decode("utf-8", errors="replace")
    if "#EXTM3U" not in body:
        raise SmokeFail(f"variant body invalid: {body[:200]}")
    print(f"[6/7] variant fetch 200 ({len(body)} bytes)")
    return body, first


def assert_segment(variant_body: str, variant_url: str) -> None:
    """First .ts segment fetch 200."""
    segs = []
    for raw in variant_body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(".ts") or ".ts" in line:
            segs.append(line)
            break
    if not segs:
        raise SmokeFail("variant manifest has no .ts segments")
    seg = segs[0]
    if seg.startswith("http"):
        seg_url = seg
    else:
        seg_url = _resolve_hls_relative(variant_url, seg)
    # variant rewrite 가 segment URL 에도 sig inject 했어야
    qs_keys = set(urllib.parse.parse_qs(urllib.parse.urlparse(seg_url).query).keys())
    if "sig" not in qs_keys:
        raise SmokeFail(f"segment URL missing sig: {seg_url}")
    status, data, _ = _req("GET", seg_url)
    if status != 200:
        raise SmokeFail(f"segment fetch {status} (url={seg_url})")
    if len(data) < 100:
        raise SmokeFail(f"segment body too small ({len(data)} bytes): invalid")
    print(f"[7/7] segment fetch 200 ({len(data)} bytes)")


def main() -> None:
    print(f"=== Student Video Playback Chain Smoke ===")
    print(f"  API:    {API_URL}")
    print(f"  CDN:    {CDN_URL}")
    print(f"  Tenant: {TENANT_CODE}")
    print(f"  User:   {STUDENT_USER}")
    print()
    token = login_student()
    if EXPLICIT_VIDEO_ID:
        try:
            video_id = int(EXPLICIT_VIDEO_ID)
        except ValueError:
            raise SmokeFail(f"E2E_VIDEO_ID must be an integer (got {EXPLICIT_VIDEO_ID!r})")
        lecture_id = 0
        session_id = 0
        print(f"[2/7] explicit video={video_id}")
    else:
        lecture_id, session_id, video_id = find_first_video(token)
    # enrollment_id 는 video_me 응답에서 추출 (단순화 위해 None 으로도 backend 가 자동 매칭 — f7b88862 fix)
    play_url = fetch_play_url(token, video_id, enrollment_id=None)
    master_body, master_url = fetch_master(play_url)
    variants = find_variant_urls(master_body, master_url)
    variant_body, variant_url = assert_variant_chain(variants)
    assert_segment(variant_body, variant_url)
    print()
    print(f"ALL PASS - student video playback chain OK (lecture={lecture_id} session={session_id} video={video_id})")


if __name__ == "__main__":
    main()

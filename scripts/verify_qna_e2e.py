#!/usr/bin/env python3
"""
배포 API 질문 등록·목록 E2E 검증.
- 동일 DB에서 학생 455의 JWT 발급 후, https://api.hakwonplus.com 로 POST(질문 등록) / GET(목록) 호출.
"""
import os
import sys
import json
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    load_dotenv(os.path.join(BASE_DIR, ".env.local"))
except ImportError:
    pass
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.prod")

import django
django.setup()

from apps.domains.students.models import Student
from rest_framework_simplejwt.tokens import RefreshToken

BASE = os.environ.get("API_BASE_URL", "https://api.hakwonplus.com").rstrip("/")
STUDENT_ID = int(os.environ.get("E2E_STUDENT_ID", "455"))

def get_list(access, headers):
    req = urllib.request.Request(
        f"{BASE}/api/v1/community/posts/",
        method="GET",
        headers={**headers, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.getcode(), json.loads(r.read().decode())

def post_create(access, headers, title, content, block_type_id=1, node_ids=None):
    body = json.dumps({
        "block_type": block_type_id,
        "title": title,
        "content": content or "",
        "node_ids": node_ids or [],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/v1/community/posts/",
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.getcode(), json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode() if e.fp else "")

def main():
    student = Student.objects.filter(id=STUDENT_ID).select_related("user").first()
    if not student or not getattr(student, "user_id", None):
        print(f"FAIL: Student id={STUDENT_ID} not found")
        return 1
    token = RefreshToken.for_user(student.user)
    access = str(token.access_token)
    headers = {"Authorization": f"Bearer {access}", "X-Tenant-Code": "hakwonplus", "Content-Type": "application/json"}

    print(f"API_BASE={BASE} student_id={STUDENT_ID}")
    code, data = get_list(access, headers)
    print(f"GET /community/posts/ -> {code}")
    if code != 200:
        print("  body:", data)
        return 1
    items = data if isinstance(data, list) else (data.get("results") or [])
    print(f"  list count = {len(items)}")

    title = "E2E 검증 질문"
    code, data = post_create(access, headers, title=title, content="E2E 본문")
    print(f"POST /community/posts/ -> {code}")
    if code != 201:
        print("  body:", data)
        return 1
    created_id = data.get("id")
    print(f"  created id = {created_id}")

    code, data = get_list(access, headers)
    if code != 200:
        print(f"GET list after POST -> {code}")
        return 1
    items = data if isinstance(data, list) else (data.get("results") or [])
    found = any(item.get("id") == created_id or item.get("title") == title for item in items)
    print(f"GET list after POST -> 200, count={len(items)}, new in list={found}")
    if not found:
        print("  FAIL: 새 글이 목록에 없음")
        return 1
    print("OK: 질문 등록 및 목록 노출 정상")
    return 0

if __name__ == "__main__":
    sys.exit(main())

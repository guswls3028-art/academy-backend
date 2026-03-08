# PATH: apps/domains/community/management/commands/verify_qna_e2e.py
"""배포 API 질문 등록·목록 E2E 검증. 학생 455 JWT로 API_BASE_URL에 GET/POST 호출."""
import json
import os
import sys
import urllib.error
import urllib.request

from django.core.management.base import BaseCommand
from rest_framework_simplejwt.tokens import RefreshToken

from apps.domains.students.models import Student


class Command(BaseCommand):
    help = "E2E: 학생 455 JWT로 API_BASE_URL에 질문 등록 후 목록에 노출되는지 검증."

    def add_arguments(self, parser):
        parser.add_argument(
            "--base",
            type=str,
            default=os.environ.get("API_BASE_URL", "https://api.hakwonplus.com"),
            help="API base URL",
        )
        parser.add_argument("--student-id", type=int, default=455, help="학생 ID")

    def handle(self, *args, **options):
        base = (options["base"] or "").rstrip("/") or "https://api.hakwonplus.com"
        student_id = options["student_id"]
        student = Student.objects.filter(id=student_id).select_related("user").first()
        if not student or not getattr(student, "user_id", None):
            self.stdout.write(self.style.ERROR(f"Student id={student_id} not found"))
            sys.exit(1)
        token = RefreshToken.for_user(student.user)
        access = str(token.access_token)
        headers = {
            "Authorization": f"Bearer {access}",
            "X-Tenant-Code": "hakwonplus",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        def get_list():
            req = urllib.request.Request(
                f"{base}/api/v1/community/posts/",
                method="GET",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.getcode(), json.loads(r.read().decode())

        def post_create(title, content, block_type_id=1, node_ids=None):
            body = json.dumps({
                "block_type": block_type_id,
                "title": title,
                "content": content or "",
                "node_ids": node_ids or [],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/api/v1/community/posts/",
                data=body,
                method="POST",
                headers=headers,
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return r.getcode(), json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                return e.code, (e.read().decode() if e.fp else "")

        self.stdout.write(f"API_BASE={base} student_id={student_id}")
        code, data = get_list()
        self.stdout.write(f"GET /community/posts/ -> {code}")
        if code != 200:
            self.stdout.write(self.style.ERROR(f"  body: {data}"))
            sys.exit(1)
        items = data if isinstance(data, list) else (data.get("results") or [])
        self.stdout.write(f"  list count = {len(items)}")

        title = "E2E 검증 질문"
        code, data = post_create(title=title, content="E2E 본문")
        self.stdout.write(f"POST /community/posts/ -> {code}")
        if code != 201:
            self.stdout.write(self.style.ERROR(f"  body: {data}"))
            sys.exit(1)
        created_id = data.get("id") if isinstance(data, dict) else None
        self.stdout.write(f"  created id = {created_id}")

        code, data = get_list()
        if code != 200:
            self.stdout.write(self.style.ERROR(f"GET list after POST -> {code}"))
            sys.exit(1)
        items = data if isinstance(data, list) else (data.get("results") or [])
        found = any(
            (item.get("id") == created_id or item.get("title") == title)
            for item in items
        )
        self.stdout.write(f"GET list after POST -> 200, count={len(items)}, new in list={found}")
        if not found:
            self.stdout.write(self.style.ERROR("FAIL: 새 글이 목록에 없음"))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("OK: 질문 등록 및 목록 노출 정상"))

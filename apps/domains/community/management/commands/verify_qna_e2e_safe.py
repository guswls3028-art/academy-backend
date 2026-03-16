"""Safe E2E verification for QnA community API. Tenant 1 only, with cleanup."""
import json
import logging
import os
import sys
import urllib.error
import urllib.request

from django.core.management.base import BaseCommand
from rest_framework_simplejwt.tokens import RefreshToken

from apps.domains.students.models import Student

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Safe E2E: QnA post create/verify/delete on Tenant 1 only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id",
            type=int,
            required=True,
            help="Tenant ID (must be 1)",
        )
        parser.add_argument(
            "--base",
            type=str,
            default=os.environ.get("API_BASE_URL", "https://api.hakwonplus.com"),
            help="API base URL",
        )
        parser.add_argument(
            "--host",
            type=str,
            default=os.environ.get("API_HOST_HEADER", ""),
            help="Host header (e.g. api.hakwonplus.com when base is ALB URL)",
        )
        parser.add_argument(
            "--student-id",
            type=int,
            default=455,
            help="Student ID to use for JWT auth",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Verify API connectivity only (GET list), skip create/delete",
        )

    def handle(self, *args, **options):
        tenant_id = options["tenant_id"]
        if tenant_id != 1:
            self.stderr.write(self.style.ERROR(
                f"REFUSED: tenant_id={tenant_id}. This command only runs on Tenant 1 (dev/test). "
                "Operational data protection policy prohibits running on other tenants."
            ))
            sys.exit(1)

        base = (options["base"] or "").rstrip("/") or "https://api.hakwonplus.com"
        host_header = (options.get("host") or os.environ.get("API_HOST_HEADER") or "").strip()
        student_id = options["student_id"]
        dry_run = options["dry_run"]

        self.stdout.write(f"[verify_qna_e2e_safe] tenant_id={tenant_id}, base={base}, student_id={student_id}, dry_run={dry_run}")

        student = Student.objects.filter(id=student_id).select_related("user").first()
        if not student or not getattr(student, "user_id", None):
            self.stderr.write(self.style.ERROR(f"Student id={student_id} not found or has no user"))
            sys.exit(1)

        token = RefreshToken.for_user(student.user)
        access = str(token.access_token)
        headers = {
            "Authorization": f"Bearer {access}",
            "X-Tenant-Code": "hakwonplus",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if host_header:
            headers["Host"] = host_header

        # Step 1: GET list (connectivity check)
        self.stdout.write("[Step 1] GET /community/posts/ ...")
        list_code, list_data = self._api_get(f"{base}/api/v1/community/posts/", headers)
        if list_code != 200:
            self.stderr.write(self.style.ERROR(f"  FAIL: status={list_code}, body={list_data}"))
            sys.exit(1)
        items = list_data if isinstance(list_data, list) else (list_data.get("results") or [])
        self.stdout.write(self.style.SUCCESS(f"  OK: status=200, count={len(items)}"))

        if dry_run:
            self.stdout.write(self.style.SUCCESS("[dry-run] API connectivity verified. No data created."))
            return

        # Step 2: POST create test post
        self.stdout.write("[Step 2] POST create test post ...")
        created_id = None
        try:
            create_code, create_data = self._api_post(
                f"{base}/api/v1/community/posts/",
                headers,
                {"title": "[E2E-SAFE] 검증 질문 (자동 삭제)", "content": "자동 E2E 검증용. 자동 삭제됩니다.", "post_type": "qna"},
            )
            if create_code != 201:
                self.stderr.write(self.style.ERROR(f"  FAIL: status={create_code}, body={create_data}"))
                sys.exit(1)
            created_id = create_data.get("id") if isinstance(create_data, dict) else None
            self.stdout.write(f"  Created post id={created_id}")

            # Step 3: GET list and verify new post appears
            self.stdout.write("[Step 3] GET list to verify new post ...")
            verify_code, verify_data = self._api_get(f"{base}/api/v1/community/posts/", headers)
            if verify_code != 200:
                self.stderr.write(self.style.ERROR(f"  FAIL: status={verify_code}"))
                sys.exit(1)
            verify_items = verify_data if isinstance(verify_data, list) else (verify_data.get("results") or [])
            found = any(item.get("id") == created_id for item in verify_items)
            if not found:
                self.stderr.write(self.style.ERROR(f"  FAIL: post id={created_id} not in list"))
                sys.exit(1)
            self.stdout.write(self.style.SUCCESS(f"  OK: post id={created_id} found in list"))
        finally:
            # Step 4: DELETE cleanup (always attempt)
            if created_id is not None:
                self.stdout.write(f"[Step 4] DELETE cleanup post id={created_id} ...")
                del_code = self._api_delete(f"{base}/api/v1/community/posts/{created_id}/", headers)
                if del_code in (204, 200):
                    self.stdout.write(self.style.SUCCESS(f"  OK: post id={created_id} deleted"))
                else:
                    self.stderr.write(self.style.WARNING(f"  WARNING: delete returned status={del_code} (orphan post may remain)"))

        self.stdout.write(self.style.SUCCESS("[verify_qna_e2e_safe] ALL CHECKS PASSED"))

    def _api_get(self, url, headers):
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.getcode(), json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, (e.read().decode() if e.fp else "")

    def _api_post(self, url, headers, body):
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.getcode(), json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, (e.read().decode() if e.fp else "")

    def _api_delete(self, url, headers):
        req = urllib.request.Request(url, method="DELETE", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.getcode()
        except urllib.error.HTTPError as e:
            return e.code

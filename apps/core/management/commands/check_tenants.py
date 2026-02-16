# PATH: apps/core/management/commands/check_tenants.py
"""
DB 테넌트 목록 확인 (ID, code, name, 활성 여부).

사용:
  python manage.py check_tenants
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "List all tenants (id, code, name, active)."

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, code, name, is_active
                FROM core_tenant
                ORDER BY id
            """)
            rows = cursor.fetchall()
        self.stdout.write("All tenants:")
        self.stdout.write("=" * 60)
        for row in rows:
            tid, code, name, is_active = row
            status = "ACTIVE" if is_active else "INACTIVE"
            self.stdout.write(f"ID: {tid:5d} | Code: {code:15s} | Name: {name:30s} | {status}")
        self.stdout.write("")
        self.stdout.write(f"Total: {len(rows)} tenants")

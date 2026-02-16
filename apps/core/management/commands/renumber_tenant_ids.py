# PATH: apps/core/management/commands/renumber_tenant_ids.py
"""
테넌트 ID를 config와 맞춤: 4(9999)→9999, 5(tchul)→2, 6(limglish)→3, 7(ymath)→4.
5,6,7은 비우고 tchul/limglish/ymath가 2,3,4로 들어가게 함.

사용:
  python manage.py renumber_tenant_ids --dry-run   # 변경 내용만 출력
  python manage.py renumber_tenant_ids            # 실제 반영
확인 (반영 전/후):
  python manage.py check_tenants
  또는 (프로젝트 루트에서): python scripts/check_all_tenants.py
"""
from django.core.management.base import BaseCommand
from django.db import connection, transaction


# (old_id, new_id) — 순서 중요: 먼저 4를 비우고, 5,6,7을 2,3,4로
RENUMBER_MAP = [
    (4, 9999),   # Local Dev Tenant (9999) → id 9999
    (5, 2),      # tchul → 2
    (6, 3),      # limglish → 3
    (7, 4),      # ymath → 4
]


def get_tenant_fk_tables(cursor):
    """PostgreSQL: core_tenant.id를 참조하는 (table, column) 목록."""
    cursor.execute("""
        SELECT c.relname AS table_name, a.attname AS column_name
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(con.conkey) AND NOT a.attisdropped
        JOIN pg_class ref ON ref.oid = con.confrelid
        WHERE con.contype = 'f'
          AND ref.relname = 'core_tenant'
          AND c.relkind = 'r'
          AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
    """)
    return [row for row in cursor.fetchall()]


class Command(BaseCommand):
    help = "Renumber tenant IDs so tchul=2, limglish=3, ymath=4; 9999→id 9999; 5,6,7 비움."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="실제 변경 없이 할 작업만 출력",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — DB 변경 없음"))

        with connection.cursor() as cursor:
            tables = get_tenant_fk_tables(cursor)
            if not tables:
                self.stdout.write(self.style.WARNING("core_tenant를 참조하는 테이블이 없습니다."))
                return

            self.stdout.write(f"tenant_id FK 테이블: {[t[0] for t in tables]}")

            for old_id, new_id in RENUMBER_MAP:
                cursor.execute("SELECT id, code, name FROM core_tenant WHERE id = %s", [old_id])
                row = cursor.fetchone()
                if not row:
                    self.stdout.write(f"  건너뜀: id={old_id} 인 테넌트 없음")
                    continue
                _id, code, name = row
                self.stdout.write(f"  {old_id} ({code}) → {new_id}")

            if dry_run:
                self.stdout.write(self.style.SUCCESS("Dry run 완료. 적용하려면 --dry-run 없이 실행하세요."))
                return

        with transaction.atomic():
            with connection.cursor() as cursor:
                for old_id, new_id in RENUMBER_MAP:
                    cursor.execute("SELECT id FROM core_tenant WHERE id = %s", [old_id])
                    if not cursor.fetchone():
                        continue
                    for table_name, column_name in tables:
                        cursor.execute(
                            f'UPDATE "{table_name}" SET "{column_name}" = %s WHERE "{column_name}" = %s',
                            [new_id, old_id],
                        )
                        if cursor.rowcount:
                            self.stdout.write(f"    {table_name}.{column_name}: {cursor.rowcount} rows")
                    cursor.execute("UPDATE core_tenant SET id = %s WHERE id = %s", [new_id, old_id])
                    self.stdout.write(self.style.SUCCESS(f"  core_tenant: {old_id} → {new_id}"))

        self.stdout.write(self.style.SUCCESS("완료. 확인: python manage.py check_tenants"))

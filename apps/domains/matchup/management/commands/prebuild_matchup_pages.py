# PATH: apps/domains/matchup/management/commands/prebuild_matchup_pages.py
"""
매치업 doc의 페이지 이미지 R2 캐시를 일괄 prebuild.

운영 케이스 (2026-04-28): T1 13/16, T2 28/29 doc이 page_image_keys 캐시 없음
→ ManualCropModal 첫 열 때 PDF 다운로드 + 100페이지 PNG 렌더 + R2 업로드
  발생해 학습자료(86~96MB)는 분 단위 로딩 지연.

이 명령을 한 번 실행하면 이후 모든 doc의 모달이 즉시 열림 (presign만).

Usage:
  python manage.py prebuild_matchup_pages --tenant-id 1
  python manage.py prebuild_matchup_pages --tenant-id 1 --tenant-id 2
  python manage.py prebuild_matchup_pages --all
  python manage.py prebuild_matchup_pages --doc-id 120
"""
import time

from django.core.management.base import BaseCommand, CommandError

from apps.domains.matchup.models import MatchupDocument
from apps.domains.matchup.services import ensure_document_page_images


class Command(BaseCommand):
    help = "매치업 doc 페이지 이미지를 R2 캐시에 prebuild (모달 로딩 지연 fix)"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, action="append", default=None,
                            help="여러 번 지정 가능: --tenant-id 1 --tenant-id 2")
        parser.add_argument("--doc-id", type=int, default=None)
        parser.add_argument("--all", action="store_true", help="모든 테넌트 전체 doc")
        parser.add_argument("--force", action="store_true",
                            help="이미 캐시된 doc도 다시 렌더 (해상도 변경 시 등 유지보수용)")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        tenant_ids = options["tenant_id"]
        doc_id = options["doc_id"]
        all_docs = options["all"]
        force = options["force"]
        dry_run = options["dry_run"]

        qs = MatchupDocument.objects.filter(status="done")
        if doc_id is not None:
            qs = qs.filter(id=doc_id)
        elif tenant_ids:
            qs = qs.filter(tenant_id__in=tenant_ids)
        elif not all_docs:
            raise CommandError("--tenant-id / --doc-id / --all 중 하나 필요")

        if not force:
            # 캐시 없는 doc만 대상
            qs = qs.exclude(meta__has_key="page_image_keys")

        targets = list(qs.order_by("id"))
        if not targets:
            self.stdout.write(self.style.WARNING("대상 없음 (이미 모두 캐시됨)"))
            return

        self.stdout.write(f"prebuild 대상: {len(targets)}개")
        for d in targets:
            self.stdout.write(f"  doc#{d.id} t={d.tenant_id} pc={d.problem_count} {d.title[:40]}")

        if dry_run:
            self.stdout.write(self.style.NOTICE("dry-run - 실제 렌더 안 함"))
            return

        ok, fail = 0, 0
        total_t = 0.0
        for i, doc in enumerate(targets, 1):
            t0 = time.monotonic()
            try:
                if force:
                    # 기존 키 무시하고 재생성
                    meta = dict(doc.meta or {})
                    meta.pop("page_image_keys", None)
                    meta.pop("page_dimensions", None)
                    doc.meta = meta
                    doc.save(update_fields=["meta", "updated_at"])
                pages = ensure_document_page_images(doc)
                dt = time.monotonic() - t0
                total_t += dt
                ok += 1
                self.stdout.write(self.style.SUCCESS(
                    f"[{i}/{len(targets)}] OK doc#{doc.id} pages={len(pages)} {dt:.1f}s"
                ))
            except Exception as e:
                fail += 1
                self.stdout.write(self.style.ERROR(
                    f"[{i}/{len(targets)}] FAIL doc#{doc.id} {type(e).__name__}: {e}"
                ))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"완료: {ok} 성공 / {fail} 실패 (총 {total_t:.1f}s, avg {total_t/max(ok,1):.1f}s/doc)"
        ))

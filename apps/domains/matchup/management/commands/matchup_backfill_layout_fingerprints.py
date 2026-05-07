# PATH: apps/domains/matchup/management/commands/matchup_backfill_layout_fingerprints.py
"""Stage 6.6.5 — LayoutFingerprint 일괄 backfill.

목표: status=done 인 MatchupDocument 중 LayoutFingerprint(version=1) 가 없는 doc
에 대해 PDF metrics 추출 + fingerprint 생성. 학원장 manual_crop 흐름 외 별도 entry.

원칙:
- tenant_id 필수 (cross-tenant 작업 영구 차단)
- dry-run default — 명시 --no-dry-run 시만 실 INSERT
- selected_problem_ids / hit_report / callback / segment_dispatcher 미접근
- R2 read 만 (write 0)
- OCR/VLM 실호출 0
- 이미 fingerprint 있는 doc skip (idempotent)
- 한 doc 실패 시 다른 doc 계속 (failure isolation)

Usage:
  python manage.py matchup_backfill_layout_fingerprints --tenant-id 2
  python manage.py matchup_backfill_layout_fingerprints --tenant-id 2 --no-dry-run --limit 50
  python manage.py matchup_backfill_layout_fingerprints --tenant-id 2 --doc-id 765 --no-dry-run
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from django.core.management.base import BaseCommand, CommandError

from apps.domains.matchup.models import LayoutFingerprint, MatchupDocument
from apps.domains.matchup.services import (
    _extract_pdf_first_page_metrics,
    _record_layout_fingerprint,
)


logger = logging.getLogger(__name__)


DEFAULT_LIMIT = 50
DEFAULT_FINGERPRINT_VERSION = 1


class Command(BaseCommand):
    help = (
        "Stage 6.6.5 — LayoutFingerprint 일괄 backfill. tenant 필수. "
        "default dry-run (실 INSERT 0). --no-dry-run 명시 시만 INSERT."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id", type=int, required=True,
            help="대상 tenant id (필수, cross-tenant 작업 차단).",
        )
        parser.add_argument(
            "--doc-id", type=int, default=None,
            help="단일 doc 만 처리 (디버깅용). 미지정 시 tenant 의 status=done 전체 batch.",
        )
        parser.add_argument(
            "--limit", type=int, default=DEFAULT_LIMIT,
            help=f"batch size (default {DEFAULT_LIMIT}).",
        )
        parser.add_argument(
            "--dry-run", dest="dry_run", action="store_true", default=True,
            help="대상 list + 결과 미리보기. INSERT 0 (default).",
        )
        parser.add_argument(
            "--no-dry-run", dest="dry_run", action="store_false",
            help="실 INSERT 활성화. dry-run 검토 후에만 사용.",
        )

    def handle(self, *args, **options):
        tenant_id: int = options["tenant_id"]
        single_doc_id: Optional[int] = options.get("doc_id")
        limit: int = options["limit"]
        dry_run: bool = options["dry_run"]

        if tenant_id <= 0:
            raise CommandError(f"invalid tenant_id={tenant_id}")
        if limit <= 0 or limit > 500:
            raise CommandError(f"limit must be in (0, 500]: got {limit}")

        # 1) 대상 doc 선정 — status=done + LayoutFingerprint(version=1) 미보유
        # tenant 안에서만 조회 (cross-tenant 영구 차단).
        existing_doc_ids = set(
            LayoutFingerprint.objects
            .filter(tenant_id=tenant_id, fingerprint_version=DEFAULT_FINGERPRINT_VERSION)
            .values_list("document_id", flat=True)
        )
        qs = (
            MatchupDocument.objects
            .filter(tenant_id=tenant_id, status="done")
            .order_by("-id")
        )
        if single_doc_id is not None:
            qs = qs.filter(id=single_doc_id)

        candidates = []
        for d in qs.iterator():
            if d.id in existing_doc_ids:
                continue
            candidates.append(d)
            if len(candidates) >= limit:
                break

        self.stdout.write(self.style.NOTICE(
            f"Stage 6.6.5 backfill — tenant={tenant_id} "
            f"doc_id={single_doc_id} limit={limit} dry_run={dry_run}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"existing fingerprints (tenant {tenant_id}, v=1): {len(existing_doc_ids)}"
        ))
        self.stdout.write(self.style.NOTICE(
            f"candidate docs to backfill: {len(candidates)}"
        ))

        if not candidates:
            self.stdout.write(self.style.SUCCESS("no candidates — all done docs already covered."))
            return

        # 2) 처리
        succeeded = 0
        failed = 0
        skipped_no_inventory = 0
        durations = []
        for d in candidates:
            t0 = time.time()
            label = f"doc#{d.id} ({d.title[:30]})"
            if not d.inventory_file_id:
                self.stdout.write(self.style.WARNING(f"  SKIP {label}: no inventory_file"))
                skipped_no_inventory += 1
                continue
            try:
                page_count, page_size = _extract_pdf_first_page_metrics(d)
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"  FAIL {label}: extract metrics — {type(exc).__name__}: {exc}"
                ))
                logger.exception(
                    "MATCHUP_BACKFILL_FINGERPRINT_EXTRACT_FAILED | tenant=%s | doc=%s",
                    tenant_id, d.id,
                )
                failed += 1
                continue

            if dry_run:
                self.stdout.write(
                    f"  DRY-RUN {label}: page_count={page_count} "
                    f"page_size={page_size}"
                )
                succeeded += 1
                durations.append(time.time() - t0)
                continue

            try:
                _record_layout_fingerprint(
                    d, page_count=page_count, page_size=page_size,
                )
                self.stdout.write(self.style.SUCCESS(
                    f"  OK {label}: page_count={page_count} size={page_size}"
                ))
                succeeded += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(
                    f"  FAIL {label}: upsert — {type(exc).__name__}: {exc}"
                ))
                logger.exception(
                    "MATCHUP_BACKFILL_FINGERPRINT_UPSERT_FAILED | tenant=%s | doc=%s",
                    tenant_id, d.id,
                )
                failed += 1
            durations.append(time.time() - t0)

        # 3) 요약
        avg_dur = sum(durations) / len(durations) if durations else 0.0
        self.stdout.write(self.style.SUCCESS(
            f"summary: succeeded={succeeded} failed={failed} "
            f"skipped_no_inventory={skipped_no_inventory} "
            f"avg_doc_seconds={avg_dur:.2f}"
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING(
                "dry-run: 실 INSERT 0. --no-dry-run 명시 시 실제 처리."
            ))

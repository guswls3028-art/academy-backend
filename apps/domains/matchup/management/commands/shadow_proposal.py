"""Stage 6.3-Pipeline — shadow proposal generation management command.

T1 sandbox 한정. 운영 callback 미연결. 실 OCR/VLM 미호출.

Usage (default dry-run, INSERT 0회):
    MATCHUP_SHADOW_PROPOSAL_ENABLED=1 \\
    python manage.py shadow_proposal \\
        --doc-id 735 --tenant-id 1 \\
        --pdf-path /path/to/sandbox.pdf

Usage (sandbox INSERT, T1 만 — 사용자 명시 승인 후):
    MATCHUP_SHADOW_PROPOSAL_ENABLED=1 \\
    python manage.py shadow_proposal \\
        --doc-id 735 --tenant-id 1 \\
        --pdf-path /path/to/sandbox.pdf \\
        --no-dry-run --allow-insert --max-payloads 5

원칙:
- ENV `MATCHUP_SHADOW_PROPOSAL_ENABLED=1` 미설정 → 자동 차단 (CommandError)
- tenant_id != 1 → 자동 차단 (T1 sandbox 외 production 보호)
- dry_run default True / allow_insert default False
- 운영 callback / segment_dispatcher 미import
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.domains.matchup.segmentation.shadow_proposal_pipeline import (
    DEFAULT_MAX_PAYLOADS, DEFAULT_MOCK_OCR_BLOCKS, DEFAULT_MOCK_VLM_PROBLEMS,
    DEFAULT_SANDBOX_TENANT_ID, SHADOW_GLOBAL_ENV,
    result_to_dict, shadow_proposal_pipeline,
)


class Command(BaseCommand):
    help = (
        "Stage 6.3-Pipeline shadow proposal generation (T1 sandbox 한정). "
        f"Set {SHADOW_GLOBAL_ENV}=1 to enable."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--doc-id", type=int, required=True,
            help="ProblemSegmentationProposal.document_id (T1 sandbox 안)",
        )
        parser.add_argument(
            "--tenant-id", type=int, default=DEFAULT_SANDBOX_TENANT_ID,
            help=f"sandbox tenant id (default {DEFAULT_SANDBOX_TENANT_ID}, T1 만)",
        )
        parser.add_argument(
            "--pdf-path", type=str, required=True,
            help="PDF 파일 경로 (sandbox 안 자료)",
        )
        parser.add_argument(
            "--analysis-version-key", type=str, default="",
            help="batch 식별자 (idempotent 키)",
        )
        parser.add_argument(
            "--dry-run", dest="dry_run", action="store_true", default=True,
            help="INSERT 0회 (default)",
        )
        parser.add_argument(
            "--no-dry-run", dest="dry_run", action="store_false",
            help="dry_run 비활성 (단 allow_insert + sandbox gate 모두 통과해야 INSERT)",
        )
        parser.add_argument(
            "--allow-insert", action="store_true", default=False,
            help="sandbox INSERT 허용 (dry_run=False + sandbox_tenant_ids=[1] 통과 시)",
        )
        parser.add_argument(
            "--max-payloads", type=int, default=DEFAULT_MAX_PAYLOADS,
            help=f"bulk cap (default {DEFAULT_MAX_PAYLOADS})",
        )
        parser.add_argument(
            "--mock-ocr-blocks", type=int, default=DEFAULT_MOCK_OCR_BLOCKS,
        )
        parser.add_argument(
            "--mock-vlm-problems", type=int, default=DEFAULT_MOCK_VLM_PROBLEMS,
        )
        parser.add_argument(
            "--out-json", type=str, default="",
            help="결과 JSON 저장 경로 (선택)",
        )
        parser.add_argument(
            "--smoke-truncate-to-cap",
            dest="smoke_truncate_to_cap",
            action="store_true",
            default=False,
            help=(
                "Stage 6.4-prep+1 smoke-only opt-in. raw payload count > "
                "max_payloads 일 때 deterministic 정렬 후 max_payloads 개만 "
                "adapter 에 전달. 기본 OFF — 운영 코드 미사용."
            ),
        )

    def handle(self, *args, **options):
        pdf_path = options["pdf_path"]
        if not Path(pdf_path).exists():
            raise CommandError(f"pdf not found: {pdf_path}")

        result = shadow_proposal_pipeline(
            pdf_path=pdf_path,
            document_id=options["doc_id"],
            tenant_id=options["tenant_id"],
            file_name=Path(pdf_path).name,
            analysis_version_key=options["analysis_version_key"],
            dry_run=options["dry_run"],
            allow_insert=options["allow_insert"],
            max_payloads=options["max_payloads"],
            mock_ocr_blocks=options["mock_ocr_blocks"],
            mock_vlm_problems=options["mock_vlm_problems"],
            smoke_truncate_to_cap=options["smoke_truncate_to_cap"],
        )

        out_dict = result_to_dict(result)
        out_str = json.dumps(out_dict, ensure_ascii=False, indent=2, default=str)

        if options["out_json"]:
            Path(options["out_json"]).write_text(out_str, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(
                f"result saved: {options['out_json']}"
            ))
        else:
            self.stdout.write(out_str)

        if not result.enabled:
            raise CommandError(
                f"pipeline disabled: {result.blocking_reason}"
            )

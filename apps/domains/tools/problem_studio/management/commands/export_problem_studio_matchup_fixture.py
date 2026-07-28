from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.domains.matchup.selectors import (
    iter_problem_studio_reference_texts,
    iter_problem_studio_teacher_comments,
)
from apps.domains.tools.problem_studio.voice_profiles import sanitize_voice_text


SCHEMA = "problem-studio-sanitized-reference/v1"


def _content_fingerprint(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind}\n{text}".encode("utf-8")).hexdigest()


def build_sanitized_fixture(
    *,
    tenant_id: int,
    max_references: int,
    max_style_samples: int,
) -> dict[str, Any]:
    references_by_hash: dict[str, dict[str, str]] = {}
    for text in iter_problem_studio_reference_texts(tenant_id=tenant_id):
        clean_text = sanitize_voice_text(text, max_chars=3000)
        if len(clean_text) < 20:
            continue
        fingerprint = _content_fingerprint("matchup_problem", clean_text)
        references_by_hash[fingerprint] = {
            "fingerprint": fingerprint,
            "problem_text": clean_text,
            "source_label": "비식별 매치업 문제 참고",
        }

    style_by_hash: dict[str, dict[str, str]] = {}
    for comment in iter_problem_studio_teacher_comments(tenant_id=tenant_id):
        clean_comment = sanitize_voice_text(comment, max_chars=2000)
        if len(clean_comment) < 10:
            continue
        fingerprint = _content_fingerprint("matchup_teacher_comment", clean_comment)
        style_by_hash[fingerprint] = {
            "fingerprint": fingerprint,
            "explanation": clean_comment,
            "source_label": "비식별 매치업 강사 코멘트",
        }

    references = [
        references_by_hash[key]
        for key in sorted(references_by_hash)[:max_references]
    ]
    style_samples = [
        style_by_hash[key]
        for key in sorted(style_by_hash)[:max_style_samples]
    ]
    return {
        "schema": SCHEMA,
        "generated_at": timezone.now().isoformat(),
        "source_tenant_fingerprint": hashlib.sha256(
            f"tenant:{tenant_id}".encode("utf-8"),
        ).hexdigest(),
        "privacy": {
            "contains_original_files": False,
            "contains_user_ids": False,
            "contains_document_names": False,
            "phone_and_email_masked": True,
        },
        "rights_contract": {
            "references": "content_reference_only",
            "style_samples": "teacher_authored_matchup_comments",
            "authorized_by": "workspace_user_request",
        },
        "references": references,
        "style_samples": style_samples,
        "counts": {
            "references": len(references),
            "style_samples": len(style_samples),
        },
    }


class Command(BaseCommand):
    help = "Export deidentified Matchup text for isolated Problem Studio testing. The database is read-only."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--output", type=str, required=True)
        parser.add_argument("--max-references", type=int, default=200)
        parser.add_argument("--max-style-samples", type=int, default=200)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        tenant_id = int(options["tenant_id"])
        max_references = max(1, min(int(options["max_references"]), 500))
        max_style_samples = max(1, min(int(options["max_style_samples"]), 500))
        fixture = build_sanitized_fixture(
            tenant_id=tenant_id,
            max_references=max_references,
            max_style_samples=max_style_samples,
        )
        summary = {
            "schema": fixture["schema"],
            "counts": fixture["counts"],
            "source_tenant_fingerprint": fixture["source_tenant_fingerprint"],
        }
        if options["dry_run"]:
            self.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return

        output = Path(options["output"]).expanduser().resolve()
        if output.exists():
            raise CommandError(f"Refusing to overwrite existing fixture: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.stdout.write(json.dumps({**summary, "output": str(output)}, ensure_ascii=False, sort_keys=True))

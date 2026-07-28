from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Tenant, TenantMembership
from apps.domains.tools.problem_studio.management.commands.export_problem_studio_matchup_fixture import (
    SCHEMA,
)
from apps.domains.tools.problem_studio.models import (
    ProblemStudioVoiceProfile,
    ProblemStudioVoiceSample,
)
from apps.domains.tools.problem_studio.voice_profiles import (
    add_voice_sample,
    create_voice_profile,
    sanitize_voice_text,
    serialize_voice_profile,
)


ISOLATED_TEST_TENANT_ID = 9999


class Command(BaseCommand):
    help = "Import a sanitized reference fixture into the isolated tenant 9999 only."

    def add_arguments(self, parser):
        parser.add_argument("--input", type=str, required=True)
        parser.add_argument("--target-tenant-id", type=int, required=True)
        parser.add_argument("--target-user-id", type=int, required=True)
        parser.add_argument("--tag", type=str, required=True)
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        target_tenant_id = int(options["target_tenant_id"])
        if target_tenant_id != ISOLATED_TEST_TENANT_ID:
            raise CommandError("Sanitized Problem Studio fixtures may only be imported into tenant 9999.")
        tag = str(options["tag"] or "").strip()
        if not tag.startswith("[E2E-") or not tag.endswith("]"):
            raise CommandError("The import tag must use the [E2E-...] format.")

        input_path = Path(options["input"]).expanduser().resolve()
        if not input_path.is_file():
            raise CommandError(f"Fixture not found: {input_path}")
        if input_path.stat().st_size > 10 * 1024 * 1024:
            raise CommandError("Fixture exceeds the 10MB isolated-test limit.")
        try:
            fixture = json.loads(input_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError("Fixture JSON is invalid.") from exc
        if not isinstance(fixture, dict) or fixture.get("schema") != SCHEMA:
            raise CommandError("Fixture schema is not supported.")
        privacy = fixture.get("privacy")
        if not isinstance(privacy, dict) or not all(
            (
                privacy.get("contains_original_files") is False,
                privacy.get("contains_user_ids") is False,
                privacy.get("contains_document_names") is False,
                privacy.get("phone_and_email_masked") is True,
            )
        ):
            raise CommandError("Fixture privacy contract is incomplete.")
        rights_contract = fixture.get("rights_contract")
        if not isinstance(rights_contract, dict) or (
            rights_contract.get("references") != "content_reference_only"
            or rights_contract.get("style_samples") != "teacher_authored_matchup_comments"
            or rights_contract.get("authorized_by") != "workspace_user_request"
        ):
            raise CommandError("Fixture rights contract is incomplete.")

        tenant = Tenant.objects.filter(id=target_tenant_id, is_active=True).first()
        user = get_user_model().objects.filter(id=int(options["target_user_id"]), is_active=True).first()
        if tenant is None or user is None:
            raise CommandError("The isolated tenant or target test user does not exist.")
        membership = TenantMembership.objects.filter(
            tenant=tenant,
            user=user,
            is_active=True,
            role__in={"owner", "admin", "teacher", "staff"},
        ).first()
        if membership is None:
            raise CommandError("The target user is not active staff in tenant 9999.")

        references = fixture.get("references")
        style_samples = fixture.get("style_samples")
        if not isinstance(references, list) or not isinstance(style_samples, list):
            raise CommandError("Fixture sample arrays are invalid.")
        if len(references) > 500 or len(style_samples) > 500:
            raise CommandError("Fixture exceeds the isolated sample-count limit.")
        summary = {
            "target_tenant_id": target_tenant_id,
            "target_user_id": user.id,
            "tag": tag,
            "references": len(references),
            "style_samples": len(style_samples),
        }
        if options["dry_run"]:
            transaction.set_rollback(True)
            self.stdout.write(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return

        profile_name = sanitize_voice_text(f"{tag} 2번 자료 격리 검증", max_chars=80)
        profile = ProblemStudioVoiceProfile.objects.filter(
            tenant=tenant,
            owner=user,
            name=profile_name,
            status=ProblemStudioVoiceProfile.Status.ACTIVE,
        ).first()
        if profile is None:
            profile = create_voice_profile(
                tenant=tenant,
                user=user,
                name=profile_name,
                subject="통합",
                style_instructions="핵심 개념을 먼저 짚고, 오답이 되는 이유를 짧고 분명하게 설명합니다.",
                is_default=False,
            )
        for sample in references:
            if not isinstance(sample, dict):
                continue
            add_voice_sample(
                profile=profile,
                user=user,
                usage_scope=ProblemStudioVoiceSample.UsageScope.CONTENT_REFERENCE,
                origin=ProblemStudioVoiceSample.Origin.PUBLISHER_REFERENCE,
                source_label=f"{tag} 비식별 내용 참고",
                problem_text=sample.get("problem_text"),
                rights_confirmed=True,
                rights_note="운영 원본과 분리된 비식별 테스트 참고 자료",
                metadata={
                    "fixture_fingerprint": sample.get("fingerprint"),
                    "test_tag": tag,
                },
                allow_internal_origin=True,
            )
        for sample in style_samples:
            if not isinstance(sample, dict):
                continue
            add_voice_sample(
                profile=profile,
                user=user,
                usage_scope=ProblemStudioVoiceSample.UsageScope.STYLE,
                origin=ProblemStudioVoiceSample.Origin.MATCHUP_COMMENT,
                source_label=f"{tag} 비식별 강사 코멘트",
                explanation=sample.get("explanation"),
                rights_confirmed=True,
                rights_note="사용자 승인 하에 복제한 비식별 매치업 강사 작성 코멘트",
                metadata={
                    "fixture_fingerprint": sample.get("fingerprint"),
                    "test_tag": tag,
                },
                allow_internal_origin=True,
            )
        profile.refresh_from_db()
        self.stdout.write(
            json.dumps(
                {
                    **summary,
                    "profile": serialize_voice_profile(profile),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

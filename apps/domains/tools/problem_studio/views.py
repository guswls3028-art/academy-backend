from __future__ import annotations

import json
import logging
import os
import secrets
from functools import lru_cache
from urllib.parse import quote

from django.core.cache import cache
from django.db import transaction
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from academy.adapters.db.django import repositories_ai as ai_repo
from apps.domains.tools.problem_studio.services import extract_sources, parse_payload, source_extraction_to_payload
from apps.domains.tools.problem_studio.async_transfer import build_source_archive
from apps.domains.tools.problem_studio.beta_access import (
    ProblemStudioBetaLimitReached,
    beta_access_snapshot,
    release_beta_run,
    reserve_beta_run,
)
from apps.domains.tools.problem_studio.document_style import (
    BUILTIN_FONTS,
    resolve_document_style_payload,
    save_document_style_preference,
    serialize_document_style_preference,
)
from apps.domains.tools.problem_studio.font_assets import (
    create_personal_font_asset,
    delete_font_asset_file,
    font_asset_download_url,
    serialize_font_asset,
)
from apps.domains.tools.problem_studio.models import (
    ProblemStudioBetaRun,
    ProblemStudioDocumentStyle,
    ProblemStudioFontAsset,
    ProblemStudioVoiceProfile,
)
from apps.domains.tools.problem_studio.explanation_workflow import (
    ProblemStudioExplanationResumeUnavailable,
    resume_explanation_workflow,
    start_explanation_workflow,
)
from apps.domains.tools.problem_studio.voice_profiles import (
    add_voice_sample,
    create_voice_profile,
    get_owned_voice_profile,
    record_generation_review,
    resolve_voice_profile_payload,
    serialize_voice_profile,
    serialize_voice_sample,
    update_voice_profile,
)
from apps.domains.tools.problem_studio.transfer_documents import (
    build_transfer_package,
    package_to_response,
)
from apps.support.tools.ai_dependencies import dispatch_tools_ai_job


_HANGUL_COMPANION_MANIFEST_PATH = os.path.join(
    os.path.dirname(__file__),
    "hangul_companion_manifest.json",
)
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_hangul_companion_manifest() -> dict[str, str | int]:
    with open(_HANGUL_COMPANION_MANIFEST_PATH, encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    required = {"version", "r2_key", "filename", "sha256", "size_bytes"}
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise RuntimeError("Problem Studio 한글 연결 프로그램 배포 정보가 올바르지 않습니다.")
    sha256 = str(manifest["sha256"]).lower()
    if (
        len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
        or int(manifest["size_bytes"]) <= 0
    ):
        raise RuntimeError("Problem Studio 한글 연결 프로그램 무결성 정보가 올바르지 않습니다.")
    return manifest


def _resolve_request_document_style(request, payload: dict) -> dict:
    return resolve_document_style_payload(
        payload,
        tenant=request.tenant,
        user=request.user,
    )


def _resolve_request_voice_profile(request, payload: dict) -> dict:
    return resolve_voice_profile_payload(
        payload,
        tenant=request.tenant,
        user=request.user,
    )


def _job_belongs_to_request_user(job, request) -> bool:
    payload = job.payload if isinstance(job.payload, dict) else {}
    request_user_id = payload.get("request_user_id")
    return request_user_id is not None and str(request_user_id) == str(request.user.id)


class ProblemStudioFontCollectionView(APIView):
    """List or upload the current teacher's private Problem Studio fonts."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        fonts = ProblemStudioFontAsset.objects.filter(
            tenant=request.tenant,
            uploaded_by=request.user,
            status=ProblemStudioFontAsset.Status.READY,
        )
        response = Response({
            "built_in_fonts": list(BUILTIN_FONTS),
            "custom_fonts": [
                serialize_font_asset(font, include_download_url=True)
                for font in fonts
            ],
        })
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"detail": "올릴 TTF 또는 OTF 글꼴 파일을 선택해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            font = create_personal_font_asset(
                tenant=request.tenant,
                user=request.user,
                upload=upload,
                display_name=request.data.get("display_name"),
                license_basis=request.data.get("license_basis"),
                license_name=request.data.get("license_name"),
                license_url=request.data.get("license_url"),
                license_note=request.data.get("license_note"),
                rights_confirmed=str(request.data.get("rights_confirmed") or "").lower()
                in {"1", "true", "yes", "on"},
                redistribution_allowed=str(request.data.get("redistribution_allowed") or "").lower()
                in {"1", "true", "yes", "on"},
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            serialize_font_asset(font, include_download_url=True),
            status=status.HTTP_201_CREATED,
        )


class ProblemStudioFontDetailView(APIView):
    """Remove a private font and reset any saved style that references it."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def delete(self, request, font_id):
        font = ProblemStudioFontAsset.objects.filter(
            id=font_id,
            tenant=request.tenant,
            uploaded_by=request.user,
            status=ProblemStudioFontAsset.Status.READY,
        ).first()
        if font is None:
            return Response({"detail": "내 글꼴을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        storage_snapshot = {
            "tenant_id": font.tenant_id,
            "asset_id": font.id,
            "r2_key": font.r2_key,
        }
        with transaction.atomic():
            preference = ProblemStudioDocumentStyle.objects.filter(
                tenant=request.tenant,
                user=request.user,
            ).first()
            if preference is not None:
                update_fields = []
                if preference.title_font_asset_id == font.id:
                    preference.title_font_asset = None
                    preference.title_font_key = "hamchorom-dotum"
                    update_fields.extend(["title_font_asset", "title_font_key"])
                if preference.body_font_asset_id == font.id:
                    preference.body_font_asset = None
                    preference.body_font_key = "hamchorom-batang"
                    update_fields.extend(["body_font_asset", "body_font_key"])
                if update_fields:
                    preference.save(update_fields=[*update_fields, "updated_at"])
            font.delete()
        try:
            delete_font_asset_file(**storage_snapshot)
        except Exception:
            logger.warning(
                "PROBLEM_STUDIO_FONT_OBJECT_CLEANUP_FAILED font_id=%s tenant_id=%s",
                font_id,
                request.tenant.id,
                exc_info=True,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProblemStudioDocumentStyleView(APIView):
    """Load or save the current teacher's reusable output typography."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    def get(self, request):
        response = Response({
            "preference": serialize_document_style_preference(
                tenant=request.tenant,
                user=request.user,
            ),
        })
        response["Cache-Control"] = "no-store"
        return response

    def put(self, request):
        if not isinstance(request.data, dict):
            return Response({"detail": "문서 스타일 값이 올바르지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            save_document_style_preference(
                dict(request.data),
                tenant=request.tenant,
                user=request.user,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "preference": serialize_document_style_preference(
                tenant=request.tenant,
                user=request.user,
            ),
        })


class ProblemStudioVoiceProfileCollectionView(APIView):
    """List or create reusable, teacher-owned explanation voice profiles."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    def get(self, request):
        profiles = ProblemStudioVoiceProfile.objects.filter(
            tenant=request.tenant,
            owner=request.user,
            status=ProblemStudioVoiceProfile.Status.ACTIVE,
        )
        response = Response({
            "profiles": [serialize_voice_profile(profile) for profile in profiles],
        })
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request):
        if not isinstance(request.data, dict):
            return Response({"detail": "문체 프로필 값이 올바르지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            profile = create_voice_profile(
                tenant=request.tenant,
                user=request.user,
                name=request.data.get("name"),
                subject=request.data.get("subject"),
                style_instructions=request.data.get("style_instructions"),
                is_default=request.data.get("is_default") is True,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_voice_profile(profile), status=status.HTTP_201_CREATED)


class ProblemStudioVoiceProfileDetailView(APIView):
    """Inspect, rename, or archive one of the current teacher's profiles."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    def _get_profile(self, request, profile_id, *, active_only: bool = True):
        return get_owned_voice_profile(
            tenant=request.tenant,
            user=request.user,
            profile_id=profile_id,
            active_only=active_only,
        )

    def get(self, request, profile_id):
        profile = self._get_profile(request, profile_id)
        if profile is None:
            return Response({"detail": "내 문체 프로필을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        response = Response(serialize_voice_profile(profile, include_samples=True))
        response["Cache-Control"] = "no-store"
        return response

    def patch(self, request, profile_id):
        profile = self._get_profile(request, profile_id, active_only=False)
        if profile is None:
            return Response({"detail": "내 문체 프로필을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        if not isinstance(request.data, dict):
            return Response({"detail": "문체 프로필 값이 올바르지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            profile = update_voice_profile(
                profile,
                name=request.data.get("name") if "name" in request.data else None,
                subject=request.data.get("subject") if "subject" in request.data else None,
                style_instructions=(
                    request.data.get("style_instructions")
                    if "style_instructions" in request.data
                    else None
                ),
                is_default=(
                    request.data.get("is_default") is True
                    if "is_default" in request.data
                    else None
                ),
                status=request.data.get("status") if "status" in request.data else None,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serialize_voice_profile(profile, include_samples=True))


class ProblemStudioVoiceSampleCollectionView(APIView):
    """Append a rights-confirmed style example or a content-only reference."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    def post(self, request, profile_id):
        profile = get_owned_voice_profile(
            tenant=request.tenant,
            user=request.user,
            profile_id=profile_id,
            active_only=True,
        )
        if profile is None:
            return Response({"detail": "내 문체 프로필을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        if not isinstance(request.data, dict):
            return Response({"detail": "문체 샘플 값이 올바르지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            sample, created = add_voice_sample(
                profile=profile,
                user=request.user,
                usage_scope=request.data.get("usage_scope"),
                origin=request.data.get("origin"),
                source_label=request.data.get("source_label"),
                problem_text=request.data.get("problem_text"),
                answer=request.data.get("answer"),
                explanation=request.data.get("explanation"),
                rights_confirmed=request.data.get("rights_confirmed") is True,
                rights_note=request.data.get("rights_note"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "sample": serialize_voice_sample(sample),
                "profile": serialize_voice_profile(profile),
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ProblemStudioTransferDocumentView(APIView):
    """POST /api/v1/tools/problem-studio/transfer-document/

    원본 파일을 AI 생성 없이 한글/워드 호환 검수 문서 패키지로 이관한다.
    큰 PDF/HWP/ZIP 산출물은 JSON/AI 워커 payload를 거치지 않고 바로 파일로
    내려보내 용량 폭발을 피한다.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        try:
            payload = parse_payload(request.data.get("payload") if hasattr(request.data, "get") else request.data)
            if not payload and isinstance(request.data, dict):
                payload = dict(request.data)
            payload = _resolve_request_document_style(request, payload)
            package = build_transfer_package(
                payload=payload,
                source_files=request.FILES.getlist("source_files"),
            )
            return package_to_response(package)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProblemStudioBetaAccessView(APIView):
    """Return the tenant-wide three-run Beta trial balance."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        response = Response({"beta_access": beta_access_snapshot(tenant=request.tenant)})
        response["Cache-Control"] = "no-store"
        return response


def _serialize_explanation_run(run: ProblemStudioBetaRun) -> dict:
    total = int(run.question_count or 0)
    completed = int(run.completed_question_count or 0)
    verified = int(run.verified_question_count or 0)
    stage = str(run.stage or ProblemStudioBetaRun.Stage.EXTRACT)
    if run.status == ProblemStudioBetaRun.Status.COMPLETED:
        public_status = "DONE"
        percent = 100
    elif run.status == ProblemStudioBetaRun.Status.RELEASED:
        public_status = "FAILED"
        percent = 0 if not total else min(99, int(100 * completed / total))
    else:
        public_status = "PENDING" if stage == ProblemStudioBetaRun.Stage.EXTRACT else "RUNNING"
        if stage == ProblemStudioBetaRun.Stage.EXTRACT:
            percent = 5
        elif stage == ProblemStudioBetaRun.Stage.SOLVE:
            percent = 10 + int(55 * completed / max(1, total))
        elif stage == ProblemStudioBetaRun.Stage.VERIFY:
            percent = 65 + int(25 * verified / max(1, completed))
        else:
            percent = 95
    stage_meta = {
        ProblemStudioBetaRun.Stage.EXTRACT: (1, "문항과 정답표 분석"),
        ProblemStudioBetaRun.Stage.SOLVE: (2, "정답·해설 생성"),
        ProblemStudioBetaRun.Stage.VERIFY: (3, "빈 정답 독립 검산"),
        ProblemStudioBetaRun.Stage.BUILD: (4, "정답·해설 PDF 조립"),
        ProblemStudioBetaRun.Stage.DONE: (4, "완료"),
    }
    step_index, step_name = stage_meta.get(stage, (1, "준비"))
    result_payload = None
    expected_prefix = f"tenants/{run.tenant_id}/tools/problem-studio/explanation-runs/{run.id}/result/"
    if public_status == "DONE" and run.result_key.startswith(expected_prefix):
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

        result_payload = dict(run.result_payload or {})
        result_payload["download_url"] = generate_presigned_get_url_storage(
            key=run.result_key,
            expires_in=900,
            filename=run.result_filename or "정답해설_Beta.pdf",
            content_type="application/pdf",
        )
    return {
        "run_id": str(run.id),
        "status": public_status,
        "stage": stage,
        "source_name": run.source_name,
        "progress": {
            "percent": max(0, min(100, percent)),
            "step_index": step_index,
            "step_total": 4,
            "step_name": stage,
            "step_name_display": step_name,
            "completed_questions": completed,
            "total_questions": total,
            "verified_questions": verified,
            "review_required_questions": int(run.review_required_count or 0),
        },
        "result": result_payload,
        "error_message": run.last_error or run.release_reason or None,
        "can_resume": (
            run.status == ProblemStudioBetaRun.Status.RELEASED
            and bool(run.source_archive_key)
        ),
        "beta_access": beta_access_snapshot(tenant=run.tenant),
    }


class ProblemStudioExplanationRunCreateView(APIView):
    """Reserve one tenant Beta run and start the resumable full-workbook PDF flow."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        archive_file = None
        archive_key = ""
        run = None
        workflow_started = False
        try:
            source_files = request.FILES.getlist("source_files")
            if len(source_files) != 1 or not str(source_files[0].name).lower().endswith(".pdf"):
                return Response(
                    {"detail": "정답·해설 PDF Beta는 한 번에 PDF 한 파일만 처리합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            payload = parse_payload(request.data.get("payload") if hasattr(request.data, "get") else {})
            subject = str(payload.get("subject") or "").strip()[:100]
            note_policy = str(payload.get("note_policy") or "").strip()[:2000]
            archive_file, source_manifest = build_source_archive(source_files)
            try:
                run = reserve_beta_run(tenant=request.tenant, user=request.user)
            except ProblemStudioBetaLimitReached:
                return Response(
                    {
                        "detail": "문제집 해설 Beta 무료 체험 3회를 모두 사용했습니다.",
                        "rejection_code": "problem_studio_beta_limit_reached",
                        "beta_access": beta_access_snapshot(tenant=request.tenant),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            archive_key = (
                f"tenants/{request.tenant.id}/tools/problem-studio/"
                f"tmp/explanation-runs/{run.id}/sources.zip"
            )
            from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

            upload_fileobj_to_r2_storage(
                fileobj=archive_file,
                key=archive_key,
                content_type="application/zip",
            )
            run.source_name = str(source_manifest[0]["name"])
            run.source_archive_key = archive_key
            run.request_payload = {
                "subject": subject,
                "note_policy": note_policy,
            }
            run.save(
                update_fields=[
                    "source_name",
                    "source_archive_key",
                    "request_payload",
                    "updated_at",
                ]
            )
            start_explanation_workflow(
                run_id=str(run.id),
                tenant_id=str(request.tenant.id),
            )
            workflow_started = True
            run.refresh_from_db()
            response = Response(_serialize_explanation_run(run), status=status.HTTP_202_ACCEPTED)
            response["Cache-Control"] = "no-store"
            return response
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Problem Studio explanation run start failed")
            return Response(
                {"detail": "정답·해설 PDF 작업을 시작할 수 없습니다. 잠시 뒤 다시 시도해 주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        finally:
            if run is not None and not workflow_started:
                release_beta_run(
                    run_id=str(run.id),
                    tenant_id=str(request.tenant.id),
                    reason="job_not_started",
                )
            if archive_key and not workflow_started:
                try:
                    from apps.infrastructure.storage.r2 import delete_object_r2_storage

                    delete_object_r2_storage(key=archive_key)
                    if run is not None:
                        ProblemStudioBetaRun.objects.filter(
                            pk=run.id,
                            tenant=request.tenant,
                        ).update(source_archive_key="")
                except Exception:
                    pass
            if archive_file is not None:
                archive_file.close()


class ProblemStudioExplanationRunStatusView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, run_id):
        run = ProblemStudioBetaRun.objects.filter(
            pk=run_id,
            tenant=request.tenant,
            requested_by=request.user,
        ).first()
        if run is None:
            return Response({"detail": "정답·해설 작업을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        response = Response(_serialize_explanation_run(run))
        response["Cache-Control"] = "no-store"
        return response


class ProblemStudioExplanationRunResumeView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    def post(self, request, run_id):
        try:
            resume_explanation_workflow(
                run_id=str(run_id),
                tenant=request.tenant,
                user=request.user,
            )
        except ProblemStudioExplanationResumeUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        run = ProblemStudioBetaRun.objects.get(
            pk=run_id,
            tenant=request.tenant,
            requested_by=request.user,
        )
        response = Response(_serialize_explanation_run(run), status=status.HTTP_202_ACCEPTED)
        response["Cache-Control"] = "no-store"
        return response


class ProblemStudioTransferJobCreateView(APIView):
    """POST /api/v1/tools/problem-studio/transfer-jobs/

    대용량 원본 이관은 API/ALB 60초 경계를 넘을 수 있으므로 R2 임시 소스
    아카이브 + tools worker로 처리한다. 완료 결과는 generic job status
    endpoint의 result.download_url로 내려간다.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        archive_file = None
        archive_key = ""
        try:
            payload = parse_payload(request.data.get("payload") if hasattr(request.data, "get") else request.data)
            if not payload and isinstance(request.data, dict):
                payload = dict(request.data)
            payload = _resolve_request_document_style(request, payload)
            payload = _resolve_request_voice_profile(request, payload)
            payload["auto_explanations"] = payload.get("auto_explanations", True) is not False
            source_files = request.FILES.getlist("source_files")
            if not source_files:
                return Response({"detail": "원본으로 옮길 소스 파일을 먼저 올려 주세요."}, status=status.HTTP_400_BAD_REQUEST)

            archive_file, source_manifest = build_source_archive(source_files)
            import uuid
            from apps.infrastructure.storage.r2 import delete_object_r2_storage, upload_fileobj_to_r2_storage

            tenant_id = str(request.tenant.id)
            unique = uuid.uuid4().hex[:12]
            archive_key = f"tenants/{tenant_id}/tools/problem-studio/tmp/{unique}/sources.zip"
            upload_fileobj_to_r2_storage(
                fileobj=archive_file,
                key=archive_key,
                content_type="application/zip",
            )

            ai_transcription = bool(payload.get("ai_transcription", True))
            result = dispatch_tools_ai_job(
                job_type=("problem_studio_transcription" if ai_transcription else "problem_studio_transfer"),
                payload={
                    "problem_studio_payload": payload,
                    "source_archive_key": archive_key,
                    "source_files": source_manifest,
                    "tenant_id": tenant_id,
                    "request_user_id": str(request.user.id),
                },
                tenant_id=tenant_id,
                source_domain="tools_problem_studio",
                source_id=None,
                tier="basic",
            )
            if not result.get("ok"):
                if archive_key:
                    try:
                        delete_object_r2_storage(key=archive_key)
                    except Exception:
                        pass
                return Response(
                    {
                        "detail": result.get("error") or "원본 이관 작업을 시작할 수 없습니다.",
                        "rejection_code": result.get("rejection_code"),
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(
                {
                    "job_id": result["job_id"],
                    "status": "PENDING",
                    "source_files": [
                        {
                            "name": item["name"],
                            "kind": item["name"].rsplit(".", 1)[-1].upper() if "." in item["name"] else "기타",
                            "sizeLabel": f"{item['size'] / (1024 * 1024):.1f}MB" if item["size"] >= 1024 * 1024 else f"{item['size'] / 1024:.1f}KB",
                            "extractedChars": 0,
                            "warning": None,
                        }
                        for item in source_manifest
                    ],
                    "warnings": [],
                    "source_text_chars": 0,
                    "beta_access": beta_access_snapshot(tenant=request.tenant),
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            if archive_file is not None:
                archive_file.close()


class ProblemStudioJobCreateView(APIView):
    """POST /api/v1/tools/problem-studio/jobs/

    문항 생성처럼 오래 걸릴 수 있는 처리는 AI-SQS 워커로 넘긴다. 업로드 파일
    본문 추출은 API에서 한 번만 수행하고, 추출된 텍스트와 메타를 worker payload로
    전달해 request 파일 수명에 의존하지 않게 한다.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        try:
            payload = parse_payload(request.data.get("payload") if hasattr(request.data, "get") else request.data)
            if not payload and isinstance(request.data, dict):
                payload = dict(request.data)
            payload = _resolve_request_document_style(request, payload)
            payload = _resolve_request_voice_profile(request, payload)
            sources = extract_sources(request.FILES.getlist("source_files"))
            source_payloads = [source_extraction_to_payload(source) for source in sources]
            result = dispatch_tools_ai_job(
                job_type="problem_studio_package",
                payload={
                    "problem_studio_payload": payload,
                    "source_files": source_payloads,
                    "tenant_id": str(request.tenant.id),
                    "request_user_id": str(request.user.id),
                },
                tenant_id=str(request.tenant.id),
                source_domain="tools_problem_studio",
                source_id=None,
                tier="basic",
            )
            if not result.get("ok"):
                return Response(
                    {
                        "detail": result.get("error") or "문항 생성 작업을 시작할 수 없습니다.",
                        "rejection_code": result.get("rejection_code"),
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            return Response(
                {
                    "job_id": result["job_id"],
                    "status": "PENDING",
                    "source_files": [
                        {
                            "name": source.name,
                            "kind": source.kind,
                            "sizeLabel": source.size_label,
                            "extractedChars": len(source.extracted_text),
                            "warning": source.warning,
                        }
                        for source in sources
                    ],
                    "warnings": [source.warning for source in sources if source.warning],
                    "source_text_chars": sum(len(source.extracted_text) for source in sources),
                },
                status=status.HTTP_202_ACCEPTED,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


class ProblemStudioJobStatusView(APIView):
    """GET /api/v1/tools/problem-studio/jobs/<job_id>/"""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, job_id: str):
        job = ai_repo.get_job_model_for_status(
            str(job_id),
            str(request.tenant.id),
            job_type="problem_studio_package",
        )
        if not job or not _job_belongs_to_request_user(job, request):
            return Response({"detail": "작업을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        result_payload = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) if job.status == "DONE" else None
        return Response({
            "job_id": job.job_id,
            "status": job.status,
            "error": job.error_message or job.last_error or "",
            "result": result_payload,
        })


class ProblemStudioGenerationReviewView(APIView):
    """Append teacher review feedback without mutating generated or Matchup source rows."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    parser_classes = [JSONParser]

    def post(self, request, job_id: str):
        job = ai_repo.get_job_model_for_status(
            str(job_id),
            str(request.tenant.id),
            job_type="problem_studio_package",
        )
        if (
            not job
            or not _job_belongs_to_request_user(job, request)
            or job.status != "DONE"
        ):
            return Response({"detail": "검수할 완료 작업을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        if not isinstance(request.data, dict):
            return Response({"detail": "검수 값이 올바르지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)

        job_payload = job.payload if isinstance(job.payload, dict) else {}
        studio_payload = job_payload.get("problem_studio_payload")
        if not isinstance(studio_payload, dict):
            studio_payload = {}
        voice_snapshot = studio_payload.get("_resolved_voice_profile")
        if not isinstance(voice_snapshot, dict) or not voice_snapshot.get("id"):
            return Response(
                {"detail": "이 생성 작업에는 문체 프로필이 연결되지 않았습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile = get_owned_voice_profile(
            tenant=request.tenant,
            user=request.user,
            profile_id=voice_snapshot["id"],
            active_only=True,
        )
        if profile is None:
            return Response({"detail": "내 문체 프로필을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        try:
            question_index = int(request.data.get("question_index"))
        except (TypeError, ValueError):
            return Response({"detail": "검수할 문항 번호가 올바르지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)
        result_payload = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job)
        questions = result_payload.get("questions") if isinstance(result_payload, dict) else None
        if (
            not isinstance(questions, list)
            or question_index < 0
            or question_index >= len(questions)
            or not isinstance(questions[question_index], dict)
        ):
            return Response({"detail": "검수할 생성 문항을 찾을 수 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            review, created = record_generation_review(
                tenant=request.tenant,
                user=request.user,
                profile=profile,
                job_id=str(job_id),
                question_index=question_index,
                original_question=questions[question_index],
                final_question=request.data.get("final_question"),
                outcome=request.data.get("outcome"),
                feedback_note=request.data.get("feedback_note"),
                learn_from_this=request.data.get("learn_from_this") is True,
                rights_confirmed=request.data.get("rights_confirmed") is True,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "review_id": str(review.id),
                "created": created,
                "learned": review.learned_sample_id is not None,
                "profile": serialize_voice_profile(profile),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ProblemStudioTransferJobStatusView(APIView):
    """Staff-only transfer status with a freshly issued result URL."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    _JOB_TYPES = {"problem_studio_transfer", "problem_studio_transcription"}

    def get(self, request, job_id: str):
        job = ai_repo.get_job_model_for_status(str(job_id), str(request.tenant.id))
        if (
            not job
            or job.job_type not in self._JOB_TYPES
            or not _job_belongs_to_request_user(job, request)
        ):
            return Response({"detail": "작업을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        progress = None
        try:
            from academy.adapters.cache.redis_progress_adapter import RedisProgressAdapter

            progress = RedisProgressAdapter().get_progress(str(job.job_id), tenant_id=str(request.tenant.id))
        except Exception:
            pass

        result_payload = None
        if job.status == "DONE":
            raw_result = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) or {}
            result_key = str(raw_result.get("r2_key") or "")
            expected_prefix = f"tenants/{request.tenant.id}/tools/problem-studio/"
            if result_key.startswith(expected_prefix):
                from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

                result_payload = {
                    key: value
                    for key, value in raw_result.items()
                    if key not in {"r2_key", "download_url"} and not key.startswith("_")
                }
                result_payload["download_url"] = generate_presigned_get_url_storage(
                    key=result_key,
                    expires_in=900,
                    filename=str(raw_result.get("filename") or "problem-studio.zip"),
                    content_type="application/zip",
                )

        return Response({
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "progress": progress,
            "result": result_payload,
            "error_message": job.error_message or job.last_error or None,
            "beta_access": beta_access_snapshot(tenant=request.tenant),
        })


class ProblemStudioHangulCompanionDownloadView(APIView):
    """Return a staff-only URL for the sealed Windows companion package."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        from academy.adapters.storage import r2_objects

        manifest = _load_hangul_companion_manifest()
        expected_size = int(manifest["size_bytes"])
        expected_sha256 = str(manifest["sha256"]).lower()
        try:
            integrity = r2_objects.head_storage_object_integrity(key=str(manifest["r2_key"]))
        except Exception:
            logger.exception("Problem Studio Hangul companion object HEAD failed")
            integrity = None
        if integrity != (expected_size, expected_sha256):
            return Response(
                {"detail": "한글 연결 프로그램 배포본을 확인하는 중입니다. 잠시 뒤 다시 시도해 주세요."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response = Response({
            "download_url": r2_objects.create_storage_download_url(
                key=str(manifest["r2_key"]),
                filename=str(manifest["filename"]),
                content_type="application/zip",
                expires_in=600,
            ),
            "filename": str(manifest["filename"]),
            "version": str(manifest["version"]),
            "sha256": expected_sha256,
            "size_bytes": expected_size,
        })
        response["Cache-Control"] = "no-store"
        return response


class ProblemStudioHangulHandoffCreateView(APIView):
    """Create a short-lived one-time handoff for the Windows companion."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, job_id: str):
        job = ai_repo.get_job_model_for_status(str(job_id), str(request.tenant.id))
        if (
            not job
            or job.job_type not in ProblemStudioTransferJobStatusView._JOB_TYPES
            or job.status != "DONE"
            or not _job_belongs_to_request_user(job, request)
        ):
            return Response({"detail": "완료된 검수본을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
        result_payload = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) or {}
        result_key = str(result_payload.get("r2_key") or "")
        expected_prefix = f"tenants/{request.tenant.id}/tools/problem-studio/"
        if not result_key.startswith(expected_prefix):
            return Response({"detail": "검수본 저장 경로가 올바르지 않습니다."}, status=status.HTTP_409_CONFLICT)

        token = secrets.token_urlsafe(32)
        cache.set(
            f"problem-studio:hangul-handoff:{token}",
            {
                "job_id": str(job.job_id),
                "tenant_id": str(request.tenant.id),
                "user_id": str(request.user.id),
            },
            timeout=300,
        )
        handoff_url = request.build_absolute_uri(
            f"/api/v1/tools/problem-studio/hangul-handoffs/{token}/"
        )
        response = Response({
            "protocol_url": f"academy-hangul://insert?handoff={quote(handoff_url, safe='')}",
            "expires_in": 300,
        })
        response["Cache-Control"] = "no-store"
        return response


class ProblemStudioHangulHandoffConsumeView(APIView):
    """Consume a handoff once and return a fresh, tenant-scoped download URL."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request, token: str):
        if len(token) < 32 or len(token) > 80:
            return Response({"detail": "연결 코드가 올바르지 않습니다."}, status=status.HTTP_404_NOT_FOUND)
        key = f"problem-studio:hangul-handoff:{token}"
        lock_key = f"{key}:lock"
        if not cache.add(lock_key, "1", timeout=30):
            return Response({"detail": "이미 사용 중인 연결 코드입니다."}, status=status.HTTP_409_CONFLICT)
        try:
            handoff = cache.get(key)
            cache.delete(key)
            if not isinstance(handoff, dict):
                return Response({"detail": "만료되었거나 사용된 연결 코드입니다."}, status=status.HTTP_404_NOT_FOUND)
            tenant_id = str(handoff.get("tenant_id") or "")
            job = ai_repo.get_job_model_for_status(str(handoff.get("job_id") or ""), tenant_id)
            if not job or job.status != "DONE" or job.job_type not in ProblemStudioTransferJobStatusView._JOB_TYPES:
                return Response({"detail": "검수본을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)
            result_payload = ai_repo.DjangoAIJobRepository().get_result_payload_for_job(job) or {}
            result_key = str(result_payload.get("r2_key") or "")
            if not result_key.startswith(f"tenants/{tenant_id}/tools/problem-studio/"):
                return Response({"detail": "검수본 저장 경로가 올바르지 않습니다."}, status=status.HTTP_409_CONFLICT)

            from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

            filename = str(result_payload.get("filename") or "problem-studio.zip")
            font_payloads = []
            raw_font_assets = result_payload.get("_font_assets")
            if isinstance(raw_font_assets, list):
                for snapshot in raw_font_assets:
                    if not isinstance(snapshot, dict):
                        continue
                    asset = ProblemStudioFontAsset.objects.filter(
                        id=snapshot.get("id"),
                        tenant_id=tenant_id,
                        uploaded_by_id=str(handoff.get("user_id") or ""),
                        status=ProblemStudioFontAsset.Status.READY,
                        sha256=snapshot.get("sha256"),
                    ).first()
                    if asset is None or asset.r2_key != snapshot.get("r2_key"):
                        return Response(
                            {"detail": "검수본에 선택한 내 글꼴을 더 이상 사용할 수 없습니다. 웹에서 다시 생성해 주세요."},
                            status=status.HTTP_409_CONFLICT,
                        )
                    font_payloads.append({
                        "id": str(asset.id),
                        "family_name": asset.family_name,
                        "file_name": asset.original_name,
                        "download_url": font_asset_download_url(asset, expires_in=300),
                        "sha256": asset.sha256,
                        "size_bytes": asset.size_bytes,
                        "content_type": asset.content_type,
                    })
            response = Response({
                "download_url": generate_presigned_get_url_storage(
                    key=result_key,
                    expires_in=300,
                    filename=filename,
                    content_type="application/zip",
                ),
                "filename": filename,
                "sha256": str(result_payload.get("sha256") or ""),
                "size_bytes": int(result_payload.get("size_bytes") or 0),
                "fonts": font_payloads,
            })
            response["Cache-Control"] = "no-store"
            return response
        finally:
            cache.delete(lock_key)

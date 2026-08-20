# PATH: apps/domains/results/views/score_draft_view.py
"""
Score Edit Draft API — 임시 저장/복원. 최종 반영은 프론트 "편집 종료" 시 patch API로만 수행.

GET  /results/admin/sessions/<session_id>/score-draft/
     → 200 { changes: [...] } or 404

PUT  /results/admin/sessions/<session_id>/score-draft/
     body: { "changes": [ { type, examId?, enrollmentId, homeworkId?, score?, metaStatus? }, ... ] }
     → 200

POST /results/admin/sessions/<session_id>/score-draft/commit/
     → 204 (draft 삭제; 실제 점수 반영은 프론트가 patch API로 이미 수행한 뒤 호출)
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status as drf_status

from apps.core.parsing import parse_bool
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.guards.score_edit_lease_guard import (
    EDIT_LEASE_TTL,
    ScoreEditLeaseConflict,
    ScoreEditLeaseStale,
    score_edit_client_id,
    score_edit_lease_payload,
    score_edit_payload_parts,
    score_edit_payload_is_invalidated,
)
from apps.domains.results.guards.score_edit_lease_state import (
    score_edit_changes_are_exclusive,
    score_edit_changes_conflict,
)
from apps.domains.results.models import ScoreEditDraft
from apps.support.results.progress_read_dependencies import (
    get_session_for_tenant_or_404,
    lock_score_edit_scope_for_session,
    score_edit_scope_session_ids,
)


def _locked_response() -> Response:
    error = ScoreEditLeaseConflict()
    return Response(error.detail, status=drf_status.HTTP_409_CONFLICT)


def _stale_response() -> Response:
    error = ScoreEditLeaseStale()
    return Response(error.detail, status=drf_status.HTTP_409_CONFLICT)


def _lock_session(*, session_id: int, tenant):
    return lock_score_edit_scope_for_session(
        session_id=int(session_id),
        tenant=tenant,
    )


class ScoreDraftView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, session_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)
        get_session_for_tenant_or_404(session_id=int(session_id), tenant=tenant)
        client_id = score_edit_client_id(request)
        scope_ids = score_edit_scope_session_ids(
            session_id=int(session_id),
            tenant=tenant,
        )
        active_since = timezone.now() - EDIT_LEASE_TTL
        active_drafts = ScoreEditDraft.objects.filter(
            session_id__in=scope_ids,
            tenant_id=tenant.id,
            updated_at__gte=active_since,
        )
        for active in active_drafts:
            if score_edit_payload_is_invalidated(active.payload):
                continue
            stored_client_id, active_changes = score_edit_payload_parts(active.payload)
            if active.editor_user_id == request.user.id:
                if stored_client_id is not None and stored_client_id != client_id:
                    return _locked_response()
            elif score_edit_changes_are_exclusive(active_changes):
                return _locked_response()

        draft = ScoreEditDraft.objects.filter(
            session_id=int(session_id),
            tenant_id=tenant.id,
            editor_user_id=request.user.id,
        ).first()
        if not draft:
            return Response({"changes": []})
        _, changes = score_edit_payload_parts(draft.payload)
        return Response({
            "changes": changes,
            "stale": score_edit_payload_is_invalidated(draft.payload),
        })

    def put(self, request, session_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)
        changes = request.data.get("changes")
        if not isinstance(changes, list):
            return Response({"detail": "changes must be a list"}, status=400)
        acknowledge_stale = parse_bool(
            request.data.get("acknowledge_stale", False),
            field_name="acknowledge_stale",
        )
        client_id = score_edit_client_id(request)
        with transaction.atomic():
            _, scope_ids = _lock_session(session_id=int(session_id), tenant=tenant)
            active_since = timezone.now() - EDIT_LEASE_TTL
            drafts = list(
                ScoreEditDraft.objects.select_for_update().filter(
                    session_id__in=scope_ids,
                    tenant_id=tenant.id,
                )
            )
            for existing in drafts:
                if existing.updated_at < active_since:
                    continue
                if score_edit_payload_is_invalidated(existing.payload):
                    continue
                stored_client_id, existing_changes = score_edit_payload_parts(existing.payload)
                if existing.editor_user_id == request.user.id:
                    if stored_client_id is not None and stored_client_id != client_id:
                        return _locked_response()
                elif score_edit_changes_conflict(existing_changes, changes):
                    return _locked_response()

            draft = next(
                (
                    item
                    for item in drafts
                    if int(item.session_id) == int(session_id)
                    and item.editor_user_id == request.user.id
                ),
                None,
            )
            if draft is not None and score_edit_payload_is_invalidated(draft.payload):
                if not acknowledge_stale:
                    return _stale_response()
            payload = score_edit_lease_payload(client_id=client_id, changes=changes)
            if draft is None:
                draft = ScoreEditDraft.objects.create(
                    session_id=int(session_id),
                    tenant_id=tenant.id,
                    editor_user_id=request.user.id,
                    payload=payload,
                )
            else:
                draft.payload = payload
                draft.save(update_fields=["payload", "updated_at"])
        return Response({"changes": changes})


class ScoreDraftCommitView(APIView):
    """편집 종료 시 프론트가 patch 적용 후 호출 — draft 삭제."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, session_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)
        client_id = score_edit_client_id(request)
        release_lease = parse_bool(
            request.data.get("release_lease", True),
            field_name="release_lease",
        )
        with transaction.atomic():
            _lock_session(session_id=int(session_id), tenant=tenant)
            draft = (
                ScoreEditDraft.objects.select_for_update()
                .filter(
                    session_id=int(session_id),
                    tenant_id=tenant.id,
                    editor_user_id=request.user.id,
                )
                .first()
            )
            if draft is None:
                return Response(status=204)
            stored_client_id, _ = score_edit_payload_parts(draft.payload)
            lease_is_active = draft.updated_at >= timezone.now() - EDIT_LEASE_TTL
            if (
                lease_is_active
                and stored_client_id is not None
                and stored_client_id != client_id
            ):
                return _locked_response()
            if (
                not release_lease
                and score_edit_payload_is_invalidated(draft.payload)
            ):
                return _stale_response()
            if release_lease:
                draft.delete()
            else:
                draft.payload = score_edit_lease_payload(client_id=client_id, changes=[])
                draft.save(update_fields=["payload", "updated_at"])
        return Response(status=204)

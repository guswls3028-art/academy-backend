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

from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.models import ScoreEditDraft
from apps.domains.lectures.models import Session


class ScoreDraftView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, session_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)
        get_object_or_404(
            Session,
            id=int(session_id),
            lecture__tenant=tenant,
        )
        draft = ScoreEditDraft.objects.filter(
            session_id=int(session_id),
            tenant_id=tenant.id,
            editor_user_id=request.user.id,
        ).first()
        if not draft:
            return Response({"changes": []})
        return Response({"changes": draft.payload or []})

    def put(self, request, session_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)
        get_object_or_404(
            Session,
            id=int(session_id),
            lecture__tenant=tenant,
        )
        changes = request.data.get("changes")
        if not isinstance(changes, list):
            return Response({"detail": "changes must be a list"}, status=400)
        draft, _ = ScoreEditDraft.objects.update_or_create(
            session_id=int(session_id),
            tenant_id=tenant.id,
            editor_user_id=request.user.id,
            defaults={"payload": changes},
        )
        return Response({"changes": draft.payload})


class ScoreDraftCommitView(APIView):
    """편집 종료 시 프론트가 patch 적용 후 호출 — draft 삭제."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, session_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)
        get_object_or_404(
            Session,
            id=int(session_id),
            lecture__tenant=tenant,
        )
        deleted, _ = ScoreEditDraft.objects.filter(
            session_id=int(session_id),
            tenant_id=tenant.id,
            editor_user_id=request.user.id,
        ).delete()
        return Response(status=204)

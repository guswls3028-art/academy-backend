from __future__ import annotations

import json
import logging

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .models import MatchupDocument, MatchupProblem
from .serializers import MatchupProblemSerializer
from .services import (
    approve_problem_public_image,
    clean_document_public_images,
    dispatch_document_public_cleanup,
    upload_problem_public_image,
)
from .views import (
    _attach_problem_image_urls,
    _is_tenant_staff,
    _jwt_required,
    _tenant_required,
)

logger = logging.getLogger(__name__)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentPublicCleanupView(View):
    """POST /api/v1/matchup/documents/<id>/public-cleanup/"""

    def post(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        try:
            run_async = body.get("async")
            if run_async is None:
                run_async = True
            if run_async:
                result = dispatch_document_public_cleanup(
                    doc,
                    force=bool(body.get("force")),
                    actor=getattr(request, "user", None),
                )
                if not result.get("ok"):
                    return JsonResponse(
                        {
                            "detail": result.get("error") or "공개용 이미지 정리 작업을 시작하지 못했습니다.",
                            "rejection_code": result.get("rejection_code"),
                        },
                        status=400,
                    )
                return JsonResponse(
                    {
                        "ok": True,
                        "queued": True,
                        "doc_id": doc.id,
                        "job_id": result.get("job_id") or "",
                        "type": result.get("type") or "matchup_public_cleanup",
                    },
                    status=202,
                )

            result = clean_document_public_images(
                doc,
                force=bool(body.get("force")),
                actor=getattr(request, "user", None),
            )
        except Exception:
            logger.exception("public cleanup failed (doc=%s)", doc.id)
            return JsonResponse({"detail": "공개용 이미지 정리에 실패했습니다."}, status=500)

        return JsonResponse(result, status=200 if result.get("ok") else 207)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class ProblemPublicCleanupApproveView(View):
    """POST /api/v1/matchup/problems/<id>/public-cleanup/approve/"""

    def post(self, request, problem_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        try:
            problem = MatchupProblem.objects.get(id=problem_id, tenant=request.tenant)
        except MatchupProblem.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            result = approve_problem_public_image(
                problem,
                actor=getattr(request, "user", None),
            )
        except ValueError as e:
            return JsonResponse({"detail": str(e)}, status=400)
        except Exception:
            logger.exception("public cleanup approve failed (problem=%s)", problem_id)
            return JsonResponse({"detail": "공개용 이미지 승인에 실패했습니다."}, status=500)

        data = MatchupProblemSerializer(problem).data
        _attach_problem_image_urls(data, problem)
        return JsonResponse({"ok": True, "result": result, "problem": data})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class ProblemPublicImageUploadView(View):
    """POST /api/v1/matchup/problems/<id>/public-image/"""

    def post(self, request, problem_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        try:
            problem = MatchupProblem.objects.get(id=problem_id, tenant=request.tenant)
        except MatchupProblem.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        image_file = request.FILES.get("file")
        if not image_file:
            return JsonResponse({"detail": "file required"}, status=400)

        try:
            result = upload_problem_public_image(
                problem,
                image_file=image_file,
                actor=getattr(request, "user", None),
            )
        except ValueError as e:
            return JsonResponse({"detail": str(e)}, status=400)
        except Exception:
            logger.exception("public image upload failed (problem=%s)", problem_id)
            return JsonResponse({"detail": "공개용 이미지 업로드에 실패했습니다."}, status=500)

        data = MatchupProblemSerializer(problem).data
        _attach_problem_image_urls(data, problem)
        return JsonResponse({"ok": True, "result": result, "problem": data}, status=201)

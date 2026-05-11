# PATH: apps/domains/matchup/views_hit_report.py
# 강사 1인 매치업 적중 보고서 — Curated Hit Report API views.
#
# 분리 경계 (D-9 audit 2026-05-08): views.py 2,223L → views.py + views_hit_report.py.
# helpers (_jwt_required, _tenant_required, _is_tenant_admin, _hit_report_writable)는
# views.py 모듈 SSOT 그대로. 외부 API contract / URL 변경 0.

from __future__ import annotations

import logging

from django.http import JsonResponse, HttpResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from .models import (
    MatchupDocument,
    MatchupHitReport,
    MatchupHitReportEntry,
    MatchupProblem,
)
from .serializers import MatchupHitReportSerializer
from .views import (
    _jwt_required,
    _tenant_required,
    _is_tenant_staff,
    _is_tenant_admin,
    _hit_report_writable,
)

logger = logging.getLogger(__name__)

try:
    from apps.infrastructure.storage.r2 import (
        upload_fileobj_to_r2_storage,
        generate_presigned_get_url_storage,
    )
except ImportError:
    upload_fileobj_to_r2_storage = None
    generate_presigned_get_url_storage = None


# ── Curated Hit Report (강사 1인의 매치업 적중 보고서) ────────────
#
# 정체성 (정정 2026-05-03):
#   매치업 보고서 = 프리랜서 강사 1인이 작성하는 3중 역할 산출물.
#     ① 수업 히스토리 (강사 자기 검토)
#     ② 제출 리포트 (소속 학원에 정기 제출하는 KPI)
#     ③ 신뢰자료+홍보물 (신규 학원/카페에서 강사 개인 브랜딩)
#   카테고리당 시험지 1장 + 강사 1명 = 보고서 1건. 강사 N명이 각자 보고서 작성 가능.

@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportListView(View):
    """GET /api/v1/matchup/hit-reports/

    강사 1인 보고서 누적 리스트. 본인 보고서 + 학원 admin/owner는 전체 조회 가능.

    Query params:
      mine=1              : 본인 작성 보고서만 (admin/owner도 본인 시점만)
      status=draft|submitted (선택)
      category=str        (선택)

    Response:
      {
        "reports": [
          { id, document_id, document_title, document_category,
            author_id, author_name, title, status, submitted_at,
            exam_count, curated_count, curated_progress, ... },
          ...
        ],
        "summary": { total, submitted, drafts }
      }
    """

    def get(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        qs = MatchupHitReport.objects.filter(tenant=request.tenant).select_related(
            "document", "author",
        )

        # 일반 강사(admin/owner 아님)는 항상 본인 보고서만. admin/owner는 mine=1로 명시 시에만.
        is_admin = _is_tenant_admin(request)
        mine = (request.GET.get("mine") or "").lower() in ("1", "true", "yes")
        user_id = getattr(getattr(request, "user", None), "id", None)
        if not is_admin or mine:
            if user_id:
                qs = qs.filter(author_id=user_id)
            else:
                qs = qs.none()

        status_filter = (request.GET.get("status") or "").strip().lower()
        if status_filter in ("draft", "submitted"):
            qs = qs.filter(status=status_filter)

        category_filter = (request.GET.get("category") or "").strip()
        if category_filter:
            qs = qs.filter(document__category=category_filter)

        reports = list(qs.order_by("-updated_at")[:200])

        # 작성 진행률 = entries 중 selected_problem_ids 또는 comment 있는 것을 카운트.
        # JSONField __len 필터는 backend별 호환성 issue 있어 Python 루프로 정직하게 산출.
        from .models import MatchupHitReportEntry
        all_entries = list(MatchupHitReportEntry.objects.filter(
            tenant=request.tenant, report_id__in=[r.id for r in reports],
        ).select_related("exam_problem").only(
            "id", "report_id", "selected_problem_ids", "comment", "excluded",
            "exam_problem__id", "exam_problem__embedding", "exam_problem__image_embedding",
            "exam_problem__text", "exam_problem__meta",
        ))
        curated_by_report: dict = {}
        for e in all_entries:
            # excluded(PDF 제외) entry는 진행률·적중률 모두에서 제외 — PDF SSOT와 동기.
            if getattr(e, "excluded", False):
                continue
            if (e.selected_problem_ids or []) or (e.comment or "").strip():
                curated_by_report[e.report_id] = curated_by_report.get(e.report_id, 0) + 1

        # 적중률(hit_rate) 산출 — sim≥0.75인 큐레이션 자료를 1건 이상 보유한 문항 비율.
        # PDF 표지 헤드라인과 동일 정의. list endpoint에서 노출하면 강사 통산 KPI 즉시 가시화.
        # 알고리즘: bulk fetch (selected_problem_ids 합집합) → 메모리 dict로 cosine 계산 → entry별 max sim ≥ 0.75 카운트.
        all_sel_ids: set = set()
        for e in all_entries:
            for pid in (e.selected_problem_ids or []):
                try:
                    all_sel_ids.add(int(pid))
                except (TypeError, ValueError):
                    pass
        sel_meta_by_id: dict = {}
        if all_sel_ids:
            for p in MatchupProblem.objects.filter(
                tenant=request.tenant, id__in=list(all_sel_ids),
            ).only("id", "embedding", "image_embedding", "meta", "text"):
                sel_meta_by_id[p.id] = p

        from .pdf_report import _compute_display_sim, _TYPE_HIT
        hit_count_by_report: dict = {}
        for e in all_entries:
            if getattr(e, "excluded", False):
                continue
            sel_ids = e.selected_problem_ids or []
            if not sel_ids:
                continue
            ep = e.exam_problem
            for pid in sel_ids:
                cand = sel_meta_by_id.get(int(pid)) if isinstance(pid, int) else None
                if not cand:
                    continue
                sim = _compute_display_sim(ep, cand)
                if sim is not None and sim >= _TYPE_HIT:  # 0.75
                    hit_count_by_report[e.report_id] = hit_count_by_report.get(e.report_id, 0) + 1
                    break  # 문항당 1번만

        rows = []
        total_hit = 0
        total_exam = 0
        for r in reports:
            doc = r.document
            exam_count = doc.problem_count if doc else 0
            curated_count = curated_by_report.get(r.id, 0)
            curated_progress = (curated_count / exam_count * 100.0) if exam_count else 0.0
            hit_count = hit_count_by_report.get(r.id, 0)
            hit_rate = (hit_count / exam_count * 100.0) if exam_count else 0.0
            total_hit += hit_count
            total_exam += exam_count

            author_name = ""
            if r.author_id and r.author is not None:
                author_name = (
                    getattr(r.author, "name", None)
                    or getattr(r.author, "username", "")
                    or getattr(r.author, "email", "")
                ) or ""
                # username 내부 prefix 제거 (t{tid}_ 제거).
                from apps.core.models.user import user_display_username
                if author_name == getattr(r.author, "username", ""):
                    author_name = user_display_username(r.author) or author_name
            elif r.submitted_by_name:
                author_name = r.submitted_by_name

            rows.append({
                "id": r.id,
                "document_id": r.document_id,
                "document_title": doc.title if doc else "",
                "document_category": doc.category if doc else "",
                "author_id": r.author_id,
                "author_name": author_name,
                "title": r.title,
                "status": r.status,
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                "exam_count": exam_count,
                "curated_count": curated_count,
                "curated_progress": round(curated_progress, 1),
                "hit_count": hit_count,
                "hit_rate": round(hit_rate, 1),
                # share_token 활성 여부만 노출 (token 자체는 노출 X — 학원장이 chip 클릭 시 generate API로 fetch).
                # frontend chip이 "활성/비활성" 표기 분기에만 사용.
                "has_share_token": bool(r.share_token),
                "created_at": r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
            })

        # 통산 적중률 = 모든 보고서의 hit_count 합 / exam_count 합. 강사 1인 누적 KPI.
        avg_hit_rate = (total_hit / total_exam * 100.0) if total_exam else 0.0
        summary = {
            "total": len(rows),
            "submitted": sum(1 for r in rows if r["status"] == "submitted"),
            "drafts": sum(1 for r in rows if r["status"] == "draft"),
            "avg_hit_rate": round(avg_hit_rate, 1),
            "total_hit": total_hit,
            "total_exam": total_exam,
        }
        return JsonResponse({"reports": rows, "summary": summary})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportDraftView(View):
    """GET /api/v1/matchup/documents/<doc_id>/hit-report-draft/

    시험지 doc + 호출자(강사) 기준 적중 보고서 조회. 없으면 자동 draft 생성(author=호출자).
    같은 시험지에 강사 N명이 각자 보고서를 만들 수 있고, 본 응답은 호출자 본인 것만 반환.
    응답에 시험지 problem 목록 + 후보 매치(강사 본인 자료 + 공용 풀) 포함.
    """

    def get(self, request, doc_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant=request.tenant)
        except MatchupDocument.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        # source_type 가드 제거 (2026-05-06 fix).
        #   배경: 이전엔 source_type/document_role/upload_intent 셋 중 하나가 시험지가
        #   아니면 400 차단. 자동 backfill 분류와 학원장 의도가 충돌해 "이미 만든 보고서
        #   진입"까지 차단되는 결함 (T2 doc 206 / report 26 등). 손들어간 hit_report
        #   데이터는 살아있는데 UI 접근만 막혀 가치 0.
        #   가드의 본래 목적("학습자료 doc에 보고서 만들기 방지")은 frontend MatchupPage
        #   의 "시험지 마킹" 토글 + 보고서 버튼 노출 조건이 이미 담당. backend hard-block
        #   은 부수효과만 큼. 학원장이 학습자료에 보고서 만들겠다 결정한 건 본인 의지로 신뢰.

        # 강사 scope: 같은 시험지에 강사별로 별개 보고서. 작성자 본인 보고서를 가져온다.
        # admin/owner가 doc 진입 시: author=user로 자기 보고서 작성. 기존 다른 강사 보고서는 영향 없음.
        report, _ = MatchupHitReport.objects.get_or_create(
            tenant=request.tenant,
            document=doc,
            author=getattr(request, "user", None),
            defaults={"title": doc.title or ""},
        )

        # 시험지 problems
        exam_problems = list(
            doc.problems.order_by("number").only(
                "id", "number", "text", "image_key", "embedding",
            )
        )
        # entry 미리 로드
        entries_by_pid = {
            e.exam_problem_id: e
            for e in report.entries.all()
        }

        # 자동 후보 매치 (find_similar_problems) — 카테고리 격리 적용됨.
        # 큐레이션 보고서 작성자는 자동 top_k=5보다 많은 후보를 보고 직접 골라야 정확도가 올라감
        # 학원장 결함 fix (2026-05-05): "15문항 이외의 다른 문항도 떴으면" — 사용자가
        # 일부 제외 시 추가 후보 자동 노출 위해 candidate_top_k 15 → 30 확장.
        # (운영 보고: 5개 후보로는 부족 — 학원장 직접 선정 워크플로우 지원).
        from .services import find_similar_problems
        candidate_top_k = 30

        # 병렬 후보 검색 — 시험지 27 문항 × 직렬 ~50s 게이트웨이 컷 회피.
        # 8 worker 동시 vector 검색 → ~6배 단축. 각 검색은 독립 read-only.
        from concurrent.futures import ThreadPoolExecutor

        sim_by_eid: dict = {}
        tenant_id = request.tenant.id
        # 저작권 격리: 보고서 작성자(강사) 본인 자료 + 공용 풀(author=NULL legacy)만 후보.
        # admin/owner가 작성 중인 보고서면 본인 자료 + legacy 풀. 작성자 외 access 시
        # _hit_report_writable이 차단하므로 여기까지 도달하지 않음.
        scope_author_id = getattr(getattr(request, "user", None), "id", None)

        def _fetch_candidates(ep_id: int):
            try:
                return ep_id, find_similar_problems(
                    problem_id=ep_id, tenant_id=tenant_id, top_k=candidate_top_k,
                    author_id=scope_author_id,
                )
            except Exception:
                logger.exception("find_similar_problems failed (problem=%s)", ep_id)
                return ep_id, []

        with ThreadPoolExecutor(max_workers=8) as pool:
            for ep_id, sim_results in pool.map(_fetch_candidates, [ep.id for ep in exam_problems]):
                sim_by_eid[ep_id] = sim_results

        problem_data = []
        all_candidate_ids = set()
        for ep in exam_problems:
            entry = entries_by_pid.get(ep.id)
            cand = []
            sim_results = sim_by_eid.get(ep.id, [])
            for cp, sim in sim_results:
                # page_index 노출 (2026-05-11): 학원장이 보고서에서 후보 "다시 자르기"
                # 진입 시 ManualCropModal 의 initialPage 로 활용 → thumbnail 클릭 1단계 절감.
                # cp.meta 는 dict 또는 None. page_index 미존재 시 None (frontend null 처리).
                cp_meta = cp.meta if isinstance(cp.meta, dict) else {}
                page_index = cp_meta.get("page_index")
                cand.append({
                    "id": cp.id,
                    "document_id": cp.document_id,
                    "number": cp.number,
                    "text_preview": (cp.text or "")[:120],
                    "similarity": round(sim, 4),
                    "image_key": cp.image_key,
                    "page_index": int(page_index) if isinstance(page_index, int) else None,
                })
                all_candidate_ids.add(cp.id)

            problem_data.append({
                "id": ep.id,
                "number": ep.number,
                "text_preview": (ep.text or "")[:200],
                "image_key": ep.image_key,
                "candidates": cand,
                "entry": (
                    {
                        "id": entry.id,
                        "selected_problem_ids": entry.selected_problem_ids or [],
                        "comment": entry.comment or "",
                        "order": entry.order,
                        "excluded": bool(entry.excluded),
                    }
                    if entry else None
                ),
            })

        # presigned URL 일괄 — 시험지 problem + 후보 problem
        url_map: dict = {}
        if generate_presigned_get_url_storage:
            for ep in exam_problems:
                if ep.image_key and ep.image_key not in url_map:
                    url_map[ep.image_key] = generate_presigned_get_url_storage(
                        key=ep.image_key, expires_in=3600,
                    )
            # 후보 image_keys
            cand_keys = set()
            for pd in problem_data:
                for c in pd["candidates"]:
                    if c["image_key"]:
                        cand_keys.add(c["image_key"])
            # 사용자 명시 선택 problem (자동 후보에 없을 수도) — 보강
            extra_qs = MatchupProblem.objects.filter(
                tenant=request.tenant,
                id__in=[
                    pid for e in entries_by_pid.values()
                    for pid in (e.selected_problem_ids or [])
                ],
            ).only("id", "image_key", "document_id", "number", "text", "meta")
            extra_meta = {p.id: p for p in extra_qs}
            for p in extra_qs:
                if p.image_key:
                    cand_keys.add(p.image_key)
            for k in cand_keys:
                if k not in url_map:
                    url_map[k] = generate_presigned_get_url_storage(
                        key=k, expires_in=3600,
                    )
            for pd in problem_data:
                if pd["image_key"]:
                    pd["image_url"] = url_map.get(pd["image_key"])
                for c in pd["candidates"]:
                    if c["image_key"]:
                        c["image_url"] = url_map.get(c["image_key"])
        else:
            extra_meta = {}

        # 후보/선택 자료의 출처 식별 메타 — 강사가 "자료 198번"만 보고 출처를 직접
        # 찾는 불편(2026-05-05 사용자 보고)을 제거하기 위해 파일명·카테고리·source_type을
        # 응답에 포함. 1 query in_bulk로 N+1 회피. 후보·extra_meta·exam doc 모두 동일 lookup.
        doc_ids: set = {ep.document_id for ep in exam_problems}
        for pd in problem_data:
            for c in pd["candidates"]:
                doc_ids.add(c["document_id"])
        for p in extra_meta.values():
            doc_ids.add(p.document_id)

        doc_meta_by_id: dict = {}
        if doc_ids:
            for d in MatchupDocument.objects.filter(
                tenant=request.tenant, id__in=doc_ids,
            ).only("id", "title", "category", "meta"):
                src = ""
                if isinstance(d.meta, dict):
                    src = str(
                        d.meta.get("source_type")
                        or d.meta.get("upload_intent")
                        or ""
                    )
                doc_meta_by_id[d.id] = {
                    "document_title": d.title or "",
                    "document_category": d.category or "",
                    "source_type": src,
                }

        def _doc_label(doc_id: int) -> dict:
            return doc_meta_by_id.get(doc_id) or {
                "document_title": "", "document_category": "", "source_type": "",
            }

        for pd in problem_data:
            for c in pd["candidates"]:
                c.update(_doc_label(c["document_id"]))

        return JsonResponse({
            "report": MatchupHitReportSerializer(report).data,
            "exam_problems": problem_data,
            "selected_problem_meta": [
                {
                    "id": p.id, "document_id": p.document_id,
                    "number": p.number,
                    "text_preview": (p.text or "")[:120],
                    "image_key": p.image_key,
                    "image_url": url_map.get(p.image_key) if p.image_key else None,
                    "page_index": (
                        int((p.meta or {}).get("page_index"))
                        if isinstance(p.meta, dict)
                        and isinstance((p.meta or {}).get("page_index"), int)
                        else None
                    ),
                    **_doc_label(p.document_id),
                }
                for p in extra_meta.values()
            ],
        })


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportDetailView(View):
    """
    PATCH  /api/v1/matchup/hit-reports/<id>/         — title/summary 수정
    POST   /api/v1/matchup/hit-reports/<id>/entries/ — 엔트리 일괄 upsert
    POST   /api/v1/matchup/hit-reports/<id>/submit/  — 학원 제출 (status=submitted)
    DELETE /api/v1/matchup/hit-reports/<id>/         — 삭제

    저작권 격리: 모든 조작은 작성자 본인 또는 학원 admin/owner만 가능 (_hit_report_writable).
    """

    def _get(self, request, report_id):
        try:
            return MatchupHitReport.objects.select_related("document").get(
                id=report_id, tenant=request.tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return None

    def patch(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        report = self._get(request, report_id)
        if not report:
            return JsonResponse({"detail": "Not found"}, status=404)
        # 저작권 격리: 작성자 본인 또는 학원 admin/owner만 수정 가능.
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 수정할 수 없습니다."},
                status=403,
            )
        if report.status == "submitted" and not _is_tenant_admin(request):
            return JsonResponse(
                {
                    "detail": "제출 완료된 보고서는 수정할 수 없습니다. "
                              "관리자에게 재작성을 요청하세요.",
                    "code": "submitted_locked",
                },
                status=403,
            )

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        update_fields = ["updated_at"]
        if "title" in body and isinstance(body["title"], str):
            report.title = body["title"][:255]
            update_fields.append("title")
        if "summary" in body and isinstance(body["summary"], str):
            report.summary = body["summary"]
            update_fields.append("summary")
        report.save(update_fields=update_fields)
        return JsonResponse(MatchupHitReportSerializer(report).data)

    def delete(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        report = self._get(request, report_id)
        if not report:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 삭제할 수 없습니다."},
                status=403,
            )
        if report.status == "submitted" and not _is_tenant_admin(request):
            return JsonResponse(
                {
                    "detail": "제출 완료된 보고서는 삭제할 수 없습니다. "
                              "관리자에게 요청하세요.",
                    "code": "submitted_locked",
                },
                status=403,
            )
        report.delete()
        return JsonResponse({"ok": True})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportEntriesUpsertView(View):
    """POST /api/v1/matchup/hit-reports/<id>/entries/

    body: {
      entries: [
        { exam_problem_id: int, selected_problem_ids: [int],
          comment: str, order: int }, ...
      ]
    }
    upsert (report, exam_problem) 단위. 빈 selected + 빈 comment면 삭제.
    """

    def post(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.select_related("document").get(
                id=report_id, tenant=request.tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        # 저작권 격리: 작성자 본인 또는 admin/owner만 entries 수정.
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 수정할 수 없습니다."},
                status=403,
            )

        # Submit lock (P1, 2026-05-05): submitted 보고서는 entries 수정 불가.
        # 학원에 제출한 KPI 자료가 임의 변경되지 않도록 보호. 재작성 필요시
        # 별도 endpoint (status=draft 복귀) 또는 admin이 manual 처리.
        # admin/owner는 우회 가능 — 학원 측 수정 권한 (예: 강사 퇴사 후 보고서 보완).
        if report.status == "submitted" and not _is_tenant_admin(request):
            return JsonResponse(
                {
                    "detail": "제출 완료된 보고서는 수정할 수 없습니다. "
                              "관리자에게 재작성을 요청하세요.",
                    "code": "submitted_locked",
                },
                status=403,
            )

        import json
        try:
            body = json.loads(request.body) if request.body else {}
        except Exception:
            return JsonResponse({"detail": "Invalid JSON"}, status=400)

        entries = body.get("entries")
        if not isinstance(entries, list):
            return JsonResponse({"detail": "entries 배열이 필요합니다."}, status=400)

        # exam_problem_id가 같은 doc의 problem인지 검증 (cross-tenant/cross-doc 차단)
        exam_problem_ids = [int(e.get("exam_problem_id", 0)) for e in entries]
        valid_exam_pids = set(
            MatchupProblem.objects
            .filter(tenant=request.tenant, document=report.document, id__in=exam_problem_ids)
            .values_list("id", flat=True)
        )

        # selected_problem_ids는 보고서 작성자 본인 자료 + 공용 풀(legacy author=NULL)만 허용.
        # 다른 강사 자료를 자기 보고서에 박는 동선 차단 = 저작권 분리.
        # admin/owner는 검증 차원에서 전체 풀 가능 (request.user 본인이 author 본인 케이스 포함).
        all_selected = set()
        for e in entries:
            for pid in (e.get("selected_problem_ids") or []):
                try:
                    all_selected.add(int(pid))
                except (TypeError, ValueError):
                    pass
        from django.db.models import Q
        selected_qs = MatchupProblem.objects.filter(
            tenant=request.tenant, id__in=all_selected,
        )
        if report.author_id and not _is_tenant_admin(request):
            selected_qs = selected_qs.filter(
                Q(document__author_id=report.author_id)
                | Q(document__author__isnull=True)
                | Q(document__isnull=True)  # exam-source problem은 author 무관
            )
        valid_selected = set(selected_qs.values_list("id", flat=True))

        # 트랜잭션 atomic — 학원장 큐레이션의 부분 커밋 방지 (entry1 save 성공,
        # entry2 FK 실패 시 양쪽 모두 rollback). 학원장 데이터 무결성 supreme.
        from django.db import transaction
        from .services import pin_problems_as_owner_curated

        upserted = 0
        deleted = 0
        ids_to_pin: set = set()
        with transaction.atomic():
            for e in entries:
                try:
                    exam_pid = int(e.get("exam_problem_id"))
                except (TypeError, ValueError):
                    continue
                if exam_pid not in valid_exam_pids:
                    continue
                sel = [
                    pid for pid in (e.get("selected_problem_ids") or [])
                    if isinstance(pid, int) and pid in valid_selected
                ]
                comment = (e.get("comment") or "")[:5000]
                excluded = bool(e.get("excluded", False))
                try:
                    order = int(e.get("order", 0))
                except (TypeError, ValueError):
                    order = 0

                if not sel and not comment.strip() and not excluded:
                    # 빈 엔트리(선택/코멘트/PDF 제외 의사 모두 없음) → 기존 삭제
                    d, _ = MatchupHitReportEntry.objects.filter(
                        report=report, exam_problem_id=exam_pid,
                    ).delete()
                    deleted += d
                    continue

                # Stage 2 (2026-05-06): selected_problem_ids 변경 immutable guard.
                # update_or_create 대신 명시적 fetch + history append + save 분리.
                entry, _ = MatchupHitReportEntry.objects.get_or_create(
                    tenant=request.tenant,
                    report=report,
                    exam_problem_id=exam_pid,
                    defaults={
                        "selected_problem_ids": [],
                        "comment": "",
                        "order": 0,
                        "excluded": False,
                    },
                )
                by_user_id = getattr(request, "user", None)
                by_user_id = by_user_id.id if by_user_id and getattr(by_user_id, "is_authenticated", False) else None
                entry.append_selection_history(
                    new_selected_ids=sel,
                    by_user_id=by_user_id,
                    source="user_ui",
                    reason="HitReportEntriesUpsertView upsert",
                )
                entry._change_source = "user_ui"
                entry.selected_problem_ids = sel
                entry.comment = comment
                entry.order = order
                entry.excluded = excluded
                entry.save()
                upserted += 1
                # selected_problem_ids 가리키는 problem 모두 dangling 사고 보호 대상.
                # entry 가 sel 을 가지는 한 retry_document/reanalyze 가 hard delete X.
                ids_to_pin.update(sel)

            # 트랜잭션 안에서 owner-curated pin 마킹 — 부분 pin 방지.
            # 5/6 사고의 진짜 write-side: entry write 와 problem.meta.manual_owner_pinned
            # 가 동일 트랜잭션. 학원장 selected 토글 즉시 보호.
            if ids_to_pin:
                pin_problems_as_owner_curated(
                    tenant_id=request.tenant.id,
                    problem_ids=list(ids_to_pin),
                )

            # report.updated_at 갱신
            report.save(update_fields=["updated_at"])
        return JsonResponse({"upserted": upserted, "deleted": deleted})


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportSubmitView(View):
    """POST /api/v1/matchup/hit-reports/<id>/submit/

    학원장 mental model (2026-05-11 정정): submit = **학원 홈페이지에 게시**.
    1인 학원 (강사=학원장) 케이스에서 "학원 KPI 제출" 단어가 논리적 모순이라 정정.
    내부 status='submitted' 표식은 유지하되 액션 의미는 publish 통합.

    동작:
      1. report.status='submitted' + submitted_at/by 기록 (내부 표식, schema 변경 0)
      2. body publish_to_landing=True (default) — 자동으로 학원 홈페이지 hit_reports
         섹션에 add + landing publish (toggle_hit_report_on_landing helper 재사용)
      3. alimtalk — 1인 학원 silent suppress (author 외 owner/admin 0명이면 noise)
      4. 응답: report + landing_url + total_published + published_to_landing

    body params:
      - publish_to_landing: bool=True — 게시 토글 (학원장이 게시 안 원하면 False).
    """

    def post(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.select_related(
                "document", "author",
            ).get(id=report_id, tenant=request.tenant)
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 게시할 수 없습니다."},
                status=403,
            )

        # body parse — publish_to_landing default True (학원장 mental model = submit이 게시)
        publish_to_landing = True
        try:
            import json as _json
            body = _json.loads((request.body or b"").decode("utf-8") or "{}")
            if isinstance(body, dict) and "publish_to_landing" in body:
                publish_to_landing = bool(body.get("publish_to_landing"))
        except (ValueError, TypeError, UnicodeDecodeError):
            pass

        # 중복 발송 방지: status 전이(draft→submitted) 1회만 알림. 이미 submitted 호출 시 알림 skip.
        was_already_submitted = report.status == "submitted"

        from django.utils import timezone
        report.status = "submitted"
        report.submitted_at = timezone.now()
        user = getattr(request, "user", None)
        if user is not None:
            # author FK가 비어있던 legacy report 백필 — 제출 시점에 작성자 식별.
            if not report.author_id:
                report.author = user
            report.submitted_by_id = getattr(user, "id", None)
            full = (
                getattr(user, "name", None)
                or getattr(user, "username", None)
                or getattr(user, "email", "")
            )
            report.submitted_by_name = (full or "")[:100]
        report.save(update_fields=[
            "status", "submitted_at", "submitted_by_id", "submitted_by_name", "author", "updated_at",
        ])

        # 학원 홈페이지 자동 게시 — submit 의미 통합 (학원장 mental model).
        # 권한: owner/admin (학원장) 만. 강사 권한은 status 변경만 (게시는 학원장 책임).
        # fail-soft: 게시 실패해도 submit 자체는 성공 (상태 보호).
        landing_info = {
            "published_to_landing": False,
            "total_published": 0,
            "landing_url": "",
            "landing_error": "",
        }
        if publish_to_landing and _is_tenant_admin(request):
            try:
                from apps.core.views_landing import (
                    toggle_hit_report_on_landing,
                    LandingHitReportError,
                )
                try:
                    res = toggle_hit_report_on_landing(
                        request.tenant, report.id, action="add",
                        auto_publish=True,
                    )
                    landing_info["published_to_landing"] = bool(res.get("registered"))
                    landing_info["total_published"] = int(res.get("total_registered") or 0)
                    landing_info["landing_url"] = f"/landing/reports/{report.id}"
                except LandingHitReportError as e:
                    landing_info["landing_error"] = e.detail
                    logger.warning(
                        "HIT_REPORT_LANDING_PUBLISH_FAILED | report=%s | %s",
                        report.id, e.detail,
                    )
            except Exception:
                logger.exception(
                    "HIT_REPORT_LANDING_PUBLISH_UNEXPECTED | report=%s", report.id,
                )

        # B-2: 학원 owner/admin에게 알림톡 (학원별 AutoSendConfig 토글 — 기본 OFF).
        # 첫 제출 1회만 발송 (status 재진입 보호). 1인 학원 silent suppress (_notify 내부 처리).
        if not was_already_submitted:
            try:
                _notify_hit_report_submitted(report, request)
            except Exception:
                logger.exception("HIT_REPORT_NOTIFY_FAILED | report_id=%s", report.id)

        payload = MatchupHitReportSerializer(report).data
        if isinstance(payload, dict):
            payload.update(landing_info)
        return JsonResponse(payload)


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportUnsubmitView(View):
    """POST /api/v1/matchup/hit-reports/<id>/unsubmit/

    제출 잠금 해제 — submitted 보고서를 다시 draft로 복귀.
    작성자 본인 또는 학원 admin/owner. 실수로 submit 클릭한 케이스 셀프 복구
    (2026-05-11 박철T 사고 — admin manage.py shell 없이는 못 풀어내는 product 결함).

    정책: submitted_by_id/name 히스토리는 유지 (재제출 시 덮어씀). submitted_at만 None.
    알림톡 발송 X (학원장이 "보고서 사라졌네"라고 인지하지 못해도 OK — 1인 강사 학원 다수).
    """

    def post(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.select_related(
                "document", "author",
            ).get(id=report_id, tenant=request.tenant)
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 잠금 해제할 수 없습니다."},
                status=403,
            )
        if report.status != "submitted":
            return JsonResponse(
                {
                    "detail": "이미 작성 중인 보고서입니다.",
                    "code": "not_submitted",
                },
                status=400,
            )

        report.status = "draft"
        report.submitted_at = None
        report.save(update_fields=["status", "submitted_at", "updated_at"])

        # 게시 의미 통합 (2026-05-11): unsubmit = 게시 취소 + 편집. 학원 홈페이지
        # hit_reports 섹션에서도 자동 제거 (학원장 mental model 일관). fail-soft —
        # 게시판 제거 실패해도 status 복귀 자체는 성공.
        landing_info = {
            "unpublished_from_landing": False,
            "total_published": 0,
            "landing_error": "",
        }
        if _is_tenant_admin(request):
            try:
                from apps.core.views_landing import (
                    toggle_hit_report_on_landing,
                    LandingHitReportError,
                )
                try:
                    res = toggle_hit_report_on_landing(
                        request.tenant, report.id, action="remove",
                        auto_publish=True,
                    )
                    landing_info["unpublished_from_landing"] = not bool(res.get("registered"))
                    landing_info["total_published"] = int(res.get("total_registered") or 0)
                except LandingHitReportError as e:
                    landing_info["landing_error"] = e.detail
                    logger.warning(
                        "HIT_REPORT_LANDING_UNPUBLISH_FAILED | report=%s | %s",
                        report.id, e.detail,
                    )
            except Exception:
                logger.exception(
                    "HIT_REPORT_LANDING_UNPUBLISH_UNEXPECTED | report=%s", report.id,
                )

        logger.info(
            "HIT_REPORT_UNSUBMIT | report_id=%s tenant_id=%s author_id=%s by_user_id=%s",
            report.id, report.tenant_id, report.author_id,
            getattr(getattr(request, "user", None), "id", None),
        )
        payload = MatchupHitReportSerializer(report).data
        if isinstance(payload, dict):
            payload.update(landing_info)
        return JsonResponse(payload)


def _notify_hit_report_submitted(report, request) -> None:
    """매치업 보고서 학원 제출 시 owner/admin 알림톡 발송.

    정책 (사용자 결정 2026-05-03):
      - AutoSendConfig 토글 — 기본 OFF, 학원이 messaging 설정에서 ON 시에만.
      - 수신자: 해당 tenant의 owner/admin 권한자 모두 (멀티 admin 학원 케이스 대응).
      - 중복 방지: status draft→submitted 전이 시점 1회만 (호출자 측 가드 + 발송 로그).
      - 발송 실패는 silent — 본 endpoint(submit)의 성공/실패와 분리.

    재사용: TYPE_SCORE 템플릿 (메모리 `community_alimtalk` 패턴, 신규 카카오 검수 회피).
    """
    from apps.domains.messaging.selectors import get_auto_send_config
    from apps.domains.messaging.services import enqueue_sms
    from apps.domains.messaging.alimtalk_content_builders import (
        get_solapi_template_id, build_unified_replacements,
    )
    from apps.domains.messaging.policy import is_messaging_disabled
    from apps.core.models import TenantMembership

    trigger = "matchup_report_submitted"
    tenant = report.tenant
    tenant_id = tenant.id

    if is_messaging_disabled(tenant_id):
        logger.info("hit_report_notify skipped: tenant %s messaging disabled", tenant_id)
        return

    config = get_auto_send_config(tenant_id, trigger)
    if not config or not config.enabled:
        logger.debug(
            "hit_report_notify skipped: trigger=%s tenant=%s (config disabled or missing)",
            trigger, tenant_id,
        )
        return

    template = config.template
    template_body = (template.body if template else "") or (
        "강사가 매치업 적중 보고서를 제출했습니다.\n"
        "어드민 → 매치업에서 보고서 inbox를 확인해 주세요."
    )

    tenant_name = (tenant.name or "").strip() or "학원"
    site_url = "https://hakwonplus.com"
    if tenant.code:
        site_url = f"https://{tenant.code}.hakwonplus.com"

    author_name = ""
    if report.author_id and report.author is not None:
        from apps.core.models.user import user_display_username
        author_name = (
            getattr(report.author, "name", None)
            or user_display_username(report.author)
            or ""
        )
    if not author_name:
        author_name = report.submitted_by_name or "강사"

    doc = report.document
    doc_title = (doc.title if doc else "") or "시험지"
    doc_category = (doc.category if doc else "") or ""

    # ITEM_LIST 슬롯 매핑 — score 템플릿 재사용 ("강의명"=학교/카테고리, "차시명"=시험지+강사)
    context = {
        "강의명": (doc_category or doc_title)[:30],
        "차시명": f"{doc_title[:20]}  ·  {author_name} 강사"[:30],
    }

    # 수신자 — owner/admin 멀티 (TenantMembership active)
    memberships = list(
        TenantMembership.objects.filter(
            tenant=tenant, is_active=True, role__in=["owner", "admin"],
        ).select_related("user").only(
            "user__id", "user__name", "user__username", "user__phone",
        )
    )
    if not memberships:
        logger.info("hit_report_notify: no owner/admin in tenant %s", tenant_id)
        return

    # 1인 학원 silent suppress (2026-05-11 학원장 mental model 정정):
    # 1인 학원 (강사=학원장) 케이스에서 author 가 유일한 owner/admin 이면
    # 본인 자신에게 "본인이 게시했습니다" 알림 = noise. 멀티 owner/admin 학원에서만
    # 발송 (강사 → 학원 owner KPI 보고 의미 살아있음).
    if report.author_id:
        non_author_recipients = [
            m for m in memberships
            if getattr(getattr(m, "user", None), "id", None) != report.author_id
        ]
        if not non_author_recipients:
            logger.info(
                "hit_report_notify suppressed: solo academy (author=%s sole owner/admin), tenant=%s",
                report.author_id, tenant_id,
            )
            return
        memberships = non_author_recipients

    solapi_tid = get_solapi_template_id(trigger)
    sent_count = 0
    sent_user_ids: list[int] = []
    for m in memberships:
        u = getattr(m, "user", None)
        if not u:
            continue
        phone = (getattr(u, "phone", "") or "").replace("-", "").strip()
        if not phone:
            logger.debug(
                "hit_report_notify: user %s has no phone, skip", getattr(u, "id", "?"),
            )
            continue

        recipient_name = getattr(u, "name", None) or getattr(u, "username", "") or ""

        sms_kwargs = dict(
            tenant_id=tenant_id,
            to=phone,
            text=template_body,
            message_mode="alimtalk",
        )
        if solapi_tid:
            replacements = build_unified_replacements(
                trigger=trigger,
                content_body=template_body,
                context=context,
                tenant_name=tenant_name,
                student_name=recipient_name,  # score 템플릿 수신자 슬롯
                site_url=site_url,
            )
            sms_kwargs["template_id"] = solapi_tid
            sms_kwargs["alimtalk_replacements"] = replacements

        try:
            ok = enqueue_sms(**sms_kwargs)
            if ok:
                sent_count += 1
                sent_user_ids.append(u.id)
        except Exception as e:
            logger.warning(
                "hit_report_notify enqueue failed: report=%s user=%s err=%s",
                report.id, u.id, e,
            )

    # 발송 로그 — meta에 영구 기록 (운영 감사 추적용. 본 컬럼은 신규 추가 없이 jsonb meta 활용 가능하나
    # MatchupHitReport는 meta 필드가 없으므로 logger.info만 남긴다).
    logger.info(
        "HIT_REPORT_NOTIFIED | tenant=%s report=%s author=%s recipients=%d/%d user_ids=%s",
        tenant_id, report.id, report.author_id, sent_count, len(memberships), sent_user_ids,
    )


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportPdfView(View):
    """GET /api/v1/matchup/hit-reports/<id>/curated.pdf

    강사 1인 적중 보고서 PDF — 수업 히스토리 + 학원 KPI + 신뢰자료/홍보물 3중 역할.
    표지(작성 강사 + 적중률 요약) + 각 문항(좌:학생 시험지 / 우:강사 수업자료 + 지도 코멘트).
    """

    def get(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.select_related("document", "author").get(
                id=report_id, tenant=request.tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        # 보고서는 강사 1인의 산출물 — 본인 또는 학원 admin/owner만 PDF 다운로드 가능.
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 다운로드할 수 없습니다."},
                status=403,
            )

        try:
            from .pdf_report import generate_curated_hit_report_pdf
            pdf_bytes = generate_curated_hit_report_pdf(report)
        except Exception:
            logger.exception("curated_hit_report_pdf failed (report=%s)", report.id)
            return JsonResponse({"detail": "PDF 생성 실패"}, status=500)

        from urllib.parse import quote
        title = report.title or report.document.title or f"matchup-hitreport-{report.id}"
        safe_name = quote(title[:80])
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = (
            f"attachment; filename=\"matchup-hitreport-{report.id}.pdf\"; "
            f"filename*=UTF-8''{safe_name}.pdf"
        )
        resp["Cache-Control"] = "private, no-cache"
        return resp


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportZipExportView(View):
    """GET /api/v1/matchup/hit-reports/<id>/share.zip

    카페·블로그 게시용 raw asset 패키지 — 강사가 PDF 그대로 가져다 쓸 수 있게.
      - pages/page_001.png ... page_N.png : 페이지별 PNG (PDF 페이지 1:1 변환)
      - cover.png                          : 표지 이미지 (page_001 alias)
      - summary.md                         : 강사명/학원/시험지/적중률/문항 코멘트 markdown
      - README.txt                         : 카페 게시 가이드

    PDF은 학원 제출용 정식 산출물 / ZIP은 강사가 카페에 자유 게시 시 paste·업로드용.
    외부 공유 link(R-C C-1)는 별개 — 본 endpoint도 staff 인증 필요. zip은 강사가
    수동 다운로드 후 본인 명의로 카페에 게시.
    """

    def get(self, request, report_id):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)
        try:
            report = MatchupHitReport.objects.select_related("document", "author").get(
                id=report_id, tenant=request.tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not _hit_report_writable(request, report):
            return JsonResponse(
                {"detail": "다른 강사의 보고서는 다운로드할 수 없습니다."},
                status=403,
            )

        try:
            zip_bytes = _build_hit_report_share_zip(report)
        except Exception:
            logger.exception("hit_report_share_zip failed (report=%s)", report.id)
            return JsonResponse({"detail": "ZIP 생성 실패"}, status=500)

        from urllib.parse import quote
        title = report.title or report.document.title or f"matchup-hitreport-{report.id}"
        safe_name = quote(title[:80])
        resp = HttpResponse(zip_bytes, content_type="application/zip")
        resp["Content-Disposition"] = (
            f"attachment; filename=\"matchup-hitreport-{report.id}-share.zip\"; "
            f"filename*=UTF-8''{safe_name}-카페공유.zip"
        )
        resp["Cache-Control"] = "private, no-cache"
        return resp


def _build_hit_report_share_zip(report) -> bytes:
    """PDF → 페이지별 PNG + summary.md + README.txt → in-memory ZIP.

    PyMuPDF로 PDF 페이지 → 200dpi PNG 변환. 이미 PDF 생성 로직(이미지 prefetch +
    레이아웃)을 재사용하므로 ZIP 생성은 PDF 1회 빌드 + 페이지 렌더 비용.
    """
    import io
    import zipfile
    from datetime import datetime

    from .pdf_report import generate_curated_hit_report_pdf, _compute_display_sim
    from academy.adapters.tools.pymupdf_renderer import PdfDocument

    pdf_bytes = generate_curated_hit_report_pdf(report)

    # PDF → 페이지별 PNG (200 dpi — 카페 업로드 시 화질 충분, 사이즈 적정).
    page_pngs: list[bytes] = []
    pdf_temp = io.BytesIO(pdf_bytes)
    pdf_temp.seek(0)
    import tempfile
    import os
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(pdf_bytes)
        with PdfDocument(tmp_path) as doc_pdf:
            for i in range(doc_pdf.page_count()):
                page_img = doc_pdf.render_page(i, dpi=200)
                buf = io.BytesIO()
                page_img.save(buf, "PNG", optimize=True)
                page_pngs.append(buf.getvalue())
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # summary.md — 카페 본문에 paste 가능한 markdown
    document = report.document
    tenant = document.tenant
    tenant_name = (tenant.name or "").strip() or "학원"

    author_name = ""
    if report.author_id and report.author is not None:
        try:
            from apps.core.models.user import user_display_username
            author_name = (
                getattr(report.author, "name", None)
                or user_display_username(report.author)
                or ""
            ).strip()
        except Exception:
            author_name = ""
    if not author_name:
        author_name = report.submitted_by_name or ""

    issued_at = (
        report.submitted_at.strftime("%Y년 %m월 %d일") if report.submitted_at
        else datetime.now().strftime("%Y년 %m월 %d일")
    )

    # 적중률 산출 — PDF 표지와 동일 정의
    exam_problems = list(
        document.problems.exclude(image_key="").order_by("number")
    )
    entries_by_eid = {e.exam_problem_id: e for e in report.entries.all()}
    all_sel_ids = set()
    for e in entries_by_eid.values():
        for pid in (e.selected_problem_ids or []):
            try:
                all_sel_ids.add(int(pid))
            except (TypeError, ValueError):
                pass
    sel_meta = {}
    if all_sel_ids:
        for p in MatchupProblem.objects.filter(
            tenant=tenant, id__in=list(all_sel_ids),
        ).only("id", "embedding", "image_embedding", "meta", "text", "number", "document_id"):
            sel_meta[p.id] = p

    hit_count = 0
    for ep in exam_problems:
        e = entries_by_eid.get(ep.id)
        sel_ids = (e.selected_problem_ids if e else []) or []
        for pid in sel_ids:
            cand = sel_meta.get(int(pid)) if isinstance(pid, int) else None
            if not cand:
                continue
            sim = _compute_display_sim(ep, cand)
            if sim is not None and sim >= 0.75:
                hit_count += 1
                break
    total_q = len(exam_problems)
    hit_rate = (hit_count / total_q * 100) if total_q else 0.0

    md_lines: list[str] = []
    md_lines.append(f"# {report.title or document.title or '매치업 적중 보고서'}")
    md_lines.append("")
    md_lines.append(f"- **학원**: {tenant_name}")
    if author_name:
        md_lines.append(f"- **강사**: {author_name}")
    md_lines.append(f"- **시험**: {document.title or ''}")
    if document.category:
        md_lines.append(f"- **카테고리**: {document.category}")
    md_lines.append(f"- **발행일**: {issued_at}")
    md_lines.append(f"- **매치업 적중률**: {hit_rate:.1f}%  (전체 {total_q}문항 중 {hit_count}문항이 학원 자료와 75%+ 유사)")
    md_lines.append("")

    if (report.summary or "").strip():
        md_lines.append("## 보고서 요약")
        md_lines.append("")
        md_lines.append(report.summary.strip())
        md_lines.append("")

    md_lines.append("## 문항별 코멘트")
    md_lines.append("")
    for ep in exam_problems:
        e = entries_by_eid.get(ep.id)
        comment = ((e.comment if e else "") or "").strip()
        if not comment:
            continue
        md_lines.append(f"### Q{ep.number}")
        md_lines.append("")
        md_lines.append(comment)
        md_lines.append("")

    md_lines.append("---")
    md_lines.append("")
    md_lines.append(f"_본 보고서는 {tenant_name}의 매치업 적중 분석 결과입니다._")
    summary_md = "\n".join(md_lines).encode("utf-8")

    # README.txt — 사용 가이드
    readme_lines = [
        "매치업 적중 보고서 — 카페/블로그 공유용 패키지",
        "",
        "구성:",
        "  pages/page_001.png  ~  page_NNN.png  : 페이지별 PNG (PDF와 동일 양식)",
        "  cover.png                            : 표지 (page_001 alias)",
        "  summary.md                           : 카페 본문에 paste 가능한 markdown 요약",
        "  README.txt                           : 본 안내 파일",
        "",
        "사용:",
        "  1. summary.md 내용을 카페 글 본문에 복사·붙여넣기",
        "  2. pages/*.png 또는 cover.png을 카페 에디터에 이미지 업로드",
        "     (네이버 카페·블로그 모두 PNG 직접 업로드 지원)",
        "  3. 본 자료는 강사 본인 명의로 자유롭게 게시 가능",
        "",
        "주의:",
        "  - 본 ZIP은 작성 강사 또는 학원 owner/admin만 다운로드 가능",
        "  - 학원의 다른 강사 자료가 포함되었을 수 있으니 게시 전 확인",
    ]
    readme_txt = "\n".join(readme_lines).encode("utf-8")

    # ZIP 패키징
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, png in enumerate(page_pngs, start=1):
            zf.writestr(f"pages/page_{i:03d}.png", png)
        if page_pngs:
            zf.writestr("cover.png", page_pngs[0])  # 페이지 1 alias = 표지
        zf.writestr("summary.md", summary_md)
        zf.writestr("README.txt", readme_txt)

    return zip_buf.getvalue()


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class DocumentHitReportPdfView(View):
    """폐기됨 (deprecated). 자동 PDF는 큐레이션 보고서로 대체.

    URL은 backward compat 위해 유지하되 410 Gone 반환.
    프론트엔드 버튼은 이미 제거됨.
    """

    def get(self, request, doc_id):
        return JsonResponse(
            {"detail": "자동 적중 PDF는 폐기되었습니다. 큐레이션 보고서를 사용하세요."},
            status=410,
        )


# ── admin 포탈 widget — 학원 홈페이지 게시 적중보고서 mini list ────────────

@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportBoardPreviewView(View):
    """GET /api/v1/matchup/hit-reports/board-preview/?limit=5

    admin 포탈 widget — 학원 홈페이지에 게시된 적중보고서 mini list.
    적중보고서 탭(HitReportListPage) 상단 띠에서 cafe 게시판 분위기로 노출.

    학원장 mental model (2026-05-11): "작성/관리(admin) ↔ 학원 게시판(landing) ↔ 외부 공유"
    단일 흐름. submit 후 결과 즉시 확인 + 게시판 entry 차례 시각화.

    동작:
      - LandingPage.published_config.sections[hit_reports].items 순회
      - tenant 격리 + owner/admin 권한
      - 응답 schema: HitReportLandingPublicView 와 align (frontend 재사용)
        + author_name, landing_url, published_at(섹션 진입 시각 추정)
      - limit 1~12 (MAX_REPORTS 정합), default 5

    fail-soft: 게시판 정보 없으면 빈 list 반환 (404 X — UI 그래도 띠 자리 보존).
    """

    def get(self, request):
        if not _is_tenant_staff(request):
            return JsonResponse({"detail": "Staff only"}, status=403)

        # limit clamp 1..12 (chip toggle MAX_REPORTS 정합)
        try:
            limit = int(request.GET.get("limit") or 5)
        except ValueError:
            limit = 5
        limit = max(1, min(limit, 12))

        # 학원 LandingPage.published_config 의 hit_reports section items
        ordered_ids: list[int] = []
        try:
            from apps.core.models import LandingPage
            try:
                lp = LandingPage.objects.get(tenant=request.tenant, is_published=True)
            except LandingPage.DoesNotExist:
                lp = None
            if lp is not None:
                pub = lp.published_config or {}
                for sec in (pub.get("sections") or []):
                    if sec.get("type") != "hit_reports":
                        continue
                    if not sec.get("enabled"):
                        continue
                    for it in (sec.get("items") or [])[:limit]:
                        try:
                            ordered_ids.append(int(it.get("report_id")))
                        except (TypeError, ValueError):
                            continue
                    break
        except Exception:
            logger.exception(
                "HIT_REPORT_BOARD_PREVIEW_LP_FETCH_FAIL | tenant=%s",
                getattr(request.tenant, "id", "?"),
            )

        if not ordered_ids:
            return JsonResponse({"reports": [], "total_published": 0})

        # 카드 메타 fetch — HitReportLandingPublicView 와 동일 schema (frontend 재사용)
        reports = list(
            MatchupHitReport.objects.filter(
                tenant=request.tenant, id__in=ordered_ids,
            ).select_related("document", "author")
        )

        entries = MatchupHitReportEntry.objects.filter(
            tenant=request.tenant,
            report_id__in=[r.id for r in reports],
            excluded=False,
        ).only("id", "report_id", "selected_problem_ids", "comment")
        curated_count: dict = {}
        for e in entries:
            if (e.selected_problem_ids or []) or (e.comment or "").strip():
                curated_count[e.report_id] = curated_count.get(e.report_id, 0) + 1

        result = []
        for r in reports:
            doc = r.document
            total = (doc.problem_count if doc else 0) or 0
            hit = curated_count.get(r.id, 0)
            rate = round((hit / total * 100) if total else 0, 1)
            author_name = ""
            if r.author is not None:
                try:
                    from apps.core.models.user import user_display_username
                    author_name = (
                        getattr(r.author, "name", None)
                        or user_display_username(r.author)
                        or ""
                    )
                except Exception:
                    author_name = getattr(r.author, "username", "") or ""
            if not author_name:
                author_name = r.submitted_by_name or ""

            result.append({
                "id": r.id,
                "doc_title": (doc.title if doc else "") or "",
                "doc_category": (doc.category if doc else "") or "",
                "hit_count": hit,
                "total_problems": total,
                "hit_rate_pct": rate,
                "author_name": author_name[:40],
                "title": (r.title or "")[:200],
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "landing_url": f"/landing/reports/{r.id}",
            })
        # 게시 순서 (LandingPage items 순서) 보존
        order_map = {rid: i for i, rid in enumerate(ordered_ids)}
        result.sort(key=lambda x: order_map.get(x["id"], 9999))

        return JsonResponse({
            "reports": result,
            "total_published": len(ordered_ids),
        })


# ── 공개 랜딩 페이지용 적중보고서 카드 메타 ────────────
# 학원장이 자기 랜딩 페이지에 매치업 적중 사례를 마케팅 카드로 노출하는 용도.
# 인증 없음(공개), tenant 격리는 subdomain 기반 _tenant_required로 강제.
# 카드 메타만 노출(시험명/학교/적중수/총문항수). PDF/이미지 본문은 노출 안 함.

@method_decorator([csrf_exempt, _tenant_required], name="dispatch")
class HitReportLandingPublicView(View):
    """GET /api/v1/matchup/landing/public/?ids=1,2,3

    공개 랜딩 페이지용 적중보고서 카드 메타.

    - **테넌트 격리 절대**: subdomain → tenant resolve. 다른 tenant의 보고서 ID 요청해도 무조건 빈 결과.
    - **노출 데이터 최소화**: 카드 메타(시험명/카테고리/적중수/총문항수/적중률)만. entry 본문/PDF/이미지 일체 노출 안 함.
    - **상한**: 한 요청에 최대 12개 ID.
    - 응답 순서는 ids 파라미터 순서 보존.
    """

    def get(self, request):
        ids_param = (request.GET.get("ids") or "").strip()
        if not ids_param:
            return JsonResponse({"reports": []})
        try:
            ids = [int(x) for x in ids_param.split(",") if x.strip()]
        except ValueError:
            return JsonResponse({"reports": []})
        ids = ids[:12]

        reports = list(
            MatchupHitReport.objects.filter(
                tenant=request.tenant, id__in=ids,
            ).select_related("document")
        )

        # 적중수 = excluded=False entry 중 selected_problem_ids 또는 comment가 있는 것.
        # HitReportListView의 curated_by_report 정의와 동일.
        entries = MatchupHitReportEntry.objects.filter(
            tenant=request.tenant,
            report_id__in=[r.id for r in reports],
            excluded=False,
        ).only("id", "report_id", "selected_problem_ids", "comment")
        curated_count: dict = {}
        for e in entries:
            if (e.selected_problem_ids or []) or (e.comment or "").strip():
                curated_count[e.report_id] = curated_count.get(e.report_id, 0) + 1

        result = []
        for r in reports:
            doc = r.document
            total = (doc.problem_count if doc else 0) or 0
            hit = curated_count.get(r.id, 0)
            rate = round((hit / total * 100) if total else 0, 1)
            result.append({
                "id": r.id,
                "doc_title": (doc.title if doc else "") or "",
                "doc_category": (doc.category if doc else "") or "",
                "hit_count": hit,
                "total_problems": total,
                "hit_rate_pct": rate,
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        # 요청 ID 순서 보존
        order_map = {rid: i for i, rid in enumerate(ids)}
        result.sort(key=lambda x: order_map.get(x["id"], 999))
        return JsonResponse({"reports": result})


def _is_report_in_published_landing(tenant, report_id: int) -> bool:
    """학원장이 자기 published 랜딩의 hit_reports section에 박은 보고서 ID인지 확인.

    이게 True인 보고서만 외부 학부모/학생에게 본문(PDF) 공개. 학원장 picker 빼면 즉시 비공개.
    """
    from apps.core.models import LandingPage
    try:
        landing = LandingPage.objects.get(tenant=tenant, is_published=True)
    except LandingPage.DoesNotExist:
        return False
    pub = landing.published_config or {}
    for sec in (pub.get("sections") or []):
        if sec.get("type") != "hit_reports" or not sec.get("enabled"):
            continue
        for it in (sec.get("items") or []):
            try:
                if int(it.get("report_id")) == int(report_id):
                    return True
            except (TypeError, ValueError):
                continue
    return False


from django.views.decorators.clickjacking import xframe_options_exempt as _xframe_exempt


def _resolve_landing_pdf_tenant(request):
    """iframe raw GET (X-Tenant-Code 헤더 없음) 대응 — ?tenant=<code> query param 우선.

    P0 fix (2026-05-12): API host(api.hakwonplus.com) middleware 가 default tenant
    (hakwonplus T1)으로 resolve 하므로 request.tenant 우선 path 는 학원 도메인 iframe
    호출 시 다른 tenant 의 보고서로 매칭 시도 → 404. 학원 frontend 가 부착한
    ?tenant=<code> 가 진짜 의도 신호이므로 query 우선.

    공개 endpoint: `_is_report_in_published_landing` 가 그 tenant 의 published 보고서만
    노출 → 임의 tenant 지정해도 학원장이 picker 박은 ID 만 접근 (보안 영향 0).
    """
    code = (request.GET.get("tenant") or "").strip()
    if code:
        try:
            from apps.core.models import Tenant
            t = Tenant.objects.filter(code=code, is_active=True).first()
            if t:
                return t
        except Exception:
            pass
    return getattr(request, "tenant", None)


@method_decorator([csrf_exempt, _xframe_exempt], name="dispatch")
class HitReportLandingPublicPdfView(View):
    """GET /api/v1/matchup/landing/public/<report_id>/curated.pdf?tenant=<code>

    학원 공개 랜딩에서 카드 클릭 시 노출되는 보고서 본문 PDF.

    - 인증 X (외부 학부모/학생 대상)
    - iframe embed 허용 (xframe_options_exempt) — 학원 도메인 hover thumbnail + 상세 페이지 PDF viewer.
    - tenant 결정: request.tenant(헤더/host) 우선 → ?tenant query param fallback (iframe raw GET 대응).
    - **공개 게이트**: 학원장이 자기 published 랜딩의 hit_reports section에 직접 picker로 등록한 ID만.
      picker에서 빼는 즉시 비공개 (다른 보고서 본문 노출 차단).
    - tenant 격리: 다른 tenant 보고서는 무조건 404.
    - PDF 응답: 시험지 문항 ↔ 강사 매칭 자료 좌우 비교 + 강사 코멘트.
    """

    def get(self, request, report_id):
        tenant = _resolve_landing_pdf_tenant(request)
        if tenant is None:
            return JsonResponse({"detail": "Tenant required"}, status=400)
        if not _is_report_in_published_landing(tenant, report_id):
            return JsonResponse({"detail": "Not found"}, status=404)
        try:
            report = MatchupHitReport.objects.select_related("document", "author").get(
                id=report_id, tenant=tenant,
            )
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            from .pdf_report import generate_curated_hit_report_pdf
            pdf_bytes = generate_curated_hit_report_pdf(report)
        except Exception:
            logger.exception("public_landing_pdf failed (report=%s)", report.id)
            return JsonResponse({"detail": "PDF 생성 실패"}, status=500)

        from urllib.parse import quote
        title = report.title or (report.document.title if report.document else "") or f"hit-report-{report.id}"
        safe_name = quote(title[:80])
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        # inline → 브라우저에서 바로 미리보기 (다운로드 아님). 학부모가 새 탭에서 즉시 확인.
        resp["Content-Disposition"] = (
            f"inline; filename=\"hit-report-{report.id}.pdf\"; "
            f"filename*=UTF-8''{safe_name}.pdf"
        )
        # 학원장이 picker에서 빼면 즉시 비공개돼야 함 — public cache 비활성, 브라우저 short-cache만.
        resp["Cache-Control"] = "private, no-cache, must-revalidate"
        # iframe embed 허용 (학원 도메인 hover preview + 상세 페이지 viewer). xframe_options_exempt와 함께 보강.
        if "X-Frame-Options" in resp:
            del resp["X-Frame-Options"]
        return resp


# ── 1클릭 공유 토큰 (#67, 2026-05-12) ─────────────────────────────────
#
# 학원장 spec:
#   선생이 학생/학부모한테 카톡으로 "이거 보면 돼" 링크만 보내면, 학생은 로그인이나
#   학원 가입 없이 클릭 한 번으로 PDF 본문 즉시 확인.
#
# 보안 모델:
#   - share_token = UUID4 (전역 unique, 추측 불가). DB index.
#   - 학원장/admin/author 만 생성·회전·취소.
#   - public endpoint 는 token 만으로 통과 (picker 등록 무관). 학원장이 회전/취소하면 즉시 차단.
#   - PDF 본문은 카드 메타와 다르게 카탈로그(다른 학원 노출)에 절대 안 들어감.

def _can_manage_share_token(request, report) -> bool:
    """share_token 생성/회전/취소 권한.

    - tenant admin/owner: 무조건 가능.
    - author 본인: 가능 (강사 자기 보고서 한정).
    """
    if _is_tenant_admin(request):
        return True
    user = getattr(request, "user", None)
    if user and report.author_id and report.author_id == getattr(user, "id", None):
        return True
    return False


@method_decorator([csrf_exempt, _jwt_required, _tenant_required], name="dispatch")
class HitReportShareLinkView(View):
    """POST/DELETE /api/v1/matchup/hit-reports/<report_id>/share-link/

    POST   : 없으면 generate, 있으면 그대로 반환. ?rotate=1 이면 신규 UUID 로 회전.
    DELETE : share_token 제거 (취소). 이후 token URL 403.

    Response (POST):
      { "share_token": "<uuid>", "share_url": "/landing/share/<uuid>", "rotated": bool }
    """

    def post(self, request, report_id):
        try:
            report = MatchupHitReport.objects.get(id=report_id, tenant=request.tenant)
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not _can_manage_share_token(request, report):
            return JsonResponse({"detail": "Forbidden"}, status=403)

        import uuid
        rotate = (request.GET.get("rotate") or "").strip() in {"1", "true", "yes"}
        had_token = bool(report.share_token)
        rotated = False
        created = False
        if not report.share_token or rotate:
            report.share_token = uuid.uuid4()
            report.save(update_fields=["share_token", "updated_at"])
            rotated = bool(rotate) and had_token
            created = not had_token  # 첫 발급 vs 회전 vs 기존

        return JsonResponse({
            "share_token": str(report.share_token),
            "share_url": f"/landing/share/{report.share_token}",
            "rotated": rotated,
            "created": created,
        })

    def delete(self, request, report_id):
        try:
            report = MatchupHitReport.objects.get(id=report_id, tenant=request.tenant)
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)
        if not _can_manage_share_token(request, report):
            return JsonResponse({"detail": "Forbidden"}, status=403)

        if report.share_token:
            report.share_token = None
            report.save(update_fields=["share_token", "updated_at"])
        return JsonResponse({"share_token": None, "share_url": None})


@method_decorator([csrf_exempt], name="dispatch")
class HitReportShareMetaView(View):
    """GET /api/v1/matchup/share/<uuid:token>/

    공개 share 메타. 인증/테넌트 X. token UUID 만으로 통과.

    Response:
      { id, title, doc_title, doc_category, hit_count, total_problems,
        hit_rate_pct, author_name, submitted_at, created_at, tenant_name,
        tenant_code, pdf_url }
    """

    def get(self, request, token):
        try:
            report = MatchupHitReport.objects.select_related(
                "document", "author", "tenant",
            ).get(share_token=token)
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        doc = report.document
        total = (doc.problem_count if doc else 0) or 0

        # 적중 수 — HitReportLandingPublicView 와 동일 정의.
        entries = MatchupHitReportEntry.objects.filter(
            tenant=report.tenant_id,
            report_id=report.id,
            excluded=False,
        ).only("id", "selected_problem_ids", "comment")
        hit = 0
        for e in entries:
            if (e.selected_problem_ids or []) or (e.comment or "").strip():
                hit += 1
        rate = round((hit / total * 100) if total else 0, 1)

        author_name = ""
        if report.author is not None:
            try:
                from apps.core.models.user import user_display_username
                author_name = (
                    getattr(report.author, "name", None)
                    or user_display_username(report.author)
                    or ""
                )
            except Exception:
                author_name = getattr(report.author, "username", "") or ""
        if not author_name:
            author_name = report.submitted_by_name or ""

        tenant = report.tenant

        # "다른 보고서" 섹션 — 학원 published landing 의 hit_reports section items 중 같은 본 보고서 제외.
        # 학생이 카톡 링크 진입 후 자연스럽게 학원 다른 적중 사례로 확장 둘러보기.
        other_ids: list[int] = []
        try:
            from apps.core.models import LandingPage
            lp = LandingPage.objects.filter(tenant=tenant, is_published=True).first()
            if lp:
                pub = lp.published_config or {}
                for sec in (pub.get("sections") or []):
                    if sec.get("type") != "hit_reports" or not sec.get("enabled"):
                        continue
                    for it in (sec.get("items") or []):
                        try:
                            rid = int(it.get("report_id"))
                            if rid and rid != report.id:
                                other_ids.append(rid)
                        except (TypeError, ValueError):
                            continue
                    break
        except Exception:
            other_ids = []
        other_ids = other_ids[:6]

        # 다른 보고서 카드 메타 inline — frontend가 별도 /matchup/landing/public 호출 안 하게 끔.
        # 학생 share page 첫 진입 latency: 2 round-trip → 1 round-trip (#67 cycle 9, 2026-05-12).
        other_reports: list[dict] = []
        if other_ids:
            sib_reports = list(
                MatchupHitReport.objects.filter(
                    tenant=tenant, id__in=other_ids,
                ).select_related("document")
            )
            sib_entries = MatchupHitReportEntry.objects.filter(
                tenant=tenant,
                report_id__in=[r.id for r in sib_reports],
                excluded=False,
            ).only("id", "report_id", "selected_problem_ids", "comment")
            sib_curated: dict = {}
            for e in sib_entries:
                if (e.selected_problem_ids or []) or (e.comment or "").strip():
                    sib_curated[e.report_id] = sib_curated.get(e.report_id, 0) + 1
            # 응답 순서 = ordered_ids 보존 (학원장이 picker에 박은 순서).
            by_id = {r.id: r for r in sib_reports}
            for rid in other_ids:
                r = by_id.get(rid)
                if not r:
                    continue
                d = r.document
                tot = (d.problem_count if d else 0) or 0
                ht = sib_curated.get(r.id, 0)
                rp = round((ht / tot * 100) if tot else 0, 1)
                other_reports.append({
                    "id": r.id,
                    "doc_title": (d.title if d else "") or "",
                    "doc_category": (d.category if d else "") or "",
                    "hit_count": ht,
                    "total_problems": tot,
                    "hit_rate_pct": rp,
                })

        return JsonResponse({
            "id": report.id,
            "title": (report.title or "")[:200],
            "doc_title": (doc.title if doc else "") or "",
            "doc_category": (doc.category if doc else "") or "",
            "hit_count": hit,
            "total_problems": total,
            "hit_rate_pct": rate,
            "author_name": author_name[:40],
            "submitted_at": report.submitted_at.isoformat() if report.submitted_at else None,
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "tenant_name": getattr(tenant, "display_name", "") or getattr(tenant, "name", "") or "",
            "tenant_code": getattr(tenant, "code", "") or "",
            "pdf_url": f"/api/v1/matchup/share/{token}/curated.pdf",
            # backward-compat: 기존 frontend가 other_report_ids로 fetch 했음. inline cards 추가로 round-trip 1회 절약.
            "other_report_ids": other_ids,
            "other_reports": other_reports,
        })


@method_decorator([csrf_exempt, _xframe_exempt], name="dispatch")
class HitReportSharePdfView(View):
    """GET /api/v1/matchup/share/<uuid:token>/curated.pdf

    공개 share PDF. token UUID 만으로 통과 (picker 등록 무관).
    iframe embed 허용. 학원장이 회전/취소하면 즉시 차단.
    """

    def get(self, request, token):
        try:
            report = MatchupHitReport.objects.select_related("document", "author", "tenant").get(
                share_token=token,
            )
        except MatchupHitReport.DoesNotExist:
            return JsonResponse({"detail": "Not found"}, status=404)

        try:
            from .pdf_report import generate_curated_hit_report_pdf
            pdf_bytes = generate_curated_hit_report_pdf(report)
        except Exception:
            logger.exception("share_pdf failed (report=%s token=%s)", report.id, token)
            return JsonResponse({"detail": "PDF 생성 실패"}, status=500)

        from urllib.parse import quote
        title = report.title or (report.document.title if report.document else "") or f"hit-report-{report.id}"
        safe_name = quote(title[:80])
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = (
            f"inline; filename=\"hit-report-{report.id}.pdf\"; "
            f"filename*=UTF-8''{safe_name}.pdf"
        )
        resp["Cache-Control"] = "private, no-cache, must-revalidate"
        if "X-Frame-Options" in resp:
            del resp["X-Frame-Options"]
        return resp

# PATH: apps/domains/matchup/services.py
# 매치업 비즈니스 로직 — 유사도 검색, R2 정리, 재시도

from __future__ import annotations

import logging
import os
from typing import List, Tuple

from apps.shared.utils.vector import cosine_similarity
from .models import MatchupDocument, MatchupProblem

logger = logging.getLogger(__name__)

try:
    from apps.infrastructure.storage.r2 import delete_object_r2_storage
except ImportError:
    delete_object_r2_storage = None  # type: ignore


# ── Heuristic reranker 가중치 ───────────────────────────
#
# V2 측정(15 케이스)에서 발견된 부작용으로 V2.5 보수화:
#  - format_match=0.12가 같은 시험지 essay-essay 트랩을 강화 → 0.0
#  - length_norm=0.06이 정제 후 짧아진 텍스트에 부정적 영향 → 0.0
#  - sim 비중 ↑, cross_doc만 살려 서답형 트랩 약화 (다른 시험지 우선)
# 휴리스틱은 여기까지. 80%+ 도약은 cross-encoder reranker (Phase 2)에서.
_W_SIM = 1.0         # V2.6: 휴리스틱 전부 비활성 — 직접 측정에서 휴리스틱이
_W_FORMAT = 0.0      #        top1 외에 top2/3 회복을 망침. 순수 sim으로 회귀.
_W_LENGTH = 0.0      #
_W_CROSS_DOC = 0.0   #

# Phase 2 cross-encoder 토글 (기본 OFF).
# bge-reranker-base는 한국어 시험 문제 의미를 잘 못 잡아 V2.6 56% → 40% 후퇴.
# v2-m3-ko로 재시도하려면 EBS 8GB→20GB 확장 필요.
# 운영 중 활성화: SSM에서 환경변수 MATCHUP_USE_CROSS_ENCODER=1 + ASG refresh.
_USE_CROSS_ENCODER = os.environ.get("MATCHUP_USE_CROSS_ENCODER", "0") == "1"


def _format_of(problem: MatchupProblem) -> str:
    """problem의 meta에서 format 추출. 미설정이면 텍스트로 즉석 감지(레거시)."""
    meta = problem.meta or {}
    fmt = meta.get("format")
    if fmt in ("essay", "choice"):
        return fmt
    text = problem.text or ""
    return "essay" if any(
        marker in text[:20] for marker in ("[서답형", "[ 서답형", "[서 답형", "[ 서 답형", "서논술형")
    ) else "choice"


def _length_score(src_len: int, cand_len: int) -> float:
    """텍스트 길이 비율 점수. 비슷한 길이일수록 1.0, 차이 클수록 0."""
    if src_len <= 0 or cand_len <= 0:
        return 0.5  # 정보 부족 — 중립
    short, long_ = sorted([src_len, cand_len])
    return short / long_


def find_similar_problems(
    problem_id: int, tenant_id: int, top_k: int = 10,
    author_id: int | None = None,
) -> List[Tuple["MatchupProblem", float]]:
    """주어진 문제와 유사한 문제를 찾아 재정렬해 반환.

    Pipeline:
      1. bi-encoder cosine으로 후보 점수화 (DB의 embedding)
      2. 휴리스틱 신호(sim·cross_doc) 결합 → 1차 정렬
      3. (가능 시) cross-encoder reranker로 상위 후보 재정렬 — phase 2
      4. top_k 반환

    author_id (저작권 격리, 2026-05-03~):
      매치업 보고서 = 강사 1인 포트폴리오 정체성. 작성 강사가 본인 자료만 후보로
      받게 author 필터링. 단, document.author=NULL legacy 자료는 모든 강사가
      공용 풀로 사용 가능 (구버전 데이터 보호).
      None=필터 없음 (학원 owner/admin이 전체 풀 검색하는 케이스 등).

    Returns: [(problem, final_score), ...] 높은 순.
    """
    try:
        source = MatchupProblem.objects.get(id=problem_id, tenant_id=tenant_id)
    except MatchupProblem.DoesNotExist:
        return []

    if not source.embedding:
        return []

    source_category = ""
    if source.document_id and source.document is not None:
        source_category = (source.document.category or "").strip()

    candidates = (
        MatchupProblem.objects
        .filter(tenant_id=tenant_id, embedding__isnull=False)
        .exclude(id=problem_id)
        # low_quality 게이트 (P0-2, 2026-05-04): 자동 품질 점수 < 0.7 cell은
        # 매치업 검색 후보에서 제외. 학원에 잘못된 매칭 결과 전달 차단.
        # 학원장 검수 UI에서 직접 manual crop으로 보정 후 매치업에 노출 가능.
        #
        # CRITICAL fix (Phase 8, 2026-05-05):
        #   기존 `.exclude(meta__low_quality=True)` 는 PostgreSQL 3-valued logic
        #   결함으로 NULL/missing 키 행을 모두 제외 (NOT NULL = NULL → WHERE 제외).
        #   T2 14797/14804 problems가 low_quality 키 없음 → 풀 항상 0건.
        #   학원장 매치업 작동률 0% 의 진짜 본질. `.exclude(meta__contains=...)` 는
        #   `meta @> '{...}'::jsonb` SQL 사용 → 정확히 매칭되는 행만 제외 (NULL 통과).
        .exclude(meta__contains={"low_quality": True})
        # 추천 pool 자동 필터 (Phase 4, 2026-05-05):
        #   MatchupDocument.meta.indexable=False 인 doc 의 problem 풀 진입 차단.
        #   callbacks._handle_matchup_ai_result가 bbox_null_ratio 기반으로 마커 부여:
        #     precise_split / coarse_split → indexable=True
        #     page_fallback / needs_review / no_problems → indexable=False
        #   page_fallback doc 의 페이지 임베딩이 매치업 풀에 노이즈로 들어가
        #   추천 0% 결함을 만들었던 결함 (2026-05-05 학원장 실측) fix.
        #   동일 NULL safety — `meta__contains` 사용으로 legacy doc 안전 통과.
        .exclude(document__meta__contains={"indexable": False})
        .defer("created_at", "updated_at")
    )

    # 저작권 격리 — author_id 지정 시 본인 자료 + 공용 풀(legacy author=NULL)만.
    # exam-source problem(document=None)은 별도 필터에서 처리되므로 여기서는 영향 X.
    if author_id is not None:
        from django.db.models import Q
        candidates = candidates.filter(
            Q(document__author_id=author_id) | Q(document__author__isnull=True)
        )

    # 텍스트 + 이미지 ensemble 가중치. OCR 짧은 source는 이미지 유사도 비중 ↑.
    # 짧은 텍스트(< 60자)면 이미지 0.5, 긴 텍스트(>= 200자)면 이미지 0.15.
    # 환경변수 override 가능: MATCHUP_IMAGE_SIM_WEIGHT
    src_text_len_for_w = len((source.text or "").strip())
    if src_text_len_for_w < 60:
        _img_w = 0.5
    elif src_text_len_for_w < 200:
        _img_w = 0.3
    else:
        _img_w = 0.15
    import os as _osw
    try:
        _img_w = float(_osw.environ.get("MATCHUP_IMAGE_SIM_WEIGHT", "") or _img_w)
    except ValueError:
        pass
    _txt_w = max(0.0, 1.0 - _img_w)
    src_img_emb = source.image_embedding

    # source 의 source_type 식별 — 시험지(test) vs 자료(reference) 분기.
    # 학원장 실측 갭 fix (2026-05-05):
    #   기존: 모든 source 가 같은 카테고리 내에서만 추천 → 박철T 같이 카테고리당 doc
    #   1~몇 개 분포면 시험지 source 의 매칭 풀 ≈ 0. 매치업 자동 추천 작동률 0%.
    #   변경: 시험지 source(school_exam_pdf / student_exam_photo)는 카테고리 격리 해제.
    #   시험지 카테고리(학교/시험일정)와 자료 카테고리(강의 회차)는 다른 분류 체계라
    #   격리하면 매칭 절대 불가. author_id 격리(강사 1인 SSOT) + low_quality 제외만 유지.
    #   자료(academy_workbook/commercial_workbook 등) source 끼리 매칭은 같은 강의
    #   단원 안에서 의미 있으므로 카테고리 격리 유지.
    is_test_source = False
    if source.document_id and source.document is not None:
        meta = source.document.meta or {}
        # 7-value source_type SSOT — legacy 2-value도 매핑되어 들어옴.
        from apps.domains.matchup.source_types import normalize_source_type
        st = normalize_source_type(meta.get("source_type") or meta.get("upload_intent") or meta.get("document_role"))
        is_test_source = st in ("school_exam_pdf", "student_exam_photo")
        # 시험지 doc 의 자기 doc 안 problem 은 sim≈1 self-doc trap 이라 항상 제외.
        if is_test_source:
            candidates = candidates.exclude(document_id=source.document_id)

    # 자료 source 는 같은 카테고리 안에서만. 시험지 source 는 author 풀 전체.
    # exam source(document_id=None)는 어차피 별도 처리.
    if source.document_id and not is_test_source:
        candidates = candidates.filter(
            document__isnull=False,
            document__category=source_category,
        )
    elif source.document_id and is_test_source:
        # 시험지 source — reference 자료 풀(matchup doc 전체)에서 검색.
        # author_id 격리는 위에서 이미 적용됨 (line 105~109).
        candidates = candidates.filter(document__isnull=False)

    src_format = _format_of(source)
    src_len = len(source.text or "")
    src_doc_id = source.document_id

    # 1차: bi-encoder + 휴리스틱 + 이미지 ensemble — numpy vectorized.
    # 운영 사고(2026-04-29 사용자 보고): T2 problem 5,717개 매 클릭마다 Python 순회로
    # cosine 5,717번 → 매치업 페이지 클릭 시 렉. numpy bulk dot product로 100배 가속.
    cand_list = list(
        candidates.only(
            "id", "document_id", "embedding", "image_embedding",
            "meta", "text",
        )
    )
    if not cand_list:
        return []

    try:
        import numpy as np
    except ImportError:
        np = None  # type: ignore

    if np is not None:
        # 텍스트 임베딩 stack
        emb_dim = len(source.embedding)
        valid_idx = []
        for i, c in enumerate(cand_list):
            if c.embedding and len(c.embedding) == emb_dim:
                valid_idx.append(i)
        if not valid_idx:
            return []
        cand_list = [cand_list[i] for i in valid_idx]

        E = np.asarray(
            [c.embedding for c in cand_list], dtype=np.float32,
        )  # (N, D)
        s = np.asarray(source.embedding, dtype=np.float32)  # (D,)
        s_n = float(np.linalg.norm(s)) or 1.0
        E_n = np.linalg.norm(E, axis=1)
        E_n = np.where(E_n <= 0, 1.0, E_n)
        text_sims = (E @ s) / (E_n * s_n)
        text_sims = np.clip(text_sims, -1.0, 1.0)
        text_sims = (text_sims + 1.0) / 2.0  # cosine_similarity()와 동일 정규화

        # 이미지 임베딩 ensemble — 양쪽 보유한 인덱스만 결합
        sims = text_sims.copy()
        if src_img_emb:
            src_img = np.asarray(src_img_emb, dtype=np.float32)
            si_n = float(np.linalg.norm(src_img)) or 1.0
            img_sims = np.zeros(len(cand_list), dtype=np.float32)
            has_img = np.zeros(len(cand_list), dtype=bool)
            for i, c in enumerate(cand_list):
                ie = c.image_embedding
                if not ie or len(ie) != len(src_img):
                    continue
                ie_arr = np.asarray(ie, dtype=np.float32)
                ie_n = float(np.linalg.norm(ie_arr)) or 1.0
                raw = float(np.dot(ie_arr, src_img) / (ie_n * si_n))
                img_sims[i] = max(0.0, min(1.0, (max(-1.0, min(1.0, raw)) + 1.0) / 2.0))
                has_img[i] = True
            # ensemble은 양쪽 보유 시에만, 아니면 텍스트만
            sims = np.where(has_img, _txt_w * text_sims + _img_w * img_sims, text_sims)

        # 페이지 폴백 패널티 — bbox=null candidate (페이지 통째 인덱싱)
        fb_mask = np.array(
            [(c.meta or {}).get("bbox") is None for c in cand_list], dtype=bool,
        )
        # 패널티: -0.15 후 ceiling 0.89 (정상 분리 후보가 0.91+이면 자연 차분, 0.85+ 진짜
        # 적중도 "직접 적중"으로 노출 — 학원 마케팅 가치 false negative 완화)
        penal = np.minimum(0.89, sims - 0.10)
        penal = np.maximum(0.0, penal)
        sims = np.where(fb_mask, penal, sims)

        # 휴리스틱 weight 모두 0이라 그대로 sim. 정렬.
        order = np.argsort(-sims)  # desc
        scored = [(cand_list[i], float(sims[i])) for i in order]
    else:
        # numpy 없는 환경 fallback (CI에는 numpy 보장 — 사실상 도달 안 함)
        scored = []
        for c in cand_list:
            if not c.embedding:
                continue
            text_sim = cosine_similarity(source.embedding, c.embedding)
            img_sim = 0.0
            if src_img_emb and c.image_embedding:
                try:
                    img_sim = cosine_similarity(src_img_emb, c.image_embedding)
                except Exception:
                    img_sim = 0.0
                sim = _txt_w * text_sim + _img_w * img_sim
            else:
                sim = text_sim
            if (c.meta or {}).get("bbox") is None:
                sim = max(0.0, min(0.84, sim - 0.15))
            scored.append((c, sim))
        scored.sort(key=lambda x: x[1], reverse=True)

    # 2차: cross-encoder reranking — 환경변수 MATCHUP_USE_CROSS_ENCODER=1 일 때만.
    # 기본 OFF: bge-reranker-base가 한국어 시험 문제에 부적합 확인됨.
    if _USE_CROSS_ENCODER:
        pre_top = scored[:max(top_k * 2, 20)]
        if len(pre_top) >= 2:
            reranked = _rerank_with_cross_encoder(source, pre_top)
            if reranked is not None:
                return reranked[:top_k]

    return scored[:top_k]


def _rerank_with_cross_encoder(source, pre_top):
    """Cross-encoder로 pre_top 재정렬. 의존성 없거나 실패 시 None.

    Returns: [(problem, score), ...] 또는 None
    """
    try:
        from . import reranker as rr
    except ImportError:
        return None
    cands_text = [(p.text or "") for p, _ in pre_top]
    rr_result = rr.rerank(source.text or "", cands_text, top_k=len(pre_top))
    if rr_result is None:
        return None
    return [(pre_top[idx][0], float(score)) for idx, score in rr_result]


def cleanup_matchup_problem_images(document: MatchupDocument) -> int:
    """매치업 문서의 problem 이미지를 R2에서 삭제. 원본 PDF/이미지는 건드리지 않음.

    호출 컨텍스트:
      1. 매치업 문서 직접 삭제 (delete_document_with_r2 내부에서)
      2. InventoryFile 삭제 cascade 직전 (R2 orphan 방지)

    Returns: 삭제 시도한 problem 이미지 개수.
    """
    problem_keys = list(
        document.problems.exclude(image_key="").values_list("image_key", flat=True)
    )
    # 수동 크롭 모달이 PDF 페이지를 R2에 캐시했다면 함께 정리 — orphan 방지.
    page_cache_keys = list((document.meta or {}).get("page_image_keys") or [])
    all_keys = [k for k in (problem_keys + page_cache_keys) if k]

    if not delete_object_r2_storage:
        return 0
    for key in all_keys:
        try:
            delete_object_r2_storage(key=key)
        except Exception:
            logger.warning("R2 delete failed: %s", key, exc_info=True)
    return len(all_keys)


def delete_document_with_r2(document: MatchupDocument) -> None:
    """매치업 문서 삭제 — 문제 크롭 이미지만 R2에서 제거.

    원본 PDF/이미지(document.r2_key)는 InventoryFile이 소유하므로 여기서 지우지 않는다.
    원본 삭제는 InventoryFile 삭제 시 이루어지고 CASCADE로 MatchupDocument도 함께 삭제된다.
    """
    cleanup_matchup_problem_images(document)
    document.delete()  # CASCADE로 problems도 삭제 (InventoryFile은 그대로)


def exclude_page_from_matchup(
    document: MatchupDocument,
    page_index: int,
) -> dict:
    """페이지를 매치업 인덱싱에서 제외 — Phase 5-deep 검수 UI.

    동작:
      1. doc.meta.excluded_pages 리스트에 page_index 추가 (set 중복 제거)
      2. 해당 페이지의 problems 즉시 삭제 (R2 이미지 포함)
      3. 다음 reanalyze 시 워커가 해당 페이지 skip (matchup_pipeline)

    Returns: {removed_problems: int, excluded_pages: List[int]}
    """
    if page_index < 0 or page_index > 999:
        raise ValueError("page_index가 범위를 벗어났습니다.")

    meta = dict(document.meta or {})
    excluded = list(meta.get("excluded_pages") or [])
    if page_index not in excluded:
        excluded.append(int(page_index))
        excluded.sort()
    meta["excluded_pages"] = excluded
    document.meta = meta

    # 해당 페이지 problems 즉시 제거 — meta.page_index로 매칭.
    # JSONField filter는 backend별 다르지만 Postgres jsonb 정확 매칭 가능.
    target_problems = [
        p for p in document.problems.all()
        if (p.meta or {}).get("page_index") == int(page_index)
    ]
    removed = 0
    for p in target_problems:
        delete_problem_with_r2(p)
        removed += 1

    document.save(update_fields=["meta", "updated_at"])
    return {"removed_problems": removed, "excluded_pages": excluded}


def include_page_to_matchup(
    document: MatchupDocument,
    page_index: int,
) -> dict:
    """페이지를 매치업 인덱싱에 다시 포함 — exclude_page_from_matchup 롤백 (P1, 2026-05-04).

    동작:
      1. doc.meta.excluded_pages 리스트에서 page_index 제거
      2. doc.meta 저장 (problems는 자동 복원되지 않음 — 다음 reanalyze 시 분석)
      3. 학원장이 별도로 reanalyze_document 호출해야 problem 복원

    학원장이 실수로 페이지를 제외했다가 복구하는 case.
    Returns: {excluded_pages: List[int], requires_reanalyze: bool}
    """
    if page_index < 0 or page_index > 999:
        raise ValueError("page_index가 범위를 벗어났습니다.")

    meta = dict(document.meta or {})
    excluded = list(meta.get("excluded_pages") or [])
    if int(page_index) not in excluded:
        # 이미 포함된 페이지 — no-op
        return {"excluded_pages": excluded, "requires_reanalyze": False}

    excluded = [p for p in excluded if int(p) != int(page_index)]
    meta["excluded_pages"] = excluded
    document.meta = meta
    document.save(update_fields=["meta", "updated_at"])
    return {"excluded_pages": excluded, "requires_reanalyze": True}


def reanalyze_document(document: MatchupDocument) -> str:
    """status 무관하게 매치업 문서 재분석 — Phase 5-deep 검수 UI.

    DocumentRetryView의 retry_document는 status='failed'만 허용. done 상태에서
    학원장이 검수 후 "재분석" 누르는 경우(excluded_pages 적용/source_type 변경
    후 재처리 등) 별도 진입점이 필요. 워커 dispatch는 retry_document와 동일.

    processing 상태에서 중복 dispatch는 금지 — 큐 적체로 메모리 사고 위험.
    """
    if document.status == "processing":
        raise RuntimeError("이미 처리 중인 문서입니다. 완료 후 다시 시도하세요.")
    return retry_document(document)


def retry_document(document: MatchupDocument) -> str:
    """실패한 문서를 재처리. 새 AI job을 디스패치하고 job_id 반환.

    manual=true problem (학원장이 ManualCropModal에서 직접 자른 것)은 보존.
    pipeline 결과의 bulk_create는 ignore_conflicts=True라 같은 number 충돌 시
    silent drop되어 manual이 우선권을 가짐.
    """
    from apps.domains.ai.gateway import dispatch_job
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

    # 기존 문제 삭제 — 단, manual=true는 학원장 직접 작업이라 보존.
    # JSONB NULL semantics 회피 (운영 사고 2026-05-03): manual 키 없는 row가
    # exclude에서 빠지는 PostgreSQL NULL semantics로 skeleton row가 영구히 살아남는
    # 결함. ID 기반 명시 exclude로 우회. 자세한 분석은 callbacks.py:_handle_matchup_ai_result.
    manual_ids = list(
        document.problems.filter(meta__manual=True).values_list("id", flat=True)
    )
    document.problems.exclude(id__in=manual_ids).delete()

    # presigned URL 6시간 — 큐 적체 시 워커가 1시간 후 picking하면 만료되어
    # 403 Forbidden으로 doc.status='failed' 반복 사이클 발생 (운영 사고 2026-04-29).
    # 6시간이면 큐 적체에도 충분.
    download_url = generate_presigned_get_url_storage(
        key=document.r2_key, expires_in=21600
    )

    # 워커 strategy 라우터 신호 — 7-value source_type SSOT.
    from apps.domains.matchup.source_types import normalize_source_type
    meta = document.meta or {}
    source_type = normalize_source_type(
        meta.get("source_type") or meta.get("upload_intent") or meta.get("document_role")
    )

    # excluded_pages — Phase 5-deep 검수 UI에서 학원장이 제외한 페이지 idx.
    # 워커가 segmentation 결과에서 해당 페이지 skip → 다시 problem 생성 X.
    excluded_pages = list((meta.get("excluded_pages") or []))

    result = dispatch_job(
        job_type="matchup_analysis",
        payload={
            "download_url": download_url,
            "tenant_id": str(document.tenant_id),
            "document_id": str(document.id),
            "filename": document.original_name,
            "upload_intent": source_type,   # legacy alias
            "source_type": source_type,     # 7-value SSOT
            "excluded_pages": excluded_pages,
        },
        tenant_id=str(document.tenant_id),
        source_domain="matchup",
        source_id=str(document.id),
    )

    if isinstance(result, dict) and not result.get("ok", True):
        raise RuntimeError(result.get("error", "dispatch failed"))

    job_id = result.get("job_id", "") if isinstance(result, dict) else str(result)
    document.status = "processing"
    document.ai_job_id = str(job_id)
    document.error_message = ""
    document.problem_count = 0
    document.save(update_fields=["status", "ai_job_id", "error_message", "problem_count", "updated_at"])

    return job_id


# ── Storage-as-canonical helpers ─────────────────────────
#
# 멘탈 모델: InventoryFile = canonical 자료. MatchupDocument = 그 위의 분석 레이어.
# 매치업 자료의 진입점은 두 가지:
#   1. 매치업 페이지에서 업로드 → InventoryFile 생성 + 즉시 승격 (1-step UX)
#   2. 저장소에서 우클릭/토글 → 기존 InventoryFile 승격
# 두 경로 모두 promote_inventory_to_matchup으로 수렴.

MATCHUP_UPLOAD_ROOT = "매치업-업로드"


def ensure_matchup_upload_folder(tenant):
    """매치업 페이지 직접 업로드용 폴더 (/매치업-업로드/{YYYY-MM}/) 자동 생성. Returns InventoryFolder."""
    from apps.domains.inventory.models import InventoryFolder
    from datetime import datetime

    root, _ = InventoryFolder.objects.get_or_create(
        tenant=tenant, scope="admin", student_ps="",
        parent=None, name=MATCHUP_UPLOAD_ROOT,
    )
    ym_key = datetime.now().strftime("%Y-%m")
    ym_folder, _ = InventoryFolder.objects.get_or_create(
        tenant=tenant, scope="admin", student_ps="",
        parent=root, name=ym_key,
    )
    return ym_folder


_AUTO_CATEGORY_SKIP_FOLDERS = {
    # 매치업 자체 폴더 — 사용자가 의도한 "학교/세트" 카테고리가 아님.
    "매치업-자동등록", "매치업-업로드",
}


def _infer_category_from_folder(inventory_file) -> str:
    """inventory_file의 부모 폴더명에서 category 추출.

    사용자의 mental model: 저장소 폴더 = 학교/세트 = 매치업 카테고리.
    `/중대부고/2026 1학기/시험지.pdf` → category="중대부고".
    매치업 시스템 폴더(매치업-업로드 등)는 무시.
    """
    folder = getattr(inventory_file, "folder", None)
    if folder is None:
        return ""
    # 가장 가까운 의미있는 부모 폴더를 거슬러 올라가며 찾는다.
    cur = folder
    seen = 0
    while cur is not None and seen < 8:
        name = (cur.name or "").strip()
        if name and name not in _AUTO_CATEGORY_SKIP_FOLDERS:
            # YYYY-MM 형식(매치업-업로드 하위)도 의미없는 카테고리 — 스킵.
            if not (len(name) == 7 and name[4] == "-" and name[:4].isdigit()):
                return name[:100]  # 모델 max_length=100
        cur = getattr(cur, "parent", None)
        seen += 1
    return ""


def promote_inventory_to_matchup(
    inventory_file,
    *,
    title: str = "",
    category: str = "",
    subject: str = "",
    grade_level: str = "",
    upload_intent: str = "",
    author=None,
):
    """InventoryFile을 매치업 분석 대상으로 승격. Returns MatchupDocument.

    중복 승격 검사는 호출 측 책임 (트랜잭션 내 select_for_update 또는 IntegrityError 처리).
    OneToOneField unique 제약으로 DB 레벨에서도 race 차단.

    category가 비어 있으면 저장소 폴더 트리에서 자동 추론 — 사용자가 폴더로
    이미 분류해둔 mental model을 그대로 매치업으로 가져온다.

    upload_intent: 7-value source_type (`student_exam_photo`/`school_exam_pdf`/
    `commercial_workbook`/`academy_workbook`/`explanation`/`answer_key`/`other`).
    Legacy 2-value (`test`/`reference`/`exam_sheet`)도 자동 매핑 수용.
    명시되면 dispatch payload + doc.meta 양쪽에 기록해 워커가 race 없이 strategy 분기.

    author: 자료를 업로드/소유하는 강사 (User). find_similar 격리의 baseline.
    None=공용 풀(레거시 호환). 호출자(view)가 request.user 전달.
    """
    from apps.domains.ai.gateway import dispatch_job
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
    from apps.domains.matchup.source_types import normalize_source_type, is_indexable

    if not (category or "").strip():
        category = _infer_category_from_folder(inventory_file)

    # 7-value SSOT 정규화 — legacy/empty 입력도 안전 default("other") 보장.
    source_type = normalize_source_type(upload_intent)
    initial_meta: dict = {
        "source_type": source_type,            # 7-value SSOT (worker dispatcher 1순위 신호)
        "upload_intent": source_type,          # legacy 호환 (이전 코드/뷰가 읽어도 OK)
        "indexable": is_indexable(source_type),  # 매치업 검색 인덱스 대상 여부 (worker가 인덱싱 분기)
    }
    # legacy document_role 보존 — 호환을 위해 시험지류는 exam_sheet, 그 외는 reference_material로 매핑.
    initial_meta["document_role"] = (
        "exam_sheet" if source_type in ("school_exam_pdf", "student_exam_photo")
        else "reference_material"
    )

    doc = MatchupDocument.objects.create(
        tenant=inventory_file.tenant,
        author=author,  # 강사 1인 포트폴리오 baseline. None=공용 풀.
        inventory_file=inventory_file,
        title=title or inventory_file.display_name,
        category=category,
        subject=subject,
        grade_level=grade_level,
        r2_key=inventory_file.r2_key,
        original_name=inventory_file.original_name,
        size_bytes=inventory_file.size_bytes,
        content_type=inventory_file.content_type,
        status="pending",
        meta=initial_meta,
    )

    try:
        # presigned URL 6시간 — 큐 적체 시 1시간 만료 → 403 사고 방어
        download_url = generate_presigned_get_url_storage(
            key=inventory_file.r2_key, expires_in=21600,
        )
        result = dispatch_job(
            job_type="matchup_analysis",
            payload={
                "download_url": download_url,
                "tenant_id": str(inventory_file.tenant_id),
                "document_id": str(doc.id),
                "filename": inventory_file.original_name,
                "upload_intent": source_type,   # 7-value SSOT (정규화 후 값)
                "source_type": source_type,     # 명시적 별칭 (worker가 직접 참조)
            },
            tenant_id=str(inventory_file.tenant_id),
            source_domain="matchup",
            source_id=str(doc.id),
        )
        if isinstance(result, dict) and not result.get("ok", True):
            raise RuntimeError(result.get("error", "dispatch failed"))
        job_id = result.get("job_id", "") if isinstance(result, dict) else str(result)
        doc.status = "processing"
        doc.ai_job_id = str(job_id)
        doc.save(update_fields=["status", "ai_job_id", "updated_at"])
    except Exception:
        logger.exception("Failed to dispatch matchup_analysis for doc %s", doc.id)
        doc.status = "failed"
        doc.error_message = "AI 분석 작업 생성에 실패했습니다."
        doc.save(update_fields=["status", "error_message", "updated_at"])

    return doc


# ── 수동 크롭 ────────────────────────────────────────────
#
# 자동 분리 결과가 처참할 때 사용자가 직접 박스를 그려 problem을 추가/수정한다.
# 즉시 반영이 핵심 — embedding 계산은 비동기 워커에 위임하되 problem record와
# 이미지 업로드는 동기 처리해 사용자 화면에 즉각 노출.

def _download_inventory_to_temp(inventory_file) -> str:
    """InventoryFile R2 객체를 임시 파일로 다운로드. 호출자가 cleanup 책임.

    Returns: 로컬 임시 파일 경로.
    """
    import tempfile
    import os

    from apps.infrastructure.storage.r2 import (
        generate_presigned_get_url_storage,
    )

    url = generate_presigned_get_url_storage(
        key=inventory_file.r2_key, expires_in=600,
    )
    if not url:
        raise RuntimeError("presigned URL 생성 실패")

    import urllib.request
    suffix = os.path.splitext(inventory_file.original_name or "")[1] or ".bin"
    fd, path = tempfile.mkstemp(prefix="matchup-manual-", suffix=suffix)
    os.close(fd)
    urllib.request.urlretrieve(url, path)
    return path


def _enqueue_manual_problem_index(problem: MatchupProblem) -> None:
    """수동 크롭 problem에 OCR + 임베딩 인덱싱 워커 잡을 큐잉.

    워커가 image_key를 다운로드해 OCR + 정제 + 임베딩 후 callback이 problem
    레코드의 text/embedding을 채운다. 인덱싱이 끝나야 매치업 검색 풀에 노출.

    잡 디스패치 결과(ai_job_id 또는 error)를 problem.meta에 기록 — 디버깅 용이.
    """
    from apps.domains.ai.gateway import dispatch_job

    if not problem.image_key:
        return

    # paste 이미지(클립보드/외부 캡처)는 카메라 사진 가능성 → OCR 전처리 적용 플래그.
    is_paste = bool((problem.meta or {}).get("paste"))
    result = dispatch_job(
        job_type="matchup_manual_index",
        payload={
            "problem_id": problem.id,
            "tenant_id": str(problem.tenant_id),
            "image_key": problem.image_key,
            "is_camera_capture": is_paste,
        },
        tenant_id=str(problem.tenant_id),
        source_domain="matchup_manual",
        source_id=str(problem.id),
    )

    # meta에 dispatch 결과 기록 (응답으로 즉시 노출 + 운영 진단)
    meta = dict(problem.meta or {})
    if isinstance(result, dict):
        meta["ai_job_id"] = result.get("job_id") or ""
        if not result.get("ok", True):
            meta["ai_dispatch_error"] = result.get("error", "dispatch failed")
            meta["ai_rejection_code"] = result.get("rejection_code") or ""
    problem.meta = meta
    problem.save(update_fields=["meta", "updated_at"])

    if isinstance(result, dict) and not result.get("ok", True):
        raise RuntimeError(result.get("error", "dispatch failed"))


def manually_crop_problem(
    document: MatchupDocument,
    *,
    page_index: int,
    bbox_norm: Tuple[float, float, float, float],
    number: int,
    text: str = "",
) -> MatchupProblem:
    """document의 page_index 페이지에서 bbox_norm 영역을 잘라 새 problem 등록.

    bbox_norm: (x, y, w, h), 모두 0..1 (페이지 크기로 정규화).
    같은 number의 problem이 이미 있으면 이미지·meta를 갱신해 덮어쓴다.

    동기 처리 흐름:
      1. R2에서 원본 PDF/이미지 다운로드 (임시 파일)
      2. PDF면 PyMuPDF로 페이지 렌더, 이미지면 그대로 PIL 로드
      3. bbox_norm을 픽셀 좌표로 변환 → PIL crop → PNG bytes
      4. R2 업로드 (matchup problem key)
      5. MatchupProblem upsert (embedding은 비어둠 — 워커가 채움)

    Returns: 생성/갱신된 MatchupProblem.
    """
    import io
    import os
    import tempfile
    from PIL import Image

    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
    from .r2_path import build_matchup_problem_key

    if document.inventory_file_id is None:
        raise ValueError("문서에 원본 파일이 연결되어 있지 않습니다.")
    if not (1 <= number <= 999):
        raise ValueError("문항 번호는 1~999 사이여야 합니다.")
    x, y, w, h = bbox_norm
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        raise ValueError("bbox는 0~1 범위로 정규화되어야 합니다.")
    if x + w > 1.001 or y + h > 1.001:
        raise ValueError("bbox가 페이지 범위를 벗어납니다.")

    inv_file = document.inventory_file
    local_path = _download_inventory_to_temp(inv_file)
    try:
        is_pdf = (
            (inv_file.content_type or "").lower() == "application/pdf"
            or (inv_file.original_name or "").lower().endswith(".pdf")
        )
        if is_pdf:
            from academy.adapters.tools.pymupdf_renderer import PdfDocument

            with PdfDocument(local_path) as doc_pdf:
                if page_index < 0 or page_index >= doc_pdf.page_count():
                    raise ValueError(
                        f"page_index {page_index}가 페이지 범위를 벗어납니다 "
                        f"(0~{doc_pdf.page_count() - 1})"
                    )
                page_img = doc_pdf.render_page(page_index, dpi=200)
        else:
            if page_index != 0:
                raise ValueError("이미지 문서는 page_index=0만 가능합니다.")
            page_img = Image.open(local_path).convert("RGB")

        pw, ph = page_img.size
        left = max(0, int(round(x * pw)))
        top = max(0, int(round(y * ph)))
        right = min(pw, int(round((x + w) * pw)))
        bottom = min(ph, int(round((y + h) * ph)))
        if right - left < 5 or bottom - top < 5:
            raise ValueError("선택 영역이 너무 작습니다.")
        crop = page_img.crop((left, top, right, bottom))

        buf = io.BytesIO()
        crop.save(buf, "PNG")
        buf.seek(0)

        # r2 키 prefix 추출 — 기존 문서 key에서 uuid prefix 재사용.
        # 패턴: tenants/{tid}/matchup/{uuid}/<filename>
        # 없으면 tenant 매치업 폴더 prefix를 새로 생성하지 않고 inventory r2 key 옆에 problems/ 디렉터리.
        from .r2_path import build_matchup_document_key  # noqa: F401  (typing only)

        prefix = ""
        parts = (document.r2_key or "").split("/")
        if len(parts) >= 4 and parts[2] == "matchup":
            prefix = parts[3]
        if not prefix:
            # storage-as-canonical 경로(tenants/{tid}/admin/inventory/...)인 경우
            # document별 안정 prefix가 필요 — doc id로 대체.
            prefix = f"manual-{document.id}"

        problem_key = build_matchup_problem_key(
            tenant_id=document.tenant_id, uuid_prefix=prefix, number=number,
        )
        upload_fileobj_to_r2_storage(
            fileobj=buf, key=problem_key, content_type="image/png",
        )

        meta_payload = {
            "manual": True,
            "page_index": int(page_index),
            "bbox_norm": [float(x), float(y), float(w), float(h)],
            "format": "choice",  # 사용자가 명시 안 하면 기본 choice
        }
        problem, created = MatchupProblem.objects.update_or_create(
            tenant=document.tenant,
            document=document,
            number=number,
            defaults={
                "text": text or "",
                "image_key": problem_key,
                "embedding": None,
                "meta": meta_payload,
                "source_type": "matchup",
            },
        )

        # 문서의 problem_count 갱신
        document.problem_count = document.problems.count()
        document.status = "done"
        document.save(update_fields=["problem_count", "status", "updated_at"])

        # 임베딩은 비동기 워커가 채움 — OCR + sentence-transformer (matchup_manual_index).
        # 동기 처리: 레코드 + 이미지만. 즉시 그리드/캔버스/탐색에 노출.
        try:
            _enqueue_manual_problem_index(problem)
        except Exception:
            logger.exception(
                "MATCHUP_MANUAL_CROP_ENQUEUE_FAILED | doc=%s | problem=%s",
                document.id, problem.id,
            )

        logger.info(
            "MATCHUP_MANUAL_CROP | doc=%s | num=%s | created=%s | bbox=%s",
            document.id, number, created, bbox_norm,
        )
        return problem
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass


def paste_image_as_problem(
    document: MatchupDocument,
    *,
    image_bytes: bytes,
    content_type: str,
    number: int,
) -> MatchupProblem:
    """클립보드/파일에서 받은 이미지를 problem으로 직접 등록.

    매뉴얼 크롭과 달리 PDF 페이지 렌더링·bbox 영역 추출이 없음 — 이미지 자체가 문항.
    직접 촬영본/외부 크롭 이미지/메신저 캡처를 분리 단계 거치지 않고 즉시 인덱싱하기 위함.

    흐름:
      1. content_type 검증 + Pillow 디코드 → PNG 정규화
      2. R2 업로드 (matchup problem key, page_index=0 가상)
      3. MatchupProblem upsert (embedding은 워커가 채움)
      4. matchup_manual_index 잡 dispatch — OCR + 임베딩 비동기

    paste 모드 problem은 meta.paste=True로 표시 → 매뉴얼 크롭 보드와 분리.
    """
    import io
    from PIL import Image, UnidentifiedImageError

    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
    from .r2_path import build_matchup_problem_key

    if not (1 <= number <= 999):
        raise ValueError("문항 번호는 1~999 사이여야 합니다.")
    allowed_ct = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct not in allowed_ct:
        raise ValueError(f"지원하지 않는 이미지 형식: {ct or '없음'}")
    if not image_bytes or len(image_bytes) > 25 * 1024 * 1024:
        raise ValueError("이미지 크기가 비어있거나 25MB를 초과합니다.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.load()
            # EXIF 회전 보정 — 폰 사진/스캔본의 회전 메타 반영
            try:
                from PIL import ImageOps
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            buf.seek(0)
    except (UnidentifiedImageError, OSError) as e:
        raise ValueError(f"이미지 디코드 실패: {e}")

    # R2 prefix — manually_crop_problem과 동일 규칙
    prefix = ""
    parts = (document.r2_key or "").split("/")
    if len(parts) >= 4 and parts[2] == "matchup":
        prefix = parts[3]
    if not prefix:
        prefix = f"manual-{document.id}"

    problem_key = build_matchup_problem_key(
        tenant_id=document.tenant_id, uuid_prefix=prefix, number=number,
    )
    upload_fileobj_to_r2_storage(
        fileobj=buf, key=problem_key, content_type="image/png",
    )

    meta_payload = {
        "manual": True,
        "paste": True,  # 매뉴얼 크롭 vs paste 구분
        "page_index": 0,
        "format": "choice",
    }
    problem, created = MatchupProblem.objects.update_or_create(
        tenant=document.tenant,
        document=document,
        number=number,
        defaults={
            "text": "",
            "image_key": problem_key,
            "embedding": None,
            "meta": meta_payload,
            "source_type": "matchup",
        },
    )

    document.problem_count = document.problems.count()
    document.status = "done"
    document.save(update_fields=["problem_count", "status", "updated_at"])

    try:
        _enqueue_manual_problem_index(problem)
    except Exception:
        logger.exception(
            "MATCHUP_PASTE_ENQUEUE_FAILED | doc=%s | problem=%s",
            document.id, problem.id,
        )

    logger.info(
        "MATCHUP_PASTE_PROBLEM | doc=%s | num=%s | created=%s | bytes=%d",
        document.id, number, created, len(image_bytes),
    )
    return problem


def ensure_document_page_images(document: MatchupDocument) -> List[dict]:
    """문서의 페이지별 이미지를 R2에 캐싱하고 presigned URL 반환.

    수동 크롭 모달에서 캔버스에 그릴 때 필요. 한 번 캐시되면 doc.meta에 보존돼
    다음 호출 시 PDF 다운로드/렌더 없이 바로 presign만 재계산.

    Returns: [{index, url, width, height}, ...] (page 순서)
    """
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

    meta = dict(document.meta or {})
    page_keys = meta.get("page_image_keys")
    page_dims = meta.get("page_dimensions") or []  # [(w, h), ...]

    if not page_keys:
        # 캐시 미스: 원본 다운로드 + 페이지 렌더 + R2 업로드.
        page_keys, page_dims = _render_and_upload_pages(document)
        meta["page_image_keys"] = page_keys
        meta["page_dimensions"] = page_dims
        document.meta = meta
        document.save(update_fields=["meta", "updated_at"])

    pages = []
    for i, key in enumerate(page_keys):
        url = generate_presigned_get_url_storage(key=key, expires_in=900)
        w, h = (page_dims[i] if i < len(page_dims) else (0, 0))
        pages.append({"index": i, "url": url, "width": w, "height": h})
    return pages


def _render_and_upload_pages(
    document: MatchupDocument,
) -> Tuple[List[str], List[Tuple[int, int]]]:
    """원본 PDF/이미지를 페이지별 PNG로 잘라 R2 업로드.

    Returns: (page_keys, page_dims) — 같은 길이.
    """
    import io
    import os
    from PIL import Image

    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

    inv_file = document.inventory_file
    if inv_file is None:
        raise RuntimeError("inventory_file이 없습니다.")

    local_path = _download_inventory_to_temp(inv_file)
    try:
        is_pdf = (
            (inv_file.content_type or "").lower() == "application/pdf"
            or (inv_file.original_name or "").lower().endswith(".pdf")
        )

        # r2 prefix 결정
        prefix = ""
        parts = (document.r2_key or "").split("/")
        if len(parts) >= 4 and parts[2] == "matchup":
            prefix = parts[3]
        if not prefix:
            prefix = f"manual-{document.id}"

        page_keys: List[str] = []
        page_dims: List[Tuple[int, int]] = []

        if is_pdf:
            from academy.adapters.tools.pymupdf_renderer import PdfDocument

            with PdfDocument(local_path) as doc_pdf:
                for i in range(doc_pdf.page_count()):
                    page_img = doc_pdf.render_page(i, dpi=150)  # 캔버스용은 150 충분
                    buf = io.BytesIO()
                    page_img.save(buf, "PNG")
                    buf.seek(0)
                    key = f"tenants/{document.tenant_id}/matchup/{prefix}/pages/{i:03d}.png"
                    upload_fileobj_to_r2_storage(
                        fileobj=buf, key=key, content_type="image/png",
                    )
                    page_keys.append(key)
                    page_dims.append(page_img.size)
        else:
            img = Image.open(local_path).convert("RGB")
            buf = io.BytesIO()
            img.save(buf, "PNG")
            buf.seek(0)
            key = f"tenants/{document.tenant_id}/matchup/{prefix}/pages/000.png"
            upload_fileobj_to_r2_storage(
                fileobj=buf, key=key, content_type="image/png",
            )
            page_keys.append(key)
            page_dims.append(img.size)

        return page_keys, page_dims
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass


def merge_problems(
    document: MatchupDocument,
    *,
    problem_ids: List[int],
    target_number: int | None = None,
) -> MatchupProblem:
    """같은 doc의 problem N개를 1개로 합친다 (시험지에서 한 문항이 컬럼/페이지에 걸쳐 쪼개진 경우).

    동작:
      1. problem_ids 순서 = vertical stack 순서 (위→아래). 첫 번째가 primary.
      2. 각 problem 이미지를 R2에서 PIL로 로드, 폭은 max로 통일(작은 폭은 padding).
      3. 세로로 concat → PNG → R2에 새 key로 업로드.
      4. primary problem의 image_key/text/embedding/meta 갱신.
         - text = 각 problem의 text를 "\n\n"으로 join
         - embedding/image_embedding = None (워커가 재계산)
         - meta = 기존 meta + {merged_from: [other_ids], merged_count: N}
         - number = target_number (지정 안 하면 min)
      5. 나머지 problem들은 R2 이미지 삭제 + row 삭제.
      6. 워커에 manual_index 잡 dispatch (OCR + 임베딩 재계산).

    Returns: 갱신된 primary MatchupProblem.

    Raises:
      ValueError — 검증 실패 (problem 부족/cross-doc/cross-tenant).
    """
    import io
    from PIL import Image

    from apps.infrastructure.storage.r2 import (
        upload_fileobj_to_r2_storage,
        generate_presigned_get_url_storage,
    )
    from .r2_path import build_matchup_problem_key

    if not problem_ids or len(problem_ids) < 2:
        raise ValueError("합칠 문항을 2개 이상 선택해주세요.")

    seen_ids: set = set()
    ordered_ids: List[int] = []
    for pid in problem_ids:
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            raise ValueError(f"잘못된 problem id: {pid}")
        if pid_int in seen_ids:
            continue
        seen_ids.add(pid_int)
        ordered_ids.append(pid_int)
    if len(ordered_ids) < 2:
        raise ValueError("합칠 문항을 2개 이상 선택해주세요.")

    # tenant + doc 일치 검증 (cross-doc/cross-tenant 차단)
    problems_qs = MatchupProblem.objects.filter(
        tenant=document.tenant, document=document, id__in=ordered_ids,
    )
    by_id = {p.id: p for p in problems_qs}
    if len(by_id) != len(ordered_ids):
        raise ValueError("선택한 문항 중 일부가 이 문서에 없습니다.")
    ordered_problems = [by_id[pid] for pid in ordered_ids]

    # target_number 결정 — 미지정 시 min(numbers).
    nums = [p.number for p in ordered_problems]
    if target_number is None:
        target_number = min(nums)
    try:
        target_number = int(target_number)
    except (TypeError, ValueError):
        raise ValueError("문항 번호가 정수가 아닙니다.")
    if not (1 <= target_number <= 999):
        raise ValueError("문항 번호는 1~999 사이여야 합니다.")

    primary = ordered_problems[0]
    others = ordered_problems[1:]

    # target_number가 다른 (이 doc에 잔존할) problem과 충돌하면 차단.
    # 합쳐서 사라질 problem들의 number는 충돌 대상에서 제외.
    merged_ids_set = {p.id for p in others}
    conflict = (
        MatchupProblem.objects.filter(
            tenant=document.tenant, document=document, number=target_number,
        )
        .exclude(id__in=merged_ids_set)
        .exclude(id=primary.id)
        .first()
    )
    if conflict:
        raise ValueError(
            f"문항 번호 {target_number}는 이미 다른 문항(Q{conflict.number})에서 사용 중입니다."
        )

    # 각 문항 이미지를 R2에서 로드.
    if not generate_presigned_get_url_storage:
        raise RuntimeError("Storage not configured")

    import urllib.request
    images: List[Image.Image] = []
    for p in ordered_problems:
        if not p.image_key:
            raise ValueError(f"Q{p.number}는 이미지가 없어 합칠 수 없습니다.")
        url = generate_presigned_get_url_storage(key=p.image_key, expires_in=600)
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
        except Exception as e:
            raise RuntimeError(f"Q{p.number} 이미지 다운로드 실패: {e}")
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
        except Exception as e:
            raise RuntimeError(f"Q{p.number} 이미지 디코드 실패: {e}")
        images.append(img)

    # vertical stack — 폭을 max로 맞추고 작은 이미지는 흰 배경 가운데 정렬.
    # 정렬은 reading order: 사용자 선택 순서대로 위→아래.
    max_w = max(img.size[0] for img in images)
    total_h = sum(img.size[1] for img in images)
    GAP = 8  # 문항 간 작은 간격 — 시각적 분리
    total_h += GAP * (len(images) - 1)

    canvas = Image.new("RGB", (max_w, total_h), color=(255, 255, 255))
    cur_y = 0
    for img in images:
        w, h = img.size
        x = (max_w - w) // 2
        # RGBA가 섞여 있으면 RGB 캔버스에 paste 시 alpha mask 필요
        canvas.paste(img.convert("RGB"), (x, cur_y))
        cur_y += h + GAP

    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    buf.seek(0)

    # R2 prefix — manually_crop_problem과 동일 규칙.
    prefix = ""
    parts = (document.r2_key or "").split("/")
    if len(parts) >= 4 and parts[2] == "matchup":
        prefix = parts[3]
    if not prefix:
        prefix = f"manual-{document.id}"

    new_key = build_matchup_problem_key(
        tenant_id=document.tenant_id, uuid_prefix=prefix, number=target_number,
    )
    upload_fileobj_to_r2_storage(
        fileobj=buf, key=new_key, content_type="image/png",
    )

    # primary 갱신 — 기존 image_key는 R2에서 삭제(같은 prefix 정리).
    old_primary_key = primary.image_key
    merged_text = "\n\n".join((p.text or "").strip() for p in ordered_problems if (p.text or "").strip())
    new_meta = dict(primary.meta or {})
    new_meta["manual"] = True
    new_meta["merged"] = True
    new_meta["merged_from"] = [p.id for p in others]
    new_meta["merged_numbers"] = [p.number for p in others]
    new_meta["merged_count"] = len(ordered_problems)
    # bbox/page_index은 합친 결과에서 의미 없음 — 명시 제거.
    new_meta.pop("bbox", None)
    new_meta.pop("bbox_norm", None)
    new_meta.pop("page_index", None)
    # 검수 신호도 합친 결과에는 적용 안 함.
    new_meta.pop("merge_suspect", None)
    new_meta.pop("number_mismatch", None)
    new_meta.pop("is_partial", None)

    # 데이터 무결성 보호: DB 변경은 단일 트랜잭션으로 묶는다.
    # primary.save() 또는 document.save()가 실패하면 others의 row 삭제도 함께 롤백되어,
    # "여러 problem이 사라졌는데 합친 problem은 갱신 안 된" 손실 상태를 차단한다.
    # R2 정리는 트랜잭션 커밋 후 best-effort — 롤백 시 R2 객체는 그대로 유지되어
    # 카드 broken image도 발생하지 않음 (다음 표시 때 그대로 보임).
    from django.db import transaction

    others_old_keys = [p.image_key for p in others if p.image_key]

    with transaction.atomic():
        for p in others:
            p.delete()

        primary.number = target_number
        primary.text = merged_text
        primary.image_key = new_key
        primary.embedding = None
        primary.image_embedding = None
        primary.meta = new_meta
        primary.save(update_fields=[
            "number", "text", "image_key", "embedding", "image_embedding", "meta", "updated_at",
        ])

        # 문서 problem_count 갱신. status는 건드리지 않는다 — 'processing' doc에서 합치기를
        # 호출했을 때 AI 워커 callback과 race로 잘못된 'done' 덮어쓰기를 막기 위함.
        document.problem_count = document.problems.count()
        document.save(update_fields=["problem_count", "updated_at"])

    # 트랜잭션 커밋 완료 — 이제 R2 cleanup (best-effort).
    if delete_object_r2_storage:
        for old_key in others_old_keys:
            try:
                delete_object_r2_storage(key=old_key)
            except Exception:
                logger.warning(
                    "R2 merged problem image delete failed: %s", old_key,
                    exc_info=True,
                )
        if old_primary_key and old_primary_key != new_key:
            try:
                delete_object_r2_storage(key=old_primary_key)
            except Exception:
                logger.warning(
                    "R2 primary old image delete failed: %s", old_primary_key,
                    exc_info=True,
                )

    # OCR + 임베딩 재계산 (비동기)
    try:
        _enqueue_manual_problem_index(primary)
    except Exception:
        logger.exception(
            "MATCHUP_MERGE_ENQUEUE_FAILED | doc=%s | problem=%s",
            document.id, primary.id,
        )

    logger.info(
        "MATCHUP_MERGE | doc=%s | primary=%s | merged=%s | target_number=%s",
        document.id, primary.id, [p.id for p in others], target_number,
    )
    return primary


def delete_problem_with_r2(problem: MatchupProblem) -> None:
    """단일 problem 삭제 + R2 cleanup.

    수동 추가/자동 추출 모두 동일하게 처리.
    """
    if problem.image_key and delete_object_r2_storage:
        try:
            delete_object_r2_storage(key=problem.image_key)
        except Exception:
            logger.warning(
                "R2 problem image delete failed: %s", problem.image_key,
                exc_info=True,
            )
    doc_id = problem.document_id
    tenant_id = problem.tenant_id
    problem.delete()
    if doc_id:
        try:
            doc = MatchupDocument.objects.get(id=doc_id, tenant_id=tenant_id)
            doc.problem_count = doc.problems.count()
            doc.save(update_fields=["problem_count", "updated_at"])
        except MatchupDocument.DoesNotExist:
            pass

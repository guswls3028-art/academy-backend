# PATH: apps/domains/matchup/services.py
# 매치업 비즈니스 로직 — 유사도 검색, R2 정리, 재시도

from __future__ import annotations

import logging
import os
from typing import List, Optional, Tuple

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


# ── 추천 풀 자격 SSOT (Stage 4, 2026-05-06) ────────────────────────
#
# 추천 풀 진입 자격을 단일 함수로 SSOT. find_similar_problems 외 batch 추천,
# 보고서 자동 매핑 등 모든 진입점에서 동일 게이트 통과.
#
# default mode (legacy null 통과):
#   기존 운영 데이터(processing_quality=NULL, proposal_status=NULL,
#   confirmation_status=NULL)는 전부 통과 — 추천 풀 0건 장애 방지.
#   blocklist 동작: 명시 차단 마커가 박힌 problem만 제외.
#     - meta.low_quality=True
#     - document.meta.indexable=False
#     - meta.proposal_status ∈ {pending, needs_review, rejected}
#     - meta.processing_quality ∈ {page_fallback, no_problems, needs_review, failed}
#
# strict mode (ENV MATCHUP_RECOMMEND_STRICT_ALLOWLIST=1):
#   미래 도입할 confirmation 신호 기반 strict allowlist.
#   meta.confirmation_status='confirmed' 또는 meta.manual=True 만 통과.
#   현재 운영 분포 (2026-05-06 실측): confirmed=0건, manual=4,270건 →
#   strict 즉시 ON 시 풀 4,270건 (legacy 25,412건 제외). 추천 정확도 ↑,
#   재현율 ↓. T2 검증 후 점진 ON 권장.
#
# manual_only mode (ENV MATCHUP_RECOMMEND_MANUAL_ONLY=1):
#   학원장 cut 자료만 추천 (P1.5/α4, 2026-05-06). strict allowlist의 부분집합.
#   strict 모드와 동시 ON 시 manual_only가 우선 (둘 다 만족).

def eligible_for_recommendation_qs(qs):
    """추천 풀 진입 자격 SSOT. 모든 진입점에서 동일 게이트 보장.

    Args:
        qs: MatchupProblem queryset (이미 tenant/embedding 필터 적용된 상태 권장)

    Returns:
        eligibility 게이트 통과한 queryset.
    """
    import os as _os
    qs = (
        qs
        # low_quality 게이트 (P0-2, 2026-05-04): 자동 품질 점수 < 0.7 cell 제외.
        # CRITICAL fix (Phase 8, 2026-05-05): meta__contains는 NULL safe (정확 매칭).
        .exclude(meta__contains={"low_quality": True})
        # Phase 4 (2026-05-05): page_fallback/no_problems/needs_review doc 차단.
        .exclude(document__meta__contains={"indexable": False})
        # Stage 0 (2026-05-06): AI proposal 미승인 결과 차단. 사용자 작성 데이터 immutable.
        .exclude(meta__contains={"proposal_status": "pending"})
        .exclude(meta__contains={"proposal_status": "needs_review"})
        .exclude(meta__contains={"proposal_status": "rejected"})
        .exclude(meta__contains={"processing_quality": "page_fallback"})
        .exclude(meta__contains={"processing_quality": "no_problems"})
        .exclude(meta__contains={"processing_quality": "needs_review"})
        .exclude(meta__contains={"processing_quality": "failed"})
    )

    if _os.environ.get("MATCHUP_RECOMMEND_STRICT_ALLOWLIST", "0") == "1":
        from django.db.models import Q
        qs = qs.filter(
            Q(meta__contains={"confirmation_status": "confirmed"})
            | Q(meta__contains={"manual": True})
        )

    if _os.environ.get("MATCHUP_RECOMMEND_MANUAL_ONLY", "0") == "1":
        qs = qs.filter(meta__contains={"manual": True})

    return qs


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

    # redis 캐싱 (P1, 2026-05-05): 학원장 같은 시험지 재클릭 / 보고서 빌더 27 problem
    # 병렬 호출에서 매번 풀 fetch + numpy ensemble 부하 차단. fail-OPEN.
    # TTL 1h — pool 변경 (새 doc / reanalyze) 후 최대 1h stale.
    from .cache import get_cached_similar, set_cached_similar
    cached = get_cached_similar(tenant_id, problem_id, top_k, author_id)
    if cached is not None:
        # 캐시된 ID로 problem 객체 단일 PK 쿼리 — DB pool fetch 회피.
        cached_ids = [pid for pid, _ in cached]
        if not cached_ids:
            return []
        problems_by_id = MatchupProblem.objects.in_bulk(cached_ids)
        return [
            (problems_by_id[pid], score)
            for pid, score in cached
            if pid in problems_by_id
        ]

    source_category = ""
    if source.document_id and source.document is not None:
        source_category = (source.document.category or "").strip()

    candidates = eligible_for_recommendation_qs(
        MatchupProblem.objects.filter(tenant_id=tenant_id, embedding__isnull=False)
        .exclude(id=problem_id)
    ).defer("created_at", "updated_at")

    # 저작권 격리 — author_id 지정 시 본인 자료 + 공용 풀(legacy author=NULL)만.
    # exam-source problem(document=None)은 별도 필터에서 처리되므로 여기서는 영향 X.
    if author_id is not None:
        from django.db.models import Q
        candidates = candidates.filter(
            Q(document__author_id=author_id) | Q(document__author__isnull=True)
        )

    # 텍스트 + 이미지 ensemble 가중치. 학원장 본질 의견 (2026-05-05 카톡):
    #   "그림 때려맞춘 걸 더 주로 내세우는데 모델이 그림보다 글씨 인식 많이 함"
    #   "우리 매치업 핵심 = 그림 매칭, 그림 비중 상향 필요"
    # 2026-05-06 디폴트 상향 (image weight V2):
    #   짧은(<60자):  0.5 → 0.7 (OCR 부족한 그림 위주 source는 image 우선)
    #   중간(<200자): 0.3 → 0.5 (text/image balance)
    #   긴(>=200자): 0.15 → 0.3 (긴 text도 그림 신호 일정 비중 보장)
    # 환경변수 override 가능: MATCHUP_IMAGE_SIM_WEIGHT (per-tenant 튜닝).
    src_text_len_for_w = len((source.text or "").strip())
    if src_text_len_for_w < 60:
        _img_w = 0.7
    elif src_text_len_for_w < 200:
        _img_w = 0.5
    else:
        _img_w = 0.3
    import os as _osw
    try:
        _img_w = float(_osw.environ.get("MATCHUP_IMAGE_SIM_WEIGHT", "") or _img_w)
    except ValueError:
        pass
    _txt_w = max(0.0, 1.0 - _img_w)
    src_img_emb = source.image_embedding

    # source 의 source_type 식별 — 시험지(test) vs 자료(reference) 분기.
    # 카테고리 격리 항상 적용 (2026-05-05 학원장 실측 결함 fix):
    #   기존: 시험지 source 는 카테고리 격리 해제 → 개포고 시험지에 단대부고 자료 추천되는
    #   격리 결함. 박철T 케이스(자료 1~몇 개) 우려로 도입했지만 실측 데이터로
    #   모든 카테고리(개포고/단대부고/숙명여고/중대부고/은광여고/박철T)가 시험지 1+ /
    #   자료 22+ 보유 확인됨. 격리 유지가 정확.
    #   시험지 doc 의 자기 doc 안 problem 은 sim≈1 self-doc trap 이라 항상 제외.
    is_test_source = False
    if source.document_id and source.document is not None:
        meta = source.document.meta or {}
        # 7-value source_type SSOT — legacy 2-value도 매핑되어 들어옴.
        from apps.domains.matchup.source_types import normalize_source_type
        st = normalize_source_type(meta.get("source_type") or meta.get("upload_intent") or meta.get("document_role"))
        is_test_source = st in ("school_exam_pdf", "student_exam_photo")
        if is_test_source:
            candidates = candidates.exclude(document_id=source.document_id)

    # 카테고리 격리 무조건 적용 (2026-05-05 학원장 실측 revert):
    #   926f1ad7에서 manual=true cross-category 예외 도입했으나 학원장 카톡 (22:51):
    #   "은광여고 시험지인데 숙명/단대 자료가 매치업 됨" → cross-category 누출 결함 재발.
    #   학원장 진짜 의도 = 같은 학교 카테고리 안에서 자기 cut한 자료 우선 노출
    #   (이는 manual boost +0.15로 처리, ca8770e3). cross-category 통과는 학원장 의도와 충돌.
    #   원칙: 자동분리/manual 모두 같은 카테고리 격리 유지 (db8ecb77 정책 복원).
    # exam source(document_id=None)는 별도 처리.
    if source.document_id:
        candidates = candidates.filter(
            document__isnull=False,
            document__category=source_category,
        )

    src_format = _format_of(source)
    src_len = len(source.text or "")
    src_doc_id = source.document_id

    # 1차: bi-encoder + 휴리스틱 + 이미지 ensemble — numpy vectorized.
    # 운영 사고(2026-04-29 사용자 보고): T2 problem 5,717개 매 클릭마다 Python 순회로
    # cosine 5,717번 → 매치업 페이지 클릭 시 렉. numpy bulk dot product로 100배 가속.
    #
    # image_embedding lazy fetch (P1, 2026-05-05 부하 fix):
    #   기존: 매 query 카테고리 풀 ~2300 problem 모두 image_embedding fetch
    #   (~2KB/row × 2300 = ~5MB serialization). 학원장 클릭 시 API CPU spike 본질.
    #   neu: 1차 text-only fetch → text cosine top N 선별 → 그 N 만 image_embedding
    #   별도 PK 인덱스 fetch. 매 query payload ~50% 감소. 정확도 보존
    #   (선별 기준은 image=1 가정 upper bound — top N 밖에서 final top_k 진입 불가).
    #
    # pgvector kNN 사전 필터 (P2.1, 2026-05-06 성능 최대화):
    #   기존: candidates queryset 전체 fetch → numpy로 cosine — N개 풀의 O(N) 스캔.
    #   (Plan B 인프라: embedding_v / image_embedding_v vector(N) 컬럼 + HNSW 인덱스
    #   matchup_problem_emb_hnsw_idx / matchup_problem_imgemb_hnsw_idx 완료, backfill
    #   29,462 / 29,935 — 메모리: project_matchup_search_load_analysis_2026_05_05).
    #   neu: MATCHUP_USE_PGVECTOR=1 시 candidates에 pgvector cosine distance annotation +
    #   ORDER BY + LIMIT 200으로 사전 필터. HNSW O(log n)로 5,000+ 풀에서 10-50배 가속.
    #   top 30 결과 동일성 보장 (top 200 안에 정답 거의 100% 포함, 정확도 회귀 없음).
    #   회귀 시 ENV 0으로 즉시 numpy path 복귀.
    import os as _osp
    _use_pgvector = _osp.environ.get("MATCHUP_USE_PGVECTOR", "0") == "1"
    if _use_pgvector and source.embedding:
        from django.db.models.expressions import RawSQL
        # pgvector cosine distance: 0 (완전 일치) ~ 2 (반대). HNSW 인덱스 활용.
        # source.embedding 은 list[float] — psycopg2가 '[1.0,2.0,...]' 형태로 직렬화 → ::vector 캐스팅.
        _src_vec_str = "[" + ",".join(f"{float(x):.6f}" for x in source.embedding) + "]"
        candidates = candidates.exclude(embedding_v=None).annotate(
            _pgv_dist=RawSQL("embedding_v <=> %s::vector", [_src_vec_str])
        ).order_by("_pgv_dist")[: max(top_k * 10, 200)]
    cand_list = list(
        candidates.only(
            "id", "document_id", "embedding",
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

        # 페이지 폴백 마스크 — fb_mask는 1차 선별과 최종 패널티 양쪽에서 사용.
        # 페이지 폴백 = 자동분리가 페이지 통째를 problem으로 등록한 경우 (anchor 누락 등).
        # 학원장 노가다 cut(manual=True)은 의도된 정확한 cut이므로 페널티 대상 아님:
        #   - manually_crop_problem: meta.bbox_norm 보유, meta.bbox 없음
        #   - paste_image_as_problem: bbox/bbox_norm 둘 다 없으나 사용자 의도된 단일 이미지
        # 결함 (2026-05-06 측정 + fix): bbox 키 단독 검사로 manual 3,542건 모두 fb_mask=True →
        #   페널티 -0.10 cap 0.89 → manual_boost +0.30이 1.0으로 saturated → ranking 정보 0.
        # fix: manual=True 또는 bbox/bbox_norm 보유 = 정상 cut이므로 페널티 X.
        fb_mask = np.array(
            [
                not (c.meta or {}).get("manual")
                and (c.meta or {}).get("bbox") is None
                and (c.meta or {}).get("bbox_norm") is None
                for c in cand_list
            ],
            dtype=bool,
        )

        # 1차 선별 — image fetch 후보 N개 정함 (image lazy fetch 정확도 보존).
        # bbox=null: max 0.89 (image 보태도 못 넘는 상한)
        # bbox=present: text + image=1 가정 upper bound (실제 image_sim 후 ≤)
        # image fetch N (default top_k×10, 최소 100). N 밖 후보는 final top_k 진입 불가.
        pre_score = np.where(
            fb_mask,
            np.maximum(0.0, np.minimum(0.89, text_sims - 0.10)),
            _txt_w * text_sims + _img_w,
        )
        image_fetch_n = min(max(top_k * 10, 100), len(cand_list))
        if image_fetch_n < len(cand_list):
            pre_top_idx = np.argpartition(-pre_score, image_fetch_n - 1)[:image_fetch_n]
        else:
            pre_top_idx = np.arange(len(cand_list))

        # image_embedding 별도 fetch — top N IDs 만. PK 인덱스 단일 query.
        img_emb_by_id: dict = {}
        if src_img_emb:
            top_ids = [int(cand_list[i].id) for i in pre_top_idx]
            img_emb_by_id = dict(
                MatchupProblem.objects.filter(id__in=top_ids)
                .values_list("id", "image_embedding")
            )

        # 이미지 임베딩 ensemble — 양쪽 보유한 인덱스만 결합 (top N만 의미 있음)
        sims = text_sims.copy()
        if src_img_emb and img_emb_by_id:
            src_img = np.asarray(src_img_emb, dtype=np.float32)
            si_n = float(np.linalg.norm(src_img)) or 1.0
            img_sims = np.zeros(len(cand_list), dtype=np.float32)
            has_img = np.zeros(len(cand_list), dtype=bool)
            for i in pre_top_idx:
                ie = img_emb_by_id.get(int(cand_list[i].id))
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
        # 패널티: -0.15 후 ceiling 0.89 (정상 분리 후보가 0.91+이면 자연 차분, 0.85+ 진짜
        # 적중도 "직접 적중"으로 노출 — 학원 마케팅 가치 false negative 완화)
        penal = np.minimum(0.89, sims - 0.10)
        penal = np.maximum(0.0, penal)
        sims = np.where(fb_mask, penal, sims)

        # manual=true boost — 학원장 노가다 cut 가치 보호 (saturation 없는 곱셈 형태).
        #   배경: 학원장 3,500+ bbox 노가다 cut → AI 학습 + 추천 풀 우선순위.
        #   기존 가산형 (+0.30): raw sim 0.7+ 인 manual 후보 모두 1.0 cap saturation →
        #     top10 모두 sim=1.0, frontend "100% 직접 적중" 거짓 신호 (2026-05-06 측정).
        #   현재 곱셈형: sim_new = sim + (1 - sim) * f. raw sim과 1.0 사이 gap의 f% 채움.
        #     - 1.0 절대 도달 X (saturation 없음, ranking 정보 보존)
        #     - 같은 raw sim 비교 시 manual이 위 (학원장 cut 우선)
        #     - raw 매우 높은 (0.95+) 후보는 boost 효과 자연 감소 (이미 충분히 가까움)
        #   ENV MATCHUP_MANUAL_BOOST 의미 = gap-close 비율 [0..1]. default 0.30.
        manual_mask = np.array(
            [(c.meta or {}).get("manual") is True for c in cand_list], dtype=bool,
        )
        import os as _osmb
        try:
            _manual_boost = float(_osmb.environ.get("MATCHUP_MANUAL_BOOST", "0.30") or "0.30")
        except ValueError:
            _manual_boost = 0.30
        _manual_boost = max(0.0, min(1.0, _manual_boost))
        sims = np.where(
            manual_mask,
            sims + (1.0 - sims) * _manual_boost,
            sims,
        )

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
            # numpy 경로와 동일 폴백 마스크 — manual cut/bbox_norm 보유는 페널티 X.
            cm = c.meta or {}
            is_page_fallback = (
                not cm.get("manual")
                and cm.get("bbox") is None
                and cm.get("bbox_norm") is None
            )
            if is_page_fallback:
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
                final = reranked[:top_k]
                set_cached_similar(
                    tenant_id, problem_id, top_k, author_id,
                    [(p.id, sc) for p, sc in final],
                )
                return final

    final = scored[:top_k]
    set_cached_similar(
        tenant_id, problem_id, top_k, author_id,
        [(p.id, sc) for p, sc in final],
    )
    return final


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
    # 보호:
    #   manual=True (운영 위험 fix 2026-05-05): 학원장이 직접 자른 problem 보존.
    #   manual_owner_pinned=True (P0 fix 2026-05-11): 적중보고서 selected_problem_ids
    #     가 가리키는 자동 problem 도 페이지 exclude 에서 보존. 미보호 시
    #     selected_problem_ids 가 dead pid 만 가리키는 dangling 재발 위험
    #     (project_matchup_hitreport_dangling_recovery_2026_05_06 사고 클래스).
    page_problems = [
        p for p in document.problems.all()
        if (p.meta or {}).get("page_index") == int(page_index)
    ]
    target_problems = [
        p for p in page_problems
        if not (p.meta or {}).get("manual")
        and not (p.meta or {}).get("manual_owner_pinned")
    ]
    preserved_manual = sum(1 for p in page_problems if (p.meta or {}).get("manual"))
    preserved_pinned = sum(
        1 for p in page_problems
        if (p.meta or {}).get("manual_owner_pinned")
        and not (p.meta or {}).get("manual")
    )
    removed = 0
    for p in target_problems:
        delete_problem_with_r2(p)
        removed += 1

    document.save(update_fields=["meta", "updated_at"])
    return {
        "removed_problems": removed,
        "excluded_pages": excluded,
        "preserved_manual": preserved_manual,
        "preserved_pinned": preserved_pinned,
    }


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


# ── Phase A (2026-05-09) — page-level state 도입 ───────────────────────
#
# basic_definition_2026_05_09 SSOT: 합격선 = '최종 Problem Image Set 학원장
# 최소 노동'. 그 1단계 = page-level 분기 (auto/skip/manual).
#
# backward compat:
#   - meta.excluded_pages 가 worker 측 SSOT 그대로. PageState.state='skip' 변경
#     시 excluded_pages 동기화. PageState 부재 시 기존 excluded_pages 그대로 동작.
#   - PageState 가 도입되어도 worker / callback / segment_dispatcher 변경 0
#     (Phase D 에서 worker 가 PageState 직접 읽도록 점진 전환).

PAGE_STATE_AUTO = "auto"
PAGE_STATE_SKIP = "skip"
PAGE_STATE_MANUAL = "manual"
PAGE_STATE_VALUES = (PAGE_STATE_AUTO, PAGE_STATE_SKIP, PAGE_STATE_MANUAL)


def _validate_page_state(state: str) -> str:
    if state not in PAGE_STATE_VALUES:
        raise ValueError(f"page state 값이 잘못됨: {state!r}")
    return state


def get_page_states(document: MatchupDocument) -> list[dict]:
    """문서의 page state 전체 list (page_index 0 ~ N-1).

    PageState row 가 없는 page 는 backward compat 적용:
      - excluded_pages 안에 있으면 state='skip'
      - 그 외 state='auto'
    UI 가 PageState 모델 직접 모르는 상태에서 동등하게 사용 가능.

    page_count 산출 우선순위 (P0 BUG fix 2026-05-09):
      1. meta.page_image_keys 길이 — worker 가 캐시한 PDF 실 페이지 수 (가장 정확).
         기존 doc.meta.page_count 가 problem max idx+1 로 잘못 설정된 사고 회피.
      2. meta.page_count (legacy 호환).
      3. problems max page_index + 1 (최후 fallback).
    """
    from .models import MatchupPageState

    meta = document.meta or {}
    page_keys = meta.get("page_image_keys") or []
    page_count = 0
    if isinstance(page_keys, list) and len(page_keys) > 0:
        page_count = len(page_keys)
    if page_count <= 0:
        page_count = int(meta.get("page_count") or 0)
    if page_count <= 0:
        # fallback — InventoryFile 페이지 수가 없으면 problems 의 max page_index+1
        max_idx = -1
        for p in document.problems.all():
            pi = (p.meta or {}).get("page_index")
            if isinstance(pi, int) and pi > max_idx:
                max_idx = pi
        page_count = max_idx + 1 if max_idx >= 0 else 0

    excluded = set(int(x) for x in ((document.meta or {}).get("excluded_pages") or []))
    db_states = {
        ps.page_index: ps
        for ps in MatchupPageState.objects.filter(document=document)
    }

    result: list[dict] = []
    for idx in range(page_count):
        ps = db_states.get(idx)
        if ps is not None:
            result.append({
                "page_index": idx,
                "state": ps.state,
                "auto_reason": ps.auto_reason,
                "updated_by_id": ps.updated_by_id,
                "updated_at": ps.updated_at.isoformat() if ps.updated_at else None,
                "source": "db",
            })
        else:
            inferred = PAGE_STATE_SKIP if idx in excluded else PAGE_STATE_AUTO
            result.append({
                "page_index": idx,
                "state": inferred,
                "auto_reason": "legacy_excluded_pages" if inferred == PAGE_STATE_SKIP else "",
                "updated_by_id": None,
                "updated_at": None,
                "source": "legacy_meta" if inferred == PAGE_STATE_SKIP else "default",
            })
    return result


def set_page_state(
    document: MatchupDocument,
    page_index: int,
    state: str,
    *,
    actor=None,
    auto_reason: str = "",
    sync_excluded_pages: bool = True,
) -> dict:
    """단일 페이지 state upsert.

    - state='skip' 이면 meta.excluded_pages 에도 page_index 추가 (worker 호환).
    - state='auto'/'manual' 이면 meta.excluded_pages 에서 page_index 제거.
    - actor 가 있으면 학원장 수동 변경 (auto_reason 클리어), 없으면 시스템 추천.
    """
    from .models import MatchupPageState

    if not isinstance(page_index, int) or page_index < 0 or page_index > 999:
        raise ValueError("page_index 범위를 벗어남")
    _validate_page_state(state)

    actor_user = actor if actor is not None and getattr(actor, "is_authenticated", False) else None
    cleared_reason = "" if actor_user is not None else auto_reason

    ps, created = MatchupPageState.objects.update_or_create(
        document=document,
        page_index=int(page_index),
        defaults={
            "tenant_id": document.tenant_id,
            "state": state,
            "auto_reason": cleared_reason,
            "updated_by": actor_user,
        },
    )

    if sync_excluded_pages:
        meta = dict(document.meta or {})
        excluded = list(meta.get("excluded_pages") or [])
        if state == PAGE_STATE_SKIP:
            if page_index not in excluded:
                excluded.append(int(page_index))
                excluded.sort()
        else:
            excluded = [p for p in excluded if int(p) != int(page_index)]
        meta["excluded_pages"] = excluded
        document.meta = meta
        document.save(update_fields=["meta", "updated_at"])

    return {
        "page_index": int(page_index),
        "state": ps.state,
        "auto_reason": ps.auto_reason,
        "created": created,
    }


def bulk_set_page_states(
    document: MatchupDocument,
    items: list[dict],
    *,
    actor=None,
) -> dict:
    """다중 페이지 state 일괄 upsert.

    items = [{page_index: int, state: str, auto_reason?: str}, ...]

    실패는 부분 적용 — 가능한 것 적용, 실패 list 반환.
    meta.excluded_pages 는 마지막에 한 번만 동기화 (성능).
    """
    from .models import MatchupPageState

    actor_user = actor if actor is not None and getattr(actor, "is_authenticated", False) else None

    applied: list[int] = []
    failed: list[dict] = []
    skip_indexes: set[int] = set()
    non_skip_indexes: set[int] = set()

    for item in items:
        try:
            pi = int(item.get("page_index"))
            state = _validate_page_state(item.get("state", ""))
            reason = item.get("auto_reason", "") if actor_user is None else ""
        except (TypeError, ValueError) as e:
            failed.append({"item": item, "error": str(e)})
            continue
        if pi < 0 or pi > 999:
            failed.append({"item": item, "error": "page_index 범위"})
            continue
        MatchupPageState.objects.update_or_create(
            document=document,
            page_index=pi,
            defaults={
                "tenant_id": document.tenant_id,
                "state": state,
                "auto_reason": reason,
                "updated_by": actor_user,
            },
        )
        applied.append(pi)
        if state == PAGE_STATE_SKIP:
            skip_indexes.add(pi)
        else:
            non_skip_indexes.add(pi)

    # 마지막에 meta.excluded_pages 동기화
    meta = dict(document.meta or {})
    excluded = set(int(x) for x in (meta.get("excluded_pages") or []))
    excluded |= skip_indexes
    excluded -= non_skip_indexes
    meta["excluded_pages"] = sorted(excluded)
    document.meta = meta
    document.save(update_fields=["meta", "updated_at"])

    return {"applied": sorted(applied), "failed": failed, "excluded_pages": meta["excluded_pages"]}


def auto_recommend_page_states(document: MatchupDocument) -> list[dict]:
    """페이지별 skip 자동 추천 — 학원장 클릭 줄이기.

    합격선 정렬 (basic_definition_2026_05_09): "자동 60-80% + 한 클릭 표지 제거"
    의 자동 60-80% 핵심. paper_type_summary.pages 가 있으면 paper_type 기반,
    없으면 problem-free 페이지 휴리스틱 fallback (P0 fix 2026-05-09).

    추천 로직 (우선순위 순):
      1. paper_type_summary.pages[i].paper_type ∈ {explanation, answer_key, cover, index, non_question}
         → state='skip' 추천 (auto_reason='paper_type_<role>')
      2. (1) 추천 0건이면 fallback: PDF 실 페이지 중 problem 0건 페이지 자동 skip
         → state='skip' (auto_reason='no_problem_detected'). 학원장이 잘못 추천 시
         체크박스 해제 1 클릭 가능.

    side effect 0 — 추천 list 만 반환. 적용은 별도 호출 (학원장 confirm 후).
    """
    meta = document.meta or {}
    summary = meta.get("paper_type_summary") or {}
    SKIP_ROLES = {"explanation", "answer_key", "cover", "index", "non_question"}

    recommendations: list[dict] = []

    # 우선순위 1: paper_type_summary.pages 기반
    if isinstance(summary, dict):
        pages = summary.get("pages") or []
        if isinstance(pages, list):
            for entry in pages:
                if not isinstance(entry, dict):
                    continue
                idx = entry.get("page_index")
                ptype = entry.get("paper_type") or entry.get("primary")
                if not isinstance(idx, int) or not isinstance(ptype, str):
                    continue
                if ptype in SKIP_ROLES:
                    recommendations.append({
                        "page_index": idx,
                        "state": PAGE_STATE_SKIP,
                        "auto_reason": f"paper_type_{ptype}",
                    })

    if recommendations:
        return recommendations

    # 우선순위 2 (fallback): problem 없는 페이지 = skip 추천 (휴리스틱)
    # 사용자 directive: 'AI 가 완벽히 자동 cut' X, '학원장 최소 노동 + 자동 60-80%'.
    # paper_type_summary 부재 doc 도 학원장 검수 노동 절감.

    # PDF 실 페이지 수 산출 (get_page_states 와 동일 우선순위)
    page_keys = meta.get("page_image_keys") or []
    if isinstance(page_keys, list) and len(page_keys) > 0:
        page_count = len(page_keys)
    else:
        page_count = int(meta.get("page_count") or 0)
    if page_count <= 0:
        return []

    # problem 가진 페이지 set
    pages_with_problem: set[int] = set()
    for p in document.problems.all():
        pi = (p.meta or {}).get("page_index")
        if isinstance(pi, int):
            pages_with_problem.add(pi)

    # problem 없고 manual 보호 영향 없는 페이지 → skip 추천
    # 안전 margin: problem 가진 페이지가 0건 (학원장이 아직 cut 안 한 doc) 이면 추천 X
    # — 추천 신뢰도 부족.
    if len(pages_with_problem) == 0:
        return []

    for idx in range(page_count):
        if idx in pages_with_problem:
            continue
        recommendations.append({
            "page_index": idx,
            "state": PAGE_STATE_SKIP,
            "auto_reason": "no_problem_detected",
        })
    return recommendations


def pin_problems_as_owner_curated(
    *,
    tenant_id: int,
    problem_ids,
) -> int:
    """학원장이 적중 보고서에서 selected 한 problem 을 dangling 사고로부터 보호.

    효과:
        - MatchupProblem.meta.manual_owner_pinned=True 마킹.
        - retry_document / _handle_matchup_ai_result / proposal-rebuild 모두 본
          flag 가 박힌 problem 은 hard delete 하지 않는다.

    배경 (2026-05-06 사고): 학원장이 보고서에 selected 한 problem 을 reanalyze 가
        무차별 hard delete → entries.selected_problem_ids 가 stale 한 dead pid 만
        가리키는 dangling 발생. 사고 직후 read-side guard 4곳 추가했으나 *write-side*
        가 누락 → 실제로는 보호 0. 본 helper 가 write-side SSOT.

    원칙:
        - 한 번 pinned 된 problem 은 unpin 하지 않는다 (다른 entry/보고서에서
          참조될 수 있고, 학원장 데이터 보호는 보수적으로 가는 것이 정도).
        - 트랜잭션 외부에서도 호출 가능 — 호출자가 atomic 안에서 묶을지 결정.
        - tenant 격리 강제 — cross-tenant pid 무시.

    Returns:
        새로 pin 한 problem 수 (이미 pinned 면 카운트 안 함).
    """
    if not problem_ids:
        return 0
    pinned = 0
    qs = MatchupProblem.objects.filter(
        tenant_id=tenant_id,
        id__in=list(problem_ids),
    ).only("id", "meta")
    for p in qs:
        meta = dict(p.meta or {})
        if meta.get("manual_owner_pinned") is True:
            continue
        meta["manual_owner_pinned"] = True
        p.meta = meta
        p.save(update_fields=["meta", "updated_at"])
        pinned += 1
    if pinned:
        logger.info(
            "MATCHUP_PIN_OWNER_CURATED | tenant=%s | newly_pinned=%d | total_ids=%d",
            tenant_id, pinned, len(problem_ids),
        )
    return pinned


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
    #
    # manual_owner_pinned 보호 (2026-05-06 위급 fix): 학원장이 적중 보고서에서 별 토글한
    # selected_problem_ids 가리키는 problem은 reanalyze 시 삭제 안 함 (selected_problem_ids
    # 무효화 차단). MatchupHitReportEntry → manual_owner_pinned=true 마킹 + 본 exclude.
    # 학원장 어제 작성 보고서 가치 보호.
    # Backfill 안전망 (2026-05-10): 본 retry 직전 시점에 어떤 hit_report entry 라도
    # 본 document 의 problem 을 selected_problem_ids 로 가리키면 자동 pin. 학원장이
    # 5/6 사고 직후 보고서를 손대지 않고 reanalyze 만 트리거하는 경우(write-side 가
    # 한 번도 호출 안 됐던 legacy 보고서)도 보호. 멱등 — 이미 pinned 면 no-op.
    from .models import MatchupHitReportEntry
    legacy_curated_ids: set = set()
    legacy_entries = MatchupHitReportEntry.objects.filter(
        tenant_id=document.tenant_id,
        report__document_id=document.id,
    ).only("selected_problem_ids")
    for e in legacy_entries:
        for pid in (e.selected_problem_ids or []):
            try:
                legacy_curated_ids.add(int(pid))
            except (TypeError, ValueError):
                pass
    if legacy_curated_ids:
        pin_problems_as_owner_curated(
            tenant_id=document.tenant_id,
            problem_ids=list(legacy_curated_ids),
        )

    manual_ids = list(
        document.problems.filter(meta__manual=True).values_list("id", flat=True)
    )
    pinned_ids = list(
        document.problems.filter(meta__manual_owner_pinned=True).values_list("id", flat=True)
    )
    protected_ids = list(set(manual_ids) | set(pinned_ids))
    document.problems.exclude(id__in=protected_ids).delete()

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


def _bbox_iou_dict(a: Optional[dict], b: Optional[dict]) -> Optional[float]:
    """두 정규화 bbox dict 의 IoU. 둘 다 {x, y, w, h} 0..1.

    누락/형식 불량 → None. union==0 → None (학습 신호로 의미 없음).
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    try:
        ax, ay = float(a.get("x", 0)), float(a.get("y", 0))
        aw, ah = float(a.get("w", 0)), float(a.get("h", 0))
        bx, by = float(b.get("x", 0)), float(b.get("y", 0))
        bw, bh = float(b.get("w", 0)), float(b.get("h", 0))
    except (TypeError, ValueError):
        return None
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return None
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2 = min(ax + aw, bx + bw)
    iy2 = min(ay + ah, by + bh)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return None
    return round(inter / union, 4)


def _find_ai_proposal_candidate(
    *, tenant_id: int, document_id: int, page_index: int, number: int,
):
    """manual cut 좌표와 비교할 AI proposal 후보 찾기.

    매칭: 같은 (tenant, doc, page, number) + status != rejected (이미 거절된 건 비교 의미 X).
    여러 건이면 가장 최근. 없으면 None.

    Stage 6.7 (auto-segmentation → proposal queue) wire-in 전에는 보통 None.
    Stage 6.5 hook 은 이 None 을 self-evident "manual_only" 로 기록.
    """
    from .models import ProblemSegmentationProposal
    return (
        ProblemSegmentationProposal.objects
        .filter(
            tenant_id=tenant_id, document_id=document_id,
            page_number=page_index, detected_problem_number=number,
        )
        .exclude(status="rejected")
        .order_by("-created_at")
        .first()
    )


def _record_manual_correction_delta(
    problem,
    document: MatchupDocument,
    *,
    page_index: int,
    bbox_norm: Tuple[float, float, float, float],
    is_recreate: bool,
    actor=None,
):
    """Stage 6.5 — manual_crop 시점에 ManualCorrectionDelta row 기록.

    실패 시 caller 가 try/except 로 흡수 — manual_crop 본 흐름에 영향 0.

    원칙:
    - 같은 (tenant, doc, page, number) 의 비-rejected AI proposal 이 있으면 IoU 계산
    - 없으면 manual_only (original_bbox=None, iou_with_ai=None, engine="manual_crop")
    - 같은 number 재cut → correction_type=bbox_adjust / 신규 → manual_create
    - selected_problem_ids / hit_report 미접근 (read-only audit log 만)
    """
    from .models import ManualCorrectionDelta

    x, y, w, h = bbox_norm
    corrected_bbox = {
        "x": float(x), "y": float(y), "w": float(w), "h": float(h),
        "page": int(page_index), "norm": True,
    }

    ai_proposal = _find_ai_proposal_candidate(
        tenant_id=problem.tenant_id,
        document_id=document.id,
        page_index=int(page_index),
        number=int(problem.number),
    )

    original_bbox = None
    iou = None
    engine_at_action = "manual_crop"
    proposal_obj = None
    if ai_proposal is not None:
        proposal_obj = ai_proposal
        original_bbox = ai_proposal.bbox if isinstance(ai_proposal.bbox, dict) else None
        iou = _bbox_iou_dict(original_bbox, corrected_bbox)
        # ai engine 명시 (yolo / vlm / ocr / native_pdf / manual_assist)
        if ai_proposal.engine:
            engine_at_action = str(ai_proposal.engine)[:32]
    else:
        # AutoSegmentationSnapshot fallback (V11 BOTTLENECK §7.1, 2026-05-10) —
        # Proposal 없으면 callback 이 instrument 한 snapshot 매칭. fine-tune loop 가동.
        # 매칭 우선순위: same (doc, page, number) → IoU 계산.
        #              none → same (doc, page) + max IoU box.
        try:
            from .models import AutoSegmentationSnapshot
            snap = (
                AutoSegmentationSnapshot.objects
                .filter(
                    tenant_id=problem.tenant_id,
                    document_id=document.id,
                    page_index=int(page_index),
                    detected_problem_number=int(problem.number),
                )
                .order_by("-created_at")
                .first()
            )
            if snap is None:
                # number 미부여 / fragment 합쳐진 case — same page 의 max IoU 박스
                page_snaps = list(
                    AutoSegmentationSnapshot.objects
                    .filter(
                        tenant_id=problem.tenant_id,
                        document_id=document.id,
                        page_index=int(page_index),
                    )
                    .order_by("-created_at")[:30]
                )
                best_iou = 0.0
                best_snap = None
                for s in page_snaps:
                    if not isinstance(s.bbox, dict):
                        continue
                    cand_iou = _bbox_iou_dict(s.bbox, corrected_bbox)
                    if cand_iou and cand_iou > best_iou:
                        best_iou = cand_iou
                        best_snap = s
                if best_snap is not None and best_iou > 0:
                    snap = best_snap
                    iou = best_iou
            if snap is not None:
                if isinstance(snap.bbox, dict):
                    original_bbox = snap.bbox
                if iou is None:
                    iou = _bbox_iou_dict(original_bbox, corrected_bbox)
                if snap.engine:
                    engine_at_action = str(snap.engine)[:32]
        except Exception:
            # snapshot lookup 실패 시 manual_only 그대로 (기존 path)
            pass

    paper_type = ""
    try:
        summary = (document.meta or {}).get("paper_type_summary") or {}
        paper_type = str(summary.get("primary") or "")[:32]
    except Exception:
        paper_type = ""

    correction_type = "bbox_adjust" if is_recreate else "manual_create"

    ManualCorrectionDelta.objects.create(
        tenant_id=problem.tenant_id,
        proposal=proposal_obj,
        problem=problem,
        document=document,
        correction_type=correction_type,
        source="user_ui",
        original_bbox=original_bbox,
        corrected_bbox=corrected_bbox,
        iou_with_ai=iou,
        paper_type_at_action=paper_type,
        engine_at_action=engine_at_action,
        notes="",
        created_by=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
    )


def _column_count_from_paper_type(paper_type: str) -> int:
    """Stage 6.6 — paper_type → 추정 column_count.

    classifier 출력값 기준 (paper_type_summary.primary):
      - clean_pdf_dual / scan_dual → 2
      - quadrant → 4
      - 그 외 (clean_pdf_single, scan_single, student_answer_photo,
        side_notes, non_question, unknown, "") → 1
    """
    if paper_type in ("clean_pdf_dual", "scan_dual"):
        return 2
    if paper_type == "quadrant":
        return 4
    return 1


def _extract_pdf_first_page_metrics(document: MatchupDocument) -> Tuple[int, dict]:
    """Stage 6.6.5 — document 의 inventory file 에서 PDF first page metrics 추출.

    PDF download → first page render → page_count + page_size dict.
    이미지 doc 도 동일 처리 (page_count=1, PIL size).

    Returns:
        (page_count, page_size_dict). page_size = {"width": int, "height": int}.

    Raises:
        ValueError — inventory_file 없거나 PDF 분석 실패. 호출자가 catch + skip.

    원칙:
    - R2 read 만 (write 0)
    - 임시 파일 cleanup 보장
    - selected_problem_ids / hit_report 미접근
    """
    import os
    from PIL import Image

    if document.inventory_file_id is None:
        raise ValueError("문서에 원본 파일이 연결되어 있지 않습니다.")

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
                page_count = doc_pdf.page_count()
                if page_count <= 0:
                    raise ValueError("PDF 페이지 수가 0 이하입니다.")
                first_page = doc_pdf.render_page(0, dpi=200)
                w, h = first_page.size
        else:
            page_img = Image.open(local_path).convert("RGB")
            w, h = page_img.size
            page_count = 1

        return int(page_count), {"width": int(w), "height": int(h)}
    finally:
        try:
            os.unlink(local_path)
        except OSError:
            pass


def _record_layout_fingerprint(
    document: MatchupDocument,
    *,
    page_count: int,
    page_size: dict,
):
    """Stage 6.6 V1 — LayoutFingerprint upsert (tenant, document, version=1).

    실패 시 caller 가 try/except 로 흡수 — manual_crop 본 흐름 영향 0.

    원칙:
    - tenant FK 필수 (cross-tenant 매칭 영구 금지 정책 보존)
    - update_or_create — same doc 재cut 시 idempotent (paper_type 갱신)
    - V1 minimum 필드만 채움 (paper_type / page_count / page_size / column_count)
    - V2 enrichment (text_density / x0_clusters / y_gap_distribution / ...) 자리 default
    - selected_problem_ids / hit_report 미접근 (read-only audit)
    """
    from .models import LayoutFingerprint

    paper_type = ""
    try:
        summary = (document.meta or {}).get("paper_type_summary") or {}
        paper_type = str(summary.get("primary") or "")[:32]
    except Exception:
        paper_type = ""

    column_count = _column_count_from_paper_type(paper_type)

    LayoutFingerprint.objects.update_or_create(
        tenant_id=document.tenant_id,
        document=document,
        fingerprint_version=1,
        defaults={
            "paper_type": paper_type,
            "page_count": int(page_count or 0),
            "page_size": dict(page_size or {}),
            "column_count": column_count,
            # V2 enrichment 자리 — 6.6.5 / 6.7 에서 채움
            "text_density": 0.0,
            "image_density": 0.0,
            "anchor_density": 0.0,
            "x0_clusters": [],
            "y_gap_distribution": {},
            "font_size_distribution": {},
            "filename_patterns": [],
            "similarity_cluster_id": "",
        },
    )


def manually_crop_problem(
    document: MatchupDocument,
    *,
    page_index: int,
    bbox_norm: Tuple[float, float, float, float],
    number: int,
    text: str = "",
    actor=None,
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
                pdf_page_count = doc_pdf.page_count()  # Stage 6.6 fingerprint 용 캐싱
                if page_index < 0 or page_index >= pdf_page_count:
                    raise ValueError(
                        f"page_index {page_index}가 페이지 범위를 벗어납니다 "
                        f"(0~{pdf_page_count - 1})"
                    )
                page_img = doc_pdf.render_page(page_index, dpi=200)
        else:
            if page_index != 0:
                raise ValueError("이미지 문서는 page_index=0만 가능합니다.")
            page_img = Image.open(local_path).convert("RGB")
            pdf_page_count = 1

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
        # update_or_create defaults — text embedding은 옛 값 유지, image embedding은 reset.
        # 정책 분리 (2026-05-06 본질 fix):
        #   text embedding: 옛 값 유지 (학원장 결함 fix 63f343ef). text 필드는 새 OCR 결과로
        #     덮어쓰지만 OCR 텍스트가 옛 cut과 유사할 가능성 큼 → "추천에서 사라짐" 결함 회피.
        #   image embedding: 명시 reset (None). 같은 number 재cut은 다른 bbox 영역 = 완전히
        #     다른 image. 옛 image_embedding 유지하면 text+image ensemble의 image_sim이
        #     잘못된 신호로 score 왜곡 → 학원장 "그림 매칭 약함" 본질 결함 일부 원인.
        #     None reset → 워커가 새 image로 갱신할 때까지 image=0 (text-only fallback,
        #     find_similar에서 has_img mask 처리). ca8770e3 boost +0.15는 사라짐 보강용으로
        #     유지 (text embedding은 옛 값 사용 중이므로 매치 신호 약화 보강).
        existing = MatchupProblem.objects.filter(
            tenant=document.tenant, document=document, number=number,
        ).first()
        is_recreate = existing is not None
        problem, created = MatchupProblem.objects.update_or_create(
            tenant=document.tenant,
            document=document,
            number=number,
            defaults={
                "text": text or "",
                "image_key": problem_key,
                "meta": meta_payload,
                "source_type": "matchup",
                # text embedding 명시 X — 옛 값 유지 (워커가 새 OCR 기반으로 덮어씀)
                "image_embedding": None,  # 명시 reset — 옛 image와 새 bbox image mismatch 차단
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

        # 검색 캐시 무효화 (학원장 결함 fix 2026-05-05): manual cut 후 신규 problem이
        # 풀에 들어와도 기존 시험지 source의 redis 캐시(TTL)에 갇혀 있어 검색 결과에
        # 안 나타남. tenant 전체 invalidate로 즉시 반영.
        try:
            from .cache import invalidate_tenant_similar_cache
            invalidate_tenant_similar_cache(document.tenant_id)
        except Exception:
            logger.exception("MATCHUP_CACHE_INVALIDATE_FAILED | tenant=%s", document.tenant_id)

        # Stage 6.5 — manual cut audit hook. 실패 시 manual_crop 본 동작 영향 0.
        # ManualCorrectionDelta 자동 채움 → V12 학습 / paper_type cluster / iou 측정 토대.
        try:
            _record_manual_correction_delta(
                problem,
                document,
                page_index=int(page_index),
                bbox_norm=bbox_norm,
                is_recreate=is_recreate,
                actor=actor,
            )
        except Exception:
            logger.exception(
                "MATCHUP_MANUAL_CORRECTION_DELTA_FAILED | doc=%s | problem=%s",
                document.id, problem.id,
            )

        # Stage 6.6 V1 — LayoutFingerprint upsert. 같은 doc 의 cut 마다 idempotent
        # update_or_create. PDF size + page_count 는 위에서 이미 계산됨 (재계산 비용 0).
        # 실패 시 manual_crop 본 동작 영향 0.
        try:
            _record_layout_fingerprint(
                document,
                page_count=int(pdf_page_count),
                page_size={"width": int(pw), "height": int(ph)},
            )
        except Exception:
            logger.exception(
                "MATCHUP_LAYOUT_FINGERPRINT_FAILED | doc=%s",
                document.id,
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
            # paste 재호출 = 다른 image 자체. 옛 image_embedding 유지하면 새 paste image와
            # mismatch → 매치업 score 왜곡. embedding과 동일하게 None reset (워커 갱신).
            "image_embedding": None,
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
    try:
        from .cache import invalidate_tenant_similar_cache
        invalidate_tenant_similar_cache(document.tenant_id)
    except Exception:
        logger.exception("MATCHUP_CACHE_INVALIDATE_FAILED | tenant=%s", document.tenant_id)

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

    # P0 보호 (2026-05-11): others 안에 manual_owner_pinned=True 가 있으면 차단.
    # others 는 transaction 안에서 단순 p.delete() 되므로, 학원장 적중보고서의
    # selected_problem_ids 가 dead pid 만 가리키는 dangling 재발 위험
    # (project_matchup_hitreport_dangling_recovery_2026_05_06 사고 클래스).
    # primary 자신이 pinned 인 case 는 허용 — PID 가 보존되므로 dangling 0,
    # image / embedding 만 갱신되며 보고서 참조 관계는 살아 있음.
    pinned_others = [p for p in others if (p.meta or {}).get("manual_owner_pinned")]
    if pinned_others:
        nums = ", ".join(f"Q{p.number}" for p in pinned_others)
        raise ValueError(
            f"적중보고서에 사용 중인 문항({nums})은 합칠 수 없습니다. "
            f"먼저 보고서에서 해당 문항의 별 표시를 해제해주세요."
        )

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

    # 검색 캐시 무효화 (P1 fix 2026-05-11): merge 후 풀에서 others 삭제 + primary
    # 이미지/임베딩 변경. 캐시가 dead pid 또는 stale embedding 반환하면 학원장
    # 추천 결과 망가짐. manual_crop / paste 와 동일 정책.
    try:
        from .cache import invalidate_tenant_similar_cache
        invalidate_tenant_similar_cache(document.tenant_id)
    except Exception:
        logger.exception("MATCHUP_CACHE_INVALIDATE_FAILED | tenant=%s", document.tenant_id)

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
    # 검색 캐시 무효화 (P1 fix 2026-05-11): 삭제된 problem pid 가 캐시에서 dead
    # reference 로 잔존 → in_bulk lookup 으로 그 entry 만 drop 되지만, 풀 자체가
    # 변경됐으므로 그대로 두면 ranking 이 stale. tenant 전체 invalidate.
    try:
        from .cache import invalidate_tenant_similar_cache
        invalidate_tenant_similar_cache(tenant_id)
    except Exception:
        logger.exception("MATCHUP_CACHE_INVALIDATE_FAILED | tenant=%s", tenant_id)

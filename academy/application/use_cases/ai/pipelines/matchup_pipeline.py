# PATH: apps/worker/ai_worker/ai/pipelines/matchup_pipeline.py
# 매치업 분석 파이프라인 — 문제 분할 + OCR + 임베딩
"""
1. 다운로드     (10%)
2. 문제 분할    (30%)
3. OCR          (50%)
4. 임베딩       (80%)
5. 이미지 업로드 (90%)
6. 완료         (100%)
"""
from __future__ import annotations

import io
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)

_TRUTHY_ENV = {"1", "true", "yes", "y", "on"}
_NON_PROBLEM_PAGE_TYPES = {
    "non_question", "explanation", "answer_key",
    "cover", "index",
}
_NON_PROBLEM_PAGE_ROLES = {
    "cover", "index", "explanation", "answer_key",
}


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY_ENV


def _env_csv(name: str, default: Tuple[str, ...] = ()) -> set[str]:
    raw = os.environ.get(name)
    if raw is None:
        return set(default)
    return {part.strip() for part in raw.split(",") if part.strip()}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _tenant_gate_allows(name: str, tenant_id: str | int | None) -> bool:
    raw = os.environ.get(name, "").strip()
    if not raw or raw == "*":
        return True
    allowed = {part.strip() for part in raw.split(",") if part.strip()}
    return str(tenant_id) in allowed


def _source_type_gate_allows(
    name: str,
    source_type: str,
    default: Tuple[str, ...],
) -> bool:
    allowed = _env_csv(name, default)
    return "*" in allowed or source_type in allowed


def _real_vlm_vision_configured() -> bool:
    adapter = os.environ.get("MATCHUP_VLM_VISION_ADAPTER", "mock").strip().lower()
    return adapter.startswith("gemini") and bool(os.environ.get("GEMINI_API_KEY"))


def _real_vlm_text_configured() -> bool:
    adapter = os.environ.get("MATCHUP_VLM_TEXT_ADAPTER", "mock").strip().lower()
    return adapter.startswith("gemini") and bool(os.environ.get("GEMINI_API_KEY"))


# ── 텍스트 정제 + format 감지 ──────────────────────────
#
# 목적: 임베딩의 품질을 형식적 텍스트(서답형 헤더, 시험지 푸터, 페이지 번호 등)에서
# 분리. 같은 시험지 내 다른 서답형이 sim 0.86으로 잡히던 트랩 해소.

# 서답형/논술형 패턴 (감지용)
_ESSAY_PATTERN = re.compile(
    r"\[\s*(?:서\s*[답술]형|논\s*[답술]형|단\s*[답술]형|약\s*[답술]형)"
)

# 정제 대상: 시험지 형식 텍스트 (임베딩 의미와 무관)
#
# V2.5 보수화: 본문 의미를 손상시키던 5개 패턴 제거
#  - 학교명 단독, 페이지 번호 단독, 학년+과목 단독, 점수 표시, OCR 잡음 라인
#  → 본문의 짧은 키워드("ㄱ", "AUG", "X2-" 등)까지 제거되던 부작용 차단.
# V2의 CASE 1 후퇴(top1 11→4)는 OCR 잡음 라인 제거 패턴이 본문 단편을 깎은 결과로 추정.
_NOISE_PATTERNS = [
    # 서답형 헤더 — "[ 서 답형 1 ( 서 논술형 ) ]"
    re.compile(r"\[\s*(?:서|논|단|약)\s*[답술]형\s*\d*\s*(?:\([^)]*\))?\s*\]"),
    # 학교명 + 학년 + 과목 푸터 — "( 1 ) 학년 ( 통합 과학 1 ) ( 8 쪽 중 3 쪽 )"
    re.compile(r"\(\s*\d+\s*\)\s*학\s*년\s*\([^)]+\)\s*\(\s*\d+\s*쪽\s*중\s*\d+\s*쪽\s*\)"),
    # 페이지 표시 — "( 8 쪽 중 3 쪽 )"
    re.compile(r"\(\s*\d+\s*쪽\s*중\s*\d+\s*쪽\s*\)"),
    # "< 본 시험 문제 의 저작권 은 ... >"
    re.compile(r"<\s*본\s*시험\s*문제[^>]{1,100}>"),
    # 페이지 이동 마커
    re.compile(r"<\s*(?:다음\s*장\s*에\s*계속|뒷면\s*에\s*계속|끝\.?\s*수고\s*했습니다)[^>]*>"),
    # 정답 단위 안내
    re.compile(r"※\s*정답[^\n]{0,80}처리\s*함\s*\.?"),
]


# 페이지 워터마크/푸터/단원헤더 — q['text'] (display + embedding) 양쪽 정제용.
#
# 운영 케이스 (Tenant 2 학습자료 13개 doc, 누적 ~437건 problem):
# 페이지 푸터/워터마크가 본문 박스 안에 prepend되어 problem.text에 그대로 들어옴.
# is_non_question_page는 페이지 SKIP만 결정하지 페이지 내 박스 텍스트 정제는 안 함.
#
# - 신민 TWORKBOOK / 신민T (Runner's High 학원 워터마크)
# - Runner's High with God min (디자인 푸터)
# - GOD MIN (배지)
# - Step N. 개념완성 / 내신완성 / 수능완성 (학습자료 단원헤더)
# - CHAPTER NN 헤더 (학습자료 챕터)
# - lorem ipsum 라틴 placeholder (디자인 표지의 잔류 텍스트가 본문 박스에 spillover)
_PAGE_NOISE_PATTERNS = [
    # 신민 TWORKBOOK 워터마크 (운영 doc#123/144/126/145 등 50건/문서)
    re.compile(r"신\s*민\s*T?WORKBOOK", re.IGNORECASE),
    re.compile(r"\bTWORKBOOK\b", re.IGNORECASE),
    # Runner's High / GOD MIN 푸터
    re.compile(r"Runner['’`]?\s*[sS5]?\s*high\s*with\s*[Gg]od\s*[Mm]in", re.IGNORECASE),
    re.compile(r"Runner['’`]?\s*[sS5]?\s*high", re.IGNORECASE),
    re.compile(r"\bGOD\s*MIN\b", re.IGNORECASE),
    # 단원 헤더 — Step 1. 개념완성 / Step 2. 내신완성 / Step 3. 수능완성
    re.compile(r"Step\s*\d\s*\.\s*(?:개념|내신|수능)\s*완\s*성"),
    # CHAPTER NN 챕터 헤더 (행 단위, 학습자료)
    re.compile(r"^\s*\d{0,2}\s*CHAPTER\s+\d{1,2}[^\n]{0,40}$", re.MULTILINE),
    # 라틴 lorem ipsum 잔재 (디자인 표지 spillover)
    re.compile(
        r"(?:adipiscing|consectetuer|laoreet|tincidunt|euismod|"
        r"volutpat|nonummy|aliquam|nibh\s+euismod)[^\s,.\n]*",
        re.IGNORECASE,
    ),
]


def strip_page_noise(text: str) -> str:
    """페이지 푸터/워터마크/단원헤더를 problem 텍스트에서 제거.

    임베딩과 사용자 표시 양쪽에 적용. 매치업 sim에서 동일 prefix가
    false sim 상승으로 작용하던 문제와 어드민 화면에서 problem 텍스트에
    "신민 TWORKBOOK Runner's High..." 노이즈가 prepend되던 결함 동시 해소.
    """
    if not text:
        return ""
    cleaned = text
    for pat in _PAGE_NOISE_PATTERNS:
        cleaned = pat.sub("", cleaned)
    # 정제 후 잔여 공백/개행 정리 — 라인이 비면 제거.
    lines = [ln.rstrip() for ln in cleaned.split("\n")]
    lines = [ln for ln in lines if ln.strip()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def detect_format(text: str) -> str:
    """문제 텍스트에서 format 감지. 'essay' (서답/논술/단답형) 또는 'choice' (객관식)."""
    if not text:
        return "choice"
    return "essay" if _ESSAY_PATTERN.search(text) else "choice"


def normalize_text_for_embedding(text: str) -> str:
    """임베딩에 쓰일 텍스트 정제 — 형식·헤더·푸터 노이즈 제거.

    원본 text는 사용자 표시용으로 별도 보관. 이 함수의 결과만 임베딩에 사용.
    """
    if not text:
        return ""
    cleaned = text
    for pat in _NOISE_PATTERNS:
        cleaned = pat.sub("", cleaned)
    # 연속 공백/개행 정리
    cleaned = re.sub(r"\n\s*\n", "\n", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def run_matchup_pipeline(
    *,
    job: AIJob,
    local_path: str,
    payload: Dict[str, Any],
    tenant_id: str | None,
    record_progress: Callable,
) -> AIResult:
    """매치업 문서 분석: 문제 분할 → OCR → 임베딩."""
    job_id = str(job.id)
    document_id = payload.get("document_id", "")

    # ── Step 1: 문제 분할 (30%) ──
    record_progress(
        job_id, "segmentation", 20,
        step_index=1, step_total=5,
        step_name_display="문제 분할",
        step_percent=0, tenant_id=tenant_id,
    )

    # source_type을 분할 호출 전에 결정 — segment_dispatcher가 paper_type 분류기에
    # handwriting_score bias로 전달해야 student_exam_photo가 STUDENT_ANSWER_PHOTO로
    # 분류되어 page-as-problem 폴백이 신뢰성 있게 작동.
    from apps.domains.matchup.source_types import normalize_source_type
    source_type = normalize_source_type(
        payload.get("source_type") or payload.get("upload_intent")
    )
    upload_intent = source_type  # legacy alias 보존
    doc_title = ""
    if (source_type == "other") and document_id:
        # payload에 명시 안 됐으면 DB에서 읽기 (race-safe)
        try:
            from apps.domains.matchup.models import MatchupDocument
            doc = MatchupDocument.objects.only("meta", "title").get(id=int(document_id))
            meta = doc.meta or {}
            source_type = normalize_source_type(
                meta.get("source_type") or meta.get("upload_intent") or meta.get("document_role")
            )
            upload_intent = source_type
            doc_title = doc.title or ""
        except Exception as e:
            logger.warning("MATCHUP_SOURCE_TYPE_LOOKUP_FAIL | doc=%s | err=%s", document_id, e)

    from academy.adapters.ai.detection.segment_dispatcher import (
        register_pdf_seg_tmp_dirs,
        segment_questions_multipage,
    )

    seg_result = segment_questions_multipage(local_path, source_type=source_type)
    register_pdf_seg_tmp_dirs(seg_result.get("tmp_dirs") or [])
    pages = seg_result.get("pages", [])
    total_boxes = seg_result.get("total_boxes", 0)

    # ── Excluded pages (Phase 5-deep 검수 UI) ──
    # 학원장이 검수 모달에서 "이 페이지 제외" 누른 페이지 idx 리스트.
    # payload 우선, 없으면 doc.meta.excluded_pages를 직접 조회 (race-safe).
    excluded_pages_raw = payload.get("excluded_pages")
    excluded: set[int] = set()
    if isinstance(excluded_pages_raw, (list, tuple)):
        for v in excluded_pages_raw:
            try:
                excluded.add(int(v))
            except (TypeError, ValueError):
                pass
    if not excluded and document_id:
        try:
            from apps.domains.matchup.models import MatchupDocument
            doc_row = MatchupDocument.objects.only("meta").get(id=int(document_id))
            for v in (doc_row.meta or {}).get("excluded_pages") or []:
                try:
                    excluded.add(int(v))
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            logger.warning("MATCHUP_EXCLUDED_PAGES_LOOKUP_FAIL | doc=%s | err=%s", document_id, e)
    if excluded:
        before = len(pages)
        pages = [p for p in pages if int(p.get("page_index", -1)) not in excluded]
        total_boxes = sum(len(p.get("boxes") or []) for p in pages)
        logger.info(
            "MATCHUP_EXCLUDED_PAGES_APPLIED | job=%s | doc=%s | excluded=%s | pages %d→%d",
            job_id, document_id, sorted(excluded), before, len(pages),
        )

    record_progress(
        job_id, "segmentation", 30,
        step_index=1, step_total=5,
        step_name_display="문제 분할",
        step_percent=100, tenant_id=tenant_id,
    )

    # source_type은 segmentation 호출 전에 결정됨 (segment_dispatcher가 paper_type
    # 분류기에 handwriting_bias로 전달해야 STUDENT_ANSWER_PHOTO 분기가 작동).
    # 7-value: student_exam_photo / school_exam_pdf / commercial_workbook /
    #          academy_workbook / explanation / answer_key / other

    # ── 인덱싱 X 사이클 (explanation / answer_key) — 즉시 0 problems 반환 ──
    # 매치업 후보 vector search에 노이즈로 들어가는 것 차단. doc.meta에 마커 저장.
    if source_type in ("explanation", "answer_key"):
        logger.info(
            "MATCHUP_SKIP_INDEXING | job=%s | doc=%s | source_type=%s",
            job_id, document_id, source_type,
        )
        record_progress(
            job_id, "done", 100,
            step_index=5, step_total=5,
            step_name_display="완료",
            step_percent=100, tenant_id=tenant_id,
        )
        return AIResult.done(job_id, {
            "problems": [],
            "document_id": document_id,
            "problem_count": 0,
            "source_type": source_type,
            "skipped_for_indexing": True,
            "skip_reason": "explanation/answer_key는 매치업 인덱스 대상 X",
            "paper_type_summary": {
                "primary": source_type, "warnings": [],
                "distribution": {source_type: 1}, "low_confidence_ratio": 0.0,
            },
        })

    # legacy title 휴리스틱 — source_type=other인 doc에 한해 fallback (하위 호환).
    if source_type == "other" and doc_title:
        title_l = doc_title
        if any(k in title_l for k in (
            "시험지", "중간고사", "기말고사", "모의고사", "TEST", "Test",
            "기출 통과", "고난도",
        )):
            source_type = "school_exam_pdf"
            upload_intent = source_type

    page_count = len(pages)
    avg_per_page = total_boxes / max(1, page_count)

    # paper_type 집계 — 결과 반환에 한 번만 계산. 분기 결정에는 이제 사용하지 않음.
    paper_type_summary = _aggregate_paper_types(pages)

    # ── page-as-problem 강제 폴백 폐기 (2026-05-05 학원장 directive) ──
    # 폐기 사유:
    # - is_over_extracted / is_low_confidence_doc / is_commercial / is_student_photo
    #   네 트리거가 운영 default가 되어 분리 인프라 결함이 metric에 가려졌음.
    # - T2 박철 운영 실측 (2026-05-05): 193 doc 진짜 분리 성공률 1.6% (3 doc 페이지당 5+).
    #   commercial_workbook 6 doc + student_exam_photo 7 doc = 100% page_fallback.
    #   doc#166 (26-1m 숙명여고) 332 페이지 → 266 problems 모두 페이지=problem.
    # - 폴백이 학원장에게 "안전"한 게 아니라 매치업 자체를 무용하게 만듦.
    #
    # 새 정책:
    # - anchor 결과 그대로 사용. over-extraction 무관 (학원장 검수에서 직접 정리).
    # - anchor 0이면 VLM 시도. VLM 실패 시 그 페이지는 problems 0 (정직한 실패).
    # - is_commercial/is_student_photo 강제 page-as-problem 제거 — VLM 시도.
    # - 학원장 검수 UI의 직접 자르기로 분리 결함 보강.

    # 강제 VLM primary (Phase 8+ 후속, 2026-05-05):
    #   학원장 manual ground truth 비교 결함:
    #   - commercial_workbook 책자: cover/index/해설/답안 페이지 가짜 problem
    #   - school_exam_pdf: anchor OCR 일부 번호 누락 시 fallback counter가 잘못
    #     매핑 (doc 204 Q24 자리에 시험지 27번 들어감)
    #   _pages_via_vlm 안의 page_role D-3 게이트 + VLM 정확 number 매핑이 본질 fix.
    force_vlm_primary = _source_type_gate_allows(
        "MATCHUP_VLM_FORCE_PRIMARY_TYPES",
        source_type,
        ("commercial_workbook", "school_exam_pdf"),
    ) and _real_vlm_vision_configured()
    if force_vlm_primary:
        logger.info(
            "MATCHUP_FORCE_VLM_PRIMARY | job=%s | doc=%s | source=%s "
            "(anchor 결과 무시 + page_role 게이트 적용)",
            job_id, document_id, source_type,
        )
        for p in pages:
            p["text_regions"] = []
            p["boxes"] = []
            p["numbers"] = []
        total_boxes = 0

    vlm_page_role_stats: Optional[Dict[str, Any]] = None
    if not force_vlm_primary:
        vlm_page_role_stats = _apply_vlm_page_role_filter(
            pages,
            source_type=source_type,
            document_id=document_id,
            tenant_id=tenant_id,
        )
        total_boxes = sum(len(p.get("boxes") or []) for p in pages)

    if total_boxes == 0:
        logger.info(
            "MATCHUP_NO_BOXES | job_id=%s | VLM 시도 (page-as-problem 폴백 폐기됨)",
            job_id,
        )
        questions_raw, vlm_stats = _pages_via_vlm(
            pages, document_id, job_id, tenant_id=tenant_id,
        )
        paper_type_summary = _aggregate_paper_types(pages)
        paper_type_summary["vlm_auto_split"] = vlm_stats
    else:
        questions_raw = _boxes_to_questions(pages)
        vlm_empty_stats = _augment_questions_with_vlm_for_empty_pages(
            pages,
            questions_raw,
            document_id=document_id,
            job_id=job_id,
            tenant_id=tenant_id,
        )
        paper_type_summary = _aggregate_paper_types(pages)
        if vlm_empty_stats:
            paper_type_summary["vlm_empty_page_fill"] = vlm_empty_stats
        vlm_underfilled_stats = _augment_questions_with_vlm_for_underfilled_pages(
            pages,
            questions_raw,
            source_type=source_type,
            document_id=document_id,
            tenant_id=tenant_id,
        )
        if vlm_underfilled_stats:
            paper_type_summary["vlm_underfilled_page_fill"] = vlm_underfilled_stats

    if vlm_page_role_stats:
        paper_type_summary["vlm_page_role_filter"] = vlm_page_role_stats

    if not questions_raw:
        return AIResult.done(job_id, {
            "problems": [],
            "document_id": document_id,
            "problem_count": 0,
        })

    # ── Skeleton INSERT — 신규 업로드 사용자에게 즉시 부분 결과 노출 ──
    # 백엔드 파이프라인이 끝(Step 5)에 일괄 INSERT하던 결함으로, 신규 업로드 doc은
    # 처음부터 끝까지 빈 화면이었음 (재분석은 이전 결과 노출). 세그멘테이션 직후
    # 번호+bbox+page_index만 가진 skeleton row를 미리 INSERT하여, 프론트
    # ProblemGrid의 부분 결과 banner가 즉시 동작하도록.
    # 최종 callbacks._handle_matchup_ai_result가 `doc.problems.all().delete()` 후
    # bulk_create하므로 정합성에 영향 없음 (삭제→재생성 패턴 유지).
    if document_id:
        try:
            _insert_skeleton_problems(questions_raw, document_id, tenant_id, job_id)
        except Exception:  # noqa: BLE001
            logger.warning("MATCHUP_SKELETON_INSERT_FAIL | job=%s | doc=%s",
                           job_id, document_id, exc_info=True)

    # ── Step 2: OCR (50%) ──
    record_progress(
        job_id, "ocr", 40,
        step_index=2, step_total=5,
        step_name_display="텍스트 추출",
        step_percent=0, tenant_id=tenant_id,
    )

    _extract_texts(questions_raw, job_id)

    record_progress(
        job_id, "ocr", 50,
        step_index=2, step_total=5,
        step_name_display="텍스트 추출",
        step_percent=100, tenant_id=tenant_id,
    )

    # ── Step 3: 임베딩 (80%) ──
    record_progress(
        job_id, "embedding", 60,
        step_index=3, step_total=5,
        step_name_display="AI 분석",
        step_percent=0, tenant_id=tenant_id,
    )

    _generate_embeddings(questions_raw, job_id)

    record_progress(
        job_id, "embedding", 80,
        step_index=3, step_total=5,
        step_name_display="AI 분석",
        step_percent=100, tenant_id=tenant_id,
    )

    # ── Step 4: 이미지 업로드 (90%) ──
    # "이미지 저장" 라벨은 사용자가 의미를 알기 어려워 "썸네일/이미지 캐시"로 명시.
    # 78페이지 PDF에서 5분간 "이미지 저장 85%" 정체로 보이던 UX 정체 해소를 위해
    # Box minimum area filter (2026-05-10 자가 시각 검수 fix) — fragment box 사전 reject.
    # V11 over-segmentation 으로 발문만/보기만/선택지만 cut 되는 fragment 가 다수.
    # 면적 너무 작거나 aspect ratio 비정상 box 는 Hybrid VLM 호출 전 silent drop.
    # ENV flag MATCHUP_BOX_AREA_MIN_RATIO (default off if "0", on with "0.05" 등).
    box_area_min_ratio_raw = os.environ.get("MATCHUP_BOX_AREA_MIN_RATIO", "0")
    try:
        box_area_min_ratio = float(box_area_min_ratio_raw)
    except (TypeError, ValueError):
        box_area_min_ratio = 0.0
    if box_area_min_ratio > 0:
        import cv2 as _cv2_for_area
        before_filter = len(questions_raw)
        kept_after_area = []
        rejected_area = 0
        for _q in questions_raw:
            try:
                _img = _cv2_for_area.imread(_q.get("image_path", ""))
                if _img is None or not _q.get("bbox"):
                    kept_after_area.append(_q)
                    continue
                _ih, _iw = _img.shape[:2]
                _x, _y, _w, _h = _q["bbox"]
                page_area = _ih * _iw
                box_area = _w * _h
                if page_area > 0 and (box_area / page_area) < box_area_min_ratio:
                    rejected_area += 1
                    continue
                kept_after_area.append(_q)
            except Exception:
                kept_after_area.append(_q)
        if rejected_area > 0:
            logger.info(
                "MATCHUP_BOX_AREA_FILTER | doc=%s | before=%d | rejected=%d | min_ratio=%s",
                document_id, before_filter, rejected_area, box_area_min_ratio,
            )
        questions_raw = kept_after_area

    # Auto-merge fragment 후처리 (2026-05-10 자가 검수 fix) — V11 over-segmentation
    # 으로 한 문항이 발문/보기/선택지 fragment 로 쪼개진 case 자동 합침.
    # 알고리즘:
    #   1. page_index 별 그룹 → column-aware 그룹 (clean_pdf_dual=2 / quadrant=4)
    #   2. column 안 y_top sort
    #   3. 인접 box (y_gap < threshold) + 다른 number 인 case 만 합침 보류 (false merge 방지)
    #   4. 같은 number 또는 number 0 (미부여) 인 fragment 만 vertical union
    # ENV flag MATCHUP_AUTO_MERGE_FRAGMENT (default off). T1 점진.
    if os.environ.get("MATCHUP_AUTO_MERGE_FRAGMENT", "0") == "1":
        try:
            primary_pt = (paper_type_summary or {}).get("primary") or ""
            cc = 1
            if primary_pt in ("clean_pdf_dual", "scan_dual"):
                cc = 2
            elif primary_pt == "quadrant":
                cc = 4
            # page_index → list of question ref
            from collections import defaultdict
            by_page = defaultdict(list)
            for _q in questions_raw:
                pi = (_q.get("meta") or {}).get("page_index")
                if isinstance(pi, int):
                    by_page[pi].append(_q)
            merged_count = 0
            kept_after_merge = []
            already_merged = set()  # id(q) of merged-into-others
            for pi, page_qs in by_page.items():
                # column 별 grouping
                # column width 는 page width 기준 — page render 시 1700px standard
                # 여기서 normalized 0~1 가정 (worker bbox 가 px 인지 norm 인지 확인 필요)
                # 보수적 처리: bbox=(x,y,w,h) px 가정 (matchup_pipeline 표준)
                # 같은 column 인지 = box center x / page_width / cc 로 col_idx 비교
                # page width 추정 = max box (x+w)
                if not page_qs:
                    continue
                page_width = max(
                    (q.get("bbox") or [0, 0, 0, 0])[0] + (q.get("bbox") or [0, 0, 0, 0])[2]
                    for q in page_qs if q.get("bbox")
                ) or 1
                col_buckets = defaultdict(list)
                for q in page_qs:
                    bbox = q.get("bbox")
                    if not bbox:
                        kept_after_merge.append(q)
                        continue
                    bx, by_, bw, bh = bbox
                    cx = bx + bw / 2
                    col_idx = max(0, min(cc - 1, int(cx / (page_width / cc))))
                    col_buckets[col_idx].append((by_, q))
                for col_idx, items in col_buckets.items():
                    # y_top 기준 sort
                    items.sort(key=lambda kv: kv[0])
                    i = 0
                    while i < len(items):
                        _, q = items[i]
                        if id(q) in already_merged:
                            i += 1
                            continue
                        bbox = q.get("bbox")
                        if not bbox:
                            kept_after_merge.append(q)
                            i += 1
                            continue
                        cur_x, cur_y, cur_w, cur_h = bbox
                        cur_num = q.get("number") or 0
                        # 다음 box 와 합침 시도 — 같은 number 또는 둘 다 number 0
                        j = i + 1
                        while j < len(items):
                            _, nq = items[j]
                            if id(nq) in already_merged:
                                j += 1
                                continue
                            nb = nq.get("bbox")
                            if not nb:
                                break
                            nx, ny, nw, nh = nb
                            nnum = nq.get("number") or 0
                            # number 다른데 둘 다 양수면 다른 문항 — skip
                            if cur_num and nnum and cur_num != nnum:
                                break
                            # vertical 인접 (gap 작음) — gap 기준 = 현재 box height 의 30% 이내
                            gap = ny - (cur_y + cur_h)
                            if gap < 0:
                                # overlap - merge 후보
                                pass
                            elif gap > cur_h * 0.30:
                                break
                            # 합침 — bbox union
                            new_x = min(cur_x, nx)
                            new_y = cur_y
                            new_x2 = max(cur_x + cur_w, nx + nw)
                            new_y2 = ny + nh
                            cur_x, cur_w = new_x, new_x2 - new_x
                            cur_h = new_y2 - cur_y
                            if not cur_num and nnum:
                                cur_num = nnum
                            already_merged.add(id(nq))
                            merged_count += 1
                            j += 1
                        # update q in-place with merged bbox
                        q["bbox"] = (cur_x, cur_y, cur_w, cur_h)
                        if cur_num:
                            q["number"] = cur_num
                        kept_after_merge.append(q)
                        i = j
            if merged_count > 0:
                logger.info(
                    "MATCHUP_AUTO_MERGE_FRAGMENT | doc=%s | merged=%d | total_after=%d",
                    document_id, merged_count, len(kept_after_merge),
                )
                questions_raw = kept_after_merge
        except Exception as _merge_err:  # noqa: BLE001
            logger.warning(
                "AUTO_MERGE_FRAGMENT_OUTER_FAIL | doc=%s | err=%s",
                document_id, _merge_err,
            )

    # Hybrid VLM verifier (2026-05-09 basic_definition_2026_05_09 SSOT) —
    # YOLO false positive 후처리. PoC v3 검증 prec 0.55→0.97. ENV flag
    # MATCHUP_HYBRID_VLM_TENANTS 매치 시만 적용. fail-soft.
    # 2026-05-10 자가 검수 후 prompt 강화 — problem_fragment 카테고리 reject.
    #
    # 2026-05-15 workbook source_type skip — academy_workbook / commercial_workbook 는
    # marginal anchor main 단위 cut (그림+stem+sub-items 통째) 의도. VLM 이 main 박스를
    # "problem_fragment" 로 잘못 분류해 reject 하는 결함 (운영 doc 774: 105 → 60, 42 fragment
    # 거짓 reject). VLM 은 시험지 sub-item cut 단위 학습 — 워크북 main 단위 와 호환 X.
    WORKBOOK_VLM_SKIP_TYPES = {"academy_workbook", "commercial_workbook"}
    try:
        from academy.adapters.ai.detection.hybrid_vlm_classifier import (
            is_hybrid_vlm_enabled_for_tenant,
            filter_questions_by_hybrid_vlm,
        )
        hybrid_source_allowed = _source_type_gate_allows(
            "MATCHUP_HYBRID_VLM_SOURCE_TYPES",
            source_type,
            tuple(
                sorted(
                    {
                        "student_exam_photo",
                        "school_exam_pdf",
                        "other",
                    }
                )
            ),
        )
        if (
            is_hybrid_vlm_enabled_for_tenant(tenant_id)
            and hybrid_source_allowed
        ):
            before_count = len(questions_raw)
            vlm_origin_questions = [
                q for q in questions_raw
                if _is_vlm_origin_question(q)
            ]
            hybrid_candidates = [
                q for q in questions_raw
                if not _is_vlm_origin_question(q)
            ]
            filtered_candidates, hvlm_stats = filter_questions_by_hybrid_vlm(
                hybrid_candidates,
                document_id=document_id,
                tenant_id=tenant_id,
                cost_cap_calls=200,
            )
            if vlm_origin_questions:
                hvlm_stats["vlm_origin_bypassed"] = len(vlm_origin_questions)
            questions_raw = filtered_candidates + vlm_origin_questions
            questions_raw.sort(
                key=lambda q: (
                    int(q.get("page_index") or 0),
                    int((q.get("bbox") or [0, 0, 0, 0])[1]),
                    int((q.get("bbox") or [0, 0, 0, 0])[0]),
                    int(q.get("number") or 0),
                )
            )
            logger.info(
                "HYBRID_VLM_FILTERED | doc=%s | before=%d | after=%d | stats=%s",
                document_id, before_count, len(questions_raw), hvlm_stats,
            )
        elif source_type in WORKBOOK_VLM_SKIP_TYPES and not hybrid_source_allowed:
            logger.info(
                "HYBRID_VLM_SKIP_WORKBOOK | doc=%s | source_type=%s | "
                "main 단위 cut 보존 (VLM 적용 X)",
                document_id, source_type,
            )
        elif not hybrid_source_allowed:
            logger.info(
                "HYBRID_VLM_SKIP_SOURCE_TYPE | doc=%s | source_type=%s",
                document_id, source_type,
            )
    except Exception as _hvlm_err:  # noqa: BLE001
        # fail-soft — filter 자체 실패 시 raw questions_raw 그대로
        logger.warning(
            "HYBRID_VLM_OUTER_FAIL | doc=%s | err=%s",
            document_id, _hvlm_err,
        )

    # 이미지 업로드 / CLIP 임베딩 / 페이지 캐시 3단계로 진행률 분산.
    record_progress(
        job_id, "upload_images", 85,
        step_index=4, step_total=5,
        step_name_display=f"문항 이미지 업로드 (0/{len(questions_raw)})",
        step_percent=0, tenant_id=tenant_id,
    )

    _upload_cropped_images(
        questions_raw, tenant_id, document_id, job_id,
        on_progress=lambda done, total: record_progress(
            job_id, "upload_images", 85,
            step_index=4, step_total=5,
            step_name_display=f"문항 이미지 업로드 ({done}/{total})",
            step_percent=int(done / total * 33) if total else 0,
            tenant_id=tenant_id,
        ),
        paper_type_summary=paper_type_summary,
    )

    # 이미지 CLIP 임베딩 — cropped 영역을 시각 임베딩으로 변환. 카메라 사진/
    # 스캔본의 OCR이 약해도 이미지 유사도로 매칭 보강 (find_similar_problems
    # ensemble 가중평균).
    record_progress(
        job_id, "upload_images", 87,
        step_index=4, step_total=5,
        step_name_display="시각 임베딩 생성",
        step_percent=33, tenant_id=tenant_id,
    )
    _generate_image_embeddings(questions_raw, job_id)
    _cleanup_cropped_image_temps(questions_raw)

    # 페이지 PNG도 같이 R2에 업로드 → ensure_document_page_images 캐시 hit.
    # 모달 첫 진입 PDF 다운로드 + 페이지 렌더 비용 사전 분산.
    record_progress(
        job_id, "upload_images", 88,
        step_index=4, step_total=5,
        step_name_display=f"페이지 캐시 생성 (0/{len(pages)})",
        step_percent=66, tenant_id=tenant_id,
    )
    page_image_keys, page_dimensions = _upload_page_images_for_modal_cache(
        pages, tenant_id, document_id, job_id,
        on_progress=lambda done, total: record_progress(
            job_id, "upload_images", 88,
            step_index=4, step_total=5,
            step_name_display=f"페이지 캐시 생성 ({done}/{total})",
            step_percent=66 + int(done / total * 33) if total else 66,
            tenant_id=tenant_id,
        ),
    )

    record_progress(
        job_id, "upload_images", 90,
        step_index=4, step_total=5,
        step_name_display="이미지 캐시 완료",
        step_percent=100, tenant_id=tenant_id,
    )

    # ── Step 5: 결과 반환 (100%) ──
    problems = []
    for q in questions_raw:
        meta_extra = q.get("meta_extra") or {}
        meta = {
            "page_index": q.get("page_index", 0),
            "bbox": q.get("bbox"),
        }
        # 공유 보기/자료 묶음 정보 (시판 교재 <보기>(N~M) 양식 등) 보존.
        # 매치업 검수 UI에서 묶음 표시 + 추천 결과에서 묶음 단위로 노출하도록 활용.
        if q.get("shared_with"):
            meta["shared_with"] = list(q["shared_with"])
        # format(essay/choice) 등은 _generate_embeddings에서 채워둠
        meta.update(meta_extra)
        problems.append({
            "number": q["number"],
            "text": q.get("text", ""),
            "image_key": q.get("image_key", ""),
            "embedding": q.get("embedding"),
            "image_embedding": q.get("image_embedding"),
            "meta": meta,
        })

    record_progress(
        job_id, "done", 100,
        step_index=5, step_total=5,
        step_name_display="완료",
        step_percent=100, tenant_id=tenant_id,
    )

    # 세그멘테이션 방식 — UI 표시 + 운영 관측용
    has_text_pages = sum(1 for p in pages if p.get("has_embedded_text"))
    scan_pages = len(pages) - has_text_pages
    if not problems:
        segmentation_method = "none"
    elif seg_result.get("is_pdf"):
        if has_text_pages == len(pages):
            segmentation_method = "text"
        elif has_text_pages == 0:
            segmentation_method = "ocr"
        else:
            segmentation_method = "mixed"
    else:
        segmentation_method = "image"

    return AIResult.done(job_id, {
        "problems": problems,
        "document_id": document_id,
        "problem_count": len(problems),
        "segmentation_method": segmentation_method,
        "page_image_keys": page_image_keys,
        "page_dimensions": page_dimensions,
        "paper_type_summary": paper_type_summary,
    })


# ── 내부 함수 ────────────────────────────────────────


def _page_confidence(page: Dict[str, Any]) -> Tuple[float, List[str]]:
    """단일 페이지 분리 신뢰도 (0~1) + 부족 신호 목록.

    Phase 3 (2026-05-02 학원장 directive): 검수 UI 우선순위 표시 + VLM fallback 트리거.

    신호 (이미 page dict에 있는 데이터로 계산, 추가 OCR 호출 X):
    - paper_type (known/unknown)
    - is_skip_page (cover/index/answer_key 휴리스틱 결과)
    - boxes 수 (정상 = 1~6 / 과다 = 8+ / 0 = 분리 실패)
    - has_embedded_text (PDF 텍스트 vs 스캔)

    Returns: (confidence 0~1, reasons: ["short_label", ...])
    """
    reasons: List[str] = []
    paper_type = page.get("paper_type") or "unknown"
    is_skip = bool(page.get("is_skip_page"))
    boxes = page.get("boxes") or []
    n_boxes = len(boxes)
    has_text = bool(page.get("has_embedded_text"))

    # skip page는 의도된 제외 → confidence 1.0 (검수 불필요)
    if is_skip:
        return 1.0, ["intentional_skip"]

    score = 1.0

    # paper_type 신호 — unknown은 텍스트로 분류 못한 케이스
    if paper_type == "unknown":
        score -= 0.30
        reasons.append("paper_type_unknown")
    elif paper_type == "student_answer_photo":
        score -= 0.40
        reasons.append("student_answer_photo")
    elif paper_type == "non_question":
        # is_skip_page에서 처리되어야 정상이지만, 미처리된 케이스 페널티
        score -= 0.20
        reasons.append("non_question_not_skipped")

    # boxes 수 신호 — 0은 분리 실패, 8+는 over-extract 의심
    if n_boxes == 0:
        score -= 0.30
        reasons.append("no_boxes_detected")
    elif n_boxes >= 8:
        score -= 0.15
        reasons.append("excessive_boxes_%d" % n_boxes)
    elif n_boxes == 1:
        # 1 box는 정상일 수도 있으나 dual-col에서 strip cut 의심 시그널
        if paper_type in ("scan_dual", "clean_pdf_dual", "quadrant"):
            score -= 0.15
            reasons.append("single_box_dual_layout")

    # 스캔본 + paper_type 모호 = OCR 부정확 가능성
    if not has_text and paper_type == "unknown":
        score -= 0.10
        reasons.append("scan_without_classification")

    # clamp [0, 1]
    score = max(0.0, min(1.0, score))
    return round(score, 2), reasons


def _aggregate_paper_types(pages: List[Dict]) -> Dict[str, Any]:
    """페이지별 paper_type을 doc 단위로 집계 + 경고 산출.

    Source 게이트의 핵심: 학생 답안지 폰사진(STUDENT_ANSWER_PHOTO)이 다수 섞이거나
    분류 불명(UNKNOWN) 비율이 높으면 자동분리 신뢰도가 낮아 어드민에서 사용자 경고가
    필요. callbacks가 이 결과를 doc.meta["paper_type_summary"]로 저장 → 프론트
    ProblemGrid가 경고 배너로 노출.

    Phase 3 (2026-05-02): per-page confidence + low_conf_pages 리스트 추가.
    검수 UI가 우선순위 페이지 표시 + VLM fallback 후보 식별.

    Returns:
      {
        "distribution": {"clean_pdf_single": N, ...},
        "low_confidence_ratio": 0.0~1.0,  # student_answer_photo + unknown 페이지 비율
        "primary": "clean_pdf_single",     # 가장 많은 유형
        "warnings": ["student_answer_photo_detected", ...],
        "low_conf_pages": [{"idx": int, "confidence": 0.0~1.0, "reasons": [...]}]
        "page_confidence_avg": 0.0~1.0,    # doc 전체 평균 신뢰도
      }
    """
    from collections import Counter

    if not pages:
        return {
            "distribution": {},
            "low_confidence_ratio": 0.0,
            "primary": "unknown",
            "warnings": [],
            "low_conf_pages": [],
            "page_confidence_avg": 0.0,
        }

    types = [p.get("paper_type") or "unknown" for p in pages]
    counter = Counter(types)
    total = len(types)

    low_conf_keys = ("student_answer_photo", "unknown")
    low_conf_count = sum(counter.get(k, 0) for k in low_conf_keys)
    low_conf_ratio = low_conf_count / max(1, total)

    # primary 결정 — 운영 audit (2026-05-04 doc#274/276 등 22 doc) 발견:
    # non_question 페이지가 most_common이지만 본문 페이지가 다수인 doc도 22건 존재.
    # 학원장 검수 UI에 "non_question 다수" 노출돼 본문 doc인데 misclassification.
    # → non_question은 priority 낮춤 (본문 분류 가능한 paper_type 있으면 그것 우선).
    _CONTENT_TYPES = (
        "clean_pdf_single", "clean_pdf_dual", "scan_single", "scan_dual",
        "quadrant", "student_answer_photo", "side_notes",
    )
    content_counter = Counter({k: v for k, v in counter.items() if k in _CONTENT_TYPES})
    if content_counter and counter.most_common(1)[0][0] == "non_question":
        # most_common이 non_question이지만 본문 paper_type이 있으면 본문 priority
        primary = content_counter.most_common(1)[0][0]
    else:
        primary = counter.most_common(1)[0][0]

    warnings: List[str] = []
    if counter.get("student_answer_photo", 0) >= 1:
        warnings.append("student_answer_photo_detected")
    if low_conf_ratio >= 0.3:
        warnings.append("low_confidence_source_majority")
    if counter.get("non_question", 0) >= total * 0.5 and total >= 4:
        # 절반 이상이 비-문항 페이지 — source 부적합 의심
        warnings.append("non_question_majority")

    # Phase 3: per-page confidence
    LOW_CONF_THRESHOLD = 0.55  # 임계값 미만 = 어드민 검수 큐 + VLM fallback 후보
    confidences: List[float] = []
    low_conf_pages: List[Dict[str, Any]] = []
    for idx, p in enumerate(pages):
        conf, reasons = _page_confidence(p)
        confidences.append(conf)
        if conf < LOW_CONF_THRESHOLD and not p.get("is_skip_page"):
            low_conf_pages.append({
                "idx": p.get("page_index", idx),
                "confidence": conf,
                "reasons": reasons,
                "paper_type": p.get("paper_type") or "unknown",
                "n_boxes": len(p.get("boxes") or []),
            })
    avg_conf = round(sum(confidences) / max(1, len(confidences)), 3)

    if low_conf_pages and len(low_conf_pages) >= max(2, total * 0.2):
        # 20% 이상 페이지가 low_conf → review_required 경고 추가
        warnings.append("review_required")

    # auto_recommend_page_states 우선순위 1 지원 (P1 fix 2026-05-11):
    #   services.auto_recommend_page_states 가 paper_type_summary["pages"][i].paper_type
    #   lookup 으로 explanation/answer_key/cover/index/non_question 페이지 skip 추천.
    #   기존 schema 에 pages 필드 부재로 dead branch — fallback(problem 0 페이지)만
    #   작동. SKIP_RELEVANT 페이지만 추출해 doc.meta payload 영향 최소화
    #   (대부분 doc 5~20개 entry).
    SKIP_RELEVANT_TYPES = {
        "explanation", "answer_key", "cover", "index", "non_question",
    }
    pages_skip_hint = [
        {
            "page_index": p.get("page_index", idx),
            "paper_type": p.get("paper_type") or "unknown",
        }
        for idx, p in enumerate(pages)
        if (p.get("paper_type") or "") in SKIP_RELEVANT_TYPES
    ]

    return {
        "distribution": dict(counter),
        "low_confidence_ratio": round(low_conf_ratio, 3),
        "primary": primary,
        "warnings": warnings,
        "low_conf_pages": low_conf_pages,
        "page_confidence_avg": avg_conf,
        "pages": pages_skip_hint,
    }


def _detect_per_page_restart_from_pages(pages: List[Dict]) -> bool:
    """`pages[].numbers` 로 per-page-restart (워크북/메인자료) 패턴 감지.

    `question_splitter._detect_per_page_restart` 와 같은 2 신호 휴리스틱이지만
    pipeline 단계에서 page dict 의 `numbers` 필드를 직접 본다 (split_questions 의
    검증된 후 결과 + cross-page validate 통과 후).

    Why: `_boxes_to_questions` global dedup `seen_numbers` 가 워크북 anchor 를
    DB unique (doc, number) 위반 방지로 catastrophic drop. 워크북 모드면 counter
    fallback 으로 강제해 모든 박스 보존.
    """
    pages_with_anchors = 0
    pages_with_low = 0
    pages_per_number: dict[int, int] = {}
    for page in pages:
        nums = page.get("numbers") or []
        int_nums = {int(n) for n in nums if isinstance(n, int)}
        if not int_nums:
            continue
        pages_with_anchors += 1
        if int_nums & {1, 2, 3}:
            pages_with_low += 1
        for n in int_nums:
            pages_per_number[n] = pages_per_number.get(n, 0) + 1

    if pages_with_anchors < 5:
        return False

    threshold_low = max(5, int(pages_with_anchors * 0.4))
    signal_a = pages_with_low >= threshold_low

    repeated = sum(1 for cnt in pages_per_number.values() if cnt >= 2)
    unique_anchors = len(pages_per_number)
    signal_b = (
        repeated >= 5
        and unique_anchors > 0
        and (repeated / unique_anchors) >= 0.3
    )

    return signal_a or signal_b


def _boxes_to_questions(pages: List[Dict]) -> List[Dict]:
    """세그멘테이션 결과를 문제 리스트로 변환.

    번호 우선순위:
      1. **시험지 (continuous numbering)**: segment dispatcher 가 ``numbers`` 를
         정수 리스트로 보내면 그대로 사용. 시험지 실제 문항 번호와 정렬.
      2. **워크북/메인자료 (per-page-restart)**: `_detect_per_page_restart_from_pages`
         감지 시 segment number 무시하고 counter fallback. 각 box 가 unique
         number 를 얻어 DB unique(doc, number) 제약 위반 없이 모든 박스 보존.
      3. ``numbers`` 비거나 None 섞여 있으면 (OpenCV fallback) counter fallback.

    Why per-page-restart 분기:
      - 워크북은 페이지마다 anchor 1, 2, 3... 리셋 → segment number 그대로 쓰면
        후속 페이지 anchor 가 `seen_numbers` 충돌로 drop (운영 doc 768 73 페이지
        실측 262 anchor → 27 problem catastrophic drop).
      - counter fallback 으로 1, 2, ..., N (anchor 총 수) 순차 부여 → 충돌 없음.
        UI display 가 페이지-로컬 anchor 와 다르긴 하지만 학원장 manual_create
        부담 0 으로 가는 가치가 훨씬 큼 ([[basic_definition]] 합격선 = 학원장 노동).

    이전엔 항상 (2)만 사용해서, 텍스트/OCR이 어떤 박스를 누락하면 그 이후의 모든
    번호가 시험지 실제 번호와 어긋났다 (DB Q10 = 시험지 11번 문제 식). 이 fix로
    박스→번호 매핑이 시험지 원본과 일치한다.
    """
    questions = []
    q_num = 1
    seen_numbers: set = set()  # 문서 전역 dedupe — unique(document, number) 충돌 방지
    # paper_type 페이지 게이트 (Phase 8+, 2026-05-05 학원장 manual ground truth):
    #   T1 doc 624 manual=56 vs T2 doc 216 anchor=59. 자동 결과 p3:13, p36:11, p38:10
    #   = cover/index/끝부분 페이지에서 가짜 problem. anchor splitter가 비-문항
    #   페이지의 box를 problem으로 등록하던 결함. 페이지 단위 paper_type이
    #   non_question/explanation/answer_key/cover/index면 boxes skip.
    # ── Per-page-restart 감지 — 워크북/메인자료에서 segment number 충돌 회피 ──
    is_per_page_restart = _detect_per_page_restart_from_pages(pages)
    if is_per_page_restart:
        logger.info(
            "MATCHUP_PER_PAGE_RESTART_DETECTED | total_pages=%d | counter mode (segment numbers 무시)",
            len(pages),
        )

    for page in pages:
        page_idx = page["page_index"]
        img_path = page["image_path"]
        boxes = page.get("boxes", []) or []
        numbers = page.get("numbers", []) or []
        page_type = (page.get("paper_type") or "").strip().lower()
        if page_type in _NON_PROBLEM_PAGE_TYPES:
            logger.info(
                "MATCHUP_SKIP_NON_PROBLEM_PAGE | page=%s | type=%s | boxes=%d (skipped)",
                page_idx, page_type, len(boxes),
            )
            continue
        # 번호가 boxes와 같은 길이이고 모두 정수면 신뢰. 그렇지 않으면 fallback.
        # 단, per-page-restart 패턴이면 segment 번호 그대로 쓰면 cross-page 충돌 발생 →
        # 강제 counter mode.
        use_segment_numbers = (
            (not is_per_page_restart)
            and len(numbers) == len(boxes)
            and all(isinstance(n, int) for n in numbers)
        )
        for i, bbox in enumerate(boxes):
            if use_segment_numbers:
                num = int(numbers[i])
            else:
                # 카운터 fallback도 충돌 안 나도록 빈 번호로 점프
                while q_num in seen_numbers:
                    q_num += 1
                num = q_num
                q_num += 1
            # 같은 number가 이미 등록됐으면 skip — DB unique constraint와 정합.
            # UI의 problem_count가 dispatch 수와 실제 DB count 어긋나는 문제 차단.
            if num in seen_numbers:
                logger.info(
                    "MATCHUP_DEDUPE_DROP | num=%d page=%d (이미 등록됨)",
                    num, page_idx,
                )
                continue
            seen_numbers.add(num)
            # per-page-restart 인 경우 local_number 를 meta 에 보존 (학원장 검수 시 참조용)
            entry = {
                "number": num,
                "page_index": page_idx,
                "image_path": img_path,
                "bbox": list(bbox),
            }
            if is_per_page_restart and i < len(numbers) and isinstance(numbers[i], int):
                entry["local_number"] = int(numbers[i])
            questions.append(entry)
    return questions


def _detect_page_with_vlm(
    page: Dict,
    document_id,
    tenant_id: str | int | None = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Gemini vision으로 page_role/paper_type/bbox 후보를 한 번 받아온다.

    반환 result는 real Gemini 응답일 때만 제공한다. mock 또는 gemini 실패 후 mock
    fallback은 운영 판단 신호로 쓰지 않는다.
    """
    try:
        from academy.adapters.ai.detection.vlm_fallback import detect_problems_vision
        result = detect_problems_vision(
            image_path=page["image_path"],
            page_meta={
                "document_id": document_id,
                "tenant_id": tenant_id,
                "page_index": page["page_index"],
                "page_width": page.get("width"),
                "page_height": page.get("height"),
            },
        )
    except Exception as e:
        logger.warning(
            "MATCHUP_VLM_PAGE_DETECT_FAIL | doc=%s | page=%s | err=%s",
            document_id, page.get("page_index"), e,
        )
        return None, None

    adapter = (result.debug or {}).get("adapter", "")
    if adapter != "gemini":
        return None, None

    raw_paper_type = getattr(result, "paper_type", None)
    if not raw_paper_type or raw_paper_type == "unknown":
        raw_paper_type = None
    return result, raw_paper_type


def _page_text_for_vlm(page: Dict) -> str:
    """페이지 role 분류에 넘길 텍스트를 만든다.

    `segment_dispatcher`가 text-PDF에서 추출한 page_text를 우선 사용한다. 이미지
    전용 문서는 여기서 OCR을 새로 호출하지 않는다. page-role filter의 목적은
    비문항 페이지 제거라서, 비싼 vision bbox 호출은 명시 opt-in으로만 둔다.
    """
    text = str(page.get("page_text") or "").strip()
    if text:
        return text[:8000]

    blocks = page.get("text_blocks") or []
    parts: List[str] = []
    for block in blocks:
        value = getattr(block, "text", None)
        if value is None and isinstance(block, dict):
            value = block.get("text")
        if value:
            parts.append(str(value))
    return "\n".join(parts).strip()[:8000]


def _classify_page_role_with_vlm_text(
    page: Dict,
    document_id,
    tenant_id: str | int | None = None,
) -> Optional[Any]:
    """Gemini Flash-Lite text 분류로 page_role을 판정한다.

    real Gemini 응답만 운영 판단에 사용한다. Gemini 실패 후 mock fallback은 보수적으로
    무시해서 기존 boxes를 지우지 않는다.
    """
    page_text = _page_text_for_vlm(page)
    if not page_text:
        return None

    try:
        from academy.adapters.ai.detection.vlm_fallback import classify_page_role_text
        result = classify_page_role_text(
            ocr_text=page_text,
            page_meta={
                "document_id": document_id,
                "tenant_id": tenant_id,
                "page_index": page.get("page_index"),
            },
        )
    except Exception as e:
        logger.warning(
            "MATCHUP_VLM_TEXT_ROLE_FAIL | doc=%s | page=%s | err=%s",
            document_id, page.get("page_index"), e,
        )
        return None

    adapter = (getattr(result, "debug", {}) or {}).get("adapter", "")
    if adapter != "gemini":
        return None
    return result


def _vlm_result_role_value(result: Any) -> str:
    role = getattr(result, "page_role", "")
    return str(getattr(role, "value", role) or "").strip().lower()


def _apply_vlm_page_role_filter(
    pages: List[Dict],
    *,
    source_type: str,
    document_id,
    tenant_id: str | int | None = None,
) -> Optional[Dict[str, Any]]:
    """Gemini page-role로 표지/목차/해설/정답 페이지를 자동 제외한다.

    기본 off. `MATCHUP_VLM_PAGE_ROLE_FILTER=1`일 때만 동작한다. 이 경로는
    기존 박스를 VLM bbox로 대체하지 않고, skip 확신이 높은 페이지의 기존
    boxes/text_regions만 제거한다.
    """
    if not _env_flag("MATCHUP_VLM_PAGE_ROLE_FILTER", False):
        return None
    if not document_id:
        return {"enabled": True, "skipped_reason": "no_document_id"}
    use_vision_fallback = _env_flag("MATCHUP_VLM_PAGE_ROLE_USE_VISION_FALLBACK", False)
    if not _real_vlm_text_configured() and not (
        use_vision_fallback and _real_vlm_vision_configured()
    ):
        return {"enabled": True, "skipped_reason": "real_vlm_not_configured"}
    if not _tenant_gate_allows("MATCHUP_VLM_PAGE_ROLE_FILTER_TENANTS", tenant_id):
        return {"enabled": True, "skipped_reason": "tenant_not_enabled"}
    if not _source_type_gate_allows(
        "MATCHUP_VLM_PAGE_ROLE_FILTER_SOURCE_TYPES",
        source_type,
        (
            "academy_workbook",
            "commercial_workbook",
            "school_exam_pdf",
            "student_exam_photo",
        ),
    ):
        return {"enabled": True, "skipped_reason": "source_type_not_enabled"}

    max_calls = max(0, _env_int("MATCHUP_VLM_PAGE_ROLE_MAX_CALLS", 50))
    min_conf = max(
        0.0,
        min(1.0, _env_float("MATCHUP_VLM_PAGE_ROLE_MIN_CONFIDENCE", 0.75)),
    )
    stats: Dict[str, Any] = {
        "enabled": True,
        "candidates": 0,
        "attempted": 0,
        "text_attempted": 0,
        "vision_attempted": 0,
        "text_missing": 0,
        "skipped_pages": 0,
        "paper_type_updates": 0,
        "cost_cap_hit": False,
        "min_confidence": min_conf,
        "vision_fallback": use_vision_fallback,
    }

    for page in pages:
        if stats["attempted"] >= max_calls:
            stats["cost_cap_hit"] = True
            break

        boxes = page.get("boxes") or []
        text_regions = page.get("text_regions") or []
        page_type = (page.get("paper_type") or "").strip().lower()
        if page.get("is_skip_page") or page_type in _NON_PROBLEM_PAGE_TYPES:
            continue
        if not boxes and not text_regions:
            continue

        stats["candidates"] += 1
        result = None
        raw_paper_type = None
        if _page_text_for_vlm(page) and _real_vlm_text_configured():
            stats["attempted"] += 1
            stats["text_attempted"] += 1
            result = _classify_page_role_with_vlm_text(
                page, document_id, tenant_id=tenant_id,
            )
        elif use_vision_fallback:
            stats["attempted"] += 1
            stats["vision_attempted"] += 1
            result, raw_paper_type = _detect_page_with_vlm(
                page, document_id, tenant_id=tenant_id,
            )
            if raw_paper_type:
                page["paper_type"] = raw_paper_type
                stats["paper_type_updates"] += 1
        else:
            stats["text_missing"] += 1

        if result is None:
            continue

        role_value = _vlm_result_role_value(result)
        confidence = float(getattr(result, "confidence", 0.0) or 0.0)
        should_skip = bool(getattr(result, "should_skip", False))

        debug = page.setdefault("paper_type_debug", {})
        debug["vlm_page_role_filter"] = {
            "role": role_value,
            "confidence": round(confidence, 3),
            "should_skip": should_skip,
        }

        if (
            confidence >= min_conf
            and (should_skip or role_value in _NON_PROBLEM_PAGE_ROLES)
        ):
            page["boxes"] = []
            page["numbers"] = []
            page["text_regions"] = []
            page["is_skip_page"] = True
            page["paper_type"] = raw_paper_type or "non_question"
            stats["skipped_pages"] += 1

    return stats


def _augment_questions_with_vlm_for_empty_pages(
    pages: List[Dict],
    questions: List[Dict],
    *,
    document_id,
    job_id: str,
    tenant_id: str | int | None = None,
) -> Optional[Dict[str, Any]]:
    """일부 페이지만 자동분리 실패한 문서에서 빈 페이지를 Gemini로 보정한다."""
    if not _env_flag("MATCHUP_VLM_AUTO_SPLIT", True):
        return None
    if not _env_flag("MATCHUP_VLM_FILL_EMPTY_PAGES", True):
        return None
    if not document_id:
        return None
    if not _real_vlm_vision_configured():
        return {
            "enabled": True,
            "skipped_reason": "real_vlm_not_configured",
            "candidates": 0,
            "attempted": 0,
            "added": 0,
        }
    if not _tenant_gate_allows("MATCHUP_VLM_FILL_EMPTY_PAGE_TENANTS", tenant_id):
        return None

    max_calls = max(0, _env_int("MATCHUP_VLM_EMPTY_PAGE_MAX_CALLS", 30))
    candidates: List[Dict] = []
    cost_cap_hit = False
    for page in pages:
        if len(candidates) >= max_calls:
            cost_cap_hit = True
            break
        page_type = (page.get("paper_type") or "").strip().lower()
        if page.get("is_skip_page") or page_type in _NON_PROBLEM_PAGE_TYPES:
            continue
        if page.get("boxes") or page.get("text_regions"):
            continue
        candidates.append(page)

    if not candidates:
        return {
            "enabled": True,
            "candidates": 0,
            "attempted": 0,
            "added": 0,
        }

    vlm_questions, vlm_stats = _pages_via_vlm(
        candidates, document_id, job_id, tenant_id=tenant_id,
    )
    used_numbers = {
        int(q.get("number"))
        for q in questions
        if str(q.get("number", "")).lstrip("-").isdigit()
    }
    next_number = 1
    remapped = 0
    added = 0
    for q in vlm_questions:
        try:
            proposed = int(q.get("number") or 0)
        except (TypeError, ValueError):
            proposed = 0
        if proposed <= 0 or proposed in used_numbers:
            while next_number in used_numbers:
                next_number += 1
            q.setdefault("meta_extra", {})["original_vlm_number"] = proposed
            q["number"] = next_number
            proposed = next_number
            remapped += 1
        used_numbers.add(proposed)
        q.setdefault("meta_extra", {})["engine"] = "vlm"
        q["meta_extra"]["vlm_reason"] = "empty_page_fallback"
        questions.append(q)
        added += 1

    stats: Dict[str, Any] = {
        "enabled": True,
        "candidates": len(candidates),
        "attempted": len(candidates),
        "added": added,
        "remapped_numbers": remapped,
        "cost_cap_hit": cost_cap_hit,
    }
    stats.update({f"vlm_{k}": v for k, v in vlm_stats.items()})
    return stats


def _bbox_iou_xywh(a: Any, b: Any) -> float:
    """(x, y, w, h) bbox IoU. 잘못된 입력은 0으로 취급한다."""
    try:
        ax, ay, aw, ah = [float(v) for v in a]
        bx, by, bw, bh = [float(v) for v in b]
    except Exception:
        return 0.0
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _is_vlm_origin_question(question: Dict[str, Any]) -> bool:
    meta = question.get("meta_extra")
    return isinstance(meta, dict) and meta.get("engine") == "vlm"


def _expected_min_boxes_for_underfilled_page(page: Dict[str, Any], source_type: str) -> int:
    """VLM 보강을 시도할 만큼 명백히 적게 잘린 페이지의 최소 기대 box 수."""
    page_type = (page.get("paper_type") or "").strip().lower()
    if page_type == "student_answer_photo" or source_type == "student_exam_photo":
        return 4
    if page_type in ("scan_dual", "quadrant"):
        return 4
    if source_type == "school_exam_pdf" and page_type == "scan_single":
        return 2
    return 0


def _augment_questions_with_vlm_for_underfilled_pages(
    pages: List[Dict],
    questions: List[Dict],
    *,
    source_type: str,
    document_id,
    tenant_id: str | int | None = None,
) -> Optional[Dict[str, Any]]:
    """스캔/학생촬영 페이지가 일부만 잘린 경우 Gemini bbox로 누락 문항을 보강한다.

    Empty-page 보강만으로는 운영 T1 학생 촬영 시험지처럼 Q4/Q6/Q8만 잡히고
    Q1/Q2/Q3/Q5/Q7이 빠지는 under-cut을 복구할 수 없다. 이 경로는 기존 box를
    지우지 않고 VLM이 찾은 missing number만 추가한다.
    """
    if not _env_flag("MATCHUP_VLM_AUTO_SPLIT", True):
        return None
    if not _env_flag("MATCHUP_VLM_FILL_UNDERFILLED_PAGES", True):
        return None
    if not document_id:
        return None
    if source_type not in ("student_exam_photo", "school_exam_pdf"):
        return None
    if not _real_vlm_vision_configured():
        return {
            "enabled": True,
            "skipped_reason": "real_vlm_not_configured",
            "candidates": 0,
            "attempted": 0,
            "added": 0,
        }
    if not _tenant_gate_allows("MATCHUP_VLM_FILL_EMPTY_PAGE_TENANTS", tenant_id):
        return None

    max_calls = max(0, _env_int("MATCHUP_VLM_UNDERFILLED_PAGE_MAX_CALLS", 20))
    candidates: List[Dict] = []
    cost_cap_hit = False
    for page in pages:
        if len(candidates) >= max_calls:
            cost_cap_hit = True
            break
        page_type = (page.get("paper_type") or "").strip().lower()
        if page.get("is_skip_page") or page_type in _NON_PROBLEM_PAGE_TYPES:
            continue
        existing_count = len(page.get("boxes") or [])
        expected_min = _expected_min_boxes_for_underfilled_page(page, source_type)
        if expected_min <= 0:
            continue
        if 0 < existing_count < expected_min:
            candidates.append(page)

    if not candidates:
        return {
            "enabled": True,
            "candidates": 0,
            "attempted": 0,
            "added": 0,
            "cost_cap_hit": cost_cap_hit,
        }

    used_numbers = {
        int(q.get("number"))
        for q in questions
        if str(q.get("number", "")).lstrip("-").isdigit()
    }
    next_number = 1
    added = 0
    duplicate_number_skips = 0
    overlap_skips = 0
    pages_used = 0
    paper_type_updates = 0

    def _next_free_number() -> int:
        nonlocal next_number
        while next_number in used_numbers:
            next_number += 1
        value = next_number
        next_number += 1
        return value

    for page in candidates:
        vlm, vlm_paper_type = _try_vlm_problem_bboxes(
            page, document_id, tenant_id=tenant_id,
        )
        if vlm_paper_type:
            page["paper_type"] = vlm_paper_type
            debug = page.setdefault("paper_type_debug", {})
            debug["vlm_underfilled_page_fill"] = {
                "paper_type": vlm_paper_type,
                "bbox_validated": vlm is not None,
            }
            paper_type_updates += 1
        if vlm is None:
            continue

        page_idx = page.get("page_index")
        existing_page_questions = [
            q for q in questions
            if q.get("page_index") == page_idx and q.get("bbox")
        ]
        page_added = 0
        for prob in vlm.problems:
            bbox = list(prob.bbox)
            if any(_bbox_iou_xywh(bbox, q.get("bbox")) >= 0.30 for q in existing_page_questions):
                overlap_skips += 1
                continue
            try:
                proposed = int(prob.number or 0)
            except (TypeError, ValueError):
                proposed = 0
            if proposed > 0 and proposed in used_numbers:
                duplicate_number_skips += 1
                continue
            number = proposed if proposed > 0 else _next_free_number()
            used_numbers.add(number)
            q_entry = {
                "number": number,
                "page_index": page_idx,
                "image_path": page["image_path"],
                "bbox": bbox,
                "meta_extra": {
                    "engine": "vlm",
                    "vlm_reason": "underfilled_page_fallback",
                },
            }
            if proposed <= 0:
                q_entry["meta_extra"]["original_vlm_number"] = proposed
            questions.append(q_entry)
            existing_page_questions.append(q_entry)
            added += 1
            page_added += 1
        if page_added:
            pages_used += 1

    return {
        "enabled": True,
        "candidates": len(candidates),
        "attempted": len(candidates),
        "added": added,
        "pages_used": pages_used,
        "cost_cap_hit": cost_cap_hit,
        "duplicate_number_skips": duplicate_number_skips,
        "overlap_skips": overlap_skips,
        "paper_type_updates": paper_type_updates,
    }


def _validate_vlm_bboxes(result, image_path: str, page_idx: int) -> Optional[Any]:
    """VLM 결과의 다층 검증 — 운영 시각 검수 결함 4종(D-1~D-4) 차단.

    운영 사고 (2026-05-03 시각 검수): 시험지 6 doc 모두 VLM 결함 패턴.
    - D-1: 4-quadrant 오분할 (Q1이 두 박스로 split, 보기/답안만 cell)
    - D-2: mid-cut strip (cell 가로 띠 한 줄)
    - D-3: 표지/헤더가 problem (PageRole=problem 응답)
    - D-4: 시험지 헤더 prepend (페이지 위쪽 너무 멀리 시작)

    각 게이트 실패 시 페이지 전체 reject → page-as-problem fallback 적용.
    이미지 dim 못 가져오면 통과 (회귀 안전망).

    Returns: result (통과) 또는 None (reject — page-as-problem fallback).
    """
    import cv2
    from academy.adapters.ai.detection.vlm_fallback import PageRole

    # D-3: page_role 게이트 — should_skip + cover/index/explanation/answer_key
    if result.page_role in (
        PageRole.COVER, PageRole.INDEX,
        PageRole.EXPLANATION, PageRole.ANSWER_KEY,
    ):
        logger.info(
            "VLM_GATE_REJECT_PAGE_ROLE | page=%s | role=%s",
            page_idx, result.page_role.value,
        )
        return None

    try:
        img = cv2.imread(image_path)
    except Exception:
        return result
    if img is None:
        return result
    h_img, w_img = img.shape[:2]
    if h_img < 100 or w_img < 100:
        return result

    # D-2/D-4 임계값 완화 (Phase 4, 2026-05-05):
    #   기존 D-2 min_h_ratio=0.05 + D-4 header_zone=0.08 — 시험지 양식 4-quadrant
    #   오분할 detect 안전망. 그러나 박철T 워크북 진단 결과 (doc#327/325/286):
    #     - 단답형/공식 문항 h_ratio 2~3% (D-2 기존 0.05에서 reject)
    #     - 첫 문항 y_ratio 4~5% (D-4 기존 0.08에서 reject)
    #   = 박철T 73 doc 수제작 + 36 doc 메인 약 100 doc이 게이트에서 차단됨.
    #
    # 새 게이트:
    #   D-2 strip: h_ratio < 1% AND w_ratio > 50% (진짜 가로 strip cut만 reject)
    #   D-2 thin:  w_ratio < 10% (좁은 cell)
    #   D-4 header: y_ratio < 4% (4% 이하만 header 침범 의심)
    header_zone = h_img * 0.04
    min_h_strip_ratio = 0.01    # D-2: 1% 이하 + w 50%+ 일 때만 strip 의심
    min_w_strip_ratio = 0.50    # D-2 strip 패턴의 w 임계
    min_w_ratio = 0.10           # D-2 thin: 좁은 cell 차단

    for p in result.problems:
        try:
            x, y, w, h = p.bbox
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue

        # D-2: 진짜 strip cut만 reject (가로로 긴 1% 미만 cell)
        if (h / h_img) < min_h_strip_ratio and (w / w_img) > min_w_strip_ratio:
            logger.info(
                "VLM_GATE_REJECT_STRIP | page=%s | num=%s | h_ratio=%.3f w_ratio=%.3f",
                page_idx, p.number, h / h_img, w / w_img,
            )
            return None
        if (w / w_img) < min_w_ratio:
            logger.info(
                "VLM_GATE_REJECT_THIN | page=%s | num=%s | w_ratio=%.3f",
                page_idx, p.number, w / w_img,
            )
            return None

        # D-4: bbox y_min — 헤더 침범 차단 (4% 이하만)
        if y < header_zone:
            logger.info(
                "VLM_GATE_REJECT_HEADER | page=%s | num=%s | y=%d zone=%.0f",
                page_idx, p.number, y, header_zone,
            )
            return None

    # D-1: bbox 인접 중첩 — 두 박스가 같은 영역 잡으면 4-quadrant 오분할
    # 단 공유 보기/자료 묶음(shared_with)은 같은 bbox가 정상 — IoU reject 면제.
    n = len(result.problems)
    for i in range(n):
        try:
            x1, y1, w1, h1 = result.problems[i].bbox
        except (TypeError, ValueError):
            continue
        num_i = int(result.problems[i].number)
        shared_i = set(getattr(result.problems[i], "shared_with", []) or [])
        for j in range(i + 1, n):
            try:
                x2, y2, w2, h2 = result.problems[j].bbox
            except (TypeError, ValueError):
                continue
            num_j = int(result.problems[j].number)
            shared_j = set(getattr(result.problems[j], "shared_with", []) or [])
            # 공유 보기 묶음: i가 j를 share or j가 i를 share — IoU 게이트 skip
            if num_j in shared_i or num_i in shared_j:
                continue
            ix = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
            iy = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
            inter = ix * iy
            union = w1 * h1 + w2 * h2 - inter
            iou = inter / max(1, union)
            if iou > 0.3:
                logger.info(
                    "VLM_GATE_REJECT_OVERLAP | page=%s | nums=(%s,%s) | iou=%.2f",
                    page_idx, num_i, num_j, iou,
                )
                return None

    # D-1 보강: number 시퀀스 — 중복 또는 큰 jump
    nums = sorted(int(p.number) for p in result.problems)
    if len(set(nums)) < len(nums):
        logger.info("VLM_GATE_REJECT_DUP_NUMS | page=%s | nums=%s", page_idx, nums)
        return None
    if len(nums) >= 2:
        gaps = [nums[i + 1] - nums[i] for i in range(len(nums) - 1)]
        if max(gaps) > 10 and (min(gaps) <= 0 or max(gaps) > min(gaps) * 5):
            logger.info(
                "VLM_GATE_REJECT_SEQ_JUMP | page=%s | nums=%s",
                page_idx, nums,
            )
            return None

    return result


def _try_vlm_problem_bboxes(
    page: Dict, document_id, tenant_id: str | int | None = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """단일 페이지에 vision_VLM 호출. (validated_result, raw_paper_type) 튜플 반환.

    paper_type은 게이트와 무관하게 항상 VLM 응답 그대로 추출 (B-2):
    bbox는 4종 결함(D-1~D-4) 게이트에서 reject되어도 paper_type 분류 신호는
    유효하므로 page meta에 보존. None은 VLM 호출 자체 실패 또는 unknown.

    Cost cap (P0-2, 2026-05-04): tenant_id를 vlm_fallback._gemini_request에 전달
    하여 tenant별 일별 호출 cap 적용.

    bbox validated_result:
      1차 게이트: adapter == "gemini" + should_skip False + conf >= 0.80 + problems >= 2
      2차 게이트: _validate_vlm_bboxes (D-1~D-4)
      통과 시 result, 실패 시 None.
    """
    result, raw_paper_type = _detect_page_with_vlm(
        page, document_id, tenant_id=tenant_id,
    )
    if result is None:
        return None, raw_paper_type

    # bbox 게이트는 별도 — paper_type은 응답 받자마자 보존
    if result.should_skip:
        return None, raw_paper_type
    # 1차 게이트 완화 (2026-05-05): `< 2` → `< 1`.
    # 박철 수제작 1-문항/페이지 layout (doc#327 등 73 doc) 차단 결함 fix.
    # D-1~D-4 (validate) 게이트가 cell 크기/IoU/헤더 검증으로 false positive 차단 유지.
    if result.confidence < 0.80 or len(result.problems) < 1:
        return None, raw_paper_type
    validated = _validate_vlm_bboxes(result, page["image_path"], page.get("page_index"))
    return validated, raw_paper_type


def _pages_via_vlm(
    pages: List[Dict], document_id, job_id: str, *,
    tenant_id: str | int | None = None,
) -> Tuple[List[Dict], Dict[str, Any]]:
    """anchor 0 페이지에 VLM bbox 시도 — page-as-problem 폴백 폐기됨.

    페이지별 라우팅 (학원장 directive 2026-05-05):
      - anchor 1+: sub-crop (anchor 결과 그대로)
      - anchor 0 + VLM 통과: VLM bbox sub-crop
      - anchor 0 + VLM 실패: 페이지 skip (problems 0). 학원장 검수 UI 직접 자르기로 보강.

    이전에는 VLM 실패 시 page-as-problem 폴백이 자동 진입하여 metric상 "성공"으로
    잡혔으나, 박철 운영 실측 (193 doc 진짜 성공률 1.6%) 결과 폴백 자체가 분리 인프라
    결함을 가리는 함정으로 판명. 정직한 실패 + 학원장 직접 보강이 운영 정책.
    """
    import os as _os

    use_vlm = _os.getenv("MATCHUP_VLM_AUTO_SPLIT", "1") == "1"
    questions: List[Dict] = []
    seen_numbers: set = set()
    fallback_counter = 1
    pixel_scale = 200.0 / 72.0  # _PDF_TO_PIXEL_SCALE — segment_dispatcher와 동일

    vlm_pages_used = 0
    vlm_problems_added = 0
    vlm_pages_attempted = 0
    pages_skipped_no_split = 0

    for page in pages:
        page_idx = page["page_index"]
        img_path = page["image_path"]
        text_regions = page.get("text_regions") or []

        # 1. anchor 1+ → sub-crop (anchor 결과 그대로 사용)
        if text_regions:
            for region in text_regions:
                num = int(region.number)
                if num in seen_numbers:
                    continue
                seen_numbers.add(num)
                rx0, ry0, rx1, ry1 = region.bbox
                bbox_px = [
                    int(rx0 * pixel_scale),
                    int(ry0 * pixel_scale),
                    int((rx1 - rx0) * pixel_scale),
                    int((ry1 - ry0) * pixel_scale),
                ]
                questions.append({
                    "number": num,
                    "page_index": page_idx,
                    "image_path": img_path,
                    "bbox": bbox_px,
                    "meta_extra": {"engine": "native_pdf"},
                })
            continue

        # 2. anchor 0 → VLM bbox 시도
        if use_vlm and document_id:
            vlm_pages_attempted += 1
            vlm, vlm_paper_type = _try_vlm_problem_bboxes(page, document_id, tenant_id=tenant_id)

            if vlm_paper_type:
                page["paper_type"] = vlm_paper_type
                debug = page.setdefault("paper_type_debug", {})
                debug["vlm_override"] = True
                debug["vlm_paper_type"] = vlm_paper_type
                debug["bbox_validated"] = vlm is not None

            if vlm is not None:
                vlm_pages_used += 1
                for prob in vlm.problems:
                    # VLM 원본 number 신뢰 시도 (정상 양수 + 미중복) → 시판 교재의
                    # 본문 번호(12, 13...)와 학원장 manual 결과 일치성 확보.
                    # 공유 보기 묶음(shared_with)은 같은 bbox + 같은 페이지에 다수
                    # problem (12, 13) 등록 — 각자 자기 번호로.
                    prob_num = int(prob.number) if prob.number and prob.number > 0 else 0
                    if prob_num and prob_num not in seen_numbers:
                        num = prob_num
                    else:
                        while fallback_counter in seen_numbers:
                            fallback_counter += 1
                        num = fallback_counter
                        fallback_counter += 1
                    seen_numbers.add(num)
                    q_entry = {
                        "number": num,
                        "page_index": page_idx,
                        "image_path": img_path,
                        "bbox": list(prob.bbox),
                        "meta_extra": {
                            "engine": "vlm",
                            "vlm_reason": "auto_split",
                        },
                    }
                    shared = list(getattr(prob, "shared_with", []) or [])
                    if shared:
                        q_entry["shared_with"] = shared
                    questions.append(q_entry)
                    vlm_problems_added += 1
                continue

        # 3. VLM 실패/비활성 → 페이지 skip. page-as-problem 폴백 폐기됨.
        # 학원장 검수 UI의 직접 자르기로 보강.
        pages_skipped_no_split += 1

    logger.info(
        "MATCHUP_VLM_AUTO_DONE | job=%s doc=%s | use_vlm=%s | "
        "vlm_attempted=%d vlm_used=%d vlm_problems=%d skipped=%d total=%d",
        job_id, document_id, use_vlm,
        vlm_pages_attempted, vlm_pages_used, vlm_problems_added,
        pages_skipped_no_split, len(questions),
    )
    return questions, {
        "enabled": use_vlm,
        "pages_attempted": vlm_pages_attempted,
        "pages_used": vlm_pages_used,
        "problems_added": vlm_problems_added,
        "pages_skipped_no_split": pages_skipped_no_split,
    }


def _extract_texts(questions: List[Dict], job_id: str) -> None:
    """
    bbox 기반 OCR 블록 매칭으로 문항별 텍스트 추출.

    접근:
      1. 페이지별 OCR 블록(줄 단위 bbox)을 한 번에 획득 (lru_cache 덕에 dispatcher와
         중복 호출 없음)
      2. 각 문항 bbox와 겹치는 블록을 모아 텍스트 연결
      3. bbox 없는 문항은 페이지 전체 텍스트 할당

    블록 기반은 페이지 전체 텍스트 + 정규식 번호 분할(legacy) 보다 정확.
    2단 레이아웃/그림/서답형 등에서 텍스트가 정확한 문항에 매핑된다.
    """
    blocks_backend = _load_ocr_blocks_backend()
    if blocks_backend is None:
        logger.info(
            "MATCHUP_TEXT_LEGACY | job_id=%s | OCR blocks unavailable, using legacy path",
            job_id,
        )
        _extract_texts_legacy(questions, job_id)
        return

    # 페이지별 OCR 블록 캐싱 (이미 google_ocr_blocks에 lru_cache 존재 — 추가 보험)
    page_blocks_cache: Dict[int, list] = {}
    page_images: Dict[int, str] = {}

    for q in questions:
        pi = q.get("page_index", 0)
        if pi not in page_images:
            page_images[pi] = q["image_path"]

    for pi, img_path in page_images.items():
        try:
            page_blocks_cache[pi] = blocks_backend(img_path)
        except Exception:
            logger.warning(
                "MATCHUP_TEXT_OCR_FAIL | job_id=%s | page=%d",
                job_id, pi, exc_info=True,
            )
            page_blocks_cache[pi] = []

    # 문항별로 bbox에 겹치는 블록만 연결
    for q in questions:
        pi = q.get("page_index", 0)
        blocks = page_blocks_cache.get(pi, [])
        bbox = q.get("bbox")

        if not blocks:
            q["text"] = ""
            continue

        if not bbox:
            q["text"] = "\n".join(b.text for b in blocks)
            continue

        bx, by, bw, bh = bbox
        bx1, by1 = bx + bw, by + bh

        relevant: List[Tuple[float, float, str]] = []
        for blk in blocks:
            ox = max(0.0, min(float(bx1), blk.x1) - max(float(bx), blk.x0))
            oy = max(0.0, min(float(by1), blk.y1) - max(float(by), blk.y0))
            overlap = ox * oy
            block_area = max(1.0, (blk.x1 - blk.x0) * (blk.y1 - blk.y0))
            if overlap / block_area >= 0.5:
                relevant.append((blk.y0, blk.x0, blk.text))

        relevant.sort(key=lambda t: (t[0], t[1]))
        q["text"] = "\n".join(t[2] for t in relevant)

    # 여전히 텍스트가 없는 문항은 페이지 전체 텍스트로 폴백
    for q in questions:
        if q.get("text"):
            continue
        pi = q.get("page_index", 0)
        blocks = page_blocks_cache.get(pi, [])
        if blocks:
            q["text"] = "\n".join(b.text for b in blocks)
        else:
            q["text"] = ""

    # 페이지 노이즈(워터마크/푸터/단원헤더) 정제 — display + embedding 양쪽 적용.
    for q in questions:
        if q.get("text"):
            q["text"] = strip_page_noise(q["text"])

    # box-merge text trim — 한 problem 텍스트에 추가 anchor 발견 시 그 위치 이전까지로 trim.
    # 운영 케이스 (Tenant 2 doc#131 q4): "13. 표는... 15. 그림은..." 두 문항 합쳐짐.
    _trim_box_merged_text(questions)

    # 정제 후에도 잔존하는 box-merge 케이스에 검수 배지 표시 (UI 가이드).
    _flag_merge_suspect(questions)

    # number↔content 매핑 검증 — 신뢰성 붕괴(C10 mismatch 56%) 1차 차단선.
    _verify_problem_numbers(questions)

    # 자동 품질 점수 — 매치업 인덱싱 게이트 (P0-2, 2026-05-04).
    _compute_quality_score(questions)


def _compute_quality_score(questions: List[Dict]) -> None:
    """problem당 quality_score 계산 + low_quality flag.

    운영 사고 fix (2026-05-04): 시각 검수에서 발견된 결함 cell이 매치업 검색
    인덱싱에 그대로 들어가 학원에 잘못된 결과 전달. 자동 품질 점수로 검색 후보
    게이트를 걸어 결함 cell을 검색에서 제외 (find_similar_problems가 low_quality
    exclude). 검수 UI는 low_quality cell을 우선순위로 표시.

    점수 (0~1):
    - bbox 적합 (0.30): bbox 있고 적당한 크기 (page-as-problem은 0.15)
    - text anchor 일치 (0.30): meta_extra.number_mismatch 없음
    - text 길이 충분 (0.20): len(text) >= 30자 (보기/답안만 cell 차단)
    - 본문 패턴 (0.20): meta_extra.no_anchor_in_text 없음

    Threshold: score < 0.7 → meta_extra.low_quality=True.
    """
    for q in questions:
        score = 0.0
        text = (q.get("text") or "").strip()
        bbox = q.get("bbox")
        meta_extra = q.get("meta_extra") or {}

        # 1. bbox 적합
        if bbox:
            try:
                _, _, w, h = bbox
                if w > 100 and h > 100:
                    score += 0.30
                elif w > 50 and h > 50:
                    score += 0.15  # 작은 박스는 부분 점수
            except (TypeError, ValueError):
                pass
        else:
            score += 0.15  # page-as-problem fallback (페이지 단위 매칭 가치)

        # 2. text anchor 일치
        if not meta_extra.get("number_mismatch"):
            score += 0.30

        # 3. text 길이 충분
        if len(text) >= 30:
            score += 0.20
        elif len(text) >= 10:
            score += 0.10

        # 4. 본문 패턴 (보기/답안만 cell 아님)
        if not meta_extra.get("no_anchor_in_text"):
            score += 0.20

        q.setdefault("meta_extra", {})["quality_score"] = round(score, 2)
        if score < 0.7:
            q["meta_extra"]["low_quality"] = True

    # 통계 로깅
    low_count = sum(
        1 for q in questions
        if (q.get("meta_extra") or {}).get("low_quality")
    )
    if low_count:
        logger.warning(
            "MATCHUP_QUALITY_SCORE | low_quality=%d/%d (인덱싱 게이트 적용)",
            low_count, len(questions),
        )


_MERGE_INNER_ANCHOR = re.compile(
    r"(?:^|\n)\s*(\d{1,2})\s*[.)]\s*(?=[가-힣A-Za-z(<\[])",
)


def _verify_problem_numbers(questions: List[Dict]) -> None:
    """problem.text 첫 anchor 번호와 q.number 일치 검증 — number↔content mismatch 차단.

    매치업 결과 PDF에서 "Q3 적중자료" 자리에 Q5 본문이 표시되던 신뢰성 결함의 1차 차단선.
    T2 시험지 doc#177/#294에서 56% 발생한 C10 mismatch — 분리 자체는 됐으나 DB의
    problem.number와 image의 본문 번호가 어긋난 케이스.

    검증 결과:
    - mismatch면 q.meta_extra["number_mismatch"] = {"db": db_num, "ocr": ocr_num} 기록.
    - 어드민 UI는 이 플래그로 검수 배지 표시 + 사용자가 manual crop으로 보정.
    - 자동 reject는 안 함 — false positive(OCR이 anchor를 잘못 인식한 케이스) 우려.

    적용 범위:
    - bbox 있는 problem만 (분리 정상) — 페이지 폴백(bbox=None)은 페이지 전체 텍스트라 번호 검증 부적합.
    - first_line의 첫 80자에서 anchor 추출 — 매치업 OCR이 헤더/푸터를 본문 앞에 prepend하지 않음 가정.
    """
    from academy.domain.tools.question_splitter import _extract_question_number

    # 보기/답안 마커 — 본문 cropping 결함 시 (다음 문항의 보기 부분만 잡힌 케이스)
    # 이런 cell은 첫 줄에 anchor 번호가 없고 보기 마커로 시작하는 게 특징.
    # T2 doc#148 reanalyze (2026-05-03)에서 VLM 4-quadrant 오분할로 1번 문항이
    # 두 cell로 split — DB#2가 "<보기> ㄱ. ... ① ㄱ ② ㄴ" 만 있는 보기/답안 cell.
    # 어드민 검수 UI가 우선순위 표시할 수 있게 flag.
    _ANSWER_MARKERS = ("<보기>", "ㄱ.", "ㄴ.", "ㄷ.", "ㄹ.", "①", "②", "③", "④", "⑤")

    mismatch_count = 0
    no_anchor_count = 0
    checked = 0
    for q in questions:
        if not q.get("bbox"):
            continue
        text = (q.get("text") or "").strip()
        if not text:
            continue
        first_line = text.split("\n", 1)[0][:80]
        ocr_num = _extract_question_number(first_line)
        if ocr_num is None:
            # anchor 없음 — 보기/답안 마커로 시작하면 본문 cropping 결함 의심.
            stripped = first_line.lstrip()
            if any(stripped.startswith(m) for m in _ANSWER_MARKERS):
                q.setdefault("meta_extra", {})["no_anchor_in_text"] = True
                no_anchor_count += 1
            continue
        db_num = q.get("number")
        if db_num is None:
            continue
        checked += 1
        if int(ocr_num) != int(db_num):
            q.setdefault("meta_extra", {})["number_mismatch"] = {
                "db": int(db_num),
                "ocr": int(ocr_num),
            }
            mismatch_count += 1

    if mismatch_count or no_anchor_count:
        logger.warning(
            "MATCHUP_NUMBER_MISMATCH | mismatch=%d/%d no_anchor=%d (checked=%d)",
            mismatch_count, len(questions), no_anchor_count, checked,
        )


def _trim_box_merged_text(questions: List[Dict]) -> None:
    """한 problem 텍스트에 추가 anchor 발견 시 그 위치 이전까지로 trim.

    운영 케이스 (Tenant 2 doc#131 q4): "13. 표는... 15. 그림은..." 두 anchor가
    한 박스에 OCR 단계에서 합쳐져 problem 텍스트가 두 문항을 동시에 포함.
    임베딩이 두 문항 의미가 섞여 매치업 sim 노이즈로 작용.

    fix: q.text의 첫 anchor 자기 자신을 건너뛴 뒤, 추가 anchor 발견 시 그
    위치 이전까지로 trim. 두 번째 anchor 이후 텍스트는 다른 problem이므로
    제거하면 임베딩이 깨끗해진다. 이미지(R2)는 그대로라 사용자 표시는 영향 없음.
    """
    for q in questions:
        text = q.get("text") or ""
        # bbox 있는 문항(박스 분리 정상)에만 적용. 페이지 폴백 problem은 페이지 전체 텍스트라 trim 부적절.
        if not q.get("bbox"):
            continue
        if len(text) < 600:
            continue
        # 첫 30자 이후의 anchor만 검사 (자기 자신 anchor 제외)
        if len(text) <= 30:
            continue
        m = _MERGE_INNER_ANCHOR.search(text[30:])
        if m is None:
            continue
        cut_at = 30 + m.start()
        trimmed = text[:cut_at].rstrip()
        # trim 후 너무 짧아지면(< 80자) 원본 유지 — false anchor 방어
        if len(trimmed) < 80:
            continue
        q["text"] = trimmed
        q.setdefault("meta_extra", {})["text_trimmed"] = True


def _flag_merge_suspect(questions: List[Dict]) -> None:
    """한 problem 텍스트에 추가 anchor가 들어있으면 box-merge 의심.

    문항 시작 anchor 1개 외에 다른 N. 패턴이 본문 안에 추가로 등장하면
    인접 문항이 박스 분리 실패로 한 problem에 합쳐진 케이스. 매치업 화면에서
    매뉴얼 크롭/Ctrl+V paste 권장 배지를 띄우기 위해 meta에 표시.

    threshold:
      - bbox 있음 (페이지 폴백 problem 제외 — 학습자료 본문 항목번호 false positive 방지)
      - text 길이 800+ AND 추가 anchor 1+
      - _trim_box_merged_text가 trim한 problem은 표시 안 함 (정제됨)
    """
    for q in questions:
        # 페이지 폴백 (bbox=None) problem은 false positive 다수라 검사 제외.
        # 운영 케이스 (Tenant 2 doc#143/144/145 객서심화): 본문 항목번호 5./7./9.가
        # 자연 등장하는 학습자료. 페이지 폴백 적용된 doc은 자동분리 결과가 아니라
        # 페이지 단위라 box-merge 개념이 부정합.
        if not q.get("bbox"):
            continue
        # text trim된 problem은 이미 정제 — 표시 불필요
        if (q.get("meta_extra") or {}).get("text_trimmed"):
            continue
        text = q.get("text") or ""
        if len(text) < 800:
            continue
        scan_text = text[30:] if len(text) > 30 else ""
        anchors = _MERGE_INNER_ANCHOR.findall(scan_text)
        if len(anchors) >= 1:
            q.setdefault("meta_extra", {})["merge_suspect"] = True
            q["meta_extra"]["merge_inner_anchors"] = len(anchors)


def _load_ocr_blocks_backend():
    """google_ocr_blocks를 반환. 임포트 실패 시 None."""
    try:
        from academy.adapters.ai.ocr.google import google_ocr_blocks
        return google_ocr_blocks
    except ImportError:
        return None


def _extract_texts_legacy(questions: List[Dict], job_id: str) -> None:
    """Vision SDK가 없는 환경용 레거시 경로 — 전체 페이지 OCR + 정규식 번호 분할."""
    try:
        from academy.adapters.ai.ocr.google import google_ocr
    except ImportError:
        from academy.adapters.ai.ocr.tesseract import tesseract_ocr as google_ocr

    page_texts: Dict[int, str] = {}
    page_images: Dict[int, str] = {}
    for q in questions:
        pi = q.get("page_index", 0)
        if pi not in page_images:
            page_images[pi] = q["image_path"]

    for pi, img_path in page_images.items():
        try:
            result = google_ocr(img_path)
            page_texts[pi] = result.text if hasattr(result, "text") else str(result)
        except Exception:
            logger.warning(
                "Page OCR failed for page %d in job %s",
                pi, job_id, exc_info=True,
            )
            page_texts[pi] = ""

    for q in questions:
        pi = q.get("page_index", 0)
        full_text = page_texts.get(pi, "")
        if not full_text:
            q["text"] = ""
            continue
        if not q.get("bbox"):
            q["text"] = full_text
            continue
        q["text"] = _extract_text_for_question(full_text, q["number"], len(questions))

    for q in questions:
        if not q.get("text") and questions:
            pi = q.get("page_index", 0)
            q["text"] = page_texts.get(pi, "")

    # 페이지 노이즈 정제 — strip_page_noise (display + embedding 양쪽).
    for q in questions:
        if q.get("text"):
            q["text"] = strip_page_noise(q["text"])

    _trim_box_merged_text(questions)
    _flag_merge_suspect(questions)
    _verify_problem_numbers(questions)


def _extract_text_for_question(full_text: str, q_number: int, total: int) -> str:
    """전체 OCR 텍스트에서 문제 번호 기반으로 해당 문제 텍스트 추출."""
    import re
    lines = full_text.split("\n")

    # 문제 번호 패턴: "1.", "1)", "Q1", "문제 1" 등
    patterns = [
        rf"^{q_number}\s*[\.\):]",
        rf"^{q_number}\s",
        rf"^Q{q_number}[\.\s]",
    ]
    next_patterns = [
        rf"^{q_number + 1}\s*[\.\):]",
        rf"^{q_number + 1}\s",
        rf"^Q{q_number + 1}[\.\s]",
    ] if q_number < total else []

    start_idx = None
    end_idx = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if start_idx is None:
            for p in patterns:
                if re.match(p, stripped):
                    start_idx = i
                    break
        elif next_patterns:
            for p in next_patterns:
                if re.match(p, stripped):
                    end_idx = i
                    break
            if end_idx != len(lines):
                break

    if start_idx is not None:
        return "\n".join(lines[start_idx:end_idx]).strip()
    return ""


def _generate_embeddings(questions: List[Dict], job_id: str) -> None:
    """문제 텍스트에서 임베딩 생성 (in-place).

    임베딩에는 정제된 텍스트 사용(헤더/푸터/형식 단어 제거).
    원본 text는 사용자 표시용으로 q['text']에 그대로 보관.
    정제 텍스트는 q['text_for_embedding']에 임시 저장.
    """
    from academy.adapters.ai.embedding.service import get_embeddings

    # 정제 텍스트 + format 감지 (in-place)
    for q in questions:
        raw = q.get("text", "")
        cleaned = normalize_text_for_embedding(raw)
        q["text_for_embedding"] = cleaned
        q.setdefault("meta_extra", {})["format"] = detect_format(raw)

    non_empty = [(i, q["text_for_embedding"]) for i, q in enumerate(questions) if q["text_for_embedding"].strip()]

    if not non_empty:
        for q in questions:
            q["embedding"] = None
        return

    try:
        batch = get_embeddings([t for _, t in non_empty])
        idx_map = {orig_idx: vec_idx for vec_idx, (orig_idx, _) in enumerate(non_empty)}

        for i, q in enumerate(questions):
            if i in idx_map:
                q["embedding"] = batch.vectors[idx_map[i]]
            else:
                q["embedding"] = None
    except Exception:
        logger.warning("Embedding generation failed for job %s", job_id, exc_info=True)
        for q in questions:
            q["embedding"] = None


def _generate_image_embeddings(questions: List[Dict], job_id: str) -> None:
    """문제 이미지에서 CLIP 임베딩 생성 (in-place).

    텍스트 임베딩과 별도. 카메라 사진/스캔본 OCR이 약해도 시각 유사도로
    매치업 정확도 보강. find_similar_problems가 ensemble 가중평균 적용.

    실패해도 텍스트 임베딩만으로 매칭 가능하므로 fail-safe.
    """
    try:
        from academy.adapters.ai.embedding.image_service import get_image_embeddings
    except ImportError:
        for q in questions:
            q["image_embedding"] = None
        return

    # 각 problem의 cropped 이미지 경로. _upload_cropped_images 이후 호출되어
    # q["image_path"]는 페이지 PNG지만, R2 업로드 직전 cropped 영역을 별도 경로로
    # 보관해야 정확. 일단 페이지 전체 이미지로 임베딩 (학습자료/페이지 폴백 케이스).
    # bbox 있는 케이스는 cropped 영역이 더 정확할 수 있으나, 여기서는 비용 절감.
    paths: List[str] = []
    indices: List[int] = []
    for i, q in enumerate(questions):
        p = q.get("cropped_image_path") or q.get("image_path") or ""
        if p:
            paths.append(p)
            indices.append(i)

    if not paths:
        for q in questions:
            q["image_embedding"] = None
        return

    try:
        batch = get_image_embeddings(paths)
        for q in questions:
            q["image_embedding"] = None
        for idx, vec in zip(indices, batch.vectors):
            if vec:
                questions[idx]["image_embedding"] = vec
    except Exception:
        logger.warning("Image embedding generation failed for job %s", job_id, exc_info=True)
        for q in questions:
            q["image_embedding"] = None


def _upload_page_images_for_modal_cache(
    pages: List[Dict],
    tenant_id: str | None,
    document_id: str,
    job_id: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[List[str], List[Tuple[int, int]]]:
    """매뉴얼 크롭 모달 캔버스용 페이지 PNG를 R2에 업로드.

    매치업 자동분리 워커가 이미 임시 dir에 페이지 PNG를 만들었으므로 그것을
    R2의 ensure_document_page_images 캐시 위치(prefix/pages/NNN.png)에 일괄
    업로드. 콜백이 doc.meta.page_image_keys/page_dimensions에 저장.

    Returns: (page_keys, page_dimensions)
    """
    import io as _io
    try:
        from PIL import Image as _PILImage
        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
    except ImportError:
        return [], []
    if not document_id or not tenant_id:
        return [], []

    # prefix는 _upload_cropped_images와 동일 규칙 — 첫 problem image_key에서 추출.
    # 자동분리 결과의 image_key는 "tenants/{tid}/matchup/{uuid}/problems/{n}.png" 패턴.
    prefix = ""
    try:
        from apps.domains.matchup.models import MatchupDocument as _MD
        doc = _MD.objects.only("r2_key").get(id=int(document_id))
        parts = (doc.r2_key or "").split("/")
        if len(parts) >= 4 and parts[2] == "matchup":
            prefix = parts[3]
    except Exception:
        pass
    if not prefix:
        prefix = f"manual-{document_id}"

    page_keys: List[str] = []
    page_dimensions: List[Tuple[int, int]] = []
    total = len(pages)
    for processed, page in enumerate(pages, 1):
        idx = int(page.get("page_index", 0))
        img_path = page.get("image_path") or ""
        if not img_path:
            if on_progress:
                try:
                    on_progress(processed, total)
                except Exception:
                    pass
            continue
        try:
            with _PILImage.open(img_path) as im:
                im.load()
                w, h = im.size
                buf = _io.BytesIO()
                im.save(buf, "PNG", optimize=True)
                buf.seek(0)
            key = f"tenants/{tenant_id}/matchup/{prefix}/pages/{idx:03d}.png"
            upload_fileobj_to_r2_storage(
                fileobj=buf, key=key, content_type="image/png",
            )
            page_keys.append(key)
            page_dimensions.append((w, h))
        except Exception:
            logger.warning(
                "MATCHUP_PAGE_CACHE_UPLOAD_FAIL | job=%s | page=%d",
                job_id, idx, exc_info=True,
            )
        if on_progress and (processed % 5 == 0 or processed == total):
            try:
                on_progress(processed, total)
            except Exception:
                pass
    logger.info(
        "MATCHUP_PAGE_CACHE | job=%s | doc=%s | pages=%d",
        job_id, document_id, len(page_keys),
    )
    return page_keys, page_dimensions


def _column_count_for_paper_type(paper_type: str) -> int:
    """paper_type → column_count. 2분할/4분할 자료 column-aware crop 위해.

    basic_definition_2026_05_09 SSOT 사용자 directive: '문항 + 다른 문항 일부 → 주의'.
    좌측 column 의 box padding 이 우측 column 침범 방지 = 다른 문항 손상 0.

    매핑:
      clean_pdf_dual / scan_dual → 2 column
      quadrant → 4 column (현재 표본 X, 정책만)
      그 외 → 1 column (전체 폭)
    """
    pt = (paper_type or "").lower()
    if pt in ("clean_pdf_dual", "scan_dual"):
        return 2
    if pt == "quadrant":
        return 4
    return 1


def _upload_cropped_images(
    questions: List[Dict],
    tenant_id: str | None,
    document_id: str,
    job_id: str,
    on_progress: Optional[Callable[[int, int], None]] = None,
    paper_type_summary: Optional[Dict[str, Any]] = None,
) -> None:
    """크롭된 문제 이미지를 R2에 업로드 (in-place로 image_key 설정).

    부수효과: q["cropped_image_path"]에 임시 파일 경로 저장. 이미지 임베딩이
    페이지 전체가 아닌 cropped 영역을 사용하도록 — 시각 매칭 정확도 향상.

    paper_type_summary (2026-05-09 사용자 directive): column-aware crop 위해 전달.
    primary paper_type 기반 column_count 결정. None 이면 default 1 (single column).
    """
    import cv2
    import os as _os
    import tempfile as _tempfile
    import uuid as _uuid

    try:
        from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
    except ImportError:
        logger.warning("R2 storage not available, skipping image upload")
        return

    uuid_prefix = str(_uuid.uuid4())
    total = len(questions)

    # Column-aware padding: paper_type 기반 column_count 산출.
    # paper_type_summary.primary 또는 pages[i].paper_type 우선. fallback = 1.
    primary_paper_type = ""
    if isinstance(paper_type_summary, dict):
        primary_paper_type = str(paper_type_summary.get("primary") or "")
    default_column_count = _column_count_for_paper_type(primary_paper_type)

    for processed, q in enumerate(questions, 1):
        try:
            img = cv2.imread(q["image_path"])
            if img is None:
                continue

            if q.get("bbox"):
                x, y, w, h = q["bbox"]
                img_h, img_w = img.shape[:2]

                # Phase C step 2 (2026-05-09 basic_definition_2026_05_09 SSOT) —
                # over-crop padding + column-aware boundary clip.
                # 사용자 directive: '작게 잘라 손상 = 실패. 조금 크게 잘라 여백/출처 = 허용'.
                # 추가 directive: '2분할 / 4분할 자료에서 다른 문항 침범 X'.
                # ENV flag default off → T1 점진 → T2.
                if os.environ.get("MATCHUP_OVER_CROP_PADDING", "0") == "1":
                    # 자가 검수 (2026-05-10 doc 615 num=1 보기 ㄴ 잘림) 결과 padding
                    # 부족 발견. pad_y_bottom h*7%→15% min 12→30px (~1줄) 강화.
                    # auto-merge 가 fragment 합치기 + padding 이 마지막 줄 안전망.
                    pad_x = max(int(w * 0.05), 8)
                    pad_y_top = max(int(h * 0.03), 6)
                    pad_y_bottom = max(int(h * 0.15), 30)

                    # column-aware: 현재 box 가 속한 column 의 좌우 경계 안으로 padding clip.
                    # paper_type=clean_pdf_dual → 2 column → 좌측 box 의 우측 padding 이
                    # 페이지 중앙 (img_w/2) 못 넘게. 4분할 (quadrant) 동일 원리.
                    cc = default_column_count
                    if cc >= 2:
                        column_w = img_w / cc
                        box_center_x = x + w / 2
                        col_idx = int(box_center_x / column_w)
                        col_idx = max(0, min(cc - 1, col_idx))  # clip 0~cc-1
                        col_left = col_idx * column_w
                        col_right = (col_idx + 1) * column_w
                        new_x = max(col_left, x - pad_x)
                        new_x2 = min(col_right, x + w + pad_x)
                        x = new_x
                        w = max(0, new_x2 - new_x)
                    else:
                        x = x - pad_x
                        w = w + pad_x * 2

                    y = y - pad_y_top
                    h = h + pad_y_top + pad_y_bottom

                x, y = max(0, int(x)), max(0, int(y))
                x2, y2 = min(img_w, x + int(w)), min(img_h, y + int(h))
                if x2 > x and y2 > y:
                    img = img[y:y2, x:x2]

            success, buf = cv2.imencode(
                ".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 6]
            )
            if not success:
                continue

            r2_key = (
                f"tenants/{tenant_id}/matchup/{uuid_prefix}"
                f"/problems/{q['number']}.png"
            )

            upload_fileobj_to_r2_storage(
                fileobj=io.BytesIO(buf.tobytes()),
                key=r2_key,
                content_type="image/png",
            )
            q["image_key"] = r2_key

            # 이미지 임베딩용 임시 파일 (cropped 영역 PNG)
            try:
                fd, tmp_path = _tempfile.mkstemp(suffix=".png", prefix="matchup_crop_")
                _os.close(fd)
                cv2.imwrite(tmp_path, img)
                q["cropped_image_path"] = tmp_path
            except Exception:
                q["cropped_image_path"] = q["image_path"]

        except Exception:
            logger.warning(
                "Image upload failed for Q%d in job %s",
                q["number"], job_id, exc_info=True,
            )
        if on_progress and (processed % 5 == 0 or processed == total):
            try:
                on_progress(processed, total)
            except Exception:
                pass


def _insert_skeleton_problems(
    questions: List[Dict],
    document_id: str,
    tenant_id: str | None,
    job_id: str,
) -> None:
    """세그멘테이션 직후 number+bbox+page_index만 가진 skeleton row를 INSERT.

    프론트 ProblemGrid 부분 결과 노출을 위해. 신규 업로드 doc도 즉시 grid에
    문항 카운트가 보이고, 점차 OCR/임베딩/이미지가 채워지는 UX.

    is_partial=True 메타 플래그로 최종 결과와 구분. 최종 callbacks가
    `doc.problems.all().delete()`로 모두 지우고 bulk_create하므로 정합성 안전.
    """
    if not questions or not document_id:
        return

    from apps.domains.matchup.models import MatchupDocument, MatchupProblem

    try:
        doc = MatchupDocument.objects.only("id", "tenant_id", "status").get(id=int(document_id))
    except MatchupDocument.DoesNotExist:
        return

    # 워커 ↔ DB 텐넌트 교차검증 (callbacks와 동일 패턴)
    if tenant_id and str(doc.tenant_id) != str(tenant_id):
        logger.warning(
            "SKELETON_INSERT_TENANT_MISMATCH | job=%s | doc=%s | doc_tenant=%s | job_tenant=%s",
            job_id, document_id, doc.tenant_id, tenant_id,
        )
        return

    # 재시도 케이스 — auto/partial rows만 새 skeleton으로 갈음한다.
    # 사용자 수동 편집 또는 학원장 curated row는 skeleton 단계에서도 보존해야
    # 워커 재시도 중 실제 운영 데이터가 사라지지 않는다.
    manual_ids = list(
        MatchupProblem.objects.filter(
            tenant_id=doc.tenant_id,
            document=doc,
            meta__manual=True,
        ).values_list("id", flat=True)
    )
    pinned_ids = list(
        MatchupProblem.objects.filter(
            tenant_id=doc.tenant_id,
            document=doc,
            meta__manual_owner_pinned=True,
        ).values_list("id", flat=True)
    )
    protected_ids = list(set(manual_ids) | set(pinned_ids))
    MatchupProblem.objects.filter(
        tenant_id=doc.tenant_id,
        document=doc,
    ).exclude(id__in=protected_ids).delete()

    rows = [
        MatchupProblem(
            tenant_id=doc.tenant_id,
            document=doc,
            number=q.get("number", 0),
            text="",  # OCR 전 — 빈 텍스트
            image_key="",  # 이미지 업로드 전
            embedding=None,
            image_embedding=None,
            meta={
                "is_partial": True,
                "page_index": q.get("page_index", 0),
                "bbox": q.get("bbox"),
            },
        )
        for q in questions
    ]
    MatchupProblem.objects.bulk_create(rows, ignore_conflicts=True)
    inserted = MatchupProblem.objects.filter(document=doc).count()
    logger.info(
        "MATCHUP_SKELETON_INSERT | job=%s | doc=%s | dispatched=%d | inserted=%d",
        job_id, document_id, len(rows), inserted,
    )


def _cleanup_cropped_image_temps(questions: List[Dict]) -> None:
    """이미지 임베딩 후 cropped 임시 파일 정리."""
    import os as _os
    for q in questions:
        p = q.get("cropped_image_path")
        if p and p != q.get("image_path") and _os.path.exists(p):
            try:
                _os.unlink(p)
            except OSError:
                pass

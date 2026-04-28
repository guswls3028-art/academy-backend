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
import re
from typing import Any, Callable, Dict, List, Tuple

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)


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

    from academy.adapters.ai.detection.segment_dispatcher import (
        register_pdf_seg_tmp_dirs,
        segment_questions_multipage,
    )

    seg_result = segment_questions_multipage(local_path)
    register_pdf_seg_tmp_dirs(seg_result.get("tmp_dirs") or [])
    pages = seg_result.get("pages", [])
    total_boxes = seg_result.get("total_boxes", 0)

    record_progress(
        job_id, "segmentation", 30,
        step_index=1, step_total=5,
        step_name_display="문제 분할",
        step_percent=100, tenant_id=tenant_id,
    )

    # ── intent 기반 분기 ──
    # 학습자료(intent=reference)에서 anchor가 폭증한 경우 본문 학습 항목(1.~60.)을
    # 문항으로 오인한 over-extraction. 페이지 단위 인덱싱으로 폴백 →
    # 자료 매칭은 페이지 단위로 충분, 노이즈 제거 우선.
    upload_intent = (payload.get("upload_intent") or "").lower()
    doc_title = ""
    if not upload_intent and document_id:
        try:
            from apps.domains.matchup.models import MatchupDocument
            doc = MatchupDocument.objects.only("meta", "title").get(id=int(document_id))
            meta = doc.meta or {}
            upload_intent = (meta.get("upload_intent") or meta.get("document_role") or "").lower()
            doc_title = doc.title or ""
        except Exception as e:
            logger.warning("MATCHUP_INTENT_LOOKUP_FAIL | doc=%s | err=%s", document_id, e)

    # 명시적 intent 미설정 시 doc.title 키워드로 자동 추정.
    # 운영 T2 28 doc 중 27개가 intent=NONE. 메타 미설정이라 페이지 폴백 트리거 자체가
    # 학습자료/시험지 구분 없이 일률적으로 적용되던 결함을 해소.
    if not upload_intent and doc_title:
        title_l = doc_title
        if any(k in title_l for k in (
            "시험지", "중간고사", "기말고사", "모의고사", "TEST", "Test",
            "기출 통과", "고난도",
        )):
            upload_intent = "exam_sheet"
        elif any(k in title_l for k in (
            "메인자료", "메인 자료", "복습과제", "복습 과제", "객서심화", "객서 심화",
            "객·서", "개념완성", "문항편", "WORKBOOK",
        )):
            upload_intent = "reference"

    # 명시적 시험지(test/exam_sheet)가 아니면 학습자료 의심 — views.py의 default도 'reference'.
    # 시험지는 사용자가 명확히 의도해 업로드해야 하고, 미설정은 학습자료로 간주해 폴백 검토.
    is_reference = upload_intent not in ("test", "exam_sheet")
    page_count = len(pages)
    avg_per_page = total_boxes / max(1, page_count)
    # 학습자료 over-extraction 휴리스틱 — 페이지 폴백 (페이지=problem) 트리거.
    # 운영 사용자 보고 (2026-04-28): doc#130 페이지에 44/45/46 문항이 명확히 분리
    # 되어 있는데도 폴백되어 페이지 통째 problem이 됨. 임계값 50 → 70로 강화하여
    # 일부 학습자료 (anchor 50~70)가 정상 anchor 분리로 복귀.
    # 운영 T2 실측:
    #   - 시험지 doc#127/140/146/147: 16~25 (폴백 안 됨, 변동 없음)
    #   - 모의고사 doc#134~142: 16~22 (폴백 안 됨, 변동 없음)
    #   - 학습자료 doc#143/144/145: 80+ anchor (over-extraction, 폴백 유지)
    #   - 학습자료 doc#120/123/130/131/132/133: 50~70 (이전 폴백 → 이제 anchor 분리 복귀)
    is_over_extracted = is_reference and (
        total_boxes >= 70
        or (total_boxes >= 40 and avg_per_page >= 5)
    )

    if total_boxes == 0:
        # 문제를 찾지 못한 경우 — 전체 페이지를 하나의 문제로 취급
        logger.info("MATCHUP_NO_BOXES | job_id=%s | treating whole pages as problems", job_id)
        questions_raw = _whole_pages_as_questions(pages)
    elif is_over_extracted:
        # 학습자료 over-extraction 폴백 — 페이지 단위 1박스로 재인덱싱.
        # 단, is_skip_page(표지/해설지/lorem ipsum) 페이지는 제외 — 그대로 두면
        # 라틴 placeholder가 problem #1/#2로 인덱싱되어 매치업 노이즈가 됨.
        kept_pages = [p for p in pages if not p.get("is_skip_page")]
        logger.info(
            "MATCHUP_REFERENCE_PAGE_FALLBACK | job_id=%s | total_boxes=%d avg=%.1f "
            "raw_pages=%d kept_pages=%d (skip=%d)",
            job_id, total_boxes, avg_per_page, page_count, len(kept_pages),
            page_count - len(kept_pages),
        )
        questions_raw = _whole_pages_as_questions(kept_pages)
    else:
        questions_raw = _boxes_to_questions(pages)

    if not questions_raw:
        return AIResult.done(job_id, {
            "problems": [],
            "document_id": document_id,
            "problem_count": 0,
        })

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
    record_progress(
        job_id, "upload_images", 85,
        step_index=4, step_total=5,
        step_name_display="이미지 저장",
        step_percent=0, tenant_id=tenant_id,
    )

    _upload_cropped_images(questions_raw, tenant_id, document_id, job_id)

    # 이미지 CLIP 임베딩 — cropped 영역을 시각 임베딩으로 변환. 카메라 사진/
    # 스캔본의 OCR이 약해도 이미지 유사도로 매칭 보강 (find_similar_problems
    # ensemble 가중평균).
    _generate_image_embeddings(questions_raw, job_id)
    _cleanup_cropped_image_temps(questions_raw)

    # 페이지 PNG도 같이 R2에 업로드 → ensure_document_page_images 캐시 hit.
    # 모달 첫 진입 PDF 다운로드 + 페이지 렌더 비용 사전 분산.
    page_image_keys, page_dimensions = _upload_page_images_for_modal_cache(
        pages, tenant_id, document_id, job_id,
    )

    record_progress(
        job_id, "upload_images", 90,
        step_index=4, step_total=5,
        step_name_display="이미지 저장",
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
    })


# ── 내부 함수 ────────────────────────────────────────


def _boxes_to_questions(pages: List[Dict]) -> List[Dict]:
    """세그멘테이션 결과를 문제 리스트로 변환.

    번호 우선순위:
      1. segment dispatcher가 boxes와 같은 길이로 ``numbers``를 같이 보내줬고
         값이 모두 정수(=텍스트/OCR 분리 성공)이면 그 번호를 사용. 시험지의
         실제 문항 번호와 정렬됨.
      2. ``numbers``가 비어있거나 None이 섞여 있으면 (OpenCV fallback) 박스 순서로
         1부터 새로 매김.

    이전엔 항상 (2)만 사용해서, 텍스트/OCR이 어떤 박스를 누락하면 그 이후의 모든
    번호가 시험지 실제 번호와 어긋났다 (DB Q10 = 시험지 11번 문제 식). 이 fix로
    박스→번호 매핑이 시험지 원본과 일치한다.
    """
    questions = []
    q_num = 1
    seen_numbers: set = set()  # 문서 전역 dedupe — unique(document, number) 충돌 방지
    for page in pages:
        page_idx = page["page_index"]
        img_path = page["image_path"]
        boxes = page.get("boxes", []) or []
        numbers = page.get("numbers", []) or []
        # 번호가 boxes와 같은 길이이고 모두 정수면 신뢰. 그렇지 않으면 fallback.
        use_segment_numbers = (
            len(numbers) == len(boxes)
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
            questions.append({
                "number": num,
                "page_index": page_idx,
                "image_path": img_path,
                "bbox": list(bbox),
            })
    return questions


def _whole_pages_as_questions(pages: List[Dict]) -> List[Dict]:
    """세그멘테이션 실패 시 전체 페이지를 하나의 문제로."""
    questions = []
    for i, page in enumerate(pages):
        questions.append({
            "number": i + 1,
            "page_index": page["page_index"],
            "image_path": page["image_path"],
            "bbox": None,  # 전체 페이지
        })
    return questions


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


_MERGE_INNER_ANCHOR = re.compile(
    r"(?:^|\n)\s*(\d{1,2})\s*[.)]\s*(?=[가-힣A-Za-z(<\[])",
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
    for page in pages:
        idx = int(page.get("page_index", 0))
        img_path = page.get("image_path") or ""
        if not img_path:
            continue
        try:
            with _PILImage.open(img_path) as im:
                im.load()
                w, h = im.size
                buf = _io.BytesIO()
                im.save(buf, "PNG")
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
    logger.info(
        "MATCHUP_PAGE_CACHE | job=%s | doc=%s | pages=%d",
        job_id, document_id, len(page_keys),
    )
    return page_keys, page_dimensions


def _upload_cropped_images(
    questions: List[Dict],
    tenant_id: str | None,
    document_id: str,
    job_id: str,
) -> None:
    """크롭된 문제 이미지를 R2에 업로드 (in-place로 image_key 설정).

    부수효과: q["cropped_image_path"]에 임시 파일 경로 저장. 이미지 임베딩이
    페이지 전체가 아닌 cropped 영역을 사용하도록 — 시각 매칭 정확도 향상.
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

    for q in questions:
        try:
            img = cv2.imread(q["image_path"])
            if img is None:
                continue

            if q.get("bbox"):
                x, y, w, h = q["bbox"]
                img_h, img_w = img.shape[:2]
                x, y = max(0, int(x)), max(0, int(y))
                x2, y2 = min(img_w, x + int(w)), min(img_h, y + int(h))
                if x2 > x and y2 > y:
                    img = img[y:y2, x:x2]

            success, buf = cv2.imencode(".png", img)
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

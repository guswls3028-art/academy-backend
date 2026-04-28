# PATH: apps/domains/matchup/pdf_report.py
"""
매치업 적중률 PDF 보고서 생성.

비즈니스 컨텍스트:
  학원이 학생에게 미리 제공한 학습자료(메인자료/복습과제/객서심화/모의고사)가
  실제 학교 시험에 얼마나 적중했는지를 유사도(cosine sim)로 증명하는 마케팅 보고서.

  학생/학부모/네이버 카페/커뮤니티에 공유 — "우리 학원이 준비한 X자료 Y번 문제가
  실제 시험에 이렇게 나왔다"를 양쪽 이미지로 직관적으로 보여줌.

레이아웃:
  - 표지: 학원 로고 + 시험지 제목 + 적중 요약 (N/M 적중, X% 평균 sim)
  - 각 문항 페이지: 좌측 시험지 | 우측 매치된 학습자료
  - 푸터: 학원명 + 페이지

생성 흐름:
  1. doc.problems 조회 (시험지)
  2. 각 problem → find_similar_problems(top_k=1)
  3. R2에서 이미지 다운로드
  4. ReportLab으로 PDF 생성
"""
from __future__ import annotations

import io
import logging
import os
import urllib.request
from datetime import datetime
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# 색상 — 학원 브랜드 톤
_HEADER_COLOR = "#0F172A"  # slate-900
_ACCENT_COLOR = "#2563EB"  # blue-600
_HIT_COLOR = "#16A34A"     # green-600 (직접 적중)
_TYPE_COLOR = "#0891B2"    # cyan-600 (유형 적중)
_CONCEPT_COLOR = "#7C3AED" # violet-600 (개념 커버)
_MISS_COLOR = "#94A3B8"    # slate-400
_BG_SUBTLE = "#F8FAFC"     # slate-50

# 매칭 분류 임계값 — GPT의 "직접/유형/개념/불일치" 분류 적용.
# 카메라 사진 vs 컴퓨터 PDF는 OCR 차이로 sim이 0.7~0.85에 다수 분포 →
# "직접만 적중"으로 묶으면 실제 적중을 놓침. 3단계로 정직하게 표시.
_DIRECT_HIT = 0.85   # 직접 적중 — 거의 같은 문제 (변형 포함)
_TYPE_HIT = 0.75     # 유형 적중 — 풀이 구조 유사
_CONCEPT_HIT = 0.60  # 개념 커버 — 같은 단원/개념
# < 0.60: 유사 자료 없음 (불일치)


def _ensure_korean_font() -> Tuple[str, str]:
    """OMR pdf_renderer와 동일 패턴 — NotoSansKR 등록."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fn = "MatchupReportRegular"
    fb = "MatchupReportBold"

    # 이미 등록됐으면 skip
    try:
        pdfmetrics.getFont(fn)
        pdfmetrics.getFont(fb)
        return fn, fb
    except Exception:
        pass

    omr_fonts_dir = os.path.join(
        os.path.dirname(__file__), "..", "assets", "omr", "renderer", "fonts",
    )
    candidates_reg = [
        os.path.join(omr_fonts_dir, "NotoSansKR-Regular.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
    ]
    candidates_bold = [
        os.path.join(omr_fonts_dir, "NotoSansKR-Bold.ttf"),
        "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf",
    ]

    reg_ok = False
    for p in candidates_reg:
        if os.path.isfile(p):
            try:
                pdfmetrics.registerFont(TTFont(fn, p))
                reg_ok = True
                break
            except Exception:
                continue

    bold_ok = False
    for p in candidates_bold:
        if os.path.isfile(p):
            try:
                pdfmetrics.registerFont(TTFont(fb, p))
                bold_ok = True
                break
            except Exception:
                continue

    if not reg_ok:
        # fallback — Helvetica (영어만)
        return "Helvetica", "Helvetica-Bold"
    if not bold_ok:
        return fn, fn
    return fn, fb


def _download_image_to_pil(url: str, max_dim: int = 1200):
    """presigned URL에서 이미지 다운로드 → PIL Image (resize)."""
    from PIL import Image
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        # 다운스케일 (PDF 용량 절감)
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        return img
    except Exception as e:
        logger.warning("image download failed (%s): %s", url, e)
        return None


def _classify_match(sim: float) -> str:
    """sim → 분류 ('direct' / 'type' / 'concept' / 'miss')."""
    if sim >= _DIRECT_HIT:
        return "direct"
    if sim >= _TYPE_HIT:
        return "type"
    if sim >= _CONCEPT_HIT:
        return "concept"
    return "miss"


def generate_matchup_hit_report_pdf(
    document, *, hit_threshold: float = _DIRECT_HIT,
) -> bytes:
    """시험지 doc 기준 적중률 PDF 생성.

    Args:
      document: MatchupDocument (시험지로 간주). 학습자료라도 호출 가능.
      hit_threshold: 적중 카운트 임계값 (기본 0.85)

    Returns:
      PDF bytes
    """
    from reportlab.lib.pagesizes import A4, portrait
    from reportlab.lib.colors import HexColor, black, white
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    from apps.domains.matchup.models import MatchupProblem
    from apps.domains.matchup.services import find_similar_problems
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
    from apps.shared.utils.vector import cosine_similarity

    fn_reg, fn_bold = _ensure_korean_font()

    # 시험지 problems (number 순)
    problems: List[MatchupProblem] = list(
        document.problems.exclude(image_key="").order_by("number")
    )

    # 각 problem 매치 결과 미리 계산.
    # find_similar_problems의 score는 휴리스틱 가중치(format/length/cross_doc) 포함되어
    # 정렬용으로는 좋지만 보고서 표시 sim으로는 인플레이션됨. top1 선정은 그 score로
    # 하고, 표시 sim은 raw cosine similarity로 별도 계산하여 정직하게 보여줌.
    matches: List[Tuple[MatchupProblem, Optional[MatchupProblem], float]] = []
    for p in problems:
        try:
            sim_results = find_similar_problems(
                problem_id=p.id, tenant_id=p.tenant_id, top_k=1,
            )
        except Exception:
            sim_results = []
        if sim_results:
            best_problem, _heuristic_score = sim_results[0]
            # 표시용 raw cosine sim — text emb (+ image emb ensemble) 직접 계산.
            # 페이지 폴백 candidate (bbox=null)는 페이지 통째 텍스트로 sim 부풀림 →
            # 0.05 패널티로 정직한 적중 분류.
            try:
                if p.embedding and best_problem.embedding:
                    raw_text_sim = float(cosine_similarity(p.embedding, best_problem.embedding))
                else:
                    raw_text_sim = 0.0
                if p.image_embedding and best_problem.image_embedding:
                    raw_img_sim = float(cosine_similarity(p.image_embedding, best_problem.image_embedding))
                    src_len = len((p.text or "").strip())
                    img_w = 0.5 if src_len < 60 else (0.3 if src_len < 200 else 0.15)
                    display_sim = (1 - img_w) * raw_text_sim + img_w * raw_img_sim
                else:
                    display_sim = raw_text_sim
                # 페이지 폴백 패널티 (bbox=null 후보) — services.py와 동일 기준.
                # -0.15 + ceiling 0.84로 페이지 통째 텍스트 false positive 차단.
                cand_meta = best_problem.meta or {}
                if cand_meta.get("bbox") is None:
                    display_sim = min(0.84, display_sim - 0.15)
                    display_sim = max(0.0, display_sim)
            except Exception:
                display_sim = float(_heuristic_score)
            matches.append((p, best_problem, display_sim))
        else:
            matches.append((p, None, 0.0))

    # 적중 통계 — 3단계 분류
    total = len(matches)
    direct_hits = sum(1 for _, m, s in matches if m is not None and s >= _DIRECT_HIT)
    type_hits = sum(1 for _, m, s in matches if m is not None and _TYPE_HIT <= s < _DIRECT_HIT)
    concept_hits = sum(1 for _, m, s in matches if m is not None and _CONCEPT_HIT <= s < _TYPE_HIT)
    misses = total - direct_hits - type_hits - concept_hits
    # 호환성: hit_threshold 인자는 직접 적중만 카운트
    hits = direct_hits
    avg_sim = (
        sum(s for _, _, s in matches) / total
        if total else 0.0
    )
    # 직접 + 유형 = 진짜 적중 (학원 마케팅 정직성)
    real_hit_count = direct_hits + type_hits
    real_hit_pct = (real_hit_count / total * 100) if total else 0.0

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=portrait(A4))
    page_w, page_h = portrait(A4)
    margin = 18 * mm
    inner_w = page_w - margin * 2

    tenant = document.tenant
    tenant_name = (tenant.name or "").strip() or "학원"
    issued_at = datetime.now().strftime("%Y년 %m월 %d일")

    # ── 표지 페이지 ──────────────────────────────────────
    # 헤더 바
    c.setFillColor(HexColor(_HEADER_COLOR))
    c.rect(0, page_h - 35 * mm, page_w, 35 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(fn_bold, 24)
    c.drawCentredString(page_w / 2, page_h - 22 * mm, tenant_name)
    c.setFont(fn_reg, 11)
    c.drawCentredString(page_w / 2, page_h - 30 * mm, "학원 자료 적중 보고서")

    # 표제 — 시험지 제목
    y = page_h - 70 * mm
    c.setFillColor(black)
    c.setFont(fn_bold, 18)
    title = (document.title or "시험지")[:60]
    c.drawCentredString(page_w / 2, y, title)
    y -= 8 * mm
    c.setFont(fn_reg, 11)
    c.setFillColor(HexColor("#475569"))  # slate-600
    c.drawCentredString(page_w / 2, y, f"발행일 {issued_at}")

    # 적중 통계 박스 — 3단계 분류
    y -= 25 * mm
    box_x = margin + 10 * mm
    box_w = inner_w - 20 * mm
    box_h = 70 * mm
    c.setFillColor(HexColor(_BG_SUBTLE))
    c.roundRect(box_x, y - box_h, box_w, box_h, 6, fill=1, stroke=0)

    # 메인 적중 수치 — 직접 + 유형 합산
    c.setFont(fn_bold, 30)
    c.setFillColor(HexColor(_HIT_COLOR if real_hit_pct >= 50 else _ACCENT_COLOR))
    c.drawCentredString(page_w / 2, y - 18 * mm, f"{real_hit_count} / {total} 적중")
    c.setFont(fn_reg, 11)
    c.setFillColor(HexColor("#475569"))
    c.drawCentredString(
        page_w / 2, y - 26 * mm,
        f"실전 대비 반영률 {real_hit_pct:.1f}%   ·   평균 유사도 {avg_sim*100:.1f}%",
    )

    # 3단계 breakdown
    bd_y = y - 40 * mm
    col_w = box_w / 4
    items = [
        ("직접 적중", direct_hits, _HIT_COLOR, "거의 같은 문제"),
        ("유형 적중", type_hits, _TYPE_COLOR, "풀이 구조 동일"),
        ("개념 커버", concept_hits, _CONCEPT_COLOR, "같은 단원/개념"),
        ("관련성 낮음", misses, _MISS_COLOR, "유사 자료 없음"),
    ]
    for i, (lbl, n, col, desc) in enumerate(items):
        cx = box_x + col_w * (i + 0.5)
        c.setFont(fn_bold, 18)
        c.setFillColor(HexColor(col))
        c.drawCentredString(cx, bd_y, str(n))
        c.setFont(fn_bold, 9)
        c.setFillColor(HexColor("#334155"))
        c.drawCentredString(cx, bd_y - 6 * mm, lbl)
        c.setFont(fn_reg, 8)
        c.setFillColor(HexColor("#64748B"))
        c.drawCentredString(cx, bd_y - 11 * mm, desc)

    # 안내 문구
    y -= box_h + 12 * mm
    c.setFont(fn_reg, 9.5)
    c.setFillColor(HexColor("#64748B"))
    notes = [
        "· 본 보고서는 우리 학원에서 학생에게 미리 제공한 학습자료가 실제 시험에",
        "  얼마나 적중했는지를 자동으로 분석한 결과입니다.",
        "· 직접 적중(85%+) = 사실상 같은 문제 (단어 한두 개만 다른 변형 포함)",
        "· 유형 적중(75~85%) = 풀이 구조가 동일하거나 매우 유사한 문제",
        "· 개념 커버(60~75%) = 같은 단원/개념을 다루는 문제",
    ]
    for line in notes:
        c.drawString(box_x, y, line)
        y -= 5.5 * mm

    # 푸터
    c.setFont(fn_reg, 9)
    c.setFillColor(HexColor("#94A3B8"))
    c.drawCentredString(page_w / 2, 12 * mm, f"{tenant_name}  ·  매치업 적중 보고서")

    c.showPage()

    # ── 각 문항 페이지 ─────────────────────────────────────
    for idx, (src, mat, sim) in enumerate(matches, start=1):
        # 헤더
        c.setFillColor(HexColor(_HEADER_COLOR))
        c.rect(0, page_h - 18 * mm, page_w, 18 * mm, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont(fn_bold, 14)
        c.drawString(margin, page_h - 12 * mm, f"Q{src.number}")
        # sim 라벨 — 3단계 분류
        cls = _classify_match(sim) if mat is not None else "miss"
        if cls == "direct":
            label = f"직접 적중  ·  {sim*100:.1f}%"
            label_color = HexColor(_HIT_COLOR)
        elif cls == "type":
            label = f"유형 적중  ·  {sim*100:.1f}%"
            label_color = HexColor(_TYPE_COLOR)
        elif cls == "concept":
            label = f"개념 커버  ·  {sim*100:.1f}%"
            label_color = HexColor(_CONCEPT_COLOR)
        else:
            label = "유사 자료 없음"
            label_color = HexColor(_MISS_COLOR)
        is_miss = cls == "miss"
        c.setFont(fn_bold, 12)
        c.setFillColor(label_color)
        c.drawRightString(page_w - margin, page_h - 12 * mm, label)

        # 본문 — 좌/우 컬럼
        col_gap = 6 * mm
        col_w = (inner_w - col_gap) / 2
        col_top = page_h - 28 * mm
        col_bottom = 30 * mm
        col_h = col_top - col_bottom

        def _draw_column(x: float, label_text: str, sub_text: str, image_url: Optional[str], color):
            # 라벨
            c.setFillColor(HexColor(color))
            c.setFont(fn_bold, 11)
            c.drawString(x, col_top + 2 * mm, label_text)
            c.setFillColor(HexColor("#475569"))
            c.setFont(fn_reg, 9)
            sub_clip = sub_text[:60]
            c.drawString(x, col_top - 4 * mm, sub_clip)
            # 이미지 박스
            box_top = col_top - 8 * mm
            box_h = box_top - col_bottom
            c.setStrokeColor(HexColor("#E2E8F0"))
            c.setLineWidth(0.5)
            c.rect(x, col_bottom, col_w, box_h, stroke=1, fill=0)
            if image_url:
                pil = _download_image_to_pil(image_url, max_dim=900)
                if pil is not None:
                    iw, ih = pil.size
                    pad = 4 * mm
                    inner_box_w = col_w - pad * 2
                    inner_box_h = box_h - pad * 2
                    scale = min(inner_box_w / iw, inner_box_h / ih)
                    draw_w = iw * scale
                    draw_h = ih * scale
                    draw_x = x + (col_w - draw_w) / 2
                    draw_y = col_bottom + (box_h - draw_h) / 2
                    c.drawImage(
                        ImageReader(pil),
                        draw_x, draw_y, draw_w, draw_h,
                        preserveAspectRatio=True, mask="auto",
                    )

        # 좌: 시험지
        src_url = ""
        if src.image_key:
            try:
                src_url = generate_presigned_get_url_storage(
                    key=src.image_key, expires_in=600,
                ) or ""
            except Exception:
                src_url = ""
        _draw_column(
            margin, "실제 시험",
            (document.title or "시험지")[:55] + f"  ·  {src.number}번",
            src_url, _HEADER_COLOR,
        )

        # 우: 매치된 자료 (개념 커버 이상이면 표시)
        if mat is not None and sim >= _CONCEPT_HIT:
            mat_doc_title = ""
            try:
                mat_doc_title = (mat.document.title or "")[:50] if mat.document_id else ""
            except Exception:
                mat_doc_title = ""
            mat_url = ""
            if mat.image_key:
                try:
                    mat_url = generate_presigned_get_url_storage(
                        key=mat.image_key, expires_in=600,
                    ) or ""
                except Exception:
                    mat_url = ""
            sub = (mat_doc_title or "학원 자료") + f"  ·  {mat.number}번"
            col_color = {
                "direct": _HIT_COLOR,
                "type": _TYPE_COLOR,
                "concept": _CONCEPT_COLOR,
            }.get(cls, _ACCENT_COLOR)
            _draw_column(
                margin + col_w + col_gap, "우리 자료",
                sub, mat_url, col_color,
            )
        else:
            # 유사 자료 없음 — 우측 박스만 그리고 "유사 자료 없음" 안내
            x_right = margin + col_w + col_gap
            c.setFillColor(HexColor(_MISS_COLOR))
            c.setFont(fn_bold, 11)
            c.drawString(x_right, col_top + 2 * mm, "우리 자료")
            c.setFillColor(HexColor("#94A3B8"))
            c.setFont(fn_reg, 9)
            c.drawString(x_right, col_top - 4 * mm, "유사 자료가 없습니다")
            box_top = col_top - 8 * mm
            box_h = box_top - col_bottom
            c.setStrokeColor(HexColor("#E2E8F0"))
            c.setDash(2, 2)
            c.rect(x_right, col_bottom, col_w, box_h, stroke=1, fill=0)
            c.setDash()
            c.setFont(fn_reg, 11)
            c.setFillColor(HexColor("#94A3B8"))
            c.drawCentredString(
                x_right + col_w / 2, col_bottom + box_h / 2,
                "유사 자료 없음",
            )

        # 푸터
        c.setFont(fn_reg, 9)
        c.setFillColor(HexColor("#94A3B8"))
        c.drawCentredString(
            page_w / 2, 12 * mm,
            f"{tenant_name}  ·  {idx} / {total}",
        )

        c.showPage()

    c.save()
    return buf.getvalue()

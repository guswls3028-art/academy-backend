# PATH: apps/domains/matchup/pdf_report.py
"""
매치업 큐레이션 적중 보고서 PDF 생성.

비즈니스 컨텍스트:
  실장이 매치업 자동 후보 중 적합한 학원 자료를 직접 골라 코멘트와 함께
  학원장/선생에게 제출하는 보고서. 학원 운영의 핵심 산출물.

레이아웃 SSOT:
  매치업 홈 우측 추천 패널에서 후보 클릭 시 뜨는 ProblemDetailModal과
  같은 좌-우 2-pane 비교 형태를 PDF에서도 그대로 재사용.
    - A4 landscape (297×210mm) → 두 이미지 풀폭 비교
    - 페이지 = 시험지 문항 1개 × 큐레이션 후보 1건 (후보 N개면 N 페이지)
    - 좌 pane (warning 톤): 실제 시험 문항
    - 우 pane (적중 분류 색): 큐레이션 자료 + 유사도 라벨
    - 하단 코멘트 band: 페이지마다 반복 (같은 문항이면 동일 코멘트 노출)
"""
from __future__ import annotations

import io
import logging
import os
import urllib.request
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)


# ── 색상 토큰 ────────────────────────────────────────────────
_HEADER_COLOR = "#0F172A"   # slate-900
_ACCENT_COLOR = "#2563EB"   # blue-600
_HIT_COLOR = "#16A34A"      # green-600 (직접 적중)
_TYPE_COLOR = "#0891B2"     # cyan-600 (유형 적중)
_CONCEPT_COLOR = "#7C3AED"  # violet-600 (개념 커버)
_MISS_COLOR = "#94A3B8"     # slate-400
_BG_SUBTLE = "#F8FAFC"      # slate-50
# 좌(시험지 원본) pane — ProblemDetailModal "내 문제(원본)" warning 톤과 매칭
_SOURCE_PANE_COLOR = "#D97706"  # amber-600
_SOURCE_PANE_BG = "#FEF3C7"     # amber-100
# 우(매치 자료) 적중 분류 없을 때 기본 톤
_MATCH_DEFAULT_COLOR = _ACCENT_COLOR
_MATCH_DEFAULT_BG = "#DBEAFE"   # blue-100

# 매칭 분류 임계값 — 카메라 사진 vs PDF OCR 차이로 sim 0.7~0.85에 다수 분포 →
# "직접만 적중"으로 묶으면 실제 적중 누락. 3단계로 정직하게 표시.
_DIRECT_HIT = 0.85   # 직접 적중 — 거의 같은 문제 (변형 포함)
_TYPE_HIT = 0.75     # 유형 적중 — 풀이 구조 동일
_CONCEPT_HIT = 0.60  # 개념 커버 — 같은 단원/개념


# ── 폰트 ────────────────────────────────────────────────────
def _ensure_korean_font():
    """OMR pdf_renderer와 동일 패턴 — NotoSansKR 등록."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fn = "MatchupReportRegular"
    fb = "MatchupReportBold"

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
        return "Helvetica", "Helvetica-Bold"
    if not bold_ok:
        return fn, fn
    return fn, fb


# ── 이미지 다운로드 ──────────────────────────────────────────
def _download_image_to_pil(url: str, max_dim: int = 1600):
    """presigned URL → PIL Image (max_dim에 맞춰 다운스케일)."""
    from PIL import Image
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        if max(img.size) > max_dim:
            ratio = max_dim / max(img.size)
            new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        return img
    except Exception as e:
        logger.warning("image download failed (%s): %s", url, e)
        return None


def _prefetch_images(urls: List[str], max_dim: int = 1600) -> dict:
    """다수의 R2 presigned URL을 병렬 다운로드하여 url→PIL 매핑 반환.

    PDF 생성 시 N 페이지 × 2 pane = 수십~수백 이미지를 직렬로 받으면
    게이트웨이 60s timeout 초과. 8 worker로 동시 다운로드 → 시간 ~1/8.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cache: dict = {}
    unique_urls = [u for u in {u for u in urls if u}]
    if not unique_urls:
        return cache
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_download_image_to_pil, u, max_dim): u for u in unique_urls}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                cache[url] = fut.result()
            except Exception as e:
                logger.warning("prefetch failed (%s): %s", url, e)
                cache[url] = None
    return cache


def _safe_url(image_key) -> str:
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
    if not image_key:
        return ""
    try:
        return generate_presigned_get_url_storage(key=image_key, expires_in=600) or ""
    except Exception:
        return ""


# ── 유사도 ──────────────────────────────────────────────────
def _classify_match(sim: float) -> str:
    """sim → 'direct' / 'type' / 'concept' / 'miss'."""
    if sim >= _DIRECT_HIT:
        return "direct"
    if sim >= _TYPE_HIT:
        return "type"
    if sim >= _CONCEPT_HIT:
        return "concept"
    return "miss"


def _compute_display_sim(source, candidate) -> float:
    """source vs candidate raw cosine sim (+ image emb ensemble + bbox=null 패널티).

    find_similar_problems의 score는 정렬용 휴리스틱(format/length/cross_doc) 가중치라
    표시값으로는 인플레이션됨. 보고서 표시 sim은 raw cosine으로 정직하게 계산.
    """
    from apps.shared.utils.vector import cosine_similarity

    try:
        if source.embedding and candidate.embedding:
            raw_text_sim = float(cosine_similarity(source.embedding, candidate.embedding))
        else:
            raw_text_sim = 0.0
        if source.image_embedding and candidate.image_embedding:
            raw_img_sim = float(cosine_similarity(
                source.image_embedding, candidate.image_embedding,
            ))
            src_len = len((source.text or "").strip())
            img_w = 0.5 if src_len < 60 else (0.3 if src_len < 200 else 0.15)
            display_sim = (1 - img_w) * raw_text_sim + img_w * raw_img_sim
        else:
            display_sim = raw_text_sim
        # 페이지 폴백 candidate (bbox=null) → 페이지 통째 텍스트로 sim 부풀림 방지
        cand_meta = candidate.meta or {}
        if cand_meta.get("bbox") is None:
            display_sim = min(0.89, display_sim - 0.10)
            display_sim = max(0.0, display_sim)
        return display_sim
    except Exception:
        return 0.0


def _pane_color_for_class(cls: str) -> str:
    return {
        "direct": _HIT_COLOR,
        "type": _TYPE_COLOR,
        "concept": _CONCEPT_COLOR,
    }.get(cls, _MATCH_DEFAULT_COLOR)


def _pane_bg_for_class(cls: str) -> str:
    return {
        "direct": "#DCFCE7",   # green-100
        "type": "#CFFAFE",     # cyan-100
        "concept": "#EDE9FE",  # violet-100
    }.get(cls, _MATCH_DEFAULT_BG)


# ── 렌더 헬퍼 ────────────────────────────────────────────────
def _draw_single_pane(c, *, x, y, w, h, label, sub, image_url,
                      accent_color, accent_bg, fn_reg, fn_bold,
                      placeholder_text=None, image_cache=None):
    """단일 pane — 캡션 strip + 이미지 박스. ProblemDetailModal pane과 동일 구조.

    image_cache (dict[url]→PIL): 미리 병렬 다운로드된 이미지. None이면 즉시 다운로드(레거시).
    """
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader

    # 캡션 strip (10mm)
    cap_h = 10 * mm
    c.setFillColor(HexColor(accent_bg))
    c.rect(x, y + h - cap_h, w, cap_h, fill=1, stroke=0)
    c.setFillColor(HexColor(accent_color))
    c.setFont(fn_bold, 10)
    c.drawString(x + 3 * mm, y + h - 6 * mm, label)
    c.setFillColor(HexColor("#475569"))
    c.setFont(fn_reg, 8.5)
    c.drawString(x + 3 * mm, y + h - 9 * mm, (sub or "")[:90])

    # 이미지 박스
    img_y = y
    img_h = h - cap_h
    c.setStrokeColor(HexColor("#E2E8F0"))
    c.setLineWidth(0.5)
    c.rect(x, img_y, w, img_h, stroke=1, fill=0)

    if not image_url:
        c.setFont(fn_reg, 11)
        c.setFillColor(HexColor("#94A3B8"))
        msg = placeholder_text or "이미지 없음"
        c.drawCentredString(x + w / 2, img_y + img_h / 2, msg)
        return

    if image_cache is not None and image_url in image_cache:
        pil = image_cache[image_url]
    else:
        pil = _download_image_to_pil(image_url, max_dim=1200)
    if pil is None:
        c.setFont(fn_reg, 10)
        c.setFillColor(HexColor("#94A3B8"))
        c.drawCentredString(x + w / 2, img_y + img_h / 2, "이미지 로드 실패")
        return

    iw, ih = pil.size
    pad = 3 * mm
    inner_iw = w - pad * 2
    inner_ih = img_h - pad * 2
    scale = min(inner_iw / iw, inner_ih / ih)
    draw_w = iw * scale
    draw_h = ih * scale
    draw_x = x + (w - draw_w) / 2
    draw_y = img_y + (img_h - draw_h) / 2
    # JPEG 75 압축으로 reportlab 임베딩 — 100MB+ PDF 회피.
    # PIL 직접 전달 시 raw 픽셀로 임베딩되어 페이지당 수 MB.
    jpg_buf = io.BytesIO()
    pil.save(jpg_buf, format="JPEG", quality=75, optimize=True)
    jpg_buf.seek(0)
    c.drawImage(
        ImageReader(jpg_buf),
        draw_x, draw_y, draw_w, draw_h,
        preserveAspectRatio=True, mask="auto",
    )


def _draw_compare_page(c, *, page_w, page_h, margin, inner_w,
                       fn_reg, fn_bold, tenant_name,
                       q_number, page_idx_in_q, q_pages,
                       classification, label_text,
                       left_label, left_sub, left_url,
                       right_label, right_sub, right_url,
                       comment_text=None,
                       footer_idx=0, footer_total=0,
                       right_placeholder=None,
                       image_cache=None):
    """문항 vs 후보 1쌍 비교 페이지 (A4 landscape).

    상단 다크 헤더: Q번호 + 후보 위치 + 적중 라벨
    중앙: 좌(시험지) / 우(매치 자료) 2-pane
    하단: (있으면) 코멘트 band → 푸터
    """
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.units import mm

    has_comment = bool(comment_text and comment_text.strip())

    # ── 상단 헤더 (16mm)
    header_h = 16 * mm
    c.setFillColor(HexColor(_HEADER_COLOR))
    c.rect(0, page_h - header_h, page_w, header_h, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(fn_bold, 14)
    q_text = f"Q{q_number}"
    if q_pages > 1:
        q_text += f"  ·  큐레이션 후보 {page_idx_in_q}/{q_pages}"
    c.drawString(margin, page_h - 11 * mm, q_text)

    if classification:
        c.setFont(fn_bold, 12)
        c.setFillColor(HexColor(_pane_color_for_class(classification)))
    else:
        c.setFont(fn_bold, 12)
        c.setFillColor(HexColor(_MISS_COLOR))
    c.drawRightString(page_w - margin, page_h - 11 * mm, label_text or "")

    # ── 푸터 (10mm)
    footer_h = 10 * mm
    c.setFont(fn_reg, 9)
    c.setFillColor(HexColor("#94A3B8"))
    if footer_total > 0:
        c.drawCentredString(
            page_w / 2, 5 * mm,
            f"{tenant_name}  ·  {footer_idx} / {footer_total}",
        )
    else:
        c.drawCentredString(page_w / 2, 5 * mm, tenant_name)

    # ── 코멘트 band (28mm) — 큐레이션 컨텍스트 유지를 위해 페이지마다 노출
    comment_band_h = 28 * mm if has_comment else 0
    if has_comment:
        cb_y = footer_h
        cb_h = comment_band_h
        c.setFillColor(HexColor(_BG_SUBTLE))
        c.rect(margin, cb_y, inner_w, cb_h, fill=1, stroke=0)
        c.setStrokeColor(HexColor("#E2E8F0"))
        c.rect(margin, cb_y, inner_w, cb_h, fill=0, stroke=1)
        c.setFillColor(HexColor("#0F172A"))
        c.setFont(fn_bold, 10)
        c.drawString(margin + 4 * mm, cb_y + cb_h - 6 * mm, "지도 코멘트")
        c.setFont(fn_reg, 9.5)
        c.setFillColor(HexColor("#334155"))
        # landscape inner_w ~269mm → 줄당 ~110자
        wrap_n = 110
        lines: List[str] = []
        for raw in comment_text.split("\n"):
            line = raw.strip()
            while len(line) > wrap_n:
                lines.append(line[:wrap_n])
                line = line[wrap_n:]
            if line:
                lines.append(line)
        ty = cb_y + cb_h - 11 * mm
        for ln in lines[:3]:
            c.drawString(margin + 4 * mm, ty, ln)
            ty -= 5 * mm

    # ── Pane 영역
    pane_top = page_h - header_h - 4 * mm
    pane_bottom = footer_h + comment_band_h + 4 * mm
    pane_h = pane_top - pane_bottom
    gap = 6 * mm
    pane_w = (inner_w - gap) / 2

    # 좌 (시험지)
    left_x = margin
    _draw_single_pane(
        c, x=left_x, y=pane_bottom, w=pane_w, h=pane_h,
        label=left_label, sub=left_sub, image_url=left_url,
        accent_color=_SOURCE_PANE_COLOR, accent_bg=_SOURCE_PANE_BG,
        fn_reg=fn_reg, fn_bold=fn_bold,
        image_cache=image_cache,
    )

    # 우 (매치 자료)
    right_x = margin + pane_w + gap
    if classification:
        right_color = _pane_color_for_class(classification)
        right_bg = _pane_bg_for_class(classification)
    else:
        right_color = _MISS_COLOR
        right_bg = "#F1F5F9"
    _draw_single_pane(
        c, x=right_x, y=pane_bottom, w=pane_w, h=pane_h,
        label=right_label, sub=right_sub, image_url=right_url,
        accent_color=right_color, accent_bg=right_bg,
        fn_reg=fn_reg, fn_bold=fn_bold,
        placeholder_text=right_placeholder,
        image_cache=image_cache,
    )


def _draw_cover(c, *, page_w, page_h, margin, inner_w,
                fn_reg, fn_bold, tenant_name, report_title,
                document_category, author_label, issued_at,
                summary_text, curated_count, total_q,
                pinned_count):
    """표지 — 학원 로고 띠 + 보고서 제목 + 메타 + 큐레이션 통계."""
    from reportlab.lib.colors import HexColor, black, white
    from reportlab.lib.units import mm

    # 헤더 띠 (32mm)
    c.setFillColor(HexColor(_HEADER_COLOR))
    c.rect(0, page_h - 32 * mm, page_w, 32 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(fn_bold, 26)
    c.drawCentredString(page_w / 2, page_h - 18 * mm, tenant_name)
    c.setFont(fn_reg, 12)
    c.drawCentredString(page_w / 2, page_h - 26 * mm, "큐레이션 적중 보고서")

    # 표제
    y = page_h - 50 * mm
    c.setFillColor(black)
    c.setFont(fn_bold, 22)
    c.drawCentredString(page_w / 2, y, (report_title or "")[:80])
    y -= 9 * mm
    c.setFillColor(HexColor("#475569"))
    c.setFont(fn_reg, 11)
    c.drawCentredString(page_w / 2, y, f"카테고리  ·  {(document_category or '미분류')[:50]}")
    y -= 6 * mm
    c.drawCentredString(
        page_w / 2, y,
        f"작성자  ·  {author_label}    발행일  ·  {issued_at}",
    )

    # 요약 박스 (선택)
    if summary_text:
        y -= 12 * mm
        box_x = margin + 30 * mm
        box_w = inner_w - 60 * mm
        box_h = 36 * mm
        c.setFillColor(HexColor(_BG_SUBTLE))
        c.roundRect(box_x, y - box_h, box_w, box_h, 6, fill=1, stroke=0)
        c.setFillColor(HexColor("#0F172A"))
        c.setFont(fn_bold, 11)
        c.drawString(box_x + 6 * mm, y - 7 * mm, "요약")
        c.setFont(fn_reg, 10)
        c.setFillColor(HexColor("#334155"))
        wrap_n = 80
        lines: List[str] = []
        for raw in summary_text.split("\n"):
            line = raw.strip()
            while len(line) > wrap_n:
                lines.append(line[:wrap_n])
                line = line[wrap_n:]
            if line:
                lines.append(line)
        ty = y - 13 * mm
        for line in lines[:5]:
            c.drawString(box_x + 6 * mm, ty, line)
            ty -= 5 * mm

    # 통계 — 큐레이션 카운트 + 후보 카운트
    stat_y = 50 * mm
    c.setFont(fn_bold, 28)
    c.setFillColor(HexColor(_HIT_COLOR if curated_count > 0 else _MISS_COLOR))
    c.drawCentredString(page_w / 2, stat_y, f"{curated_count} / {total_q} 문항 큐레이션")
    c.setFont(fn_reg, 11)
    c.setFillColor(HexColor("#475569"))
    c.drawCentredString(
        page_w / 2, stat_y - 8 * mm,
        f"선택된 학원 자료 총 {pinned_count}건",
    )

    # 푸터
    c.setFont(fn_reg, 9)
    c.setFillColor(HexColor("#94A3B8"))
    c.drawCentredString(page_w / 2, 8 * mm, f"{tenant_name}  ·  적중 큐레이션 보고서")


# ── 메인 진입 ────────────────────────────────────────────────
def generate_curated_hit_report_pdf(report) -> bytes:
    """큐레이션 보고서 PDF.

    페이지 구성:
      1. 표지
      2. 각 시험지 문항 × 선택 후보 1건 = 1 페이지 (A4 landscape, 좌-우 2-pane).
         후보 N개면 N 페이지. 선택 0건이면 placeholder 1 페이지.
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    from apps.domains.matchup.models import MatchupProblem

    fn_reg, fn_bold = _ensure_korean_font()

    document = report.document
    tenant = document.tenant
    tenant_name = (tenant.name or "").strip() or "학원"

    exam_problems = list(
        document.problems.exclude(image_key="").order_by("number")
    )
    entries_by_eid = {e.exam_problem_id: e for e in report.entries.all()}

    # 선택된 학원 problem prefetch
    all_selected_ids = set()
    for e in entries_by_eid.values():
        for pid in (e.selected_problem_ids or []):
            try:
                all_selected_ids.add(int(pid))
            except (TypeError, ValueError):
                pass

    selected_meta = {}
    if all_selected_ids:
        for p in MatchupProblem.objects.filter(
            tenant=tenant, id__in=list(all_selected_ids),
        ).select_related("document"):
            selected_meta[p.id] = p

    # 본문 총 페이지 수 미리 계산 (footer "x / total")
    body_page_count = 0
    for ep in exam_problems:
        e = entries_by_eid.get(ep.id)
        sel = (e.selected_problem_ids if e else []) or []
        body_page_count += max(1, len(sel))

    # ── 이미지 prefetch (병렬) ──
    # 게이트웨이 60s 컷 회피. 후보 N개 × 2 pane 직렬 다운로드는 N=20 정도부터 timeout.
    # 8 worker 병렬 + url 캐시로 중복 다운로드 제거.
    ep_url_by_id = {ep.id: _safe_url(ep.image_key) for ep in exam_problems}
    sel_url_by_pid = {p.id: _safe_url(p.image_key) for p in selected_meta.values()}
    all_urls = [u for u in list(ep_url_by_id.values()) + list(sel_url_by_pid.values()) if u]
    image_cache = _prefetch_images(all_urls, max_dim=1200)

    # 표지 통계
    curated_count = sum(
        1 for e in entries_by_eid.values()
        if (e.selected_problem_ids or []) or (e.comment or "").strip()
    )
    pinned_count = sum(len(e.selected_problem_ids or []) for e in entries_by_eid.values())

    buf = io.BytesIO()
    page_size = landscape(A4)
    c = canvas.Canvas(buf, pagesize=page_size)
    page_w, page_h = page_size
    margin = 14 * mm
    inner_w = page_w - margin * 2

    issued_at = (
        report.submitted_at.strftime("%Y년 %m월 %d일") if report.submitted_at
        else datetime.now().strftime("%Y년 %m월 %d일")
    )
    author_label = (report.submitted_by_name or "").strip() or "작성자 미기재"

    # ── 표지 ──
    _draw_cover(
        c, page_w=page_w, page_h=page_h, margin=margin, inner_w=inner_w,
        fn_reg=fn_reg, fn_bold=fn_bold,
        tenant_name=tenant_name,
        report_title=(report.title or document.title or "시험지 적중 보고서"),
        document_category=(document.category or "미분류"),
        author_label=author_label, issued_at=issued_at,
        summary_text=(report.summary or "").strip(),
        curated_count=curated_count, total_q=len(exam_problems),
        pinned_count=pinned_count,
    )
    c.showPage()

    # ── 본문: 문항 × 후보 페이지 ──
    page_idx = 0
    doc_title_short = (document.title or "시험지")[:60]

    for ep in exam_problems:
        ep_url = ep_url_by_id.get(ep.id, "")
        entry = entries_by_eid.get(ep.id)
        sel_ids = (entry.selected_problem_ids if entry else []) or []
        comment = (entry.comment if entry else "") or ""

        if not sel_ids:
            page_idx += 1
            _draw_compare_page(
                c, page_w=page_w, page_h=page_h, margin=margin, inner_w=inner_w,
                fn_reg=fn_reg, fn_bold=fn_bold, tenant_name=tenant_name,
                q_number=ep.number, page_idx_in_q=1, q_pages=1,
                classification=None, label_text="선택 없음",
                left_label="실제 시험",
                left_sub=f"{doc_title_short}  ·  {ep.number}번",
                left_url=ep_url,
                right_label="큐레이션 자료",
                right_sub="선택된 자료가 없습니다",
                right_url=None,
                right_placeholder="큐레이션 미작성",
                comment_text=comment if comment else None,
                footer_idx=page_idx, footer_total=body_page_count,
                image_cache=image_cache,
            )
            c.showPage()
            continue

        for ci, pid in enumerate(sel_ids, start=1):
            page_idx += 1
            p = selected_meta.get(int(pid))
            if not p:
                _draw_compare_page(
                    c, page_w=page_w, page_h=page_h, margin=margin, inner_w=inner_w,
                    fn_reg=fn_reg, fn_bold=fn_bold, tenant_name=tenant_name,
                    q_number=ep.number, page_idx_in_q=ci, q_pages=len(sel_ids),
                    classification=None, label_text="자료 누락",
                    left_label="실제 시험",
                    left_sub=f"{doc_title_short}  ·  {ep.number}번",
                    left_url=ep_url,
                    right_label="큐레이션 자료",
                    right_sub=f"#{pid} (자료 누락)",
                    right_url=None,
                    right_placeholder="자료를 찾을 수 없음",
                    comment_text=comment if comment else None,
                    footer_idx=page_idx, footer_total=body_page_count,
                    image_cache=image_cache,
                )
                c.showPage()
                continue

            sim = _compute_display_sim(ep, p)
            cls = _classify_match(sim)
            label_text = {
                "direct": f"직접 적중  ·  {sim*100:.1f}%",
                "type": f"유형 적중  ·  {sim*100:.1f}%",
                "concept": f"개념 커버  ·  {sim*100:.1f}%",
            }.get(cls, f"유사도 {sim*100:.1f}%")

            mat_url = sel_url_by_pid.get(p.id, "")
            mat_doc_title = ""
            try:
                mat_doc_title = (p.document.title or "")[:50] if p.document_id else ""
            except Exception:
                mat_doc_title = ""
            mat_sub = (mat_doc_title or "학원 자료") + f"  ·  {p.number}번"

            _draw_compare_page(
                c, page_w=page_w, page_h=page_h, margin=margin, inner_w=inner_w,
                fn_reg=fn_reg, fn_bold=fn_bold, tenant_name=tenant_name,
                q_number=ep.number, page_idx_in_q=ci, q_pages=len(sel_ids),
                classification=cls, label_text=label_text,
                left_label="실제 시험",
                left_sub=f"{doc_title_short}  ·  {ep.number}번",
                left_url=ep_url,
                right_label="큐레이션 자료",
                right_sub=mat_sub,
                right_url=mat_url,
                comment_text=comment if comment else None,
                footer_idx=page_idx, footer_total=body_page_count,
                image_cache=image_cache,
            )
            c.showPage()

    c.save()
    return buf.getvalue()

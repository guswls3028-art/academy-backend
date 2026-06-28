# PATH: apps/domains/matchup/pdf_report.py
"""
매치업 적중 보고서 PDF — 강사 1인의 3중 역할 산출물.

비즈니스 컨텍스트 (정정 2026-05-03):
  프리랜서 강사 1인이 작성/제출하는 보고서. 동일 PDF가 동시에 3 역할을 수행한다.
    ① 수업 히스토리 (강사 본인 자기 검토용 누적 기록)
    ② 제출 리포트 (소속 학원에 정기 제출하는 KPI/평가 input)
    ③ 신뢰자료+홍보물 (신규 학원·학부모·카페에서 강사 개인 브랜딩)
  좌 pane = 학생이 제출한 학교 시험지. 우 pane = 그 강사 본인이 수업에 쓴 자료.

레이아웃 SSOT:
  매치업 홈 우측 추천 패널에서 후보 클릭 시 뜨는 ProblemDetailModal과
  같은 좌-우 2-pane 비교 형태를 PDF에서도 그대로 재사용.
    - A4 landscape (297×210mm) → 두 이미지 풀폭 비교
    - 페이지 = 시험지 문항 1개 + 큐레이션 후보 묶음
      (후보 2개 단위로 다음 페이지에 이어서 표시)
    - 좌 pane (warning 톤): 실제 시험 문항
    - 우 pane (적중 분류 색): 강사 수업 자료 여러 건 + 유사도 라벨
    - 하단 코멘트 band: 페이지마다 반복 (강사의 지도 노트)
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
# 1:N 보고서는 후보 수보다 이미지 가독성이 우선이다.
# 4-up은 표/긴 문항이 너무 작게 렌더링되어 상담·공유용 산출물 품질이 떨어진다.
_MAX_MATCHES_PER_GROUP_PAGE = 2


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
    게이트웨이 60s timeout 초과. 12 worker로 동시 다운로드 → 시간 ~1/12.

    P1 perf fix (2026-05-04): 100+ 문항 PDF 30s → 60s 게이트웨이 컷 직전.
    8 → 12 workers (R2 throughput 여유 확인). max_dim은 호출자가 1000 (큐레이션 PDF
    표준 사용 사이즈). 추가 perf 위해서는 호출자 max_dim ↓ 또는 streaming PDF 도입.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cache: dict = {}
    unique_urls = [u for u in {u for u in urls if u}]
    if not unique_urls:
        return cache
    with ThreadPoolExecutor(max_workers=12) as pool:
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


def _compute_display_sim(source, candidate) -> Optional[float]:
    """source vs candidate raw cosine sim (+ image emb ensemble + bbox=null 패널티).

    find_similar_problems의 score는 정렬용 휴리스틱(format/length/cross_doc) 가중치라
    표시값으로는 인플레이션됨. 보고서 표시 sim은 raw cosine으로 정직하게 계산.

    Returns:
      float — sim 측정 가능 (0.0~1.0)
      None  — text/image embedding 둘 다 없어 측정 불가 (호출자가 "측정 불가" UI 표시)
    """
    from apps.shared.utils.vector import cosine_similarity

    try:
        has_text_emb = bool(source.embedding and candidate.embedding)
        has_img_emb = bool(source.image_embedding and candidate.image_embedding)
        if not has_text_emb and not has_img_emb:
            return None  # 측정 불가 — sim=0.0%로 표시하면 misleading
        raw_text_sim = (
            float(cosine_similarity(source.embedding, candidate.embedding))
            if has_text_emb else 0.0
        )
        if has_img_emb:
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
        logger.exception("compute_display_sim failed (src=%s, cand=%s)",
                         getattr(source, "id", "?"), getattr(candidate, "id", "?"))
        return None


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
    # 한글 wide char 보정 — pane 폭 기준으로 시각 길이 자르기 + ellipsis.
    # pane_w ~131mm, 캡션 좌측 패딩 3mm, 폰트 8.5pt → 영문 1글자 ~1.5mm, 한글 ~3mm.
    # max visual length 80 (영문 80자 / 한글 40자) 보수적으로.
    sub_text = sub or ""
    visual_max = 80
    out, vw = "", 0
    for ch in sub_text:
        cw = 2 if ord(ch) >= 0x3000 else 1
        if vw + cw > visual_max:
            out += "…"
            break
        out += ch
        vw += cw
    c.drawString(x + 3 * mm, y + h - 9 * mm, out)

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
        pil = _download_image_to_pil(image_url, max_dim=1000)
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
    # JPEG 65 압축으로 reportlab 임베딩 — 100MB+ PDF 회피.
    # 운영 검증 (2026-05-02): 32문항×3후보 = 96 페이지 시나리오에서 max_dim 1200/q75
    # → 11MB/32s. 학부모 모바일/카톡 부담. q65 + max_dim 1000으로 사이즈/속도 절감.
    jpg_buf = io.BytesIO()
    pil.save(jpg_buf, format="JPEG", quality=65, optimize=True)
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
        # landscape inner_w ~269mm → 한글 wide char 기준 줄당 ~55자 안전 (영문 110자)
        # 한글이 포함되면 wide char로 폭이 약 2배 → wrap을 보수적으로 55자.
        # ord >= 0x3000 (CJK) 한 글자를 2칸으로 카운트하여 시각 폭 기준 wrap.
        def _visual_len(s: str) -> int:
            return sum(2 if ord(ch) >= 0x3000 else 1 for ch in s)
        def _wrap_visual(s: str, max_w: int = 110) -> List[str]:
            out, cur, cur_w = [], "", 0
            for ch in s:
                w = 2 if ord(ch) >= 0x3000 else 1
                if cur_w + w > max_w:
                    out.append(cur)
                    cur, cur_w = ch, w
                else:
                    cur += ch
                    cur_w += w
            if cur:
                out.append(cur)
            return out
        lines: List[str] = []
        for raw in comment_text.split("\n"):
            line = raw.strip()
            if not line:
                continue
            lines.extend(_wrap_visual(line, 110))
        ty = cb_y + cb_h - 11 * mm
        max_lines = 3
        shown = lines[:max_lines]
        for ln in shown:
            c.drawString(margin + 4 * mm, ty, ln)
            ty -= 5 * mm
        # 잘림 표시 — 사용자가 PDF에 모든 코멘트가 안 나온다는 걸 인지해야.
        if len(lines) > max_lines:
            c.setFillColor(HexColor("#94A3B8"))
            c.setFont(fn_reg, 8)
            c.drawString(
                margin + 4 * mm, ty + 1 * mm,
                f"… +{len(lines) - max_lines}줄 더 (편집기에서 전체 확인)",
            )

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


def _draw_group_compare_page(c, *, page_w, page_h, margin, inner_w,
                             fn_reg, fn_bold, tenant_name,
                             q_number, group_idx, group_count,
                             total_matches, match_start_idx,
                             label_text, header_classification,
                             left_label, left_sub, left_url,
                             matches,
                             comment_text=None,
                             footer_idx=0, footer_total=0,
                             image_cache=None):
    """문항 1개와 후보 여러 건을 한 페이지에 묶어 보여주는 비교 페이지."""
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.units import mm

    has_comment = bool(comment_text and comment_text.strip())

    # ── 상단 헤더
    header_h = 16 * mm
    c.setFillColor(HexColor(_HEADER_COLOR))
    c.rect(0, page_h - header_h, page_w, header_h, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(fn_bold, 14)
    q_text = f"Q{q_number}"
    if total_matches > 0:
        q_text += f"  ·  대비 자료 {total_matches}건"
        if group_count > 1:
            group_end = match_start_idx + len(matches) - 1
            q_text += f"  ({match_start_idx}-{group_end}/{total_matches})"
    else:
        q_text += "  ·  선택 없음"
    c.drawString(margin, page_h - 11 * mm, q_text)

    if header_classification:
        c.setFillColor(HexColor(_pane_color_for_class(header_classification)))
    else:
        c.setFillColor(HexColor(_MISS_COLOR))
    c.setFont(fn_bold, 12)
    c.drawRightString(page_w - margin, page_h - 11 * mm, label_text or "")

    # ── 푸터
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

    # ── 코멘트 band
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

        def _wrap_visual(s: str, max_w: int = 110) -> List[str]:
            out, cur, cur_w = [], "", 0
            for ch in s:
                w = 2 if ord(ch) >= 0x3000 else 1
                if cur_w + w > max_w:
                    out.append(cur)
                    cur, cur_w = ch, w
                else:
                    cur += ch
                    cur_w += w
            if cur:
                out.append(cur)
            return out

        lines: List[str] = []
        for raw in comment_text.split("\n"):
            line = raw.strip()
            if not line:
                continue
            lines.extend(_wrap_visual(line, 110))
        ty = cb_y + cb_h - 11 * mm
        max_lines = 3
        shown = lines[:max_lines]
        for ln in shown:
            c.drawString(margin + 4 * mm, ty, ln)
            ty -= 5 * mm
        if len(lines) > max_lines:
            c.setFillColor(HexColor("#94A3B8"))
            c.setFont(fn_reg, 8)
            c.drawString(
                margin + 4 * mm, ty + 1 * mm,
                f"… +{len(lines) - max_lines}줄 더 (편집기에서 전체 확인)",
            )

    # ── Pane 영역
    pane_top = page_h - header_h - 4 * mm
    pane_bottom = footer_h + comment_band_h + 4 * mm
    pane_h = pane_top - pane_bottom
    gap = 6 * mm
    left_w = inner_w * 0.42
    right_w = inner_w - left_w - gap

    _draw_single_pane(
        c, x=margin, y=pane_bottom, w=left_w, h=pane_h,
        label=left_label, sub=left_sub, image_url=left_url,
        accent_color=_SOURCE_PANE_COLOR, accent_bg=_SOURCE_PANE_BG,
        fn_reg=fn_reg, fn_bold=fn_bold,
        image_cache=image_cache,
    )

    right_x = margin + left_w + gap
    if not matches:
        _draw_single_pane(
            c, x=right_x, y=pane_bottom, w=right_w, h=pane_h,
            label="큐레이션 자료", sub="선택된 자료가 없습니다", image_url=None,
            accent_color=_MISS_COLOR, accent_bg="#F1F5F9",
            fn_reg=fn_reg, fn_bold=fn_bold,
            placeholder_text="큐레이션 미작성",
            image_cache=image_cache,
        )
        return

    match_gap = 3 * mm
    rows = 1 if len(matches) == 1 else 2
    cols = 1 if len(matches) <= 2 else 2
    cell_w = (right_w - match_gap * (cols - 1)) / cols
    cell_h = (pane_h - match_gap * (rows - 1)) / rows
    for idx, match in enumerate(matches):
        col = idx % cols
        row = idx // cols
        x = right_x + col * (cell_w + match_gap)
        y = pane_bottom + (rows - 1 - row) * (cell_h + match_gap)
        cls = match.get("classification")
        _draw_single_pane(
            c, x=x, y=y, w=cell_w, h=cell_h,
            label=match.get("label") or f"대비 자료 {match_start_idx + idx}",
            sub=match.get("sub") or "",
            image_url=match.get("url") or None,
            accent_color=_pane_color_for_class(cls) if cls else _MISS_COLOR,
            accent_bg=_pane_bg_for_class(cls) if cls else "#F1F5F9",
            fn_reg=fn_reg, fn_bold=fn_bold,
            placeholder_text=match.get("placeholder") or "이미지 없음",
            image_cache=image_cache,
        )


def _draw_cover(c, *, page_w, page_h, margin, inner_w,
                fn_reg, fn_bold, tenant_name, report_title,
                document_category, author_label, issued_at,
                summary_text, curated_count, total_q,
                pinned_count, hit_problem_count, hit_rate,
                curated_problem_count, curated_progress):
    """표지 — 학원 로고 띠 + 매치업 적중률 헤드라인 + 보고서 제목 + 메타 + 통계."""
    from reportlab.lib.colors import HexColor, black, white
    from reportlab.lib.units import mm

    # 헤더 띠 (32mm) — 학원명 + "{강사명} 적중 보고서" 정체성 표시.
    c.setFillColor(HexColor(_HEADER_COLOR))
    c.rect(0, page_h - 32 * mm, page_w, 32 * mm, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(fn_bold, 26)
    c.drawCentredString(page_w / 2, page_h - 18 * mm, tenant_name)
    c.setFont(fn_reg, 12)
    sub_title = (
        f"{author_label} 강사 적중 보고서"
        if author_label and author_label != "작성자 미기재"
        else "매치업 적중 보고서"
    )
    c.drawCentredString(page_w / 2, page_h - 26 * mm, sub_title)

    # ── 매치업 적중률 헤드라인 (학원 마케팅 1순위 정보) ──
    # 표지 맨 앞 큰 글씨로. 학생/학부모가 PDF 열자마자 보는 위치.
    # 분모 = 전체 시험지 문항 수 (강사 직접 요청). 학부모는 본 시험 전체에서
    # 우리 학원이 미리 다룬 비율을 보고 싶어함.
    headline_y = page_h - 56 * mm
    if total_q == 0:
        c.setFillColor(HexColor(_MISS_COLOR))
        c.setFont(fn_bold, 24)
        c.drawCentredString(page_w / 2, headline_y, "분리된 시험지 문항이 없습니다")
    elif curated_problem_count == 0:
        c.setFillColor(HexColor(_MISS_COLOR))
        c.setFont(fn_bold, 24)
        c.drawCentredString(page_w / 2, headline_y, "큐레이션된 문항이 없습니다")
        c.setFont(fn_reg, 11)
        c.setFillColor(HexColor("#94A3B8"))
        c.drawCentredString(
            page_w / 2, headline_y - 8 * mm,
            "편집기에서 시험지 문항별 학원 자료를 1건 이상 선택하세요.",
        )
    else:
        if hit_rate >= 50:
            hr_color = _HIT_COLOR
        elif hit_rate >= 25:
            hr_color = _ACCENT_COLOR
        else:
            hr_color = _MISS_COLOR
        c.setFillColor(HexColor(hr_color))
        c.setFont(fn_bold, 44)
        c.drawCentredString(page_w / 2, headline_y, f"매치업 적중률  {hit_rate:.1f}%")
        c.setFillColor(HexColor("#475569"))
        c.setFont(fn_reg, 12)
        c.drawCentredString(
            page_w / 2, headline_y - 9 * mm,
            f"전체 {total_q}문항 중 {hit_problem_count}문항이 학원 자료와 75%+ 유사",
        )
        # 큐레이션 진행률 보조 라인 — 부분 작업 보고서임을 정직하게 표시.
        if curated_problem_count < total_q:
            c.setFont(fn_reg, 10)
            c.setFillColor(HexColor("#94A3B8"))
            c.drawCentredString(
                page_w / 2, headline_y - 15 * mm,
                f"※ 큐레이션 작성 {curated_problem_count}/{total_q} 문항 ({curated_progress:.0f}%) — "
                "미작성 문항은 적중에서 제외",
            )

    # 표제 (적중률 아래)
    y = headline_y - 22 * mm
    c.setFillColor(black)
    c.setFont(fn_bold, 18)
    c.drawCentredString(page_w / 2, y, (report_title or "")[:80])
    y -= 7 * mm
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
        box_h = 30 * mm
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
        for line in lines[:4]:
            c.drawString(box_x + 6 * mm, ty, line)
            ty -= 5 * mm

    # 보조 통계 — 헤드라인과 metric 분리 명시 (혼란 방지).
    # 헤드라인 분모 = curated_problem_count (sel_ids 1건+ 문항)
    # 보조 = curated_count (sel_ids OR comment 작성 문항) — 코멘트만 있어도 작성으로 카운트.
    stat_y = 25 * mm
    c.setFont(fn_reg, 11)
    c.setFillColor(HexColor("#64748B"))
    c.drawCentredString(
        page_w / 2, stat_y,
        f"자료 선택: {curated_problem_count} / {total_q} 문항   ·   "
        f"코멘트 작성: {curated_count} / {total_q} 문항   ·   "
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
      2. 시험지 문항 1개 + 선택 후보 묶음 = 1 페이지.
         후보 2~4개는 같은 페이지에 표시하고, 5개 이상은 4개씩 다음 페이지로 넘긴다.
         선택 0건이면 placeholder 1 페이지.
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    from apps.domains.matchup.models import MatchupProblem
    from apps.domains.matchup.services import public_image_key_for_report

    fn_reg, fn_bold = _ensure_korean_font()

    document = report.document
    tenant = document.tenant
    tenant_name = (tenant.name or "").strip() or "학원"

    exam_problems = list(
        document.problems.exclude(image_key="").order_by("number")
    )
    entries_by_eid = {e.exam_problem_id: e for e in report.entries.all()}
    # 강사가 명시적으로 PDF 제외 토글한 Q는 본문 + 적중률 분모 모두에서 skip
    # (2026-05-05 박철T 보고: 매칭 못한 Q 페이지가 결과물에 들어가는 게 거슬림).
    excluded_ep_ids = {
        e.exam_problem_id for e in entries_by_eid.values()
        if getattr(e, "excluded", False)
    }
    if excluded_ep_ids:
        exam_problems = [ep for ep in exam_problems if ep.id not in excluded_ep_ids]

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
        if not sel:
            body_page_count += 1
        else:
            body_page_count += max(
                1,
                (len(sel) + _MAX_MATCHES_PER_GROUP_PAGE - 1) // _MAX_MATCHES_PER_GROUP_PAGE,
            )

    # ── 이미지 prefetch (병렬) ──
    # 게이트웨이 60s 컷 회피. 후보 N개 × 2 pane 직렬 다운로드는 N=20 정도부터 timeout.
    # 12 worker 병렬 + url 캐시로 중복 다운로드 제거.
    # P1 fix (2026-05-04): 100+ 문항 케이스에서 max_dim 1000 → 800으로 추가 절감
    # (PDF 사이즈 ~30% 감소 / download 시간 ~25% 절감). 운영 검증: 32문항×3=96 페이지
    # 30.9s/7.6MB → ~22s/5.4MB 추정.
    ep_url_by_id = {ep.id: _safe_url(public_image_key_for_report(ep)) for ep in exam_problems}
    sel_url_by_pid = {
        p.id: _safe_url(public_image_key_for_report(p))
        for p in selected_meta.values()
    }
    all_urls = [u for u in list(ep_url_by_id.values()) + list(sel_url_by_pid.values()) if u]
    # 100+ 문항 doc은 페이지 사이즈가 클수록 perf 영향 큼 → max_dim 적응적 조정.
    pdf_max_dim = 800 if body_page_count >= 80 else 1000
    image_cache = _prefetch_images(all_urls, max_dim=pdf_max_dim)
    if body_page_count >= 100:
        logger.warning(
            "MATCHUP_PDF_LARGE | report=%s | body_pages=%d | "
            "ZIP export 권장 (게이트웨이 60s 컷 risk)",
            report.id, body_page_count,
        )

    # 표지 통계 — excluded(PDF 제외 토글) entry는 본문/표지 모두에서 빠짐.
    curated_count = sum(
        1 for e in entries_by_eid.values()
        if not getattr(e, "excluded", False)
        and ((e.selected_problem_ids or []) or (e.comment or "").strip())
    )
    pinned_count = sum(
        len(e.selected_problem_ids or [])
        for e in entries_by_eid.values()
        if not getattr(e, "excluded", False)
    )

    # ── 매치업 적중률 (표지 헤드라인) ──
    # 강사 직접 요청 (2026-05-02): "전 문항에 대한 평균 적중률".
    # 분모 = 전체 시험지 문항 수 (total_q). 학부모가 본 시험에서 우리 학원이 미리
    # 다룬 비율 = 마케팅 핵심. curated 분모(부분 작업 보호)는 보조 라인으로 격하.
    # 분자: 큐레이션 자료 1건 이상이 시험지 문항과 sim ≥ 0.75 (직접+유형 적중).
    hit_problem_count = 0
    curated_problem_count = 0  # 큐레이션 자료가 1건 이상 선택된 문항 수 (보조)
    for ep in exam_problems:
        e = entries_by_eid.get(ep.id)
        sel_ids = (e.selected_problem_ids if e else []) or []
        if not sel_ids:
            continue
        curated_problem_count += 1
        for pid in sel_ids:
            p = selected_meta.get(int(pid))
            if not p:
                continue
            sim = _compute_display_sim(ep, p)
            if sim is not None and sim >= _TYPE_HIT:  # 0.75
                hit_problem_count += 1
                break  # 문항당 1번만 카운트
    total_q = len(exam_problems)
    # 분모 = 전체 시험지 문항 (강사 요청). 시험지 분리 0건 시 0%.
    hit_rate = (hit_problem_count / total_q * 100) if total_q else 0.0
    # 큐레이션 진행률 (보조) = 사용자가 작업한 비율
    curated_progress = (curated_problem_count / total_q * 100) if total_q else 0.0

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
    # 작성자(강사) 라벨 — author FK가 1순위. 없으면 legacy submitted_by_name 폴백.
    author_label = ""
    if report.author_id and report.author is not None:
        try:
            from apps.core.models.user import user_display_username
            author_label = (
                getattr(report.author, "name", None)
                or user_display_username(report.author)
                or getattr(report.author, "email", "")
                or ""
            ).strip()
        except Exception:
            author_label = ""
    if not author_label:
        author_label = (report.submitted_by_name or "").strip()
    if not author_label:
        author_label = "작성자 미기재"

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
        hit_problem_count=hit_problem_count, hit_rate=hit_rate,
        curated_problem_count=curated_problem_count,
        curated_progress=curated_progress,
    )
    c.showPage()

    # ── 본문: 문항 단위 그룹 페이지 ──
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

        if len(sel_ids) == 1:
            pid = sel_ids[0]
            page_idx += 1
            p = selected_meta.get(int(pid))
            if not p:
                _draw_compare_page(
                    c, page_w=page_w, page_h=page_h, margin=margin, inner_w=inner_w,
                    fn_reg=fn_reg, fn_bold=fn_bold, tenant_name=tenant_name,
                    q_number=ep.number, page_idx_in_q=1, q_pages=len(sel_ids),
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
            if sim is None:
                # embedding 누락 — 측정 불가. "0.0%" 대신 명시적 표시.
                cls = None
                label_text = "유사도 측정 불가 (임베딩 누락)"
            else:
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
                q_number=ep.number, page_idx_in_q=1, q_pages=len(sel_ids),
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
            continue

        match_items = []
        for ci, pid in enumerate(sel_ids, start=1):
            p = selected_meta.get(int(pid))
            if not p:
                match_items.append({
                    "classification": None,
                    "label": f"대비 자료 {ci}/{len(sel_ids)}",
                    "sub": f"#{pid} (자료 누락)",
                    "url": None,
                    "placeholder": "자료를 찾을 수 없음",
                    "label_text": "자료 누락",
                })
                continue

            sim = _compute_display_sim(ep, p)
            if sim is None:
                cls = None
                label_text = "유사도 측정 불가"
            else:
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
            match_items.append({
                "classification": cls,
                "label": f"대비 자료 {ci}/{len(sel_ids)}",
                "sub": f"{mat_sub}  ·  {label_text}",
                "url": mat_url,
                "placeholder": "이미지 없음",
                "label_text": label_text,
            })

        group_count = max(
            1,
            (len(match_items) + _MAX_MATCHES_PER_GROUP_PAGE - 1) // _MAX_MATCHES_PER_GROUP_PAGE,
        )
        rank = {"direct": 4, "type": 3, "concept": 2, "miss": 1}
        best = max(
            match_items,
            key=lambda item: rank.get(item.get("classification") or "", 0),
        )
        best_cls = best.get("classification")
        best_label = best.get("label_text") or ""
        header_label = (
            f"최고 {best_label}"
            if best_cls and best_label
            else f"선택 자료 {len(sel_ids)}건"
        )
        for offset in range(0, len(match_items), _MAX_MATCHES_PER_GROUP_PAGE):
            page_idx += 1
            chunk = match_items[offset:offset + _MAX_MATCHES_PER_GROUP_PAGE]
            _draw_group_compare_page(
                c, page_w=page_w, page_h=page_h, margin=margin, inner_w=inner_w,
                fn_reg=fn_reg, fn_bold=fn_bold, tenant_name=tenant_name,
                q_number=ep.number,
                group_idx=(offset // _MAX_MATCHES_PER_GROUP_PAGE) + 1,
                group_count=group_count,
                total_matches=len(match_items),
                match_start_idx=offset + 1,
                label_text=header_label,
                header_classification=best_cls,
                left_label="실제 시험",
                left_sub=f"{doc_title_short}  ·  {ep.number}번",
                left_url=ep_url,
                matches=chunk,
                comment_text=comment if comment else None,
                footer_idx=page_idx, footer_total=body_page_count,
                image_cache=image_cache,
            )
            c.showPage()

    c.save()
    return buf.getvalue()

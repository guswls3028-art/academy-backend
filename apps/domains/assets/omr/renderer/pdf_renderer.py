# apps/domains/assets/omr/renderer/pdf_renderer.py
"""
OMR PDF 렌더러 — reportlab 기반 벡터 PDF 생성

좌표계: meta_generator.py (좌상단 mm) → reportlab (좌하단 pt)

═══════════════════════════════════════════════════
시각 시스템 정의
═══════════════════════════════════════════════════

A. STROKE HIERARCHY (v8)
  S1  0.80pt  #333333  카드 외곽 / MC·서술형 최외곽 (좌우 동일)
  S2  0.50pt  #555555  헤더↔본문 구분선 (카드 내부 구획)
  S3  0.35pt  #aaaaaa  5행 강조 가로선
  S4  0.25pt  #cccccc  일반 행 구분 / 번호↔버블 세로선 (최소 인쇄 보장)
  S5  0.35pt  #444444  버블 외곽
  --  1.00pt  #666666  10행 강조 가로선

B. SPACING (mm)
  로고 영역 상단 패딩     5
  로고 → 시험명           3
  시험명 → 부제           2
  카드 헤더 높이          5.5
  카드 간 간격            0 (밀착, 선 공유)
  좌패널 ↔ 우답안         3 (LP_GAP)

C. TEXT HIERARCHY
  시험명      Bold  12pt  #111111
  부제        Reg    7pt  #666666
  섹션 헤더   Bold   7pt  #222222
  문항번호    Bold   7pt  #111111
  버블 숫자   Reg    5pt  #bbbbbb
  안내 제목   Bold  6.5pt #222222
  안내 본문   Reg   5.5pt #555555

D. CARD GROUPING
  로고+시험명+부제  열린 영역 (테두리 없음)
  성명             독립 카드 (S1 외곽 + S2 헤더선)
  전화번호         독립 카드 (밀착, 상변 공유)
  안내             독립 카드 (밀착, 상변 공유)
  MC 컬럼          독립 카드 (S1 외곽)
  서술형 컬럼      독립 카드 (S1 외곽)

E. BALANCE (CONTENT_H=195mm 기준)
  로고+제목  76mm  (39%)
  성명       16mm  ( 8%)
  전화번호   75mm  (39%)
  안내       28mm  (14%)
═══════════════════════════════════════════════════
"""
from __future__ import annotations

import io
import math
import os
import platform

from reportlab.lib.units import mm as MM
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.colors import black, white, HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.services.meta_generator import (
    PAGE_W, PAGE_H,
    CONTENT_X, CONTENT_Y, CONTENT_H,
    LP_W, LP_BORDER, LP_PAD_X,
    ANS_X,
    MC_COL_W, MC_COL_GAP, MC_HEADER_H, MC_NUM_W, MC_BUB_PAD,
    BUB_W, BUB_H,
    ID_DIGITS, ID_VALUES, ID_BUB_GAP, ID_BUB_H,
    MARGIN_R,
)

# ══════════════════════════════════════════════
# A. STROKE HIERARCHY
# ══════════════════════════════════════════════
S1 = 0.80   # 카드/컬럼 외곽
S2 = 0.50   # 헤더↔본문
S3 = 0.35   # 5행 강조
S4 = 0.25   # 보조선 (최소 인쇄 보장)
S5 = 0.35   # 버블

C1 = HexColor("#333333")   # S1 외곽
C2 = HexColor("#555555")   # S2 구획
C3 = HexColor("#aaaaaa")   # S3 강조
C4 = HexColor("#cccccc")   # S4 보조
C5 = HexColor("#444444")   # S5 버블

# C. TEXT HIERARCHY
CT  = HexColor("#111111")   # 본문
CT2 = HexColor("#222222")   # 헤더
CT3 = HexColor("#666666")   # 부제/안내본문
CT4 = HexColor("#c0c0c0")   # 버블숫자

# 헤더 배경
C_HDR = HexColor("#f4f4f4")
C_HDR_ESSAY = HexColor("#e8e8e8")   # 서술형 헤더
C_ZEBRA = HexColor("#fafafa")       # zebra striping
C_BUB_FILL = HexColor("#f8f8f8")    # 버블 내부 fill
C_G10 = HexColor("#666666")         # 10행 강조선
C_ESSAY_LINE = HexColor("#e8e8e8")  # 서술형 줄선

# B. SPACING (mm)
PAD_LOGO_TOP = 5.0
GAP_LOGO_TITLE = 3.0
GAP_TITLE_SUB = 2.0
HEADER_H = 5.5

# E. BALANCE (mm, 위→아래)
H_NOTE = 28.0
H_PHONE = 75.0
H_NAME = 16.0
H_LOGO = CONTENT_H - H_NOTE - H_PHONE - H_NAME

# 폰트
_FONT_OK = False
_FN = "OMRFont"
_FB = "OMRFontBold"


def _mm(v: float) -> float:
    return v * MM

def _y(y_mm: float) -> float:
    return _mm(PAGE_H) - _mm(y_mm)


def _register_fonts():
    global _FONT_OK
    if _FONT_OK:
        return
    reg, bold = [], []
    if platform.system() == "Windows":
        fd = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        reg = [os.path.join(fd, "malgun.ttf"), os.path.join(fd, "NotoSansKR-VF.ttf")]
        bold = [os.path.join(fd, "malgunbd.ttf")]
    else:
        # Linux — .ttf 우선 (.ttc는 subfontIndex 필요)
        reg = ["/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
               "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"]
        bold = ["/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
                "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf"]
    bd = os.path.join(os.path.dirname(__file__), "fonts")
    reg.append(os.path.join(bd, "NotoSansKR-Regular.ttf"))
    bold.append(os.path.join(bd, "NotoSansKR-Bold.ttf"))

    # .ttc fallback (subfontIndex=0 for Korean)
    ttc_reg = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"]
    ttc_bold = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]

    # Regular
    reg_ok = False
    for p in reg:
        if os.path.isfile(p):
            try:
                pdfmetrics.registerFont(TTFont(_FN, p))
                reg_ok = True
                break
            except Exception:
                continue
    if not reg_ok:
        for p in ttc_reg:
            if os.path.isfile(p):
                try:
                    pdfmetrics.registerFont(TTFont(_FN, p, subfontIndex=0))
                    reg_ok = True
                    break
                except Exception:
                    continue

    # Bold
    bold_ok = False
    for p in bold:
        if os.path.isfile(p):
            try:
                pdfmetrics.registerFont(TTFont(_FB, p))
                bold_ok = True
                break
            except Exception:
                continue
    if not bold_ok:
        for p in ttc_bold:
            if os.path.isfile(p):
                try:
                    pdfmetrics.registerFont(TTFont(_FB, p, subfontIndex=0))
                    bold_ok = True
                    break
                except Exception:
                    continue
    # 최후 fallback: Regular 폰트 파일을 Bold 이름으로도 등록
    if not bold_ok:
        for p in (reg + ttc_reg):
            if os.path.isfile(p):
                try:
                    if p.endswith(".ttc"):
                        pdfmetrics.registerFont(TTFont(_FB, p, subfontIndex=0))
                    else:
                        pdfmetrics.registerFont(TTFont(_FB, p))
                    break
                except Exception:
                    continue
    _FONT_OK = True


# ══════════════════════════════════════════════
# 공통 그리기 헬퍼
# ══════════════════════════════════════════════

def _card(c, x_pt, top_mm, h_mm, w_pt):
    """S1 외곽 카드."""
    c.setStrokeColor(C1); c.setLineWidth(S1)
    c.rect(x_pt, _y(top_mm + h_mm), w_pt, _mm(h_mm), stroke=1, fill=0)

def _header(c, x_pt, w_pt, top_mm, label):
    """S2 구분선 + 배경 + 라벨."""
    c.setFillColor(C_HDR)
    c.rect(x_pt, _y(top_mm + HEADER_H), w_pt, _mm(HEADER_H), fill=1, stroke=0)
    c.setStrokeColor(C2); c.setLineWidth(S2)
    c.line(x_pt, _y(top_mm + HEADER_H), x_pt + w_pt, _y(top_mm + HEADER_H))
    c.setFont(_FB, 7); c.setFillColor(CT2)
    c.drawCentredString(x_pt + w_pt / 2, _y(top_mm + HEADER_H - 1.5), label)


class OMRPdfRenderer:

    def render(self, doc: OMRDocument) -> bytes:
        _register_fonts()
        buf = io.BytesIO()
        cv = canvas.Canvas(buf, pagesize=landscape(A4))
        cv.setTitle(f"{doc.exam_title} OMR")
        self._corners(cv)
        self._left(cv, doc)
        self._mc(cv, doc)
        self._essay(cv, doc)
        self._timing_marks(cv, doc)
        cv.save()
        return buf.getvalue()

    # ── 코너 마크 (v11: 비대칭 4종 — AI 방향 판별용) ──
    # TL=square, TR=L-shape, BL=T-shape, BR=plus
    # 모든 마커는 굵은 채움 도형. meta_generator._build_marker_meta()와 동기화.
    _CORNER_OFF = 3.0   # 페이지 가장자리로부터 오프셋 (mm)
    _CORNER_SZ = 5.0    # 마커 기본 크기 (mm)
    _CORNER_TH = 1.5    # 마커 팔 두께 (mm)

    def _corners(self, c):
        off = self._CORNER_OFF
        sz = self._CORNER_SZ
        th = self._CORNER_TH
        pw, ph = PAGE_W, PAGE_H
        c.setFillColor(black)

        # TL: 채워진 정사각형 (5×5mm)
        c.rect(_mm(off), _y(off + sz), _mm(sz), _mm(sz), fill=1, stroke=0)

        # TR: L자 (오른쪽+아래 팔, 좌우반전)
        tr_x = pw - off - sz
        tr_y = off
        # 가로 팔
        c.rect(_mm(tr_x), _y(tr_y + th), _mm(sz), _mm(th), fill=1, stroke=0)
        # 세로 팔 (오른쪽 끝에서 아래로)
        c.rect(_mm(pw - off - th), _y(tr_y + sz), _mm(th), _mm(sz), fill=1, stroke=0)

        # BL: 채움 삼각형 (▲, 꼭짓점 위로)
        bl_cx = off + sz / 2
        bl_cy = ph - off - sz / 2
        p = c.beginPath()
        p.moveTo(_mm(bl_cx - sz / 2), _y(bl_cy + sz / 2))   # 좌하
        p.lineTo(_mm(bl_cx + sz / 2), _y(bl_cy + sz / 2))   # 우하
        p.lineTo(_mm(bl_cx), _y(bl_cy - sz / 2))             # 상단 꼭짓점
        p.close()
        c.drawPath(p, fill=1, stroke=0)

        # BR: 십자가 (+)
        br_cx = pw - off - sz / 2
        br_cy = ph - off - sz / 2
        # 가로 바
        c.rect(_mm(br_cx - sz / 2), _y(br_cy + th / 2), _mm(sz), _mm(th), fill=1, stroke=0)
        # 세로 바
        c.rect(_mm(br_cx - th / 2), _y(br_cy + sz / 2), _mm(th), _mm(sz), fill=1, stroke=0)

    # ══════════════════════════════════════════
    # 좌측 패널
    # ══════════════════════════════════════════
    def _left(self, c, doc: OMRDocument):
        x = _mm(CONTENT_X)
        w = _mm(LP_W)

        t_logo = CONTENT_Y
        t_name = t_logo + H_LOGO
        t_phone = t_name + H_NAME
        t_note = t_phone + H_PHONE

        # 0) 브랜드 컬러 바 (2mm, 상단)
        if doc.brand_color:
            try:
                bc = HexColor(doc.brand_color)
                c.setFillColor(bc)
                c.roundRect(x, _y(t_logo + 2), w, _mm(2), _mm(1), fill=1, stroke=0)
            except Exception:
                pass

        # 1) 로고+제목 — 테두리 없는 열린 영역
        self._logo_title(c, doc, x, w, t_logo, H_LOGO)

        # 2) 성명 카드
        _card(c, x, t_name, H_NAME, w)
        _header(c, x, w, t_name, "성 명")

        # 3) 전화번호 카드
        _card(c, x, t_phone, H_PHONE, w)
        _header(c, x, w, t_phone, "학생 식별번호 (전화번호 뒤 8자리)")
        self._phone(c, x, w, t_phone)

        # 4) 안내 카드
        _card(c, x, t_note, H_NOTE, w)
        self._note(c, x, w, t_note)

        # 5) QR 예약 영역 (안내 카드 우하단)
        qr_x = x + w - _mm(9)
        qr_y = _y(t_note + H_NOTE - 1)
        c.setStrokeColor(C4); c.setLineWidth(S4)
        c.setDash(1, 1)
        c.rect(qr_x, qr_y, _mm(8), _mm(8), stroke=1, fill=0)
        c.setDash()

    def _logo_title(self, c, doc, x, w, top, h):
        """로고 + 시험명 + 부제. 테두리 없음. 내부 여백 확보."""
        ix = x + _mm(LP_PAD_X)
        iw = w - 2 * _mm(LP_PAD_X)
        cx = x + w / 2  # 중심축

        # 로고 — 영역의 45% 이내, 상단 5mm 패딩
        logo_ceil = _y(top + PAD_LOGO_TOP)
        logo_max = _mm(min(h * 0.45, 30))
        cursor = logo_ceil  # 다음 요소 기준점 (reportlab y)

        if doc.logo_bytes:
            try:
                img = ImageReader(io.BytesIO(doc.logo_bytes))
                ow, oh = img.getSize()
                sc = min(iw * 0.85 / ow, logo_max / oh)
                dw, dh = ow * sc, oh * sc
                img_x = ix + (iw - dw) / 2
                img_y = logo_ceil - dh
                c.drawImage(img, img_x, img_y, dw, dh,
                            preserveAspectRatio=True, mask="auto")
                cursor = img_y - _mm(GAP_LOGO_TITLE)
            except Exception:
                cursor = logo_ceil - _mm(8)
        else:
            cursor = logo_ceil - _mm(8)

        # 시험명 — 자동 축소 (15자 초과 → 10pt, 20자 초과 → 9pt)
        title_len = len(doc.exam_title) if doc.exam_title else 0
        title_pt = 9 if title_len > 20 else (10 if title_len > 15 else 12)
        c.setFont(_FB, title_pt); c.setFillColor(CT)
        c.drawCentredString(cx, cursor, doc.exam_title)

        # 부제 — 7pt regular, 시험명 아래 2mm
        parts = []
        if doc.lecture_name: parts.append(doc.lecture_name)
        if doc.session_name: parts.append(doc.session_name)
        if parts:
            c.setFont(_FN, 7); c.setFillColor(CT3)
            c.drawCentredString(cx, cursor - _mm(GAP_TITLE_SUB + 3), " / ".join(parts))

    def _phone(self, c, x, w, top):
        """전화번호 쓰기 칸 + 버블 그리드."""
        inner_x = x + _mm(LP_BORDER + LP_PAD_X)
        inner_w = LP_W - 2 * LP_BORDER - 2 * LP_PAD_X

        dw = 6.2   # 숫자 셀 폭 mm
        sw = 4.0    # 대시 구분자 폭 mm
        gw = ID_DIGITS * dw + sw
        gx = inner_x + _mm((inner_w - gw) / 2)

        wy = top + HEADER_H + 2.0   # 쓰기 칸 y
        ch = 7.5                     # 쓰기 칸 높이

        # 쓰기 칸
        c.setLineWidth(S4 + 0.15); c.setStrokeColor(C2)
        for d in range(ID_DIGITS):
            dx = gx + _mm((d * dw) if d < 4 else (4 * dw + sw + (d - 4) * dw))
            c.rect(dx, _y(wy + ch), _mm(dw), _mm(ch), stroke=1, fill=0)

        # 대시
        if ID_DIGITS > 4:
            sx = gx + _mm(4 * dw)
            c.setFont(_FB, 10); c.setFillColor(CT)
            c.drawCentredString(sx + _mm(sw / 2), _y(wy + ch - 1.5), "–")

        # 버블 그리드 (gap은 meta_generator.ID_BUB_GAP과 동기화)
        by0 = top + HEADER_H + ch + 4.0
        bg = ID_BUB_GAP  # 0.6mm — meta_generator와 동기화
        c.setLineWidth(S5)
        for d in range(ID_DIGITS):
            dx = gx + _mm((d * dw) if d < 4 else (4 * dw + sw + (d - 4) * dw))
            ccx = dx + _mm(dw / 2)
            for v in range(ID_VALUES):
                by = by0 + v * (BUB_H + bg)
                bcy = _y(by + BUB_H / 2)
                c.setStrokeColor(C5); c.setFillColor(C_BUB_FILL)
                c.ellipse(ccx - _mm(BUB_W/2), bcy - _mm(BUB_H/2),
                          ccx + _mm(BUB_W/2), bcy + _mm(BUB_H/2), stroke=1, fill=1)
                c.setFont(_FN, 5.5); c.setFillColor(CT4)
                c.drawCentredString(ccx, bcy - _mm(0.8), str(v))

    def _note(self, c, x, w, top):
        """답안지 작성 안내."""
        ix = x + _mm(3)
        ty = _y(top + 3)
        lh = 3.6  # 줄 간격 mm

        c.setFont(_FB, 7); c.setFillColor(CT2)
        c.drawString(ix, ty, "답안지 작성 안내")

        y0 = ty - _mm(4.0)

        # 1번
        c.setFont(_FN, 6); c.setFillColor(CT3)
        c.drawString(ix, y0, "1. 전화번호는 본인 휴대폰 번호 뒤 8자리를 적어주세요.")
        c.drawString(ix + _mm(2.5), y0 - _mm(lh), "휴대폰이 없으면 부모님 번호 뒤 8자리를 적어주세요.")

        # 2번 — "사인펜" bold
        c.setFont(_FN, 6); c.setFillColor(CT3)
        c.drawString(ix, y0 - _mm(lh * 2), "2. 객관식은 ")
        c.setFont(_FB, 6)
        c.drawString(ix + _mm(13.5), y0 - _mm(lh * 2), "컴퓨터용 사인펜")
        c.setFont(_FN, 6)
        c.drawString(ix + _mm(31), y0 - _mm(lh * 2), "으로 칠해주세요.")

        # 3번
        c.drawString(ix, y0 - _mm(lh * 3), "3. 서술형은 답을 정자로 깔끔하게 적어주세요.")

        # 4번 — "수정테이프" bold
        c.drawString(ix, y0 - _mm(lh * 4), "4. ")
        c.setFont(_FB, 6)
        c.drawString(ix + _mm(3), y0 - _mm(lh * 4), "수정테이프")
        c.setFont(_FN, 6)
        c.drawString(ix + _mm(16), y0 - _mm(lh * 4), "를 사용해주세요. (수정액 금지)")

        # 5번 — 마킹 예시
        c.drawString(ix, y0 - _mm(lh * 5), "5. 올바른 마킹: ")
        # 올바른 마킹 — 채워진 타원
        mark_x = ix + _mm(17)
        mark_y = y0 - _mm(lh * 5) + _mm(0.5)
        c.setFillColor(HexColor("#333333"))
        c.ellipse(mark_x, mark_y - _mm(1.5), mark_x + _mm(3), mark_y + _mm(1.5),
                  fill=1, stroke=0)
        # 잘못된 마킹
        c.setFont(_FN, 6); c.setFillColor(CT3)
        c.drawString(mark_x + _mm(5), y0 - _mm(lh * 5), "잘못된 마킹: ")
        c.setFont(_FB, 7); c.setFillColor(HexColor("#999999"))
        c.drawString(mark_x + _mm(17), y0 - _mm(lh * 5), "✓  △  ─")

    # ══════════════════════════════════════════
    # MC 컬럼
    # ══════════════════════════════════════════
    def _mc(self, c, doc):
        if doc.mc_count <= 0:
            return
        mc = doc.mc_count
        if mc <= 20:   pc, nc = mc, 1
        elif mc <= 40: pc, nc = math.ceil(mc/2), 2
        else:          pc, nc = math.ceil(mc/3), 3
        for i in range(nc):
            cx = ANS_X + i * (MC_COL_W + MC_COL_GAP)
            s = i * pc + 1
            e = min(s + pc - 1, mc)
            self._mc_col(c, doc, cx, s, e, nc)

    def _timing_marks(self, c, doc):
        """
        OMR 인식용 타이밍 마크 v11 — 상용 OMR 수준 식별 밀도.

        1. MC 컬럼 좌측: 매 행 중심에 사각 마크 (2.5×2.0mm)
        2. MC 컬럼 우측: 매 행 중심에 사각 마크 (2.5×2.0mm), 5행마다 확대 (3.0×2.5mm)
        3. 컬럼 상/하단: 삼각 마크 (3mm, 기존 2mm에서 확대)
        4. 상하단 정렬 바: 컬럼 전체 폭 연속 바 (행 좌표 글로벌 기준)

        meta_generator.py와 동기화.
        """
        mc = doc.mc_count
        if mc <= 0:
            return

        if mc <= 20:   pc, nc = mc, 1
        elif mc <= 40: pc, nc = math.ceil(mc/2), 2
        else:          pc, nc = math.ceil(mc/3), 3

        bt = CONTENT_Y + MC_HEADER_H
        bh = CONTENT_H - MC_HEADER_H
        c.setFillColor(black)

        for ci in range(nc):
            col_x = ANS_X + ci * (MC_COL_W + MC_COL_GAP)
            s = ci * pc + 1
            e = min(s + pc - 1, mc)
            cnt = e - s + 1
            rh = bh / cnt if cnt else bh

            is_first_col = (ci == 0)
            is_last_col = (ci == nc - 1)

            # ── 좌측 타이밍 마크: 첫 번째 컬럼만 (겹침 방지) ──
            if is_first_col:
                lm_x = col_x - 3.5
                lm_w = 2.5
                lm_h = 2.0
                for qi in range(cnt):
                    rc = bt + (qi + 0.5) * rh
                    c.rect(
                        _mm(lm_x), _y(rc + lm_h / 2),
                        _mm(lm_w), _mm(lm_h),
                        fill=1, stroke=0,
                    )

            # ── 우측 타이밍 마크: 마지막 컬럼만 (겹침 방지) ──
            if is_last_col:
                rm_x = col_x + MC_COL_W + 1.0
                for qi in range(cnt):
                    rc = bt + (qi + 0.5) * rh
                    if qi % 5 == 0:
                        c.rect(
                            _mm(rm_x), _y(rc + 1.25),
                            _mm(3.0), _mm(2.5),
                            fill=1, stroke=0,
                        )
                    else:
                        c.rect(
                            _mm(rm_x), _y(rc + 1.0),
                            _mm(2.5), _mm(2.0),
                            fill=1, stroke=0,
                        )

            # ── 컬럼 상/하단 삼각형 마커 (3mm) ──
            top_cx = col_x + MC_COL_W / 2
            top_cy = CONTENT_Y - 2.0
            self._triangle_down(c, top_cx, top_cy, 3.0)
            bot_cy = CONTENT_Y + CONTENT_H + 2.0
            self._triangle_up(c, top_cx, bot_cy, 3.0)

            # ── 상하단 정렬 바 (컬럼 전체 폭, 1.5mm 두께) ──
            bar_h = 1.5
            c.rect(
                _mm(col_x), _y(CONTENT_Y - 0.2),
                _mm(MC_COL_W), _mm(bar_h),
                fill=1, stroke=0,
            )
            c.rect(
                _mm(col_x), _y(CONTENT_Y + CONTENT_H + bar_h + 0.2),
                _mm(MC_COL_W), _mm(bar_h),
                fill=1, stroke=0,
            )

    @staticmethod
    def _triangle_down(c, cx_mm, cy_mm, size_mm):
        """아래 방향 삼각형 (채움)."""
        s = size_mm / 2
        p = c.beginPath()
        p.moveTo(_mm(cx_mm - s), _y(cy_mm - s))
        p.lineTo(_mm(cx_mm + s), _y(cy_mm - s))
        p.lineTo(_mm(cx_mm), _y(cy_mm + s))
        p.close()
        c.setFillColor(black)
        c.drawPath(p, fill=1, stroke=0)

    @staticmethod
    def _triangle_up(c, cx_mm, cy_mm, size_mm):
        """위 방향 삼각형 (채움)."""
        s = size_mm / 2
        p = c.beginPath()
        p.moveTo(_mm(cx_mm - s), _y(cy_mm + s))
        p.lineTo(_mm(cx_mm + s), _y(cy_mm + s))
        p.lineTo(_mm(cx_mm), _y(cy_mm - s))
        p.close()
        c.setFillColor(black)
        c.drawPath(p, fill=1, stroke=0)

    def _mc_col(self, c, doc, col_mm, s, e, ncols):
        cx = _mm(col_mm)
        ct = _y(CONTENT_Y)
        cw = _mm(MC_COL_W)
        ch = _mm(CONTENT_H)

        # S1 외곽
        c.setStrokeColor(C1); c.setLineWidth(S1)
        c.rect(cx, ct - ch, cw, ch, stroke=1, fill=0)

        # 헤더
        hh = _mm(MC_HEADER_H)
        c.setFillColor(C_HDR)
        c.rect(cx, ct - hh, cw, hh, fill=1, stroke=0)
        c.setStrokeColor(C2); c.setLineWidth(S2)
        c.line(cx, ct - hh, cx + cw, ct - hh)

        nw = _mm(MC_NUM_W)
        # 헤더 내 번호 세로선 (S4)
        c.setStrokeColor(C4); c.setLineWidth(S4)
        c.line(cx + nw, ct, cx + nw, ct - hh)

        c.setFont(_FB, 5.5); c.setFillColor(CT2)
        c.drawCentredString(cx + nw/2, ct - hh + _mm(1.5), "번호")
        cnt = e - s + 1
        lb = f"{s}번 ~ {e}번" if ncols > 1 else f"객관식 {cnt}문항"
        c.drawCentredString(cx + nw + (cw - nw)/2, ct - hh + _mm(1.5), lb)

        # 바디
        bt = CONTENT_Y + MC_HEADER_H
        bh = CONTENT_H - MC_HEADER_H
        rh = bh / cnt if cnt else bh

        # 번호↔버블 세로선 (S4, 전체 높이)
        c.setStrokeColor(C4); c.setLineWidth(S4)
        c.line(cx + nw, _y(bt), cx + nw, _y(bt + bh))

        # 버블 x좌표
        nc = doc.n_choices
        ax = col_mm + MC_NUM_W + MC_BUB_PAD
        aw = MC_COL_W - MC_NUM_W - 2 * MC_BUB_PAD
        gap = (aw - nc * BUB_W) / (nc + 1)
        bxs = [ax + gap*(i+1) + BUB_W*i + BUB_W/2 for i in range(nc)]

        # 선지 세로선 제거 — 버블 간격만으로 충분히 구분됨

        # 행
        for qi in range(cnt):
            qn = s + qi
            rt = bt + qi * rh
            rc = rt + rh / 2

            # zebra striping (5행 그룹 교대)
            group_idx = qi // 5
            if group_idx % 2 == 1:
                c.setFillColor(C_ZEBRA)
                c.rect(cx, _y(rt + rh), cw, _mm(rh), fill=1, stroke=0)

            # 행 구분선
            if qi > 0:
                g10 = (qi % 10 == 0)
                g5 = (qi % 5 == 0)
                if g10:
                    c.setStrokeColor(C_G10); c.setLineWidth(1.0)
                elif g5:
                    c.setStrokeColor(C3); c.setLineWidth(S3)
                else:
                    c.setStrokeColor(C4); c.setLineWidth(S4)
                c.line(cx, _y(rt), cx + cw, _y(rt))

            # 번호 (8pt bold)
            c.setFont(_FB, 8); c.setFillColor(CT)
            c.drawCentredString(cx + nw/2, _y(rc) - _mm(1.4), str(qn))

            # 버블 (fill: #f8f8f8)
            for bi, bx_mm in enumerate(bxs):
                bx = _mm(bx_mm)
                by = _y(rc)
                c.setStrokeColor(C5); c.setLineWidth(S5); c.setFillColor(C_BUB_FILL)
                c.ellipse(bx - _mm(BUB_W/2), by - _mm(BUB_H/2),
                          bx + _mm(BUB_W/2), by + _mm(BUB_H/2), stroke=1, fill=1)
                c.setFont(_FN, 5.5); c.setFillColor(CT4)
                c.drawCentredString(bx, by - _mm(0.8), str(bi + 1))

    # ══════════════════════════════════════════
    # 서술형 컬럼
    # ══════════════════════════════════════════
    def _essay(self, c, doc):
        if doc.essay_count <= 0:
            return
        mc = doc.mc_count
        nmc = 0 if mc <= 0 else (1 if mc <= 20 else (2 if mc <= 40 else 3))
        ex_mm = ANS_X + nmc * (MC_COL_W + MC_COL_GAP)
        ew_mm = max(40.0, PAGE_W - MARGIN_R - ex_mm)

        ex = _mm(ex_mm)
        ew = _mm(ew_mm)
        et = _y(CONTENT_Y)
        eh = _mm(CONTENT_H)

        # S1 외곽
        c.setStrokeColor(C1); c.setLineWidth(S1)
        c.rect(ex, et - eh, ew, eh, stroke=1, fill=0)

        # 헤더 (서술형 → 다른 배경)
        hh = _mm(MC_HEADER_H)
        c.setFillColor(C_HDR_ESSAY)
        c.rect(ex, et - hh, ew, hh, fill=1, stroke=0)
        c.setStrokeColor(C2); c.setLineWidth(S2)
        c.line(ex, et - hh, ex + ew, et - hh)

        nw = _mm(MC_NUM_W)
        c.setStrokeColor(C4); c.setLineWidth(S4)
        c.line(ex + nw, et, ex + nw, et - hh)

        c.setFont(_FB, 5.5); c.setFillColor(CT2)
        c.drawCentredString(ex + nw/2, et - hh + _mm(1.5), "번호")
        c.drawCentredString(ex + nw + (ew - nw)/2, et - hh + _mm(1.5),
                            f"서술형 {doc.essay_count}문항")

        bt = CONTENT_Y + MC_HEADER_H
        bh = CONTENT_H - MC_HEADER_H
        rh = bh / doc.essay_count

        # 번호 세로선
        c.setStrokeColor(C4); c.setLineWidth(S4)
        c.line(ex + nw, _y(bt), ex + nw, _y(bt + bh))

        for i in range(doc.essay_count):
            rt = bt + i * rh
            rc = rt + rh / 2

            # zebra striping
            group_idx = i // 5
            if group_idx % 2 == 1:
                c.setFillColor(C_ZEBRA)
                c.rect(ex, _y(rt + rh), ew, _mm(rh), fill=1, stroke=0)

            if i > 0:
                g10 = (i % 10 == 0)
                g5 = (i % 5 == 0)
                if g10:
                    c.setStrokeColor(C_G10); c.setLineWidth(1.0)
                elif g5:
                    c.setStrokeColor(C3); c.setLineWidth(S3)
                else:
                    c.setStrokeColor(C4); c.setLineWidth(S4)
                c.line(ex, _y(rt), ex + ew, _y(rt))

            # 번호 (8pt bold)
            c.setFont(_FB, 8); c.setFillColor(CT)
            c.drawCentredString(ex + nw/2, _y(rc) - _mm(1.4), str(i + 1))

            # 서술형: 빈 칸 (줄선 없음)

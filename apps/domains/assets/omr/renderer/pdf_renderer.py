# apps/domains/assets/omr/renderer/pdf_renderer.py
"""
OMR PDF 렌더러 — reportlab 기반 벡터 PDF 생성

좌표계: meta_generator.py (좌상단 mm) → reportlab (좌하단 pt)

═══════════════════════════════════════════════════
시각 시스템 정의
═══════════════════════════════════════════════════

A. STROKE HIERARCHY
  S1  0.80pt  #333333  카드 외곽 / MC·서술형 최외곽 (좌우 동일)
  S2  0.50pt  #555555  헤더↔본문 구분선 (카드 내부 구획)
  S3  0.35pt  #aaaaaa  5행 강조 가로선
  S4  0.15pt  #cccccc  일반 행 구분 / 번호↔버블 세로선 / 선지 세로선
  S5  0.35pt  #444444  버블 외곽

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
    ID_DIGITS, ID_VALUES,
    MARGIN_R,
)

# ══════════════════════════════════════════════
# A. STROKE HIERARCHY
# ══════════════════════════════════════════════
S1 = 0.80   # 카드/컬럼 외곽
S2 = 0.50   # 헤더↔본문
S3 = 0.35   # 5행 강조
S4 = 0.15   # 보조선 (가장 약함)
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
CT4 = HexColor("#bbbbbb")   # 버블숫자

# 헤더 배경
C_HDR = HexColor("#f4f4f4")

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
        cv.save()
        return buf.getvalue()

    # ── 코너 마크 ──
    def _corners(self, c):
        L, W, off = _mm(5), _mm(0.5), _mm(3)
        pw, ph = _mm(PAGE_W), _mm(PAGE_H)
        c.setFillColor(black)
        for ax, ay, aw, ah in [
            (off, ph-off-W, L, W), (off, ph-off-L, W, L),             # TL
            (pw-off-L, ph-off-W, L, W), (pw-off-W, ph-off-L, W, L),   # TR
            (off, off, L, W), (off, off, W, L),                         # BL
            (pw-off-L, off, L, W), (pw-off-W, off, W, L),              # BR
        ]:
            c.rect(ax, ay, aw, ah, fill=1, stroke=0)

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

        # 1) 로고+제목 — 테두리 없는 열린 영역
        self._logo_title(c, doc, x, w, t_logo, H_LOGO)

        # 2) 성명 카드
        _card(c, x, t_name, H_NAME, w)
        _header(c, x, w, t_name, "성 명")

        # 3) 전화번호 카드
        _card(c, x, t_phone, H_PHONE, w)
        _header(c, x, w, t_phone, "전화번호 뒤 8자리")
        self._phone(c, x, w, t_phone)

        # 4) 안내 카드
        _card(c, x, t_note, H_NOTE, w)
        self._note(c, x, w, t_note)

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

        # 시험명 — 12pt bold, 중심축
        c.setFont(_FB, 12); c.setFillColor(CT)
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

        # 버블 그리드
        by0 = top + HEADER_H + ch + 4.0
        bg = 0.4
        c.setLineWidth(S5)
        for d in range(ID_DIGITS):
            dx = gx + _mm((d * dw) if d < 4 else (4 * dw + sw + (d - 4) * dw))
            ccx = dx + _mm(dw / 2)
            for v in range(ID_VALUES):
                by = by0 + v * (BUB_H + bg)
                bcy = _y(by + BUB_H / 2)
                c.setStrokeColor(C5); c.setFillColor(white)
                c.ellipse(ccx - _mm(BUB_W/2), bcy - _mm(BUB_H/2),
                          ccx + _mm(BUB_W/2), bcy + _mm(BUB_H/2), stroke=1, fill=1)
                c.setFont(_FN, 4.5); c.setFillColor(CT4)
                c.drawCentredString(ccx, bcy - _mm(0.8), str(v))

    def _note(self, c, x, w, top):
        """답안지 작성 안내. 원문 5줄 유지."""
        ix = x + _mm(3)
        ty = _y(top + 3)

        c.setFont(_FB, 6.5); c.setFillColor(CT2)
        c.drawString(ix, ty, "답안지 작성 안내")

        c.setFont(_FN, 5.5); c.setFillColor(CT3)
        lines = [
            "1. 전화번호는 본인 휴대폰 번호 뒤 8자리를 적어주세요.",
            "   휴대폰이 없는 학생은 부모님 번호 뒤 8자리를 적어주세요.",
            "2. 객관식은 컴퓨터용 사인펜으로 버블을 빈틈없이 칠해주세요.",
            "3. 서술형은 답을 정자로 깔끔하게 적어주세요.",
            "4. 수정할 때는 수정테이프를 사용해주세요.",
        ]
        for i, ln in enumerate(lines):
            c.drawString(ix, ty - _mm(4.0 + i * 3.8), ln)

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
        lb = f"{s}번 ~ {e}번" if ncols > 1 else "객관식"
        c.drawCentredString(cx + nw + (cw - nw)/2, ct - hh + _mm(1.5), lb)

        # 바디
        bt = CONTENT_Y + MC_HEADER_H
        bh = CONTENT_H - MC_HEADER_H
        cnt = e - s + 1
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

        # 선지 세로선 (S4, 전체 높이)
        c.setStrokeColor(C4); c.setLineWidth(S4)
        for i in range(nc - 1):
            mx = _mm((bxs[i] + bxs[i+1]) / 2)
            c.line(mx, _y(bt), mx, _y(bt + bh))

        # 행
        for qi in range(cnt):
            qn = s + qi
            rt = bt + qi * rh
            rc = rt + rh / 2

            # 행 구분선
            if qi > 0:
                g5 = (qi % 5 == 0)
                c.setStrokeColor(C3 if g5 else C4)
                c.setLineWidth(S3 if g5 else S4)
                c.line(cx, _y(rt), cx + cw, _y(rt))

            # 번호
            c.setFont(_FB, 7); c.setFillColor(CT)
            c.drawCentredString(cx + nw/2, _y(rc) - _mm(1.2), str(qn))

            # 버블
            for bi, bx_mm in enumerate(bxs):
                bx = _mm(bx_mm)
                by = _y(rc)
                c.setStrokeColor(C5); c.setLineWidth(S5); c.setFillColor(white)
                c.ellipse(bx - _mm(BUB_W/2), by - _mm(BUB_H/2),
                          bx + _mm(BUB_W/2), by + _mm(BUB_H/2), stroke=1, fill=1)
                c.setFont(_FN, 5); c.setFillColor(CT4)
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

        # 헤더
        hh = _mm(MC_HEADER_H)
        c.setFillColor(C_HDR)
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
            if i > 0:
                g5 = (i % 5 == 0)
                c.setStrokeColor(C3 if g5 else C4)
                c.setLineWidth(S3 if g5 else S4)
                c.line(ex, _y(rt), ex + ew, _y(rt))
            c.setFont(_FB, 7); c.setFillColor(CT)
            c.drawCentredString(ex + nw/2, _y(rc) - _mm(1.2), str(i + 1))

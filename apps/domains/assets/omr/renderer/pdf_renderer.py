# apps/domains/assets/omr/renderer/pdf_renderer.py
"""
OMR PDF 렌더러 v14 — reportlab 기반 벡터 PDF 생성 (한국 표준 OMR 스타일)

좌표계: meta_generator.py (좌상단 mm) → reportlab (좌하단 pt)

═══════════════════════════════════════════════════
시각 시스템 정의 v14
═══════════════════════════════════════════════════

A. STROKE & COLOR
  S1  2.00pt  #000000  외곽 + 컬럼 구분 (통합 프레임)
  S2  0.80pt  #333333  헤더↔본문 구분선
  S3  0.50pt  #777777  5행 강조 가로선
  S4  0.20pt  #cccccc  일반 행 구분 — 미세
  S5  0.30pt  #444444  버블 외곽

B. SPACING (mm)
  로고 영역 상단 패딩     5
  로고 → 시험명           3
  시험명 → 부제           2
  카드 헤더 높이          5.5
  좌패널 ↔ 우답안         3 (LP_GAP)

C. TEXT HIERARCHY
  시험명      Bold  12pt  #000000
  부제        Reg    7pt  #666666
  섹션 헤더   Bold  5.5pt #222222
  문항번호    Bold   8pt  #000000
  버블 숫자   Bold  6.5pt #888888
  안내 제목   Bold  6.5pt #222222
  안내 본문   Reg   5.5pt #666666

D. 통합 프레임 구조 (v14)
  좌측 패널  단일 rect + 내부 구분선
  답안 영역  단일 외곽 rect + 세로선 칸막이 (독립 박스 없음)
  그리기 순서: 배경 fill → 내부선 → 외곽 stroke → 텍스트 → 버블

E. 타이밍 마크 (한국 표준)
  상하단     균일 간격 수평 바 (4×1.5mm, 8mm 간격)
  좌우       행 바 (5행: 1.5mm, 10행: 2.0mm, LP_GAP 내 안착)

F. 인식 시스템
  코너 마커    8mm 비대칭 4종 (TL=square, TR=L, BL=triangle, BR=plus)
  AI 파이프라인은 코너 마커 + 버블 좌표만 사용 (타이밍 마크 무관)
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
    ID_DIGIT_W, ID_SEP_W,
    MARGIN_R,
)

# ══════════════════════════════════════════════
# A. STROKE & COLOR (v14 — 통합 프레임 + 한국 표준)
# ══════════════════════════════════════════════
# 디자인: v8 깔끔함 + v14 구조 (통합 프레임, 타이밍, ①②③④⑤)
# 핵심: 가늘고 연한 선. 버블만 또렷. 나머지 전부 뒤로.
S1 = 0.80   # 외곽 — v8 원본값. 가늘고 깔끔
S2 = 0.50   # 헤더 구분
S3 = 0.35   # 5행 강조 — 은은
S4 = 0.25   # 일반 행 — 최소 인쇄 보장
S5 = 0.35   # 버블 외곽

# ── v8 톤: 순검정 없음. 전체 부드러운 회색 ──
C1 = HexColor("#333333")      # 외곽 — 회색 (순검정 아님)
C2 = HexColor("#555555")      # 헤더/컬럼 구분
C3 = HexColor("#aaaaaa")      # 5행
C4 = HexColor("#cccccc")      # 일반 행
C5 = HexColor("#444444")      # 버블 외곽

# C. TEXT HIERARCHY
CT  = HexColor("#111111")      # 번호
CT2 = HexColor("#222222")      # 헤더 라벨
CT3 = HexColor("#666666")      # 부제/안내본문
CT4 = HexColor("#999999")      # 버블숫자 — 인쇄 시 보이되 마킹하면 덮임

# 배경 — 극도로 은은
C_HDR = HexColor("#f4f4f4")         # MC 헤더
C_HDR_ESSAY = HexColor("#f4f4f4")   # 서술형 헤더 — 동일 톤
C_ZEBRA = HexColor("#fafafa")       # zebra
C_BUB_FILL = HexColor("#f8f8f8")    # 버블 내부 — 미세 회색 (순백보다 부드러움)
C_G10 = HexColor("#666666")         # 10행

# 번호 칼럼 배경
C_NUM_BG = white                    # 깔끔

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
        self._answer_area(cv, doc)
        cv.save()
        return buf.getvalue()

    # ── 코너 마크 — ㄱ자 브래킷(시각) + 분리된 비대칭 마커(AI) ──
    # 브래킷과 마커 사이 2mm 갭 → 다른 blob으로 인식됨
    _CORNER_OFF = 2.5   # 마커 오프셋 (페이지 가장자리에서)
    _CORNER_SZ = 5.0    # 비대칭 마커 크기 (mm)
    _CORNER_TH = 1.5    # 마커 팔 두께 (mm)
    _BRACKET_OFF = 1.5  # 브래킷 오프셋 (마커보다 바깥)
    _BRACKET_ARM = 8.0  # 브래킷 팔 길이

    def _corners(self, c):
        off = self._CORNER_OFF
        sz = self._CORNER_SZ
        th = self._CORNER_TH
        boff = self._BRACKET_OFF
        barm = self._BRACKET_ARM
        pw, ph = PAGE_W, PAGE_H

        # ── 비대칭 채움 마커 (AI용) — 페이지 안쪽 ──
        c.setFillColor(black)
        # TL: 정사각형
        c.rect(_mm(off), _y(off + sz), _mm(sz), _mm(sz), fill=1, stroke=0)
        # TR: L자
        tr_x = pw - off - sz
        c.rect(_mm(tr_x), _y(off + th), _mm(sz), _mm(th), fill=1, stroke=0)
        c.rect(_mm(pw - off - th), _y(off + sz), _mm(th), _mm(sz), fill=1, stroke=0)
        # BL: 삼각형
        p = c.beginPath()
        p.moveTo(_mm(off), _y(ph - off))
        p.lineTo(_mm(off + sz), _y(ph - off))
        p.lineTo(_mm(off + sz / 2), _y(ph - off - sz))
        p.close()
        c.drawPath(p, fill=1, stroke=0)
        # BR: 십자
        br_cx = pw - off - sz / 2
        br_cy = ph - off - sz / 2
        c.rect(_mm(br_cx - sz/2), _y(br_cy + th/2), _mm(sz), _mm(th), fill=1, stroke=0)
        c.rect(_mm(br_cx - th/2), _y(br_cy + sz/2), _mm(th), _mm(sz), fill=1, stroke=0)

        # ── ㄱ자 브래킷 (시각용) — 마커 바깥쪽, 2mm 분리 ──
        c.setStrokeColor(HexColor("#333333"))
        c.setLineWidth(0.8)
        gap = off + sz + 1.5  # 마커 끝 + 여백
        # TL
        c.line(_mm(boff), _y(boff), _mm(boff + barm), _y(boff))
        c.line(_mm(boff), _y(boff), _mm(boff), _y(boff + barm))
        # TR
        c.line(_mm(pw - boff - barm), _y(boff), _mm(pw - boff), _y(boff))
        c.line(_mm(pw - boff), _y(boff), _mm(pw - boff), _y(boff + barm))
        # BL
        c.line(_mm(boff), _y(ph - boff), _mm(boff + barm), _y(ph - boff))
        c.line(_mm(boff), _y(ph - boff), _mm(boff), _y(ph - boff - barm))
        # BR
        c.line(_mm(pw - boff - barm), _y(ph - boff), _mm(pw - boff), _y(ph - boff))
        c.line(_mm(pw - boff), _y(ph - boff), _mm(pw - boff), _y(ph - boff - barm))

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

        # 내부 요소 먼저 (fill → line → text)
        # 1) 로고+제목
        self._logo_title(c, doc, x, w, t_logo, H_LOGO)

        # 2) 성명
        _header(c, x, w, t_name, "성 명")
        c.setStrokeColor(C1); c.setLineWidth(S1)
        c.line(x, _y(t_name), x + w, _y(t_name))

        # 3) 전화번호
        _header(c, x, w, t_phone, "학생 식별번호 (전화번호 뒤 8자리)")
        c.setStrokeColor(C1); c.setLineWidth(S1)
        c.line(x, _y(t_phone), x + w, _y(t_phone))
        self._phone(c, x, w, t_phone)

        # 4) 안내
        c.setStrokeColor(C1); c.setLineWidth(S1)
        c.line(x, _y(t_note), x + w, _y(t_note))
        self._note(c, x, w, t_note)

        # 외곽 프레임 — 맨 마지막 (fill에 덮이지 않음)
        c.setStrokeColor(C1); c.setLineWidth(S1)
        c.rect(x, _y(CONTENT_Y + CONTENT_H), w, _mm(CONTENT_H), stroke=1, fill=0)

        # 5) QR 예약 영역 (안내 카드 우하단)
        qr_x = x + w - _mm(9)
        qr_y = _y(t_note + H_NOTE - 1)
        c.setStrokeColor(C4); c.setLineWidth(S4)
        c.setDash(1, 1)
        c.rect(qr_x, qr_y, _mm(8), _mm(8), stroke=1, fill=0)
        c.setDash()

    def _logo_title(self, c, doc, x, w, top, h):
        """로고 + 시험명 + 부제. 로고는 항상 존재."""
        ix = x + _mm(LP_PAD_X)
        iw = w - 2 * _mm(LP_PAD_X)
        cx = x + w / 2  # 중심축

        # 로고 — 영역의 45% 이내, 상단 5mm 패딩
        logo_ceil = _y(top + PAD_LOGO_TOP)
        logo_max = _mm(min(h * 0.45, 30))
        cursor = logo_ceil

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

        # 시험명
        title_len = len(doc.exam_title) if doc.exam_title else 0
        title_pt = 9 if title_len > 20 else (10 if title_len > 15 else 12)
        c.setFont(_FB, title_pt); c.setFillColor(CT)
        c.drawCentredString(cx, cursor, doc.exam_title)

        # 부제
        parts = []
        if doc.lecture_name: parts.append(doc.lecture_name)
        if doc.session_name: parts.append(doc.session_name)
        if parts:
            c.setFont(_FN, 7); c.setFillColor(CT3)
            c.drawCentredString(cx, cursor - _mm(GAP_TITLE_SUB + 3), " / ".join(parts))

    def _phone(self, c, x, w, top):
        """전화번호 쓰기 칸 + 버블 그리드 — 동일 칼럼 그리드 사용."""
        inner_x = x + _mm(LP_BORDER + LP_PAD_X)
        inner_w = LP_W - 2 * LP_BORDER - 2 * LP_PAD_X

        # ── 통일 칼럼 그리드 (쓰기칸 = 버블그리드 동일 폭) ──
        dw = ID_DIGIT_W              # 5.8mm — meta_generator SSOT
        sw = ID_SEP_W                # 3.5mm — 대시 구분자
        gw = ID_DIGITS * dw + sw
        gx = inner_x + _mm((inner_w - gw) / 2)

        wy = top + HEADER_H + 2.0    # 쓰기 칸 y
        ch = 7.0                      # 쓰기 칸 높이

        # 쓰기 칸 — 버블 칼럼과 정확히 같은 x 사용
        c.setLineWidth(S4 + 0.15); c.setStrokeColor(C2)
        for d in range(ID_DIGITS):
            col_x_mm = (d * dw) if d < 4 else (4 * dw + sw + (d - 4) * dw)
            dx = gx + _mm(col_x_mm)
            c.rect(dx, _y(wy + ch), _mm(dw), _mm(ch), stroke=1, fill=0)

        # 대시
        if ID_DIGITS > 4:
            sx = gx + _mm(4 * dw)
            c.setFont(_FB, 10); c.setFillColor(CT)
            c.drawCentredString(sx + _mm(sw / 2), _y(wy + ch - 1.5), "–")

        # 버블 그리드 — 동일 칼럼 그리드
        by0 = top + HEADER_H + ch + 3.5
        bg = ID_BUB_GAP
        c.setLineWidth(S5)
        for d in range(ID_DIGITS):
            col_x_mm = (d * dw) if d < 4 else (4 * dw + sw + (d - 4) * dw)
            dx = gx + _mm(col_x_mm)
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
        """답안지 작성 안내 — 일관된 들여쓰기."""
        pad = 3.0                     # 좌측 패딩 mm
        ix = x + _mm(pad)             # 텍스트 시작 x
        nix = ix + _mm(2.5)           # 번호 뒤 들여쓰기 (2번째 줄)
        lh = 3.8                      # 줄 간격 mm

        ty = _y(top + 3.5)

        c.setFont(_FB, 6.5); c.setFillColor(CT2)
        c.drawString(ix, ty, "답안지 작성 안내")

        y0 = ty - _mm(4.5)
        line = 0

        def _line_y(n):
            return y0 - _mm(lh * n)

        # 1. 전화번호
        c.setFont(_FN, 5.5); c.setFillColor(CT3)
        c.drawString(ix, _line_y(line), "1. 전화번호는 본인 휴대폰 번호 뒤 8자리를 적어주세요.")
        line += 1
        c.drawString(nix, _line_y(line), "휴대폰이 없으면 부모님 번호 뒤 8자리를 적어주세요.")
        line += 1

        # 2. 사인펜
        c.drawString(ix, _line_y(line), "2. 객관식은 ")
        c.setFont(_FB, 5.5)
        c.drawString(ix + _mm(13), _line_y(line), "컴퓨터용 사인펜")
        c.setFont(_FN, 5.5)
        c.drawString(ix + _mm(29.5), _line_y(line), "으로 칠해주세요.")
        line += 1

        # 3. 수정테이프
        c.drawString(ix, _line_y(line), "3. ")
        c.setFont(_FB, 5.5)
        c.drawString(ix + _mm(3), _line_y(line), "수정테이프")
        c.setFont(_FN, 5.5)
        c.drawString(ix + _mm(15), _line_y(line), "를 사용해주세요. (수정액 금지)")
        line += 1

        # 4. 마킹 예시
        c.drawString(ix, _line_y(line), "4. 올바른 마킹:  ")
        mark_x = ix + _mm(17)
        mark_y = _line_y(line) + _mm(0.5)
        c.setFillColor(HexColor("#333333"))
        c.ellipse(mark_x, mark_y - _mm(1.3), mark_x + _mm(2.8), mark_y + _mm(1.3),
                  fill=1, stroke=0)
        c.setFont(_FN, 5.5); c.setFillColor(CT3)
        c.drawString(mark_x + _mm(5), _line_y(line), "잘못된 마킹: ")
        c.setFont(_FB, 6); c.setFillColor(HexColor("#999999"))
        c.drawString(mark_x + _mm(16), _line_y(line), "✓  △  ─")

    # ══════════════════════════════════════════
    # 답안 영역 — 통합 프레임 + 한국 표준 타이밍
    # ══════════════════════════════════════════
    # 그리기 순서: ① 배경 → ② 내부선 → ③ 외곽 → ④ 텍스트 → ⑤ 버블 → ⑥ 타이밍
    # fill이 stroke를 덮는 문제를 원천 차단.
    # MC_COL_GAP은 visual width에 흡수하여 빈 틈 제거.

    def _answer_area(self, c, doc):
        mc = doc.mc_count
        ec = doc.essay_count
        if mc <= 0 and ec <= 0:
            return

        if mc <= 0:    pc, nmc = 0, 0
        elif mc <= 20: pc, nmc = mc, 1
        elif mc <= 40: pc, nmc = math.ceil(mc/2), 2
        else:          pc, nmc = math.ceil(mc/3), 3

        has_essay = ec > 0

        # 프레임 범위 (mm)
        frame_x = ANS_X
        frame_w = (PAGE_W - MARGIN_R - frame_x) if has_essay else (
            nmc * MC_COL_W + max(0, nmc - 1) * MC_COL_GAP)

        fx = _mm(frame_x); fw = _mm(frame_w)
        ft = _y(CONTENT_Y); fh = _mm(CONTENT_H)
        fb = ft - fh
        hh = _mm(MC_HEADER_H)
        nw = _mm(MC_NUM_W)
        bt = CONTENT_Y + MC_HEADER_H
        bh = CONTENT_H - MC_HEADER_H

        # 섹션 경계 — visual width가 gap을 흡수하여 빈 틈 없음
        # (type, x_mm, vis_w_mm, data_w_mm, start, end)
        sections = []
        for ci in range(nmc):
            col_x = frame_x + ci * (MC_COL_W + MC_COL_GAP)
            s = ci * pc + 1
            e = min(s + pc - 1, mc)
            if ci < nmc - 1 or has_essay:
                vw = MC_COL_W + MC_COL_GAP
            else:
                vw = frame_x + frame_w - col_x
            sections.append(('mc', col_x, vw, MC_COL_W, s, e))
        if has_essay:
            ex = frame_x + nmc * (MC_COL_W + MC_COL_GAP)
            ew = frame_x + frame_w - ex
            sections.append(('essay', ex, ew, ew, 1, ec))

        # ═══ ① 배경 (fill) — 모든 stroke보다 먼저 ═══
        for typ, sx, vw, dw, ss, se in sections:
            sxp = _mm(sx); vwp = _mm(vw)
            cnt = se - ss + 1
            rh = bh / cnt if cnt > 0 else bh

            # 헤더 배경
            c.setFillColor(C_HDR if typ == 'mc' else C_HDR_ESSAY)
            c.rect(sxp, ft - hh, vwp, hh, fill=1, stroke=0)

            # 번호 칼럼 배경
            c.setFillColor(C_NUM_BG)
            c.rect(sxp, fb, nw, _mm(bh), fill=1, stroke=0)

            # 지브라 (MC만, 행 높이 충분할 때)
            if typ == 'mc' and rh >= 7.0:
                for qi in range(cnt):
                    if (qi // 5) % 2 == 1:
                        c.setFillColor(C_ZEBRA)
                        c.rect(sxp, _y(bt + qi * rh + rh), vwp, _mm(rh),
                               fill=1, stroke=0)

        # ═══ ② 내부선 ═══
        # 행 구분선
        for typ, sx, vw, dw, ss, se in sections:
            sxp = _mm(sx); vwp = _mm(vw)
            cnt = se - ss + 1
            rh = bh / cnt if cnt > 0 else bh
            for qi in range(1, cnt):
                rt_mm = bt + qi * rh
                g10 = (qi % 10 == 0)
                g5 = (qi % 5 == 0)
                if g10:
                    c.setStrokeColor(C_G10); c.setLineWidth(1.0)
                elif g5:
                    c.setStrokeColor(C3); c.setLineWidth(S3)
                else:
                    c.setStrokeColor(C4); c.setLineWidth(S4)
                c.line(sxp, _y(rt_mm), sxp + vwp, _y(rt_mm))

        # 번호 세로선 (가는선)
        c.setStrokeColor(C4); c.setLineWidth(S4)
        for _, sx, *_rest in sections:
            c.line(_mm(sx) + nw, ft - hh, _mm(sx) + nw, fb)

        # 헤더 가로 구분선 (전체 폭)
        c.setStrokeColor(C2); c.setLineWidth(S2)
        c.line(fx, ft - hh, fx + fw, ft - hh)

        # 컬럼 세로 구분선 — 외곽과 동일 (통일감)
        for i, (_, sx, *_rest) in enumerate(sections):
            if i > 0:
                c.setStrokeColor(C1); c.setLineWidth(S1)
                c.line(_mm(sx), ft, _mm(sx), fb)

        # ═══ ③ 외곽 프레임 — 최상위 (fill에 덮이지 않음) ═══
        c.setStrokeColor(C1); c.setLineWidth(S1)
        c.rect(fx, fb, fw, fh, stroke=1, fill=0)

        # ═══ ④ 텍스트 (헤더 라벨 + 선택지 번호 + 행 번호) ═══
        # 수능 표준: 헤더에 ①②③④⑤ 표시하여 학생이 선택지 위치 즉시 파악
        _CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"]
        nc = doc.n_choices
        for typ, sx, vw, dw, ss, se in sections:
            sxp = _mm(sx); vwp = _mm(vw)
            cnt = se - ss + 1
            rh = bh / cnt if cnt > 0 else bh

            # 헤더: "번호 | 객관식 1번 ~ 20번" 또는 "번호 | 서술형 N문항"
            c.setFont(_FB, 5.5); c.setFillColor(CT2)
            c.drawCentredString(sxp + nw / 2, ft - hh + _mm(1.5), "번호")
            if typ == 'mc':
                lb = f"객관식 {ss}번 ~ {se}번"
                c.setFont(_FB, 6); c.setFillColor(CT2)
                c.drawCentredString(sxp + nw + (vwp - nw) / 2,
                                    ft - hh + _mm(1.5), lb)
            else:
                c.setFont(_FB, 6); c.setFillColor(CT2)
                c.drawCentredString(sxp + nw + (vwp - nw) / 2,
                                    ft - hh + _mm(1.5), f"서술형 {cnt}문항")

            for qi in range(cnt):
                qn = ss + qi
                rc = bt + (qi + 0.5) * rh
                c.setFont(_FB, 8); c.setFillColor(CT)
                c.drawCentredString(sxp + nw / 2, _y(rc) - _mm(1.4), str(qn))

        # ═══ ⑤ 버블 (MC만) ═══
        nc = doc.n_choices
        for typ, sx, vw, dw, ss, se in sections:
            if typ != 'mc':
                continue
            cnt = se - ss + 1
            rh = bh / cnt if cnt > 0 else bh
            ax = sx + MC_NUM_W + MC_BUB_PAD
            aw = dw - MC_NUM_W - 2 * MC_BUB_PAD
            bgap = (aw - nc * BUB_W) / (nc + 1)
            bxs = [ax + bgap * (j + 1) + BUB_W * j + BUB_W / 2
                   for j in range(nc)]

            for qi in range(cnt):
                rc = bt + (qi + 0.5) * rh
                for bi, bx_mm in enumerate(bxs):
                    bx = _mm(bx_mm); by = _y(rc)
                    c.setStrokeColor(C5); c.setLineWidth(S5)
                    c.setFillColor(C_BUB_FILL)
                    c.ellipse(bx - _mm(BUB_W / 2), by - _mm(BUB_H / 2),
                              bx + _mm(BUB_W / 2), by + _mm(BUB_H / 2),
                              stroke=1, fill=1)
                    c.setFont(_FB, 6.5); c.setFillColor(CT4)
                    c.drawCentredString(bx, by - _mm(1.0), str(bi + 1))

        # ═══ ⑥ 타이밍 마크 (프레임 외부) ═══
        self._render_timing(c, frame_x, frame_w, sections, bt, bh)

    # ══════════════════════════════════════════
    # 타이밍 마크 — 한국 OMR 표준 스타일
    # ══════════════════════════════════════════
    # 상하단: 균일 간격 수평 바 (바코드 스트립)
    # 좌우: 5행/10행 행 바 (정렬 보정용)
    # 깔끔하고 균일한 패턴 — 난잡한 빗금 아님.

    _TM_BAR_W = 2.5      # 상하단 바 폭 — 작고 섬세
    _TM_BAR_H = 0.8      # 상하단 바 높이 — 얇게
    _TM_PERIOD = 10.0    # 바 간격 — 넉넉 (덜 빽빽)
    _TM_OFFSET = 1.5     # 프레임에서 간격
    _TM_MARGIN = 5.0     # 프레임 좌우 여백 — 넉넉
    _TM_ROW_W5 = 1.2     # 5행 — 김 붙은 것처럼 짧게
    _TM_ROW_W10 = 1.5    # 10행
    _TM_ROW_H5 = 0.3     # 5행 — 미세
    _TM_ROW_H10 = 0.4    # 10행
    _TM_ROW_GAP = 0.5    # 프레임에서 간격

    def _render_timing(self, c, frame_x, frame_w, sections, bt, bh):
        """인식 마크 — 버블 좌표에 정렬된 기준점. 최외곽 테두리에 배치.

        상단: 각 MC 컬럼 시작 x에 작은 사각형 (컬럼 위치 보정)
        하단: 동일
        좌측: 5행마다 작은 사각형 (행 위치 보정)
        모든 마크가 실제 버블 좌표와 1:1 대응 → AI 미세 보정에 사용 가능.
        """
        c.setFillColor(HexColor("#555555"))
        mk = 1.5  # 마크 크기 (mm) — 작고 섬세

        # ── 상하단: 각 MC 컬럼 시작점에 마크 ──
        for typ, sx, vw, dw, ss, se in sections:
            if typ != 'mc':
                continue
            # 컬럼 시작 x + 번호 칼럼 끝 x (버블 시작점)
            bub_start_x = sx + MC_NUM_W
            # 상단 (페이지 최상단 근처)
            c.rect(_mm(bub_start_x - mk/2), _y(1.5 + mk),
                   _mm(mk), _mm(mk), fill=1, stroke=0)
            # 하단
            c.rect(_mm(bub_start_x - mk/2), _y(PAGE_H - 1.5),
                   _mm(mk), _mm(mk), fill=1, stroke=0)
            # 컬럼 끝 x
            col_end_x = sx + dw
            c.rect(_mm(col_end_x - mk/2), _y(1.5 + mk),
                   _mm(mk), _mm(mk), fill=1, stroke=0)
            c.rect(_mm(col_end_x - mk/2), _y(PAGE_H - 1.5),
                   _mm(mk), _mm(mk), fill=1, stroke=0)

        # ── 좌측: 5행마다 행 경계에 마크 (첫 섹션 기준) ──
        if not sections:
            return
        first_sec = sections[0]
        typ, sx, vw, dw, ss, se = first_sec
        cnt = se - ss + 1
        rh = bh / cnt if cnt > 0 else bh
        for qi in range(0, cnt + 1, 5):
            row_y = bt + qi * rh
            # 좌측 (페이지 왼쪽 가장자리)
            c.rect(_mm(1.0), _y(row_y + mk/2),
                   _mm(mk), _mm(mk), fill=1, stroke=0)

        # ── 우측: 마지막 섹션 기준 ──
        last_sec = sections[-1]
        typ, sx, vw, dw, ss, se = last_sec
        cnt = se - ss + 1
        rh = bh / cnt if cnt > 0 else bh
        for qi in range(0, cnt + 1, 5):
            row_y = bt + qi * rh
            c.rect(_mm(PAGE_W - 1.0 - mk), _y(row_y + mk/2),
                   _mm(mk), _mm(mk), fill=1, stroke=0)

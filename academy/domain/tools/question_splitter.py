# PATH: academy/domain/tools/question_splitter.py
# Rule-based question splitting from PDF pages.
#
# Detects question number patterns in extracted text blocks and
# determines bounding regions for each question.

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    # forward-ref only — runtime 에는 caller (segment_dispatcher / pipeline) 가
    # PaperTypeResult 인스턴스를 그대로 전달.
    from academy.domain.tools.paper_type import PaperTypeResult  # noqa: F401


# 시험지 문항 번호 현실 상한.
# 통합과학/화학/물리 등 정기고사 최대 관측: ~32.
# 여유를 두고 60으로 상한 (수능형/심화 포함).
# 이 상한을 넘는 OCR 결과는 오인식(예: "ㄷ8"→128)으로 간주해 거부.
_MAX_LEGIT_QUESTION_NUMBER = 60


@dataclass
class TextBlock:
    """A block of text with its bounding box on the page."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def is_non_question_page(blocks: List[TextBlock]) -> bool:
    """
    비문항 페이지 감지 — 표지, 진도표, 안내문, 정답지, 해설지 등.

    휴리스틱:
    - 정답지/해설지 패턴 감지 → True
    - 문항 지시문이나 보기 번호가 있으면 → False (문항 페이지)
    - 디자인 표지(WORKBOOK/PROJECT 류) → True
    - 시험지 헤더(제 N 교시 / 탐구 영역 / 홀수형) → True
    - 학습자료 챕터 표지(객서심화 / 메인자료 / 복습과제 등) → True
    - 비문항 키워드가 여러 개 있으면 → True
    """
    full_text = " ".join(b.text for b in blocks).strip()
    if not full_text:
        return True

    # 정답지 감지 (최우선): "⑴ × ⑵ O" "⑴ ② ⑵ ④" 같은 패턴 반복
    answer_pattern = re.findall(r"[⑴⑵⑶⑷⑸⑹⑺⑻⑼]\s*[×OX①②③④⑤]", full_text)
    if len(answer_pattern) >= 5:
        return True

    # ── 정답표 페이지 감지 ──
    # 운영 케이스 (Tenant 2 doc#143 p55/p65): "1. ④ 2. ④ 3. ① 4. ③ ..." 60+ 반복.
    # is_non_question_page 미차단 시 60 false anchor가 한 페이지에 폭증.
    # 패턴: "N. ①②③④⑤" 또는 "N. ④" 가 5개 이상 + 본문 지시문 없음.
    answer_table = re.findall(r"\b\d{1,3}\.\s*[①②③④⑤]", full_text)
    if len(answer_table) >= 5:
        question_indicators_early = [
            "옳은 것", "구하시오", "표시하시오", "고르시오", "서술하시오",
            "풀이 과정", "이에 대한 설명", "다음 중", "보기에서",
        ]
        if not any(kw in full_text for kw in question_indicators_early):
            return True

    # 해설지 감지: "번호. ⑴ ...이다." 소문항 패턴
    sub_q_pattern = re.findall(r"\d+\.\s*[⑴⑵⑶⑷⑸⑹⑺⑻⑼]", full_text)
    if len(sub_q_pattern) >= 2:
        question_indicators_early = [
            "옳은 것", "구하시오", "표시하시오", "고르시오", "서술하시오",
            "풀이 과정", "이에 대한 설명", "다음 중", "보기에서",
        ]
        if not any(kw in full_text for kw in question_indicators_early):
            return True

    # ── 해설지 페이지 감지 ──
    # 운영 케이스:
    #   1. Tenant 2 doc#120 p112-116: "10. 정답 ④ 문제 해설 ..."
    #   2. Step 3. 수능완성 1. 정답 ③ 문제 해설 ..."
    #   3. T1 doc 772 (지권의 변화 메인자료) p72 정답해설지: "30. [정답] 영덕"
    #      / "31. [정답] (가)는 ..." — N. 와 정답 사이 "[" 또는 "(" prefix 등장.
    # 패턴: "N. 정답" / "N. [정답]" / "N. (정답)" / "N. 문제 해설" 5+ 반복.
    explanation_answer = re.findall(
        r"\b\d{1,3}\s*\.\s*[\[\(\s]*(?:정\s*답|문제\s*해설)",
        full_text,
    )
    if len(explanation_answer) >= 3:
        return True

    # ── 단독 "정답 ①②③④⑤" 패턴 (N. 접두어 없음) ──
    # 운영 케이스 (모의고사 해설지): OCR이 N. 접두어를 흘리거나 layout이 깨져
    # "정답 ③", "정답 ⑤" 만 반복되는 페이지. 일반 본문에는 "정답"이 3+ 등장하지 않음.
    standalone_answer = re.findall(r"정\s*답\s*[①②③④⑤]", full_text)
    if len(standalone_answer) >= 3:
        return True

    # ── 해설지 OX 마커 패턴 — T1 doc 772 p56 케이스 ──
    # 운영 케이스 (지권의 변화 메인자료 p56 정답+문제 해설 페이지):
    #   "7. 그림은 ... | 1. A-C 지역... (O) | 2. 규모는 ... (X) | 3. ... (X) | ..."
    # 워크북 본문 페이지에는 "(O/X)" 가 표시 영역 placeholder 로만 등장 (학원장이
    # 채우기 전). 해설지에는 "(O)" / "(X)" / "(✓)" 단독 마커가 정답으로 채워져 있어
    # 본문 페이지보다 단독 OX 마커 빈도가 훨씬 높다.
    # 임계: 단독 OX 마커 (괄호 안 단일 O / X / ✓) 가 6+ 등장 + "정답" / "해설" 단어 동반.
    standalone_ox = re.findall(r"[\(（][\s]*[OX✓×][\s]*[\)）]", full_text)
    has_answer_word = "정답" in full_text or "해설" in full_text
    if len(standalone_ox) >= 6 and has_answer_word:
        return True

    # ── 학습자료 본문 항목번호 (zb\d+ ID) 페이지 감지 ──
    # 운영 케이스 (Tenant 2 doc#143 객서심화): "5. zb5) 다음 글을 읽고",
    # "11. zb11) 다음은", "17. zb17) 그림 (가)는" — 학습 항목 ID로 본문에 다수 등장.
    # zb패턴이 있고 + 학습 항목 번호로 anchor가 5+ 잡히는 페이지는 학습자료 본문.
    zb_markers = re.findall(r"\bzb\s*\d{1,3}\s*\)", full_text)
    if len(zb_markers) >= 3:
        return True

    # 문항 페이지 강력 지표: 보기 번호 패턴
    choice_patterns = ["①", "②", "③", "④", "⑤", "ㄱ.", "ㄴ.", "ㄷ."]
    has_choices = any(p in full_text for p in choice_patterns)

    question_indicators = [
        "옳은 것", "구하시오", "표시하시오", "고르시오", "서술하시오",
        "풀이 과정", "이에 대한 설명", "다음 중", "보기에서",
    ]
    has_question_indicator = any(kw in full_text for kw in question_indicators)

    if has_choices or has_question_indicator:
        return False

    # ── 디자인 표지 페이지 감지 (학습자료/문제집 표지) ──
    # 운영 케이스 (Tenant 2): "Runner's High with God Min", "신과 함께 PROJECT WORKBOOK",
    # "1학기 중간고사 대비 문항편", "객·서 최종대비", "복습과제", "메인자료" 등.
    # 텍스트 양은 적고(< 300자), 디자인 키워드가 있으면 표지로 판정.
    design_cover_markers = re.findall(
        r"(?:WORKBOOK|PROJECT|Runner['’]?s\s*High|GOD\s*MIN|"
        r"\bTEST\s*[-–—]|TEST\s+\d|"
        r"신과\s*함께|객\s*[·‧・·]\s*서\s*최종\s*대비|"
        r"객서\s*심화|객\s*·\s*서|"
        r"최종\s*대비|중간고사\s*대비|기말고사\s*대비|"
        r"내신\s*대비|기출\s*통과|"
        r"문항편|해설편|정답편|"
        r"복습\s*과제|메인\s*자료|개념\s*완성)",
        full_text,
    )
    if len(design_cover_markers) >= 1 and len(full_text) < 300:
        return True
    # 디자인 키워드가 2+ 동시에 있으면 텍스트 길이와 무관 (대형 표지 디자인)
    if len(design_cover_markers) >= 2 and len(full_text) < 800:
        return True

    # ── Lorem ipsum placeholder 표지 감지 ──
    # 운영 케이스 (Tenant 2 18 doc): "RUNNER'S HIGH WITH GOD MIN" + 라틴 lorem ipsum
    # placeholder 텍스트로 채워진 디자인 표지.
    # adipiscing/dolore/laoreet/aliquam/ipsum 같은 라틴어 단어가 한 페이지에 다수 등장.
    lorem_markers = re.findall(
        r"(?:adipiscing|dolore|laoreet|aliquam|nibh|tincidunt|euismod|"
        r"volutpat|nonummy|ipsum|consectetur|magna)",
        full_text,
        re.IGNORECASE,
    )
    if len(lorem_markers) >= 3:
        return True

    # ── 시험지 헤더 페이지 감지 (수능/모의고사 표제) ──
    # "제 4교시 / 신민T 신념 모의고사 / 통합과학 N제 / 탐구 영역 / 홀수형" 같은 표제 페이지.
    exam_header_markers = re.findall(
        r"(?:제\s*\d+\s*교시|탐구\s*영역|홀수형|짝수형|"
        r"수능\s*모의고사|N제|모의고사\s*\d+회차?)",
        full_text,
    )
    # 표제 페이지는 본문 대비 텍스트가 아주 적음 (디자인+여백)
    if len(exam_header_markers) >= 2 and len(full_text) < 400:
        return True
    # 헤더 키워드 3+ 동시 등장 + 본문 지시문 없음 → 표제 페이지 (길이 무관)
    if len(exam_header_markers) >= 3 and not has_question_indicator and not has_choices:
        return True

    # 표지 페이지 감지: 시험지 메타정보는 있고 문항 지표는 없음.
    # "학년도 1학기 기말고사 과목명 성명" 류 조합이 표지의 특징.
    cover_markers = re.findall(
        r"(?:\d+학년도|\d+학기|기말\s?고사|중간\s?고사|과목\s?명|"
        r"문제지|답안지|답란\s?지|수험\s?번호|응시\s?번호|"
        r"성\s?명|학\s?번|반\s?번호)",
        full_text,
    )
    # 표지는 보통 본문 대비 매우 짧음. 500자 기준은 빈 박스+헤더 정도.
    if len(cover_markers) >= 2 and len(full_text) < 500:
        return True

    # ── 텍스트 매우 적은 페이지 (디자인/이미지 위주) ──
    # 문항 지시문도 없고 보기도 없는데 텍스트가 100자 미만이면 표지/디자인 페이지.
    # "1.", "2." 같은 번호만 있어서 anchor가 잡히는 표지 디자인 페이지 차단.
    if len(full_text) < 100:
        return True

    # 목차/차례 페이지 감지 — 키워드 + 페이지 번호 점선 패턴.
    # ".... 5", "··· 12" 같은 점선 가이드 또는 "목차/차례/Contents" 헤더.
    toc_keyword = bool(
        re.search(r"(?:^|\s)(?:목\s?차|차\s?례|Contents?|Table\s+of\s+Contents|INDEX)(?:\s|$)", full_text)
    )
    dot_leader_count = len(re.findall(r"[.·…]{3,}\s*\d{1,3}\b", full_text))
    if toc_keyword or dot_leader_count >= 4:
        # 문항 지표가 없을 때만 — 본문 페이지가 아니라는 확신
        if not has_choices and not has_question_indicator:
            return True

    # 설명조 종결어미 빈도 기반 해설지 감지
    explanation_markers = re.findall(
        r"(?:이므로|때문이다|따라서|그러므로|해설|나타난다|관측된다|생성된다)",
        full_text,
    )
    if len(explanation_markers) >= 3 and not has_question_indicator:
        return True

    # 비문항 지표: 진도표, 강의방침, 안내 등
    non_question_indicators = [
        "진도", "운영 방침", "재시험", "클리닉", "홈페이지",
        "대단원", "중단원", "세부 내용", "난이도",
        "주차", "복습과제", "워크북",
    ]
    non_q_count = sum(1 for kw in non_question_indicators if kw in full_text)
    if non_q_count >= 3:
        return True

    return False


@dataclass
class QuestionRegion:
    """A detected question region on a page."""
    number: int
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1)
    page_index: int


# 선택형(객관식) 문항 번호 패턴. "1.", "1) ", "(1) ", "[1] ", "문제 1.", "문 1)".
# 운영 케이스 (Tenant 2 모의고사): "12)그림은..." 처럼 닫는 ")" 뒤에 공백 없이
# 한글/영문/괄호가 바로 오는 PDF가 흔함. 공백 강제 시 anchor 다수 미검출.
# → 다음 글자가 "공백 또는 한글/영문/숫자/구두점"이면 anchor로 인정.
# (단, ")" 직후가 또 ")" 이거나 "."이면 보기 ①②③④⑤ 또는 본문 일부일 수 있어 거부.)
_QUESTION_PATTERN = re.compile(
    r"^\s*(?:"
    r"(\d{1,3})\s*[.)](?=\s|[가-힣A-Za-z(<【\[\"'“‘])"  # "1." / "1) " / "12)그림"
    r"|"
    r"\((\d{1,3})\)\s"              # "(1) "
    r"|"
    r"\[(\d{1,3})\]\s"              # "[1] "
    r"|"
    r"(?:문제|문)\s*(\d{1,3})\s*[.)]"  # "문제1." or "문 1)"
    r")"
)

# 서술형/논술형/단답형 섹션은 1부터 번호를 리셋하므로 선택형 번호와 충돌함.
# 섹션별 number-space offset을 부여해 크로스-페이지 중복 제거 시 legit 문항이
# 잘못 드롭되지 않도록 한다.
# 예: `[서술형 1]` → 101, `[서술형 2]` → 102, `[논술형 1]` → 201.
# OCR은 "술"을 "답"으로 자주 오인식하므로 variant도 허용.
_SECTION_OFFSETS = {
    "서술": 100, "서답": 100,
    "논술": 200, "논답": 200,
    "단답": 300, "단술": 300,
    "약술": 400, "약답": 400,
}
_SECTION_PATTERN = re.compile(
    r"^\s*\[?\s*"
    r"(서\s*술|서\s*답|논\s*술|논\s*답|단\s*답|단\s*술|약\s*술|약\s*답)"
    # 형 필수 — "서술 1가지 방법..." 같은 본문 false positive 차단.
    # OCR이 형을 흘리는 경우는 드물고, 본문에서 "서술/논술/단답" 단어가
    # 숫자 앞에 등장하는 케이스가 더 흔함. 형 없는 패턴은 anchor 후보에서 제외.
    r"\s*형\s*(\d{1,3})"
)


# Marginal 큰 번호 패턴 — 워크북/메인자료 페이지 좌측 marginal column 에 standalone
# "3." / "4." 형식으로 박힌 큰 번호. 학원장 mental model 의 "한 문제 단위" anchor.
# 본문 sub-item anchor ("1. 다음 글은...") 와 구분하기 위해 매우 짧은 standalone block 만 인정.
_MARGINAL_NUMBER_PATTERN = re.compile(
    r"^\s*(\d{1,3})\s*\.?\s*$"
)


def _extract_marginal_question_number(text: str) -> Optional[int]:
    """짧은 standalone 'N.' / 'N' block (또는 multi-line block 첫 줄) 에서 큰 문제 번호 추출.

    워크북 marginal column 의 main question anchor 용. 본문 sub-item anchor 와 구분.

    Production text extraction (`page.get_text("blocks")`) 은 마진 "3." 와 본문 첫 줄
    "그림은 화산..." 을 한 block 으로 묶어 반환한다 (multi-line block). local fixture
    diag 의 `get_text("dict")` 와 다르다. 따라서 block.text 의 **첫 줄** 만 분리해
    marginal 패턴 검사.

    조건:
      - 첫 줄 length ≤ 5 char (e.g. "3.", "12.", "3")
      - 첫 줄 regex: 숫자 + 선택적 점 + 공백 외 다른 문자 없음
    """
    stripped = text.strip()
    if not stripped:
        return None
    # multi-line block 첫 줄 추출 (production blocks 모드 대응).
    first_line = stripped.split("\n", 1)[0].strip()
    if not first_line or len(first_line) > 5:
        return None
    m = _MARGINAL_NUMBER_PATTERN.match(first_line)
    if not m:
        return None
    try:
        num = int(m.group(1))
        if 1 <= num <= _MAX_LEGIT_QUESTION_NUMBER:
            return num
    except ValueError:
        pass
    return None


def _extract_question_number(text: str) -> Optional[int]:
    """Extract question number from text block content.

    선택형 1~60 그대로. 서술형 N → 100+N. 논술형 N → 200+N. 단답형 N → 300+N.
    번호 공간을 분리해 서술형 리셋 번호가 선택형과 충돌하지 않게 한다.
    """
    text = text.strip()
    if not text:
        return None

    # 1. 서술형/논술형/단답형/약술형 섹션 패턴 먼저 검사
    sec_m = _SECTION_PATTERN.match(text)
    if sec_m:
        section_key = re.sub(r"\s+", "", sec_m.group(1))[:2]  # "서술" 등
        offset = _SECTION_OFFSETS.get(section_key, 0)
        try:
            sub_num = int(sec_m.group(2))
            if 1 <= sub_num <= _MAX_LEGIT_QUESTION_NUMBER:
                return offset + sub_num
        except ValueError:
            pass

    # 2. 선택형 번호 패턴
    m = _QUESTION_PATTERN.match(text)
    if not m:
        return None

    for g in m.groups():
        if g is not None:
            try:
                num = int(g)
                if 1 <= num <= _MAX_LEGIT_QUESTION_NUMBER:
                    return num
            except ValueError:
                continue
    return None


def _detect_column_layout(
    blocks: List[TextBlock],
    page_width: float,
) -> bool:
    """Detect if the page has a dual-column layout.

    Two heuristics (OR):
      1. >20% of text blocks have x0 > page_width * 0.45 (right column populated)
      2. anchor "N." pattern blocks distributed in both halves (≥2 each side)

    조건 (2) 추가 근거: 운영 doc#177/329/292/291 (T2 시험지) — Vision OCR이
    페이지 좌/우 column을 한 line으로 묶거나 우측 column block 수가 적어 (1)
    임계값을 넘기지 못하는 경우, dual-column 미인식 → split_questions 단계에서
    next_block.y0 < start_block.y0 인 cross-column anchor를 single-column으로
    잘못 처리 → strip(10px) bbox 결함. anchor 분포 기반 판정으로 보완.
    """
    if not blocks or page_width <= 0:
        return False

    mid_x = page_width * 0.45
    right_count = sum(1 for b in blocks if b.x0 > mid_x)
    if right_count > len(blocks) * 0.2:
        return True

    # Fallback: anchor "N."이 좌/우 column 모두에 배치되어 있으면 dual-column
    anchor_pattern = re.compile(r"^\s*(\d{1,3})\s*[.)]")
    left_anchors = sum(
        1 for b in blocks
        if b.x0 <= mid_x and anchor_pattern.match(b.text or "")
    )
    right_anchors = sum(
        1 for b in blocks
        if b.x0 > mid_x and anchor_pattern.match(b.text or "")
    )
    return left_anchors >= 2 and right_anchors >= 2


def _detect_quad_layout(
    blocks: List[TextBlock],
    page_width: float,
    page_height: float,
) -> bool:
    """4분할(2x2) 레이아웃 감지 — 한 페이지에 4문항이 grid로 배치된 시험지.

    운영 케이스 (Tenant 2 doc#148 4분할 기출 시험지): 가로 띠로 잘려 multi-question
    bleed 발생. 4분면 각각 독립 anchor 검색이 필요하다.

    Heuristic:
      - 4 quadrant 모두에 텍스트 블록이 일정량 이상 분포
      - 페이지 가운데 가로띠 + 세로띠에 텍스트가 거의 없음 (gutter)
    """
    if not blocks or page_width <= 0 or page_height <= 0 or len(blocks) < 12:
        return False

    mid_x = page_width / 2
    mid_y = page_height / 2
    gutter_x = page_width * 0.05
    gutter_y = page_height * 0.05

    tl = sum(1 for b in blocks if b.x1 < mid_x and b.y1 < mid_y)
    tr = sum(1 for b in blocks if b.x0 > mid_x and b.y1 < mid_y)
    bl = sum(1 for b in blocks if b.x1 < mid_x and b.y0 > mid_y)
    br = sum(1 for b in blocks if b.x0 > mid_x and b.y0 > mid_y)

    # 4분면 모두 충분한 텍스트
    if min(tl, tr, bl, br) < 3:
        return False

    # 가운데 가로띠/세로띠에 텍스트 거의 없음 (gutter 검증)
    in_h_gutter = sum(
        1 for b in blocks if mid_y - gutter_y < b.y0 < mid_y + gutter_y
    )
    in_v_gutter = sum(
        1 for b in blocks if mid_x - gutter_x < b.x0 < mid_x + gutter_x
    )
    threshold = max(2, len(blocks) * 0.06)
    return in_h_gutter <= threshold and in_v_gutter <= threshold


def count_marginal_anchor_candidates(
    text_blocks: List[TextBlock],
    page_width: float,
) -> int:
    """페이지에서 marginal column 큰 번호 anchor 후보 개수.

    `_pdf_to_images` 의 phase 1 (doc-level workbook 감지) 에서 사용.
    페이지마다 short standalone "N." block (x0 < 15% page width, text ≤ 5 char)
    개수를 센다. 모든 페이지에 marginal block 이 분포하면 워크북 doc.
    """
    if not text_blocks or page_width <= 0:
        return 0
    threshold_x = page_width * 0.15
    count = 0
    for b in text_blocks:
        if b.x0 >= threshold_x:
            continue
        if _extract_marginal_question_number(b.text) is not None:
            count += 1
    return count


def split_questions(
    text_blocks: List[TextBlock],
    page_width: float,
    page_height: float,
    page_index: int = 0,
    paper_type: Optional["PaperTypeResult"] = None,
    prefer_marginal: bool = False,
) -> List[QuestionRegion]:
    """Split a page into question regions based on detected question numbers.

    Args:
        text_blocks: Text blocks extracted from the page with positions.
        page_width: Page width in points.
        page_height: Page height in points.
        page_index: Index of the page in the PDF.
        paper_type: Optional explicit layout decision (paper_type.PaperTypeResult).
                    If provided, the dual/quad detection heuristics are bypassed
                    and the explicit layout is used. None → fall back to the
                    in-module heuristics for backward compatibility.
        prefer_marginal: 워크북/메인자료 doc-level 신호. True 면 페이지에 marginal
                    column 큰 번호 standalone "N." block 이 있으면 그것만 anchor 로 사용
                    (본문 sub-item anchor 들 reject). 학원장 mental model "한 문제 =
                    Q3 (그림+설명+sub-items 통째)" 정합. False (시험지) 면 marginal
                    1개 이상이면 marginal 만 사용 (보수적 임계), 0~1 개면 body 사용.

    Returns:
        List of QuestionRegion sorted by question number.
    """
    if not text_blocks:
        return []

    # paper_type이 명시되면 휴리스틱 우회. NON_QUESTION이면 빈 리스트 반환.
    if paper_type is not None:
        if paper_type.is_non_question:
            return []
        is_quad_layout = paper_type.is_quadrant
        is_dual_column = paper_type.is_dual_column and not is_quad_layout
    else:
        # 4분할 레이아웃 우선 검사 — 가로 + 세로 gutter가 모두 있으면 quad.
        # 4분할이면 dual column 분기와 다른 좌표 구속 필요.
        is_quad_layout = _detect_quad_layout(text_blocks, page_width, page_height)
        is_dual_column = (not is_quad_layout) and _detect_column_layout(text_blocks, page_width)
    mid_x = page_width * 0.5
    mid_y = page_height * 0.5

    # layout strategy 결정 — 정렬·x/y 경계·후처리 클램프를 strategy에 위임.
    from academy.domain.tools.region_splitters import get_strategy_by_layout_flags

    strategy = get_strategy_by_layout_flags(
        is_quad=is_quad_layout, is_dual=is_dual_column,
    )

    sorted_blocks = strategy.sort_blocks(text_blocks, mid_x, mid_y)

    # Find question start positions — marginal/body candidates 구분 수집.
    # marginal = 페이지 좌측 marginal column 의 짧은 standalone "N." block.
    #           워크북/메인자료 의 "큰 번호" main question anchor.
    # body = "1. 다음 글은..." 처럼 anchor 뒤 문장이 이어지는 일반 시험지 anchor.
    marginal_threshold_x = page_width * 0.15
    candidates: List[Tuple[int, int, bool]] = []  # (qnum, block_idx, is_marginal)
    for idx, block in enumerate(sorted_blocks):
        # marginal candidate 우선 검사 (짧은 standalone block).
        marginal_num = _extract_marginal_question_number(block.text)
        if (
            marginal_num is not None
            and block.x0 < marginal_threshold_x
        ):
            candidates.append((marginal_num, idx, True))
            continue
        # body anchor (current regex).
        body_num = _extract_question_number(block.text)
        if body_num is not None:
            candidates.append((body_num, idx, False))

    if not candidates:
        return []

    # Marginal preference 분기:
    # - prefer_marginal=True (워크북 doc-level 신호): marginal candidate 1+ 면 마진만.
    #   페이지에 main question 1 개 (Q1) 만 있는 케이스도 학원장 mental model 의
    #   "한 문제" 단위 = marginal anchor.
    # - prefer_marginal=False (시험지): marginal 2+ 인 경우만 마진 preference (보수적
    #   임계). 시험지에 standalone "N." block 우연히 1개 있는 경우 본문 anchor 도 사용.
    marginal_count = sum(1 for _, _, m in candidates if m)
    marginal_threshold = 1 if prefer_marginal else 2
    if marginal_count >= marginal_threshold:
        candidates = [c for c in candidates if c[2]]

    question_starts: List[Tuple[int, int]] = [(qn, ix) for qn, ix, _ in candidates]

    # 페이지 내 중복 번호 제거. 본문 내 "그림 4는..." 같은 표현이 regex와
    # 매치되는 경우, layout 순서상 먼저 나온 것을 실제 문항 앵커로 간주.
    seen: dict[int, int] = {}
    deduped: List[Tuple[int, int]] = []
    for qnum, idx in question_starts:
        if qnum in seen:
            continue
        seen[qnum] = idx
        deduped.append((qnum, idx))
    question_starts = deduped

    # Build regions: each question spans from its start to the next question start
    regions: List[QuestionRegion] = []
    margin = 2.0  # small margin in points

    for i, (qnum, start_idx) in enumerate(question_starts):
        start_block = sorted_blocks[start_idx]
        next_block = (
            sorted_blocks[question_starts[i + 1][1]]
            if i + 1 < len(question_starts)
            else None
        )

        # Defensive: region_blocks 비면 skip (sort 결과 inconsistency 방어)
        end_idx = question_starts[i + 1][1] if next_block is not None else len(sorted_blocks)
        if not sorted_blocks[start_idx:end_idx]:
            continue

        # Strategy 호출: x range / y end 계산
        x0, x1 = strategy.compute_x_range(start_block, page_width, mid_x, margin)
        y0 = max(0, start_block.y0 - margin)
        y1 = strategy.compute_y_end(
            start_block, next_block,
            page_width, page_height, mid_x, mid_y, margin,
        )

        y1 = min(page_height, max(y1, y0 + 10))

        # Strip 절대 차단: height < 페이지의 5% (시험지 problem의 합리적 최소 크기)는
        # OCR false anchor 또는 cross-column anchor sort 오류의 흔적. strategy의
        # compute_y_end fallback이 miss한 경우의 마지막 보루.
        # T2 운영 reanalyze (2026-04-30): 11건 strip 잔존 (h=10~100). 임계 100px도
        # doc#177 q1 (정확히 100px, h=y1-y0이 page_height의 0.9%)에서 통과되어
        # 5% 비율 임계로 변경. 11200 * 0.05 = 560px 최소.
        if y1 - y0 < page_height * 0.05:
            y1 = page_height

        # Strategy post-clamp: quad는 quadrant 경계 / dual은 column 경계 추가 구속.
        x0, x1 = strategy.post_clamp_x(start_block, x0, x1, page_width, mid_x, margin)
        y0, y1 = strategy.post_clamp_y(start_block, y0, y1, page_height, mid_y, margin)

        regions.append(
            QuestionRegion(
                number=qnum,
                bbox=(x0, y0, x1, y1),
                page_index=page_index,
            )
        )

    # Sort by question number, fallback to layout order
    regions.sort(key=lambda r: r.number)

    return regions


def _detect_per_page_restart(
    regions_per_page: List[List["QuestionRegion"]],
) -> bool:
    """페이지별/섹션별 anchor 번호 리셋 패턴 감지 (global dedup 끄기 여부).

    Why: 시험지(continuous numbering)는 cross-page dedup 이 false anchor("그림 4는") 를 잘 거른다.
    하지만 페이지/섹션별 anchor 번호가 리셋되는 doc 들 (워크북·메인자료·여러 섹션 모아둔 자료) 에서는
    같은 dedup 이 후속 페이지 anchor 를 전부 drop 시켜 catastrophic under-cut 을 일으킨다
    (T2 운영 1 doc 단위 81% drop 실측, 24% clean_pdf_dual 학원장 manual_create 의 본질).

    감지 대상:
      A. 페이지별 리셋 워크북 (지권의 변화 메인자료) — 같은 1, 2, 3 시퀀스가 N 페이지에 반복.
      B. 섹션별 리셋 / 멀티-섹션 자료 (빅뱅 복습과제) — 큰 번호 범위가 두 번 이상 등장.
      C. 일반 시험지 false anchor (개포고 시험지 마지막 2 페이지에 "1.","2.","3." OCR 오탐) — 회피.

    Heuristic (3-신호 OR):
      1. anchor 가 있는 페이지가 5+ 개여야 함 (너무 작은 doc 은 시험지 protect).
      2. **신호 A (페이지 리셋)**: anchor 1, 2, 3 중 하나라도 포함된 페이지가
         (a) 절대 ≥ 5 페이지 AND (b) anchor 보유 페이지의 40% 이상.
      3. **신호 B (섹션 리셋)**: 2 페이지 이상에 걸쳐 등장하는 anchor 가
         (a) 절대 ≥ 5 개 AND (b) 전체 unique anchor 의 30% 이상.

    A 또는 B 하나라도 True 면 per-page-restart 로 간주 (global dedup off).
    개포고 시험지: pages_with_low=3 (5 미만) AND repeated=3 (5 미만) → A,B 모두 False → continuous 유지.
    """
    pages_with_anchors = sum(1 for p in regions_per_page if p)
    if pages_with_anchors < 5:
        return False

    # 신호 A — low anchor 가 많은 페이지에 분산 (페이지 리셋)
    pages_with_low = 0
    pages_per_number: dict[int, int] = {}
    for page_regions in regions_per_page:
        nums = {r.number for r in page_regions}
        if nums & {1, 2, 3}:
            pages_with_low += 1
        for n in nums:
            pages_per_number[n] = pages_per_number.get(n, 0) + 1

    threshold_low = max(5, int(pages_with_anchors * 0.4))
    signal_a = pages_with_low >= threshold_low

    # 신호 B — multi-page anchor 비율이 높음 (섹션 리셋)
    repeated = sum(1 for cnt in pages_per_number.values() if cnt >= 2)
    unique = len(pages_per_number)
    signal_b = (
        repeated >= 5
        and unique > 0
        and (repeated / unique) >= 0.3
    )

    return signal_a or signal_b


def _drop_outliers_in_seen(
    seen_numbers: set[int],
) -> set[int]:
    """sorted-unique number-space 별 sequence outlier 식별.

    100 단위 number-space 분리 (선택형 <100 / 서술형 100~ / 논술형 200~ / 단답 300~).
    median gap 대비 5x + abs >= 5 인 gap 이후의 모든 번호를 outlier 로 표시.
    예: [3, 4, 5, 6, 7, 46] → 46 드롭.
    """
    outlier_nums: set[int] = set()
    by_space: dict[int, List[int]] = {}
    for n in sorted(seen_numbers):
        space = n // 100
        by_space.setdefault(space, []).append(n)

    for space_nums in by_space.values():
        if len(space_nums) < 4:
            continue
        gaps = [space_nums[i + 1] - space_nums[i] for i in range(len(space_nums) - 1)]
        sorted_gaps = sorted(gaps)
        median_gap = sorted_gaps[len(sorted_gaps) // 2]
        for i, gap in enumerate(gaps):
            if gap >= 5 and gap >= median_gap * 5:
                outlier_nums.update(space_nums[i + 1:])
                break
    return outlier_nums


def validate_anchors_across_pages(
    regions_per_page: List[List[QuestionRegion]],
) -> List[List[QuestionRegion]]:
    """
    여러 페이지의 anchor를 모아 문서 전역 검증.

    두 모드:
      A. **Continuous numbering (시험지)** — 기본 모드.
         1. 크로스-페이지 중복 → 처음 등장 page 만 유지. 본문 내 "그림 4는" 같은 오탐 제거.
         2. Sequence outlier (median gap × 5 + abs ≥ 5) 드롭.
      B. **Per-page-restart (워크북/메인자료)** — `_detect_per_page_restart` 가 True 일 때.
         페이지마다 anchor 1, 2, 3... 가 리셋되는 doc 이라 global dedup 이 후속 페이지를 전부
         drop 시키는 결함을 방지. 페이지간 dedup 을 끄고 page-local outlier 만 적용.

    입력 형식: [per_page_regions]
    반환: 필터링된 [per_page_regions] (페이지 구조 유지, 내부 regions만 필터)
    """
    if not regions_per_page:
        return regions_per_page

    # ── Per-page-restart 감지 — workbook/메인자료 페이지 리셋 패턴 ──
    is_per_page_restart = _detect_per_page_restart(regions_per_page)

    if is_per_page_restart:
        # 페이지간 dedup OFF — 각 페이지 anchor 그대로 유지.
        # outlier 는 페이지 안에서만 적용 (페이지 내 false anchor 잔존 방지).
        filtered: List[List[QuestionRegion]] = []
        for page_regions in regions_per_page:
            page_nums = {r.number for r in page_regions}
            page_outliers = _drop_outliers_in_seen(page_nums)
            filtered.append([r for r in page_regions if r.number not in page_outliers])
        return filtered

    # ── Continuous mode — 기존 시험지 dedup ──
    seen_numbers: set[int] = set()
    filtered = []
    for page_regions in regions_per_page:
        kept: List[QuestionRegion] = []
        for r in page_regions:
            if r.number in seen_numbers:
                continue
            seen_numbers.add(r.number)
            kept.append(r)
        filtered.append(kept)

    outlier_nums = _drop_outliers_in_seen(seen_numbers)
    if outlier_nums:
        filtered = [
            [r for r in page_regions if r.number not in outlier_nums]
            for page_regions in filtered
        ]

    return filtered

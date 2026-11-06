# PATH: academy/adapters/ai/detection/vlm_fallback.py
"""VLM fallback adapter — low_conf 페이지 대상 1차/2차 분류기.

학원장 directive (2026-05-02): VLM은 메인 엔진 X, fallback only.

Tier 구조 (2026-11-05 OpenAI 전환):
  Tier 1 (free):     OpenCV + OCR + YOLO (이미 운영)
  Tier 2 (text):     GPT-6 Luna text — page_role 분류 (OCR 결과 입력)
  Tier 3 (vision):   GPT-6.1 Terra vision — bbox 추출 / 손글씨/그림 dominant 페이지
  Tier 4 (재시도):   동일 OpenAI 모델 retry (text-only)
  Tier 5 (금지):     Pro 등 비싼 모델 사용 X

호출 시점: low_conf_pages (paper_type_summary.low_conf_pages) 가 비지 않을 때만.
호출 횟수 cap: doc당 max 50 호출 (in-memory counter).

Public API:
  classify_page_role_text(ocr_blocks, page_meta) -> PageRoleResult  # Tier 2
  detect_problems_vision(image_path, page_meta) -> ProblemBboxResult  # Tier 3

env 설정:
  MATCHUP_VLM_TEXT_ADAPTER=openai  # 또는 mock (default)
  MATCHUP_VLM_VISION_ADAPTER=openai     # 또는 mock (default)
  OPENAI_API_KEY=...                          # SSM /academy/workers/env에 통합
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


# ── 출력 schema ─────────────────────────────────────────────────


class PageRole(str, Enum):
    """VLM이 분류하는 페이지 역할 — 6분류."""

    COVER = "cover"             # 표지
    INDEX = "index"             # 목차
    PROBLEM = "problem"         # 문항
    EXPLANATION = "explanation" # 해설/본문
    ANSWER_KEY = "answer_key"   # 정답지
    MIXED = "mixed"             # 혼재 (문항 + 해설 등)


@dataclass
class PageRoleResult:
    """Tier 2 text-LLM 결과 — page_role + anchor_role 결정."""

    page_role: PageRole
    should_skip: bool                # 매치업 인덱싱 X (cover/index/explanation/answer_key)
    confidence: float                # 0.0 ~ 1.0
    debug: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProblemBbox:
    """단일 문항 bbox (픽셀 좌표)."""

    number: int                       # 문항 번호 (OCR 또는 VLM 추출)
    bbox: Tuple[int, int, int, int]   # (x, y, w, h)
    confidence: float                 # 0.0 ~ 1.0
    # 공유 보기/자료 묶음 — "<보기>(N~M)" 같이 보기가 여러 문항에 묶일 때
    # 묶인 다른 문항 번호 list. 묶음 문항은 동일 bbox 가지며 D-1 IoU 게이트는
    # shared_with set이 일치하는 문항 쌍은 overlap reject 면제.
    shared_with: List[int] = field(default_factory=list)
    # True only after every OCR reference and whole-page coverage is validated.
    ocr_grounded: bool = False


@dataclass
class ProblemBboxResult:
    """Tier 3 vision 결과 — 페이지 내 문항 bbox 리스트.

    paper_type: VLM이 직접 분류한 페이지 유형 (B-2 보강, 2026-05-04).
      값은 PaperType enum value (clean_pdf_single/dual, scan_single/dual, quadrant,
      student_answer_photo, side_notes, non_question, unknown). 호출자(_pages_via_vlm)
      가 page dict의 paper_type을 이 값으로 override 해 _aggregate_paper_types가
      heuristic 보다 정확한 신호 사용.
    """

    page_role: PageRole
    should_skip: bool
    problems: List[ProblemBbox]
    confidence: float
    paper_type: str = "unknown"
    debug: Dict[str, Any] = field(default_factory=dict)


# ── Adapter protocol (mock + real 공통) ─────────────────────────


class VLMTextAdapter(Protocol):
    """Tier 2 text-LLM 어댑터 — page_role 분류."""

    def classify(
        self,
        *,
        ocr_text: str,
        ocr_blocks: List[Dict[str, Any]] | None = None,
        page_meta: Dict[str, Any] | None = None,
    ) -> PageRoleResult: ...


class VLMVisionAdapter(Protocol):
    """Tier 3 vision-VLM 어댑터 — bbox 추출."""

    def detect_problems(
        self,
        *,
        image_path: str,
        page_meta: Dict[str, Any] | None = None,
    ) -> ProblemBboxResult: ...


# ── Mock 구현 ───────────────────────────────────────────────────


class MockVLMTextAdapter:
    """Mock — keyword heuristic으로 page_role 추정.

    실제 OpenAI gpt-5-nano text 호출 wire-up 전까지 사용.
    출력 schema는 real adapter와 동일하므로 downstream code 영향 없음.
    """

    SKIP_KEYWORDS = {
        PageRole.COVER:       ("표지", "cover", "PROJECT", "시리즈"),
        PageRole.INDEX:       ("CONTENTS", "목차", "Part ", "Chapter ", "PART ", "CHAPTER "),
        PageRole.EXPLANATION: ("해설", "풀이", "정답 및 해설", "Step "),
        PageRole.ANSWER_KEY:  ("정답지", "정답표", "ANSWER KEY", "answers"),
    }

    def classify(
        self,
        *,
        ocr_text: str,
        ocr_blocks: List[Dict[str, Any]] | None = None,
        page_meta: Dict[str, Any] | None = None,
    ) -> PageRoleResult:
        text = (ocr_text or "")
        for role, kws in self.SKIP_KEYWORDS.items():
            if any(kw in text for kw in kws):
                return PageRoleResult(
                    page_role=role,
                    should_skip=True,
                    confidence=0.7,
                    debug={"adapter": "mock", "matched_keywords": [
                        kw for kw in kws if kw in text
                    ]},
                )
        # 기본: 문항 페이지로 가정 (불확실)
        return PageRoleResult(
            page_role=PageRole.PROBLEM,
            should_skip=False,
            confidence=0.5,
            debug={"adapter": "mock", "matched_keywords": []},
        )


class MockVLMVisionAdapter:
    """Mock — 단일 page-as-problem bbox 반환.

    실제 OpenAI gpt-5-nano vision 호출 wire-up 전까지 사용.
    도메인 코드가 결과 schema에 의존해도 깨지지 않게 conservative default.
    """

    def detect_problems(
        self,
        *,
        image_path: str,
        page_meta: Dict[str, Any] | None = None,
    ) -> ProblemBboxResult:
        # 기존 page 메타에서 bbox 후보 사용 (있으면)
        meta = page_meta or {}
        boxes = meta.get("boxes") or []
        problems: List[ProblemBbox] = []
        for i, b in enumerate(boxes, start=1):
            # box format: (x, y, w, h)
            try:
                x, y, w, h = b[:4]
                problems.append(ProblemBbox(
                    number=i,
                    bbox=(int(x), int(y), int(w), int(h)),
                    confidence=0.5,
                ))
            except Exception:
                continue
        return ProblemBboxResult(
            page_role=PageRole.PROBLEM,
            should_skip=False,
            problems=problems,
            confidence=0.5,
            paper_type="unknown",
            debug={"adapter": "mock", "fallback_to_existing_boxes": True},
        )


# ── Factory + 환경 변수 기반 선택 ───────────────────────────────


_TEXT_ADAPTER: Optional[VLMTextAdapter] = None
_VISION_ADAPTER: Optional[VLMVisionAdapter] = None


# ── OpenAI REST 호출 헬퍼 + cost guard ──────────────────────────


# Call budgets bound network attempts, including retries; dollar costs vary by model/tokens.
_VLM_DOC_CALL_LIMIT = int(os.getenv("MATCHUP_VLM_PER_DOC_LIMIT", "50"))
# Tenant 단위 일별 호출 cap (P0-2 cost cap, 2026-05-04). 한 학원이 다수 doc 업로드 시
# cost 폭주 방지. default 500 network attempts/day. 학원장 정책에
# 따라 env로 조정. 일 reset은 자동 (date key 변경 시 새 카운터).
_VLM_TENANT_DAILY_LIMIT = int(os.getenv("MATCHUP_VLM_PER_TENANT_DAILY_LIMIT", "500"))
# Network timeout is bounded per attempt; the caller allows one transient retry.
_OPENAI_TIMEOUT = int(os.getenv("MATCHUP_VLM_TIMEOUT_SEC", "90"))
# 페이지당 30+ 문항이 있는 dual-col commercial_workbook도 응답 잘리지 않도록 8192로 확장.
_OPENAI_MAX_OUTPUT_TOKENS = int(os.getenv("MATCHUP_VLM_MAX_OUTPUT_TOKENS", "8192"))
# 429/transient 5xx: one bounded retry; failures remain explicit.
_OPENAI_RETRY_BACKOFF_SEC = int(os.getenv("MATCHUP_VLM_RETRY_BACKOFF", "5"))
# Bound transmitted image size; normalized coordinates still map to original pixels.
_OPENAI_VISION_MAX_DIM = int(os.getenv("MATCHUP_VLM_VISION_MAX_DIM", "1600"))
_OPENAI_VISION_JPEG_Q = int(os.getenv("MATCHUP_VLM_VISION_JPEG_Q", "85"))

_doc_call_counter: Dict[str, int] = {}
_tenant_call_counter: Dict[tuple, int] = {}  # (tenant_id_str, date_str) → count


def _check_doc_quota(document_id: str | int | None) -> None:
    """doc별 VLM 호출 횟수 cap. 초과 시 RuntimeError."""
    if not document_id:
        return  # ondemand classify endpoint(예: 검수 UI)는 doc id 항상 전달.
    key = str(document_id)
    cur = _doc_call_counter.get(key, 0)
    if cur >= _VLM_DOC_CALL_LIMIT:
        raise RuntimeError(
            f"VLM 호출 한도 초과 (doc={key}, limit={_VLM_DOC_CALL_LIMIT}). "
            f"ASG 재기동 또는 MATCHUP_VLM_PER_DOC_LIMIT env로 조정."
        )
    _doc_call_counter[key] = cur + 1


def _check_tenant_quota(tenant_id: str | int | None) -> None:
    """tenant별 일별 VLM 호출 cap. 초과 시 RuntimeError.

    cost 폭주 방지 (P0-2, 2026-05-04). 한 학원이 다수 doc 업로드 시 호출 폭주 차단.
    date 자동 변경 (KST date key) — 다음 날 자동 reset.
    in-memory counter — ASG 재기동 시 리셋. 운영 모니터링은 별도 metric 필요.
    """
    if not tenant_id:
        return
    from datetime import date
    today = date.today().isoformat()
    key = (str(tenant_id), today)
    cur = _tenant_call_counter.get(key, 0)
    if cur >= _VLM_TENANT_DAILY_LIMIT:
        raise RuntimeError(
            f"VLM 호출 한도 초과 (tenant={tenant_id}, date={today}, "
            f"limit={_VLM_TENANT_DAILY_LIMIT}). "
            f"내일 자동 reset 또는 MATCHUP_VLM_PER_TENANT_DAILY_LIMIT env로 조정."
        )
    _tenant_call_counter[key] = cur + 1


def reset_tenant_quota(tenant_id: str | int | None = None) -> None:
    """테스트/관리용 — tenant counter 리셋. None이면 전체 리셋."""
    global _tenant_call_counter
    if tenant_id is None:
        _tenant_call_counter = {}
    else:
        _tenant_call_counter = {
            k: v for k, v in _tenant_call_counter.items() if k[0] != str(tenant_id)
        }


def _openai_request(
    *,
    model: str,
    parts: List[Dict[str, Any]],
    response_schema_hint: str = "",
    document_id: str | int | None = None,
    tenant_id: str | int | None = None,
    enforce_per_doc_cap: bool = True,
    reasoning_effort: str = "low",
    max_output_tokens: int | None = None,
) -> Dict[str, Any]:
    """OpenAI Responses JSON request; incomplete/refused output fails closed.

    Each network attempt consumes the existing document/tenant call budgets.
    Provider bodies, input documents and credentials are never error details.
    Hybrid verification may use its own document cap, but not bypass tenant caps.
    """
    import json
    import time
    import requests

    from academy.adapters.ai.openai_credentials import resolve_openai_api_key

    api_key = resolve_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")
    payload: Dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": parts}],
        "instructions": "Return one complete JSON object. " + response_schema_hint,
        "text": {"format": {"type": "json_object"}},
        "reasoning": {"effort": reasoning_effort},
        "max_output_tokens": max_output_tokens or _OPENAI_MAX_OUTPUT_TOKENS,
        "store": False,
    }
    resp = None
    for attempt in (0, 1):
        _check_tenant_quota(tenant_id)
        if enforce_per_doc_cap:
            _check_doc_quota(document_id)
        try:
            from apps.domains.ai.services.quota import consume_ai_quota
            consume_ai_quota("matchup_vlm")
        except Exception:
            logger.warning("matchup_vlm quota tracking unavailable")
        try:
            resp = requests.post(
                "https://api.openai.com/v1/responses", json=payload,
                timeout=_OPENAI_TIMEOUT,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            )
        except requests.Timeout:
            raise RuntimeError(f"OpenAI API timeout({_OPENAI_TIMEOUT}s)") from None
        except Exception:
            raise RuntimeError("OpenAI API transport failed") from None
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            time.sleep(_OPENAI_RETRY_BACKOFF_SEC)
            continue
        break
    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else 0
        raise RuntimeError(f"OpenAI API request failed (status={code})")
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError("OpenAI response JSON decoding failed") from None
    if not isinstance(data, dict) or data.get("status") != "completed":
        raise RuntimeError("OpenAI response incomplete")
    texts = []
    for item in data.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("status") != "completed":
            raise RuntimeError("OpenAI message incomplete")
        for content in item.get("content") or []:
            if content.get("type") == "refusal":
                raise RuntimeError("OpenAI response refused")
            if content.get("type") == "output_text":
                texts.append(content.get("text", ""))
    try:
        result = json.loads("".join(texts))
    except (TypeError, ValueError):
        raise RuntimeError("OpenAI response is not complete JSON") from None
    if not isinstance(result, dict):
        raise RuntimeError("OpenAI response must be a JSON object")
    return result


def reset_doc_quota(document_id: str | int | None = None) -> None:
    """테스트/관리 도구용 — doc 단위 또는 전체 카운터 리셋."""
    if document_id is None:
        _doc_call_counter.clear()
    else:
        _doc_call_counter.pop(str(document_id), None)


# ── OpenAI 어댑터 ───────────────────────────────────────────────


_PAGE_ROLE_PROMPT = """당신은 한국어 시험지/학습자료 페이지를 분석하는 분류기입니다.
페이지의 OCR 텍스트가 아래 주어집니다. 이 페이지가 어떤 역할인지 판단하세요.

가능한 page_role:
  - cover: 표지 (시험지명/학교명/학년/시리즈 표지)
  - index: 목차 (CONTENTS, 목차, Chapter/Part 리스트)
  - problem: 문항 페이지 (실제 문제 + 보기)
  - explanation: 해설/풀이 페이지
  - answer_key: 정답표 페이지
  - mixed: 문항 + 해설 혼재

JSON으로 다음 schema로만 응답하세요:
{
  "page_role": "...",
  "should_skip": <true if cover/index/explanation/answer_key>,
  "confidence": <0.0~1.0>,
  "reason": "<한 문장>"
}

OCR 텍스트:
"""


_PROBLEM_BBOX_PROMPT = """당신은 한국어 시험지·교재 페이지의 문항 영역을 검출하는 시스템입니다.
첨부된 페이지 이미지에서 각 문항의 위치(bbox)와 번호를 찾아 JSON으로만 응답하세요.

먼저 페이지의 layout을 식별하세요:
- single_column: 한 열에 문항이 위에서 아래로 나열됨 (1, 2, 3, 4, ...). 시판 교재에 흔함.
- dual_column: 두 열로 분할, 각 열은 위→아래 순서 (좌측 열 모두 끝나고 우측 열).
- quadrant: 4분할 (상-하 × 좌-우, 페이지가 4 사분면에 4 문항). 학생 답안지 폰사진에 흔함.
- mixed: 위 layout이 섞여 있음.

다음으로 paper_type (페이지 유형)을 식별하세요:
- clean_pdf_single: 깨끗한 인쇄 PDF, 단일 컬럼 (출판사 시판 교재 본문)
- clean_pdf_dual: 깨끗한 인쇄 PDF, 2단 컬럼
- scan_single: 스캔본 시험지 단일 컬럼 (학교 인쇄 + 종이 스캔)
- scan_dual: 스캔본 시험지 2단 컬럼
- quadrant: 4분할 (2x2 그리드) 페이지
- student_answer_photo: 학생이 풀고 폰으로 찍은 시험지 (손글씨 답안 + perspective + 회전 + 종이 그림자)
- side_notes: 학습자료 본문 (Step N. / 항목번호가 본문에 다수 등장)
- non_question: 표지·목차·해설·정답지·빈 페이지 (should_skip=true와 일치)
- unknown: 분류 불명

문항 박스 작성 규칙 (필수):
- 각 박스는 인쇄된 독립 문항 번호(예: "1.", "(1)", "[서답형 1]")부터 본문, 보기, 답란까지 모두 포함하도록 잡으세요.
- 객관식 선택지 ①②③④⑤, 문항 안의 소문항 번호, 손글씨 번호는 독립 문항 번호가 아닙니다.
- 박스 안에 실제 본문 텍스트가 반드시 있어야 합니다. 빈 영역에 박스를 만들지 마세요.
- 페이지 상단의 헤더(과목명·학교명·학년·시험회차·로고·페이지 번호)는 박스에 포함하지 마세요.
- 페이지 하단의 푸터(페이지 번호·"다음 면에 계속"·저작권 문구)도 박스에 포함하지 마세요.
- 손글씨 답안이 있어도 인쇄된 본문 영역만 검출하세요.
- 인접한 문항 박스가 서로 겹치지 않도록 하세요.
- 표지·목차·해설·정답지·문제 없는 빈 페이지면 problems = [], should_skip = true.

공유 보기/자료 (시판 교재·기출에 흔함) — 매우 중요:
- "<보기>(12~13)", "[12-13]", "다음을 읽고 12, 13번에 답하시오" 같이 보기/자료 하나가 여러 문항(예: 12와 13)에 묶이면,
  보기 + 묶인 모든 문항(12 + 13) 전체를 통째로 감싸는 동일한 bbox를 묶인 각 문항(12, 13) 모두에 부여하세요.
- 즉 묶인 문항들(12, 13)은 **동일한 bbox(같은 좌표)**를 share. 보기 따로 잘라 떼어내지 말 것.
- shared_with: 묶인 문항 번호 list (선택 출력) — 12번 객체에 "shared_with":[13], 13번 객체에 "shared_with":[12].
- 묶음 문항이 같은 bbox를 가지면 D-1 IoU 게이트가 reject할 수 있으므로, shared_with 표시로 게이트가 묶음을 인식.

서술형/논술형 영역도 problem 페이지 — 매우 중요:
- "[서답형 N]", "[서술형 N]", "[논술형 N]", "(서답형)" 표시가 있는 페이지는 **problem 페이지**입니다 (page_role=problem).
- 서술형은 객관식 다음 번호로 이어집니다 (예: 객관식 1~25 + 서답형 26, 27, 28...). 빈 답안 칸이 있어도 문항 본문을 problem으로 등록.
- answer_key (정답표) 와의 차이:
  - answer_key = 시험지 끝의 출제자 정답표 (객관식 ①②③④⑤ 또는 [정답: ...] 식의 정답 모음)
  - 서술형 답안 영역 = problem 페이지의 일부 (학생이 푸는 곳, 정답 X)
- 서술형 페이지를 answer_key/non_question으로 분류하지 마세요. should_skip=false, page_role=problem.

bbox 좌표 (프로젝트 정규화 좌표계):
- box_2d = [ymin, xmin, ymax, xmax], 각 값은 0~1000 범위의 정수입니다.
- 페이지 좌상단이 (0, 0), 우하단이 (1000, 1000)입니다.
- 원본이나 압축 이미지의 픽셀 수를 좌표로 사용하지 마세요.
- ymin/xmin은 인쇄된 독립 문항 번호를 포함해야 합니다.

JSON schema (이 외 키는 추가하지 마세요):
{
  "page_role": "problem|cover|index|explanation|answer_key|mixed",
  "should_skip": <bool>,
  "layout": "single_column|dual_column|quadrant|mixed|other",
  "paper_type": "clean_pdf_single|clean_pdf_dual|scan_single|scan_dual|quadrant|student_answer_photo|side_notes|non_question|unknown",
  "problems": [{"number": <int>, "box_2d": [ymin, xmin, ymax, xmax], "confidence": <0.0~1.0>, "shared_with": [<int>, ...]}],
  "confidence": <0.0~1.0>
}
"""

# paper_type 응답 valid set — VLM이 invalid 값 응답 시 unknown으로 폴백.
_VALID_PAPER_TYPES = frozenset({
    "clean_pdf_single", "clean_pdf_dual", "scan_single", "scan_dual",
    "quadrant", "student_answer_photo", "side_notes", "non_question", "unknown",
})


def _normalize_paper_type(raw: Any) -> str:
    """문자열 → valid paper_type. 모르는 값은 unknown."""
    s = str(raw or "").strip().lower()
    return s if s in _VALID_PAPER_TYPES else "unknown"


def _normalize_role(raw: str) -> PageRole:
    """문자열 → PageRole enum (모르는 값은 PROBLEM 보수적 기본)."""
    try:
        return PageRole(raw)
    except (ValueError, KeyError):
        logger.warning("Unknown page_role %r from VLM, defaulting to PROBLEM", raw)
        return PageRole.PROBLEM


def _validate_page_response(data: Dict[str, Any], *, vision: bool = False) -> None:
    """Do not promote malformed model output into a real page decision."""
    import math
    if data.get("page_role") not in {role.value for role in PageRole}:
        raise RuntimeError("OpenAI page role is invalid")
    if type(data.get("should_skip")) is not bool:
        raise RuntimeError("OpenAI page skip flag is invalid")
    confidence = data.get("confidence")
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise RuntimeError("OpenAI page confidence is invalid")
    if data["should_skip"] and data["page_role"] in {"problem", "mixed"}:
        raise RuntimeError("OpenAI page role and skip flag conflict")
    if vision and not isinstance(data.get("problems"), list):
        raise RuntimeError("OpenAI problem list is invalid")


class OpenAIVLMTextAdapter:
    """Tier 2 — OpenAI text 호출로 page_role 분류."""

    def __init__(self, model: str = "gpt-6-luna"):
        self.model = model

    def classify(
        self,
        *,
        ocr_text: str,
        ocr_blocks: List[Dict[str, Any]] | None = None,
        page_meta: Dict[str, Any] | None = None,
    ) -> PageRoleResult:
        meta = page_meta or {}
        document_id = meta.get("document_id")
        tenant_id = meta.get("tenant_id")
        prompt = _PAGE_ROLE_PROMPT + (ocr_text or "")[:6000]
        try:
            data = _openai_request(
                model=self.model,
                parts=[{"type": "input_text", "text": prompt}],
                max_output_tokens=2048,
                document_id=document_id,
                tenant_id=tenant_id,
            )
            _validate_page_response(data)
        except Exception as e:
            logger.warning("OpenAI text classify 실패: %s", e)
            # 명시적 fallback: keyword heuristic mock + 실패 reason debug에 노출.
            mock_result = MockVLMTextAdapter().classify(
                ocr_text=ocr_text, ocr_blocks=ocr_blocks, page_meta=page_meta,
            )
            mock_result.debug["adapter"] = "mock_after_openai_fail"
            mock_result.debug["openai_error"] = str(e)[:300]
            mock_result.debug["model"] = self.model
            return mock_result
        return PageRoleResult(
            page_role=_normalize_role(str(data.get("page_role", "problem"))),
            should_skip=bool(data.get("should_skip", False)),
            confidence=float(data.get("confidence", 0.6)),
            debug={
                "adapter": "openai",
                "model": self.model,
                "reason": str(data.get("reason", ""))[:200],
            },
        )


class OpenAIVLMVisionAdapter:
    """Tier 3 — OpenAI vision 호출로 페이지 이미지에서 문항 bbox 검출."""

    def __init__(self, model: str = "gpt-6.1-terra"):
        self.model = model

    def detect_problems(
        self,
        *,
        image_path: str,
        page_meta: Dict[str, Any] | None = None,
    ) -> ProblemBboxResult:
        import base64
        import io as _io
        import mimetypes

        meta = page_meta or {}
        document_id = meta.get("document_id")
        tenant_id = meta.get("tenant_id")
        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()
        except Exception as e:
            raise RuntimeError(f"VLM vision 이미지 읽기 실패: {e}") from e

        # 전송 비용을 제한하기 위해 큰 이미지를 1600px / JPEG 85로 압축.
        # 0..1000 yxyx 응답을 원본 너비/높이로 환산한다. 전송 압축 비율과 무관하다.
        scale = 1.0
        orig_w = orig_h = 0
        try:
            from PIL import Image  # type: ignore
            _orig = Image.open(_io.BytesIO(img_bytes))
            orig_w, orig_h = _orig.size
            if max(orig_w, orig_h) > _OPENAI_VISION_MAX_DIM:
                scale = _OPENAI_VISION_MAX_DIM / max(orig_w, orig_h)
                new_size = (int(orig_w * scale), int(orig_h * scale))
                _rsz = _orig.convert("RGB").resize(new_size, Image.LANCZOS)
                # 원본 이미지를 즉시 해제해 provider 호출 중 메모리 점유를 줄인다.
                _orig.close()
                del _orig
                buf = _io.BytesIO()
                _rsz.save(buf, format="JPEG", quality=_OPENAI_VISION_JPEG_Q, optimize=True)
                _rsz.close()
                del _rsz
                img_bytes = buf.getvalue()
                mime = "image/jpeg"
            else:
                _orig.close()
                del _orig
                mime, _ = mimetypes.guess_type(image_path)
                mime = mime or "image/png"
        except Exception:
            # PIL 부재 / 손상 이미지 — 원본 그대로 전송 (기존 동작 유지).
            mime, _ = mimetypes.guess_type(image_path)
            mime = mime or "image/png"

        if not orig_w or not orig_h:
            raise RuntimeError("VLM vision 원본 이미지 크기를 읽을 수 없습니다")
        b64 = base64.b64encode(img_bytes).decode("ascii")

        grounded_blocks = None
        try:
            prompt = _PROBLEM_BBOX_PROMPT
            if meta.get("paper_type") == "student_answer_photo":
                from academy.adapters.ai.ocr.google import google_ocr_blocks
                from academy.adapters.ai.detection.photo_grounding import (
                    grounded_photo_regions, photo_grounding_prompt, photo_ocr_blocks,
                )
                grounded_blocks = photo_ocr_blocks(google_ocr_blocks(image_path), orig_w, orig_h)
                prompt = photo_grounding_prompt(grounded_blocks, orig_w, orig_h)
            data = _openai_request(
                model=self.model,
                parts=[
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": f"data:{mime};base64,{b64}", "detail": "high"},
                ],
                document_id=document_id,
                tenant_id=tenant_id,
                reasoning_effort="medium" if grounded_blocks is not None else "low",
            )
            if grounded_blocks is None:
                _validate_page_response(data, vision=True)
            if grounded_blocks is not None:
                regions = grounded_photo_regions(data, grounded_blocks, orig_w, orig_h)
                confidence = float(data.get("confidence", 0.7))
                return ProblemBboxResult(
                    page_role=PageRole.PROBLEM, should_skip=False,
                    problems=[ProblemBbox(
                        number=r["number"], bbox=r["bbox"], confidence=confidence,
                        shared_with=r["shared_with"], ocr_grounded=True,
                    ) for r in regions],
                    confidence=confidence, paper_type="student_answer_photo",
                    debug={"adapter": "openai", "model": self.model,
                           "raw_count": len(regions), "ocr_blocks": len(grounded_blocks),
                           "coordinate_system": "ocr_source_pixels", "ocr_grounded": True},
                )
        except Exception as e:
            logger.warning("OpenAI vision detect 실패: %s", e)
            # 명시적 fallback — debug에 실패 사유 표시 (검수 UI에서 사용자가 판단 가능).
            mock_result = MockVLMVisionAdapter().detect_problems(
                image_path=image_path, page_meta=page_meta,
            )
            mock_result.debug["adapter"] = "mock_after_openai_fail"
            mock_result.debug["openai_error"] = str(e)[:300]
            mock_result.debug["model"] = self.model
            return mock_result

        problems_raw = data.get("problems") or []
        problems: List[ProblemBbox] = []
        for p in problems_raw:
            if not isinstance(p, dict) or type(p.get("number")) is not int or p["number"] <= 0:
                continue
            try:
                box = p.get("box_2d")
                if not isinstance(box, list) or len(box) != 4 or not all(type(v) is int for v in box):
                    continue
                ymin, xmin, ymax, xmax = box
                if not (0 <= ymin < ymax <= 1000 and 0 <= xmin < xmax <= 1000):
                    continue
                x0, y0 = round(xmin * orig_w / 1000), round(ymin * orig_h / 1000)
                x1, y1 = round(xmax * orig_w / 1000), round(ymax * orig_h / 1000)
                if x1 <= x0 or y1 <= y0:
                    continue
                shared_raw = p.get("shared_with") or []
                shared_with = [int(s) for s in shared_raw if isinstance(s, (int, str)) and str(s).lstrip("-").isdigit()]
                problems.append(ProblemBbox(
                    number=p["number"],
                    bbox=(x0, y0, x1 - x0, y1 - y0),
                    confidence=float(p.get("confidence", 0.7)),
                    shared_with=shared_with,
                ))
            except (TypeError, ValueError, IndexError):
                continue

        return ProblemBboxResult(
            page_role=_normalize_role(str(data.get("page_role", "problem"))),
            should_skip=bool(data.get("should_skip", False)),
            problems=problems,
            confidence=float(data.get("confidence", 0.7)),
            paper_type=_normalize_paper_type(data.get("paper_type")),
            debug={
                "adapter": "openai", "model": self.model,
                "raw_count": len(problems_raw),
                "scale": round(scale, 3),
                "coordinate_system": "normalized_1000_yxyx",
                "layout": str(data.get("layout", "")),
            },
        )


# ── Factory ─────────────────────────────────────────────────────


# Legacy environment names are rollout aliases only: they never call Google.
_LEGACY_ADAPTERS = {"gemini_flash", "gemini_flash_lite"}


def is_openai_adapter(choice: str) -> bool:
    return choice.strip().lower() in {"openai", "openai_luna", "openai_terra"} | _LEGACY_ADAPTERS


def get_text_adapter() -> VLMTextAdapter:
    """Explicit mock remains disabled; legacy provider selections migrate to OpenAI."""
    global _TEXT_ADAPTER
    if _TEXT_ADAPTER is None:
        choice = os.getenv("MATCHUP_VLM_TEXT_ADAPTER", "mock").strip().lower()
        if is_openai_adapter(choice):
            _TEXT_ADAPTER = OpenAIVLMTextAdapter(
                model=os.getenv("MATCHUP_VLM_TEXT_MODEL") or "gpt-6-luna")
        elif choice == "mock":
            _TEXT_ADAPTER = MockVLMTextAdapter()
        else:
            raise ValueError("Unknown MATCHUP_VLM_TEXT_ADAPTER")
    return _TEXT_ADAPTER


def get_vision_adapter() -> VLMVisionAdapter:
    global _VISION_ADAPTER
    if _VISION_ADAPTER is None:
        choice = os.getenv("MATCHUP_VLM_VISION_ADAPTER", "mock").strip().lower()
        if is_openai_adapter(choice):
            _VISION_ADAPTER = OpenAIVLMVisionAdapter(
                model=os.getenv("MATCHUP_VLM_VISION_MODEL") or "gpt-6.1-terra")
        elif choice == "mock":
            _VISION_ADAPTER = MockVLMVisionAdapter()
        else:
            raise ValueError("Unknown MATCHUP_VLM_VISION_ADAPTER")
    return _VISION_ADAPTER


# 테스트/리셋용
def _reset_adapters() -> None:
    global _TEXT_ADAPTER, _VISION_ADAPTER
    _TEXT_ADAPTER = None
    _VISION_ADAPTER = None


# ── Public 진입점 ───────────────────────────────────────────────


def classify_page_role_text(
    *,
    ocr_text: str,
    ocr_blocks: List[Dict[str, Any]] | None = None,
    page_meta: Dict[str, Any] | None = None,
) -> PageRoleResult:
    """Tier 2 text-LLM 호출 — page_role 분류.

    호출 시점: low_conf 페이지 (page_confidence < 0.55) 만.
    """
    adapter = get_text_adapter()
    return adapter.classify(
        ocr_text=ocr_text,
        ocr_blocks=ocr_blocks,
        page_meta=page_meta,
    )


def detect_problems_vision(
    *,
    image_path: str,
    page_meta: Dict[str, Any] | None = None,
) -> ProblemBboxResult:
    """Tier 3 vision-VLM 호출 — bbox 추출.

    호출 시점: classify_page_role_text가 PROBLEM/MIXED + low confidence 일 때만.
    """
    adapter = get_vision_adapter()
    return adapter.detect_problems(
        image_path=image_path,
        page_meta=page_meta,
    )

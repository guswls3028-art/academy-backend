"""segment_ocr 멀티 anchor block 분할 + 섹션 가드 회귀 테스트.

운영 사고: doc#329 page 1 OCR이 "5. ... 7. ..." 한 줄로 묶여서 Q7 anchor 손실.
서답형 페이지의 "서 답형 1. ... 서 답형 2. ..." 가 잘못 split되어 1번이 선택형
Q1으로 오인식되는 회귀 케이스도 함께 보호.
"""
from __future__ import annotations

from academy.adapters.ai.detection.segment_ocr import _split_multi_anchor_blocks
from academy.domain.tools.question_splitter import TextBlock, _extract_question_number


def _tb(text: str, x0: float = 0, y0: float = 0, x1: float = 1000, y1: float = 100) -> TextBlock:
    return TextBlock(text=text, x0=x0, y0=y0, x1=x1, y1=y1)


def test_split_two_anchors_in_same_line():
    """좌/우 컬럼이 한 line으로 묶인 경우 두 anchor sub-block으로 분할."""
    block = _tb(
        "5. 그림 ( 가 ) 는 세포 에서 일어나는 물질 대사 I 과 Ⅱ 를 , ( 나 ) 는 7. 그림 은 생태계 를 구성 하는 요소 사이 의 상호",
        x0=0, x1=2000,
    )
    out = _split_multi_anchor_blocks([block])
    assert len(out) == 2
    nums = [_extract_question_number(b.text) for b in out]
    assert 5 in nums and 7 in nums
    # 두 번째 sub-block의 x 좌표는 첫 번째보다 오른쪽
    out_sorted = sorted(out, key=lambda b: b.x0)
    assert out_sorted[0].x0 < out_sorted[1].x0


def test_split_shared_range_anchor_in_same_line():
    """좌/우 컬럼 한 줄에 붙은 '[9, 10]' 공통 자료 anchor도 분할한다."""
    block = _tb(
        "6. 그림 ( 가 ) 는 어떤 별의 진화 과정이다 [9, 10] 그림은 주기율표의 일부를 나타낸 것이다",
        x0=0,
        x1=2000,
    )
    out = _split_multi_anchor_blocks([block])
    assert len(out) == 2
    nums = [_extract_question_number(b.text) for b in out]
    assert nums == [6, 9]


def test_section_block_not_split():
    """서답형 헤더 블록은 split되지 않는다 (가드)."""
    block = _tb(
        "서 답형 1. ( 서 논술형 ) 그림 ( 가 ) 는 사람 에서 세포 호흡 을 서 답형 2. ( 서 논술형 ) 그림 은 뇌 의 구조 를 나타낸 것"
    )
    out = _split_multi_anchor_blocks([block])
    # 단일 블록 그대로 — section 블록은 split 금지
    assert len(out) == 1
    assert out[0].text.startswith("서")


def test_short_block_not_split():
    """길이 < 20 블록은 split 안 함."""
    block = _tb("3. 그림")
    out = _split_multi_anchor_blocks([block])
    assert len(out) == 1


def test_single_anchor_not_split():
    """한 anchor만 있는 블록은 split 안 함."""
    block = _tb("3. 표 는 생물 의 특성 의 예 를 나타낸 것이다 .")
    out = _split_multi_anchor_blocks([block])
    assert len(out) == 1


def test_anchor_out_of_range_not_split():
    """선택형 1~60 범위 밖 anchor 는 split 트리거 안 함."""
    # "100. ..." 같은 블록은 본문 숫자일 가능성이 더 큼.
    block = _tb("100. 추가 설명 으로 옳은 것은 200. 다음 중 옳지 않은 것은 ?")
    out = _split_multi_anchor_blocks([block])
    # nums 둘 다 60 초과 → split 안 함
    assert len(out) == 1


def test_three_anchors_split():
    """3개 anchor 가 한 줄에 묶여도 3개로 split."""
    block = _tb(
        "1. 다음 중 옳은 것은 ? 2. 다음 중 옳지 않은 것은 ? 3. 그림 ( 가 ) 와 ( 나 ) 의 차이 를 쓰시 오 .",
        x0=0, x1=3000,
    )
    out = _split_multi_anchor_blocks([block])
    assert len(out) == 3
    nums = [_extract_question_number(b.text) for b in out]
    assert nums == [1, 2, 3]

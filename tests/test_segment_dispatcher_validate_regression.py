"""segment_dispatcher._collect_pdf_pages가 validate_anchors_across_pages 결과를
호출자에 정확히 반환하는지 회귀 테스트.

운영 사고: 과거 코드는 boxes_per_page는 갱신하지만 regions_per_page는 원본 그대로
반환해서 segment_questions_multipage가 len(regions) != len(boxes)로 [None]*N 폴백을
선택했다. 결과적으로 OCR이 검출한 시험지 번호가 페이지 단위로 통째 사라짐.
"""
from __future__ import annotations

from unittest.mock import patch

from academy.domain.tools.question_splitter import QuestionRegion


class _FakePageInfo(dict):
    """_pdf_to_images 출력 형태."""


def _make_pages(n: int):
    return [
        _FakePageInfo(
            image_path=f"/tmp/p{i}.png",
            has_embedded_text=False,
            text_boxes=[],
            text_regions=[],
            is_skip_page=False,
        )
        for i in range(n)
    ]


def _r(num: int, page_idx: int) -> QuestionRegion:
    return QuestionRegion(
        number=num,
        bbox=(0.0, 0.0, 100.0, 100.0),
        page_index=page_idx,
    )


def test_collect_pdf_pages_returns_validated_regions():
    """validate가 dup/outlier를 드롭하면 regions_per_page도 갱신되어 반환되어야 한다."""
    from academy.adapters.ai.detection import segment_dispatcher as sd

    fake_pages = _make_pages(2)
    # page 0 has anchors [2, 3], page 1 has anchors [3, 4] — Q3 cross-page dup
    boxes_p0 = [(0, 0, 100, 100), (0, 100, 100, 100)]
    boxes_p1 = [(0, 0, 100, 100), (0, 100, 100, 100)]
    regions_p0 = [_r(2, 0), _r(3, 0)]
    regions_p1 = [_r(3, 1), _r(4, 1)]

    def fake_pdf_to_images(_path, **_kwargs):
        # 운영 시그니처는 (pdf_path, *, handwriting_bias=...). 향후 kwarg 추가에도 안전.
        return fake_pages, "/tmp/dummy"

    def fake_boxes_and_regions(info, page_idx, **_kwargs):
        # 운영 시그니처는 (info, page_idx, *, handwriting_bias, source_type, ...).
        if page_idx == 0:
            return boxes_p0, regions_p0
        return boxes_p1, regions_p1

    with patch.object(sd, "_pdf_to_images", fake_pdf_to_images), \
         patch.object(sd, "_boxes_and_regions_for_pdf_page", fake_boxes_and_regions):
        page_infos, boxes_per_page, regions_per_page, _tmp = sd._collect_pdf_pages("/tmp/x.pdf")

    # page 0 keeps both [2, 3]; page 1 should drop dup 3 and keep only [4]
    assert [r.number for r in regions_per_page[0]] == [2, 3]
    assert [r.number for r in regions_per_page[1]] == [4], (
        "regions_per_page must reflect validate_anchors_across_pages output (dup-3 dropped)"
    )
    # boxes도 같은 길이
    assert len(boxes_per_page[1]) == len(regions_per_page[1])


def test_segment_questions_multipage_propagates_numbers():
    """multipage 호출 후 페이지 numbers는 None 폴백 없이 검증된 번호를 반환해야 한다."""
    from academy.adapters.ai.detection import segment_dispatcher as sd

    fake_pages = _make_pages(2)
    boxes_p0 = [(0, 0, 100, 100), (0, 100, 100, 100)]
    boxes_p1 = [(0, 0, 100, 100), (0, 100, 100, 100)]
    regions_p0 = [_r(2, 0), _r(3, 0)]
    regions_p1 = [_r(3, 1), _r(4, 1)]

    def fake_pdf_to_images(_path, **_kwargs):
        return fake_pages, "/tmp/dummy"

    def fake_boxes_and_regions(info, page_idx, **_kwargs):
        if page_idx == 0:
            return boxes_p0, regions_p0
        return boxes_p1, regions_p1

    def fake_is_pdf(path):
        return True

    with patch.object(sd, "_pdf_to_images", fake_pdf_to_images), \
         patch.object(sd, "_boxes_and_regions_for_pdf_page", fake_boxes_and_regions), \
         patch.object(sd, "_is_pdf", fake_is_pdf):
        result = sd.segment_questions_multipage("/tmp/x.pdf")

    pages = result["pages"]
    # page 0: 2 boxes both with numbers
    assert pages[0]["numbers"] == [2, 3]
    # page 1: 1 box (dup 3 dropped) with number 4 — NOT [None]
    assert pages[1]["numbers"] == [4], (
        f"page 1 numbers should be [4], got {pages[1]['numbers']}. "
        "이 회귀가 None 으로 떨어지면 운영 시험지 번호가 다시 잘못 매겨진다."
    )

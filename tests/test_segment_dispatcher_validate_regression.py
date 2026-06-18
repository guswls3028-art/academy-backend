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


def test_expand_single_text_regions_to_visual_content_expands_x_only(tmp_path):
    """단일열 text crop은 렌더 이미지의 그림/표 잉크까지 x축만 보강한다."""
    import cv2
    import numpy as np

    from academy.adapters.ai.detection.segment_dispatcher import (
        _expand_single_text_regions_to_visual_content,
    )

    image = np.full((200, 200), 255, dtype=np.uint8)
    image[55:95, 25:80] = 0      # text area
    image[60:90, 105:175] = 0    # nearby figure/table outside text bbox
    image_path = str(tmp_path / "page.png")
    cv2.imwrite(image_path, image)

    region = QuestionRegion(
        number=1,
        bbox=(20.0, 50.0, 85.0, 110.0),
        page_index=0,
    )

    _expand_single_text_regions_to_visual_content(
        image_path,
        [region],
        page_width=200.0,
        page_height=200.0,
    )

    assert region.bbox[0] == 20.0
    assert region.bbox[2] > 170.0
    assert region.bbox[1] == 50.0
    assert region.bbox[3] == 110.0


def test_expand_single_text_regions_to_visual_content_ignores_far_decoration(tmp_path):
    """페이지 외곽 장식 띠는 본문 그림/표처럼 x축 확장에 포함하지 않는다."""
    import cv2
    import numpy as np

    from academy.adapters.ai.detection.segment_dispatcher import (
        _expand_single_text_regions_to_visual_content,
    )

    image = np.full((200, 200), 255, dtype=np.uint8)
    image[55:95, 25:80] = 0       # text area
    image[60:90, 105:145] = 0     # nearby figure/table
    image[50:180, 185:198] = 0    # far page decoration/sidebar
    image_path = str(tmp_path / "page.png")
    cv2.imwrite(image_path, image)

    region = QuestionRegion(
        number=1,
        bbox=(20.0, 50.0, 85.0, 110.0),
        page_index=0,
    )

    _expand_single_text_regions_to_visual_content(
        image_path,
        [region],
        page_width=200.0,
        page_height=200.0,
    )

    assert 145.0 < region.bbox[2] < 170.0


def test_should_expand_visual_x_for_pixel_dual_without_text_dual():
    from academy.adapters.ai.detection.segment_dispatcher import (
        _should_expand_text_regions_by_visual_x,
    )

    class _Paper:
        is_quadrant = False

    assert _should_expand_text_regions_by_visual_x(
        _Paper(),
        {"is_dual_text": False, "is_dual_pixel": True},
    )
    assert not _should_expand_text_regions_by_visual_x(
        _Paper(),
        {"is_dual_text": True, "is_dual_pixel": True},
    )

    class _Quad:
        is_quadrant = True

    assert not _should_expand_text_regions_by_visual_x(
        _Quad(),
        {"is_dual_text": False, "is_dual_pixel": True},
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

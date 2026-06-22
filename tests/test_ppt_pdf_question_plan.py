from __future__ import annotations

from dataclasses import dataclass

from academy.application.use_cases.tools.generate_ppt import (
    _add_segmented_pdf_slides_to_composer,
    _build_pdf_question_plan,
)


@dataclass
class _Block:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


class _FakeDoc:
    def __init__(self, pages: list[list[_Block]], width: float = 600.0, height: float = 840.0):
        self._pages = pages
        self._width = width
        self._height = height

    def page_count(self) -> int:
        return len(self._pages)

    def extract_text_blocks(self, page_index: int) -> list[_Block]:
        return self._pages[page_index]

    def page_dimensions(self, _page_index: int) -> tuple[float, float]:
        return self._width, self._height


def _question_text(n: int) -> str:
    return (
        f"{n}. 다음 중 옳은 것은? ① 보기 하나 ② 보기 둘 ③ 보기 셋 "
        "풀이 과정과 자료를 충분히 포함한 문항 본문입니다."
    )


def test_ppt_pdf_plan_uses_whole_page_for_scan_pdf_without_text():
    plan = _build_pdf_question_plan(_FakeDoc([[], []]))

    assert plan.use_whole_page is True
    assert plan.regions_per_page == [[], []]


def test_ppt_pdf_plan_attempts_split_for_short_text_pdf():
    pages = [[
        _Block("1. 다음 중 옳은 것은? ① ㄱ ② ㄴ", 40, 100, 560, 130),
    ]]

    plan = _build_pdf_question_plan(_FakeDoc(pages))

    assert plan.use_whole_page is False
    assert [r.number for r in plan.regions_per_page[0]] == [1]


def test_ppt_pdf_plan_applies_cross_page_anchor_validation():
    pages = [
        [
            _Block(_question_text(2), 40, 100, 560, 130),
            _Block(_question_text(3), 40, 360, 560, 390),
        ],
        [
            _Block(_question_text(3), 40, 100, 560, 130),
            _Block(_question_text(4), 40, 360, 560, 390),
        ],
    ]

    plan = _build_pdf_question_plan(_FakeDoc(pages))

    assert plan.use_whole_page is False
    assert [r.number for r in plan.regions_per_page[0]] == [2, 3]
    assert [r.number for r in plan.regions_per_page[1]] == [4]


def test_ppt_pdf_plan_prefers_marginal_anchors_for_workbook_docs():
    pages = []
    for idx in range(5):
        y = 90 + idx
        pages.append([
            _Block("1.", 24, y, 36, y + 15),
            _Block(
                "다음 자료를 읽고 물음에 답하시오. ① 보기 하나 ② 보기 둘 ③ 보기 셋",
                60,
                y,
                560,
                y + 15,
            ),
            _Block("1. 하위 항목입니다.", 90, y + 60, 560, y + 75),
            _Block("2. 하위 항목입니다.", 90, y + 85, 560, y + 100),
        ])

    plan = _build_pdf_question_plan(_FakeDoc(pages))

    assert plan.use_whole_page is False
    assert plan.workbook_doc is True
    assert [[r.number for r in page] for page in plan.regions_per_page] == [[1], [1], [1], [1], [1]]


def test_ppt_pdf_plan_preserves_short_page_restart_workbook_docs():
    pages = []
    for idx in range(3):
        y = 90 + idx
        pages.append([
            _Block("1.", 24, y, 36, y + 15),
            _Block(
                "다음 자료를 읽고 답하시오. ① 보기 하나 ② 보기 둘",
                60,
                y,
                560,
                y + 15,
            ),
            _Block("2.", 24, y + 230, 36, y + 245),
            _Block(
                "다음 설명으로 옳은 것은? ① 보기 하나 ② 보기 둘",
                60,
                y + 230,
                560,
                y + 245,
            ),
        ])

    plan = _build_pdf_question_plan(_FakeDoc(pages))

    assert plan.use_whole_page is False
    assert plan.workbook_doc is True
    assert [[r.number for r in page] for page in plan.regions_per_page] == [[1, 2], [1, 2], [1, 2]]


def test_ppt_pdf_plan_does_not_treat_short_exam_false_low_anchor_as_restart():
    pages = [
        [
            _Block(_question_text(1), 40, 90, 560, 110),
            _Block(_question_text(2), 40, 230, 560, 250),
            _Block(_question_text(3), 40, 370, 560, 390),
        ],
        [
            _Block("1. 그림 1은 보기 자료의 번호입니다.", 60, 90, 560, 110),
            _Block(_question_text(4), 40, 230, 560, 250),
            _Block(_question_text(5), 40, 370, 560, 390),
        ],
    ]

    plan = _build_pdf_question_plan(_FakeDoc(pages))

    assert plan.use_whole_page is False
    assert plan.workbook_doc is False
    assert [[r.number for r in page] for page in plan.regions_per_page] == [[1, 2, 3], [4, 5]]


def test_ppt_pdf_image_segmentation_fallback_adds_question_slides(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    from academy.adapters.ai.detection import segment_dispatcher

    image_path = tmp_path / "page.png"
    image = Image.new("RGB", (320, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 30, 130, 110), fill="black")
    draw.rectangle((170, 30, 290, 110), fill="black")
    image.save(image_path)

    cleanup_calls: list[list[str]] = []

    def fake_segment_questions_multipage(_pdf_path):
        return {
            "pages": [{
                "page_index": 0,
                "image_path": str(image_path),
                "boxes": [(10, 20, 140, 110), (160, 20, 140, 110)],
            }],
            "total_boxes": 2,
            "is_pdf": True,
            "tmp_dirs": ["seg-tmp"],
        }

    def fake_cleanup_pdf_seg_tmp_dirs(paths):
        cleanup_calls.append(paths)

    class _Composer:
        def __init__(self):
            self.slides: list[bytes] = []

        def add_slide(self, image_bytes: bytes):
            self.slides.append(image_bytes)

    monkeypatch.setattr(
        segment_dispatcher,
        "segment_questions_multipage",
        fake_segment_questions_multipage,
    )
    monkeypatch.setattr(
        segment_dispatcher,
        "cleanup_pdf_seg_tmp_dirs",
        fake_cleanup_pdf_seg_tmp_dirs,
    )
    composer = _Composer()

    added = _add_segmented_pdf_slides_to_composer(
        "source.pdf",
        composer=composer,
        apply_user_settings=lambda b: b,
    )

    assert added == 2
    assert len(composer.slides) == 2
    assert all(slide.startswith(b"\x89PNG") for slide in composer.slides)
    assert cleanup_calls == [["seg-tmp"]]

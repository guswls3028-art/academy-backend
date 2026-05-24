from __future__ import annotations

from dataclasses import dataclass

from academy.application.use_cases.tools.generate_ppt import _build_pdf_question_plan


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

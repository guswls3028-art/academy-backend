from __future__ import annotations

import binascii
import struct
import tempfile
import zlib
from pathlib import Path
from unittest import TestCase

from academy.application.use_cases.ai.pipelines.matchup_pipeline import (
    _boxes_to_questions,
    _filter_questions_by_min_area,
)
from academy.domain.tools.paper_type import PaperType, PaperTypeResult
from academy.domain.tools.question_splitter import TextBlock, split_questions


def _paper_type(*, dual: bool) -> PaperTypeResult:
    return PaperTypeResult(
        paper_type=PaperType.CLEAN_PDF_DUAL if dual else PaperType.CLEAN_PDF_SINGLE,
        confidence=0.9,
        is_dual_column=dual,
        is_quadrant=False,
        is_handwriting_present=False,
        has_embedded_text=True,
        debug={"is_dual_text": dual},
    )


def _write_blank_png(path: Path, *, width: int, height: int) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)
        )

    row = b"\x00" + (b"\x00\x00\x00" * width)
    raw = row * height
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, level=1))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(payload)


class CleanPdfQuestionSplitterV2Tests(TestCase):
    def test_two_column_flow_uses_same_column_next_anchor(self):
        page_width = 612.0
        page_height = 864.0
        blocks = [
            TextBlock("Step 1. 개념완성", 82.6, 136.1, 161.0, 146.0),
            TextBlock("3. 그림은 물질을 구성하는 입자들을 나타낸 것이다. 3)", 303.2, 165.0, 494.7, 179.8),
            TextBlock("1. 그림은 빅뱅 우주론을 모형을 나타낸 것이다.1)", 42.5, 165.8, 216.2, 180.5),
            TextBlock("A와 B에 해당하는 기본 입자는 무엇인지 쓰시오.", 303.2, 288.9, 454.6, 297.4),
            TextBlock("이 우주론에서 주장하는 내용으로 옳은 것은 ○표, 옳지 않은 것은 ×표 하시오.", 42.5, 322.0, 280.7, 343.1),
            TextBlock("(1) 우주의 크기는 무한하다. ( )", 42.5, 357.9, 146.0, 365.9),
            TextBlock("(2) 우주의 나이는 유한하다. ( )", 42.5, 384.8, 146.0, 392.8),
            TextBlock("(3) 우주의 온도는 계속 감소한다. ( )", 42.5, 411.6, 161.0, 419.7),
            TextBlock("(4) 우주는 항상 일정한 밀도를 유지한다. ( )", 42.5, 438.5, 181.6, 446.6),
            TextBlock("4. 다음 (가)~(다)는 초기 우주에서 헬륨 원자핵이 생성되는 과정을", 303.2, 484.0, 543.8, 498.8),
            TextBlock("2. 빅뱅 우주론의 증거에 대한 설명으로 옳은 것은 ○표, 옳지 않은", 42.5, 484.4, 282.9, 499.1),
            TextBlock("순서대로 설명한 것이다. ( ) 안에 들어갈 알맞은 말을 쓰시오.4)", 324.7, 507.0, 541.4, 529.4),
            TextBlock("것은 ×표 하시오.2)", 64.0, 507.1, 128.4, 516.2),
            TextBlock("(가) 빅뱅이 일어난 직후의 초기 우주에서 ( ㉠ ), 전자와 같은 기본 입자가 생겨", 308.9, 539.8, 535.6, 547.6),
            TextBlock("(1) 우주는 현재 정지한 상태이며, 시간에 따라 변하지 않는다. ( )", 42.5, 534.2, 240.1, 542.2),
            TextBlock("(나) ( ㉠ )이/가 결합하여 ( ㉡ )과/와 중성자가 생성되었다.", 308.9, 565.3, 484.1, 573.1),
            TextBlock("(2) 과거의 우주의 온도가 매우 높았음을 알려주는 빛의 흔적이 관측되었다. ( )", 42.5, 561.0, 276.2, 569.1),
        ]

        regions = split_questions(
            blocks,
            page_width=page_width,
            page_height=page_height,
            page_index=12,
            paper_type=_paper_type(dual=True),
        )
        by_number = {region.number: region for region in regions}

        self.assertEqual([region.number for region in regions], [1, 2, 3, 4])
        self.assertLess(by_number[1].bbox[2], page_width * 0.52)
        self.assertGreater(by_number[3].bbox[0], page_width * 0.45)
        self.assertGreater(by_number[3].bbox[2], page_width * 0.95)
        self.assertLessEqual(by_number[1].bbox[3], by_number[2].bbox[1] + 1.0)
        self.assertLessEqual(by_number[3].bbox[3], by_number[4].bbox[1] + 1.0)

        workbook_pass_regions = split_questions(
            blocks,
            page_width=page_width,
            page_height=page_height,
            page_index=12,
            paper_type=_paper_type(dual=True),
            prefer_marginal=True,
        )
        workbook_by_number = {region.number: region for region in workbook_pass_regions}
        self.assertGreater(workbook_by_number[3].bbox[2], page_width * 0.95)

    def test_full_width_visual_questions_are_not_half_cropped(self):
        page_width = 612.0
        page_height = 864.0
        blocks = [
            TextBlock("[2021년 고1 6월 학평 통합과학 17번]", 70.9, 134.8, 224.6, 143.6),
            TextBlock("35. 그림은 중심부의 핵융합 반응이 끝난 두 별 (가)와 (나)의 내부 구조를 나타낸 것이다.35)", 70.9, 154.0, 391.6, 168.8),
            TextBlock("(가) (나)", 76.6, 312.8, 388.5, 320.8),
            TextBlock("이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?", 70.9, 336.6, 319.3, 345.6),
            TextBlock("ㄱ. 질량은 (가)가 (나)보다 크다.", 79.4, 373.7, 186.7, 381.8),
            TextBlock("① ㄱ ② ㄷ ③ ㄱ, ㄴ ④ ㄴ, ㄷ ⑤ ㄱ, ㄴ, ㄷ", 76.1, 423.4, 465.5, 431.4),
            TextBlock("[2021년 고1 11월 학평 통합과학 8번]", 70.9, 444.5, 224.6, 453.3),
            TextBlock("36. 그림 (가)는 어느 별의 진화 과정에서 중심부의 핵융합 반응이 끝난 직후 별의 내부 구조를, (나)는 전자 배치 모형을 나타낸 것이다.36)", 70.9, 463.7, 535.3, 478.5),
            TextBlock("(가) (나)", 116.6, 626.0, 368.5, 634.0),
            TextBlock("이에 대한 설명으로 옳은 것만을 <보기>에서 있는 대로 고른 것은?", 70.9, 649.9, 319.3, 658.9),
        ]

        regions = split_questions(
            blocks,
            page_width=page_width,
            page_height=page_height,
            page_index=37,
            paper_type=_paper_type(dual=True),
        )

        self.assertEqual([region.number for region in regions], [35, 36])
        for region in regions:
            width_ratio = (region.bbox[2] - region.bbox[0]) / page_width
            self.assertGreaterEqual(width_ratio, 0.65)
            self.assertIn("flow_full", region.semantic_flags)

    def test_source_prefixed_question_in_same_block_is_detected(self):
        page_width = 612.0
        page_height = 864.0
        blocks = [
            TextBlock("[2019년 고2 3월 학평 물리학Ⅰ 2번]", 70.9, 134.8, 219.2, 143.6),
            TextBlock("51. 다음은 빅뱅 이후 (가)~(라)의 시기를 거쳐 입자가 생성된 과정을 나타낸 것이다. 51)", 70.9, 154.0, 377.9, 168.8),
            TextBlock("이에 대한 옳은 설명만을 <보기>에서 있는 대로 고른 것은?", 70.9, 251.3, 291.3, 260.3),
            TextBlock("① ㄱ ② ㄴ ③ ㄱ, ㄷ ④ ㄴ, ㄷ ⑤ ㄱ, ㄴ, ㄷ", 76.1, 354.2, 465.5, 362.2),
            TextBlock("[2019년 고2 3월 학평 물리학Ⅰ 18번] 52. 그림은 태양에서 수소(H) 원자핵 4개가 융합하여 헬륨(He) 원자핵 1개가 되는 반응이 일어날 때 에너지가 발생하는 것을 모식적으로 나타낸 것이다.", 70.9, 431.7, 533.0, 460.6),
            TextBlock("로 나타낸 것이다. 수소 원자핵 1개와 헬륨 원자핵의 질량은 각각 m, M이다. 52)", 101.5, 468.7, 370.5, 477.7),
            TextBlock("이에 대한 옳은 설명만을 <보기>에서 있는 대로 고른 것은?", 70.9, 574.7, 291.3, 583.7),
        ]

        regions = split_questions(
            blocks,
            page_width=page_width,
            page_height=page_height,
            page_index=45,
            paper_type=_paper_type(dual=True),
        )

        self.assertEqual([region.number for region in regions], [51, 52])
        self.assertIn("flow_full", regions[1].semantic_flags)

    def test_answer_explanation_page_is_not_extracted_as_question(self):
        page_width = 612.0
        page_height = 864.0
        blocks = [
            TextBlock("문제 해설", 49.9, 125.3, 80.3, 133.4),
            TextBlock("(1)", 42.5, 141.3, 54.3, 149.3),
            TextBlock("헬륨 핵융합 반응으로 만들어지는 A는 탄소이며, 규소 핵융합 반응으로", 42.5, 154.0, 280.1, 162.0),
            TextBlock("(2) 별에서는 가장 먼저 수소 핵융합 반응이 일어나고, 그 결과 헬륨이", 42.5, 192.4, 280.0, 200.4),
            TextBlock("만들어진다. 중심부에서 수소 핵융합 반응이 끝나면 핵융합 반응이 멈", 42.5, 205.2, 277.9, 213.3),
            TextBlock("리, 우라늄 등의 원소가 만들어진다. 83)", 42.5, 320.4, 166.1, 328.5),
        ]

        regions = split_questions(
            blocks,
            page_width=page_width,
            page_height=page_height,
            page_index=76,
            paper_type=_paper_type(dual=False),
        )

        self.assertEqual(regions, [])


class CleanPdfQuestionPipelineV2Tests(TestCase):
    def test_boxes_to_questions_propagates_clean_pdf_v2_flags(self):
        questions = _boxes_to_questions([
            {
                "page_index": 18,
                "image_path": "page_018.png",
                "boxes": [(97, 326, 596, 131)],
                "numbers": [13],
                "bbox_meta": [{"semantic_flags": ["clean_pdf_v2", "flow_left"]}],
                "paper_type": "clean_pdf_dual",
            }
        ])

        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["number"], 13)
        self.assertEqual(
            questions[0]["meta_extra"]["segmentation_flags"],
            ["clean_pdf_v2", "flow_left"],
        )

    def test_min_area_filter_keeps_short_clean_pdf_v2_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "page.png"
            _write_blank_png(image_path, width=2000, height=2000)
            questions = [
                {
                    "number": 13,
                    "image_path": str(image_path),
                    "bbox": [97, 326, 596, 131],
                    "meta_extra": {
                        "number_source": "segment",
                        "segmentation_flags": ["clean_pdf_v2"],
                    },
                },
                {
                    "number": 99,
                    "image_path": str(image_path),
                    "bbox": [97, 326, 100, 100],
                    "meta_extra": {"number_source": "segment"},
                },
            ]

            kept = _filter_questions_by_min_area(
                questions,
                min_ratio_raw="0.02",
                document_id=303,
            )

        self.assertEqual([q["number"] for q in kept], [13])

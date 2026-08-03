import struct
import zlib

from io import BytesIO
from PIL import Image

from academy.adapters.tools.hwp_endnote_images import (
    _collect_endnote_picture_ids,
    _load_picture,
)
from academy.application.use_cases.ai.pipelines.hwp_question_pipeline import (
    merge_paired_teacher_explanations,
)


def test_collects_picture_ids_inside_numbered_endnotes():
    endnote = b"  ne" + struct.pack("<I", 3)
    picture_a = bytearray(73)
    struct.pack_into("<H", picture_a, 71, 10)
    picture_b = bytearray(73)
    struct.pack_into("<H", picture_b, 71, 11)
    records = [
        (71, 2, endnote),
        (85, 3, bytes(picture_a)),
        (85, 3, bytes(picture_b)),
        (71, 2, b"xxxx" + struct.pack("<I", 1)),
    ]
    assert _collect_endnote_picture_ids(records) == [(3, [10, 11])]


def test_ignores_picture_records_outside_endnotes():
    picture = bytearray(73)
    struct.pack_into("<H", picture, 71, 7)
    assert _collect_endnote_picture_ids([(85, 3, bytes(picture))]) == []


def test_loads_raw_deflate_compressed_hwp_bitmap():
    source = BytesIO()
    Image.new("RGB", (32, 24), "white").save(source, format="BMP")
    compressor = zlib.compressobj(level=6, wbits=-15)
    compressed = compressor.compress(source.getvalue()) + compressor.flush()

    image = _load_picture(compressed)

    assert image is not None
    assert image.size == (32, 24)
    image.close()


def test_paired_teacher_hwp_matches_clean_problem_source_numbers_only():
    result = merge_paired_teacher_explanations(
        primary_result={
            "questions": [
                {"number": 1, "original_number": 1},
                {"number": 8, "original_number": 3},
            ],
            "explanations": [{"question_number": 99, "text": "PDF 해설"}],
        },
        teacher_explanations=[
            {"question_number": 1, "image_key": "q1.png"},
            {"question_number": 3, "image_key": "q3.png"},
            {"question_number": 7, "image_key": "q7.png"},
        ],
    )

    assert [item["question_number"] for item in result["explanations"]] == [1, 3]
    assert result["teacher_explanation_count"] == 2
    assert result["unmatched_teacher_explanation_numbers"] == [7]
    assert result["explanation_source_mode"] == "paired_teacher_hwp"

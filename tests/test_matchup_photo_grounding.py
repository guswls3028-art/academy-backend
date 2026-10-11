"""Photo detection preserves observed content and rejects partial page success."""
import copy
import io
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from academy.adapters.ai.detection import vlm_fallback
from academy.adapters.ai.detection.photo_grounding import (
    grounded_photo_regions, photo_grounding_prompt, photo_ocr_blocks,
)


def example():
    blocks = [
        SimpleNamespace(text="12. 다음 자료를 보고 알맞은 답을 고르시오.", x0=110, y0=100, x1=700, y1=130),
        SimpleNamespace(text="표와 그림의 설명을 모두 포함해야 합니다.", x0=100, y0=190, x1=790, y1=460),
        SimpleNamespace(text="① 가 ② 나 ③ 다 ④ 라 ⑤ 마", x0=100, y0=500, x1=805, y1=530),
        SimpleNamespace(text="13. 아래 조건에 맞는 답을 고르시오.", x0=110, y0=560, x1=700, y1=590),
        SimpleNamespace(text="두 번째 문항의 모든 선택지를 보존합니다.", x0=105, y0=700, x1=820, y1=750),
    ]
    data = {"confidence": .95, "problems": [
        {"number": 12, "anchor_id": 0, "shared_with": []},
        {"number": 13, "anchor_id": 3, "shared_with": []},
    ]}
    return blocks, data


def test_source_coordinates_keep_right_edge_choices_and_next_question_separate():
    raw, data = example()
    blocks = photo_ocr_blocks(raw, 1920, 2560)
    regions = grounded_photo_regions(data, blocks, 1920, 2560)
    first, second = regions
    x, y, w, h = first["bbox"]
    assert x <= 100 and y <= 100 and x + w >= 805 and y + h >= 530
    assert y + h < second["bbox"][1]
    assert [r["number"] for r in regions] == [12, 13]
    assert '"box_2d":[39,57,51,365]' in photo_grounding_prompt(blocks, 1920, 2560)


def test_occluded_number_gets_left_room_without_crossing_neighbor_text():
    raw, data = example()
    raw[0].text = "다음 자료를 보고 알맞은 답을 고르시오."
    raw[0].x0 = raw[1].x0 = raw[2].x0 = 150
    region = grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)[0]
    assert region["bbox"][0] <= 110


def test_shared_stem_preserves_both_numbers_and_identical_complete_crop():
    raw, data = example()
    for p in data["problems"]:
        p["shared_with"] = [13 if p["number"] == 12 else 12]
    regions = grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)
    assert regions[0]["bbox"] == regions[1]["bbox"]
    assert regions[0]["shared_with"] == [13]


@pytest.mark.parametrize("mutation", [
    lambda d: d["problems"].pop(),
    lambda d: d["problems"].pop(0),
    lambda d: d["problems"][0].update(number=True),
    lambda d: d["problems"][0].update(number=13),
    lambda d: d["problems"][0].update(anchor_id=999),
    lambda d: d["problems"][0].update(anchor_id=False),
    lambda d: d["problems"][0].update(anchor_id=3),
    lambda d: d["problems"][0].update(context_id=999),
    lambda d: d["problems"][0].update(shared_with=[13]),
    lambda d: d["problems"][0].update(shared_with=[99]),
])
def test_partial_or_contradictory_assignment_is_not_a_successful_shorter_page(mutation):
    raw, data = example()
    # The omitted choices must be substantial prose for the coverage oracle.
    raw[2].text = "각 선택지의 설명이 잘리지 않고 모두 남아야 합니다."
    mutation(data)
    with pytest.raises(ValueError):
        grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)


def test_header_cannot_be_used_as_a_question_anchor():
    raw, data = example()
    raw[0].text = "선택형 문항수 32"
    with pytest.raises(ValueError, match="header"):
        grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)


def test_continuation_of_a_long_question_cannot_become_a_new_question():
    raw, data = example()
    raw[3].text = "이에 대한 설명으로 옳은 것만 모두 고르시오."
    with pytest.raises(ValueError, match="continuation"):
        grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)


def test_real_numbered_body_can_discuss_an_academic_year():
    raw, data = example()
    raw[0].text = "12. 다음 학년도 자료에 대한 설명으로 옳은 것은?"
    assert len(grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)) == 2


def test_model_only_returns_starts_server_includes_table_choices_and_inner_steps():
    raw, data = example()
    raw[1].text = "1. 실험 과정의 첫 번째 단계에 대한 내용입니다."
    regions = grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)
    assert regions[0]["ids"] == {0, 1, 2}
    assert regions[1]["ids"] == {3, 4}
    assert "content_ids" not in photo_grounding_prompt(photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)


@pytest.mark.parametrize("explicit_context", [True, False])
def test_shared_context_above_first_number_is_preserved(explicit_context):
    raw, data = example()
    raw.append(SimpleNamespace(text="[12~13] 다음 공통 자료를 보고 답하시오.", x0=105, y0=20, x1=790, y1=60))
    for p in data["problems"]:
        p["shared_with"] = [25 - p["number"]]
        if explicit_context:
            p["context_id"] = 5
    regions = grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)
    assert regions[0]["bbox"] == regions[1]["bbox"]
    assert regions[0]["bbox"][1] <= 20
    assert regions[0]["ids"] == set(range(6))


def test_slanted_first_line_split_by_ocr_is_kept_whole():
    raw, data = example()
    raw[0].x1 = 350
    raw.append(SimpleNamespace(text="이어진 첫 문장 전체가 잘리지 않아야 합니다.", x0=355, y0=75, x1=790, y1=115))
    first = grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)[0]
    assert first["bbox"][1] <= 75 and 5 in first["ids"]


def test_sloping_columns_keep_far_right_choices_in_the_left_question():
    raw, data = example()
    raw[2].x0, raw[2].x1 = 820, 870
    raw[4].x0, raw[4].x1 = 105, 870
    raw.extend([
        SimpleNamespace(text="14. 다음 오른쪽 첫 문항에 답하시오.", x0=850, y0=100, x1=1300, y1=130),
        SimpleNamespace(text="15. 다음 오른쪽 둘째 문항에 답하시오.", x0=950, y0=560, x1=1400, y1=590),
    ])
    data["problems"].extend([{"number":14,"anchor_id":5}, {"number":15,"anchor_id":6}])
    regions = grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)
    assert 2 in regions[0]["ids"] and 2 not in regions[2]["ids"]
    assert 4 in regions[1]["ids"] and 4 not in regions[3]["ids"]


def test_shared_group_cannot_swallow_an_intervening_question():
    raw, data = example()
    raw.append(SimpleNamespace(text="14. 마지막 독립 문항의 설명입니다.", x0=110, y0=900, x1=790, y1=930))
    data["problems"][0]["shared_with"] = [14]
    data["problems"].append({"number":14,"anchor_id":5,"shared_with":[12]})
    with pytest.raises(ValueError, match="Interleaved"):
        grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)


def test_split_footer_does_not_extend_last_question_to_page_bottom():
    raw, data = example()
    raw.extend([
        SimpleNamespace(text="고등학교의 실험 조건을 비교하는 본문입니다.", x0=110, y0=800, x1=750, y1=840),
        SimpleNamespace(text="숙명 여자 고등 학", x0=400, y0=2420, x1=800, y1=2470),
        SimpleNamespace(text="다음 면 에 계", x0=650, y0=2420, x1=800, y1=2470),
    ])
    last = grounded_photo_regions(data, photo_ocr_blocks(raw, 1920, 2560), 1920, 2560)[-1]
    assert 5 in last["ids"] and not {6, 7} & last["ids"]
    assert last["bbox"][1] + last["bbox"][3] < 900


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), 3000])
def test_invalid_observation_does_not_invent_a_crop(coordinate):
    raw, _ = example()
    raw[0].x0 = coordinate
    with pytest.raises(ValueError):
        photo_ocr_blocks(raw, 1920, 2560)


def test_photo_adapter_uses_observed_pixels_and_bounded_thinking(monkeypatch, tmp_path):
    raw, data = example()
    path = tmp_path / "photo.png"
    Image.new("RGB", (1920, 2560), "white").save(path)
    monkeypatch.setitem(sys.modules, "academy.adapters.ai.ocr.google",
                        SimpleNamespace(google_ocr_blocks=lambda _: raw))
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        return copy.deepcopy(data)

    monkeypatch.setattr(vlm_fallback, "_gemini_request", provider)
    result = vlm_fallback.GeminiVLMVisionAdapter().detect_problems(
        image_path=str(path), page_meta={"paper_type": "student_answer_photo", "tenant_id": 17, "document_id": 23})
    assert [p.number for p in result.problems] == [12, 13]
    assert all(p.ocr_grounded for p in result.problems)
    assert result.debug["coordinate_system"] == "ocr_source_pixels"
    assert calls[0]["thinking_budget"] == 2048
    assert calls[0]["tenant_id"] == 17 and calls[0]["document_id"] == 23


@pytest.mark.parametrize("finish,text", [
    ("MAX_TOKENS", '{"problems":[{"number":12}]}'),
    ("MAX_TOKENS", '{"problems":[{"number":12},{"number":13'),
    ("SAFETY", '{"problems":[{"number":12}]}'),
    ("", '{"problems":[{"number":12}]}'),
    ("STOP", '{"problems":[{"number":12},{"number":13'),
])
def test_provider_completion_and_full_json_are_required(monkeypatch, finish, text):
    import requests
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic")
    response = MagicMock(status_code=200)
    response.json.return_value = {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]}
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError):
        vlm_fallback._gemini_request(model="gemini-2.5-flash", parts=[{"text": "synthetic"}])


def test_crop_writer_keeps_exact_grounded_bounds_without_extra_percentage_padding(monkeypatch, tmp_path):
    from academy.application.use_cases.ai.pipelines import matchup_pipeline as pipeline
    import apps.infrastructure.storage.r2 as r2
    path = tmp_path / "photo.png"
    Image.new("RGB", (1000, 1000), "white").save(path)
    saved = []
    monkeypatch.setenv("MATCHUP_OVER_CROP_PADDING", "1")
    monkeypatch.setattr(r2, "upload_fileobj_to_r2_storage", lambda **kwargs: saved.append(kwargs["fileobj"].read()))
    q = {"number": 12, "page_index": 0, "image_path": str(path), "bbox": [100, 100, 500, 300],
         "meta_extra": {"photo_ocr_grounded": True}}
    pipeline._upload_cropped_images([q], "17", "23", "grounded-test-job")
    assert len(saved) == 1
    with Image.open(io.BytesIO(saved[0])) as crop:
        assert crop.size == (500, 300)
    Path(q["cropped_image_path"]).unlink()

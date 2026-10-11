"""Gemini boxes use 0..1000 yxyx, independently of transport image resizing."""
import base64
import io

import pytest
from PIL import Image

from academy.adapters.ai.detection import vlm_fallback


@pytest.mark.parametrize("size", [(500, 1000), (1920, 2560), (2560, 1920)])
def test_normalized_crop_maps_to_full_original_image(monkeypatch, tmp_path, size):
    path = tmp_path / "page.png"
    Image.new("RGB", size, "white").save(path)
    calls = []

    def detect(**kwargs):
        calls.append(kwargs)
        return {"confidence": .95, "page_role": "problem", "problems": [
            {"number": 12, "box_2d": [100, 200, 700, 900], "confidence": .95},
        ]}

    monkeypatch.setattr(vlm_fallback, "_gemini_request", detect)
    result = vlm_fallback.GeminiVLMVisionAdapter().detect_problems(
        image_path=str(path), page_meta={"tenant_id": 17, "document_id": 23})
    assert len(result.problems) == 1
    w, h = size
    assert result.problems[0].bbox == (
        round(w * .2), round(h * .1), round(w * .9) - round(w * .2),
        round(h * .7) - round(h * .1))
    assert result.problems[0].number == 12
    assert calls[0]["tenant_id"] == 17 and calls[0]["document_id"] == 23
    sent = Image.open(io.BytesIO(base64.b64decode(calls[0]["parts"][1]["inline_data"]["data"])))
    assert max(sent.size) <= 1600
    sent.close()


@pytest.mark.parametrize("bad", [
    {"number": 1, "box_2d": [-1, 0, 500, 500]},
    {"number": 1, "box_2d": [0, 0, 1001, 500]},
    {"number": 1, "box_2d": [500, 0, 100, 500]},
    {"number": 1, "box_2d": [100, 0, 100, 500]},
    {"number": 1, "box_2d": [0, 0, 100]},
    {"number": 1, "box_2d": [False, 0, 500, 500]},
    {"number": 1, "box_2d": ["0", 0, 500, 500]},
    {"number": 0, "box_2d": [100, 100, 500, 500]},
    {"number": -1, "box_2d": [100, 100, 500, 500]},
    {"box_2d": [100, 100, 500, 500]},
    {"number": 2, "bbox": [10, 10, 500, 500]},
    None,
])
def test_invalid_provider_coordinates_cannot_become_question_crops(monkeypatch, tmp_path, bad):
    path = tmp_path / "page.png"
    Image.new("RGB", (1000, 800), "white").save(path)
    monkeypatch.setattr(vlm_fallback, "_gemini_request", lambda **kwargs: {
        "confidence": .95, "page_role": "problem", "problems": [
            bad, {"number": 7, "box_2d": [100, 100, 900, 900], "shared_with": [8]},
        ]})
    result = vlm_fallback.GeminiVLMVisionAdapter().detect_problems(image_path=str(path))
    assert [p.number for p in result.problems] == [7]
    assert result.problems[0].bbox == (100, 80, 800, 640)
    assert result.problems[0].shared_with == [8]

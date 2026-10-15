from __future__ import annotations

import io
import json
import math
from unittest.mock import patch

import pytest
from PIL import Image, ImageChops

from apps.domains.tools.problem_studio.conversion_ai import redraw_figure, render_figure


def test_text_anchor_pair_renders_like_existing_four_coordinate_label():
    pair = {"type": "text", "points": [100, 200], "text": "A 가"}
    legacy = {**pair, "points": [100, 200, 0, 0]}
    rendered = render_figure({"shapes": [pair]})
    assert rendered == render_figure({"shapes": [legacy]})
    with Image.open(io.BytesIO(rendered)) as image:
        assert image.format == "PNG"
        assert image.size == (1000, 1000)
        bounds = ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()
        assert bounds is not None
        assert bounds[0] >= 100 and bounds[1] >= 200


@pytest.mark.parametrize("kind", ["line", "rectangle", "ellipse"])
def test_geometric_shapes_still_require_four_coordinates(kind):
    with pytest.raises(ValueError, match="그림 좌표"):
        render_figure({"shapes": [{"type": kind, "points": [100, 200]}]})
    assert render_figure({"shapes": [{"type": kind, "points": [100, 200, 300, 400]}]}).startswith(b"\x89PNG")


@pytest.mark.parametrize("points", [
    [], [100], [100, 200, 300], [100, 200, 300, 400, 500],
    [True, 200], ["100", 200], [math.nan, 200], [100, math.inf],
    [-1, 200], [100, 1001], [100, 200, math.inf, 0], [100, 200, 0, -1],
])
def test_text_anchor_rejects_invalid_or_unbounded_coordinates(points):
    with pytest.raises(ValueError, match="그림 좌표"):
        render_figure({"shapes": [{"type": "text", "points": points, "text": "A"}]})


def test_figure_bounds_and_primitive_allowlist_remain_enforced():
    with pytest.raises(ValueError, match="지원하지 않는"):
        render_figure({"shapes": [{"type": "svg", "points": [0, 0, 1, 1]}]})
    with pytest.raises(ValueError, match="도형 정보"):
        render_figure({"shapes": [{"type": "line", "points": [0, 0, 1, 1]}] * 401})
    with pytest.raises(ValueError, match="도형 범위"):
        render_figure({"shapes": [{"type": "rectangle", "points": [300, 200, 100, 400]}]})
    with pytest.raises(ValueError, match="그림 글자"):
        render_figure({"shapes": [{"type": "text", "points": [100, 200], "text": "A" * 121}]})


@pytest.mark.parametrize("invalid", [
    json.JSONDecodeError("Extra data", "{} {}", 3),
    {"shapes": [{"type": "text", "points": [100, 1001], "text": "A"}]},
])
def test_redraw_repairs_invalid_output_once_before_returning_validated_png(invalid):
    valid = {"shapes": [{"type": "text", "points": [100, 200], "text": "A"}]}
    with patch("apps.domains.tools.problem_studio.conversion_ai._ask", side_effect=[invalid, valid]) as ask:
        assert redraw_figure(b"synthetic source", "image/png") == render_figure(valid)
    assert ask.call_count == 2
    assert all(call.kwargs["thorough"] for call in ask.call_args_list)


def test_redraw_rejects_repeated_invalid_output_without_unbounded_retry():
    invalid = {"shapes": [{"type": "line", "points": [100, 200]}]}
    with patch("apps.domains.tools.problem_studio.conversion_ai._ask", return_value=invalid) as ask:
        with pytest.raises(ValueError, match="그림 좌표"):
            redraw_figure(b"synthetic source", "image/png")
    assert ask.call_count == 2


def test_redraw_does_not_retry_provider_errors_or_valid_output():
    with patch("apps.domains.tools.problem_studio.conversion_ai._ask", side_effect=RuntimeError("provider unavailable")) as ask:
        with pytest.raises(RuntimeError, match="provider unavailable"):
            redraw_figure(b"synthetic source", "image/png")
    assert ask.call_count == 1
    valid = {"shapes": [{"type": "line", "points": [100, 200, 300, 400]}]}
    with patch("apps.domains.tools.problem_studio.conversion_ai._ask", return_value=valid) as ask:
        assert redraw_figure(b"synthetic source", "image/png").startswith(b"\x89PNG")
    assert ask.call_count == 1

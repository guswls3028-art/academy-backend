from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from academy.adapters.ai.omr.marker_detector import _count_significant_defects


@pytest.mark.parametrize(
    "defects",
    [
        np.array([[[0, 1, 2, 512]], [[1, 2, 3, 128]]], dtype=np.int32),
        np.array([[0, 1, 2, 512], [1, 2, 3, 128]], dtype=np.int32),
    ],
    ids=["opencv4-layout", "opencv5-layout"],
)
def test_convexity_defect_layouts_are_supported(defects: np.ndarray) -> None:
    contour = np.zeros((4, 1, 2), dtype=np.int32)
    hull_indices = np.arange(4, dtype=np.int32).reshape(-1, 1)

    with patch(
        "academy.adapters.ai.omr.marker_detector.cv2.convexityDefects",
        return_value=defects,
    ):
        count, max_depth = _count_significant_defects(
            contour,
            hull_indices,
            char_size=10.0,
            depth_ratio=0.1,
        )

    assert count == 1
    assert max_depth == 2.0

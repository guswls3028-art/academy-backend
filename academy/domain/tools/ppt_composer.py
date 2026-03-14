# PATH: academy/domain/tools/ppt_composer.py
# PPT composition — assembles slides from image data with configurable layout.
#
# Isolated domain service: uses pptx_writer adapter for python-pptx operations.

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Tuple


@dataclass
class PptConfig:
    """PPT generation configuration."""
    aspect_ratio: Literal["16:9", "4:3"] = "16:9"
    background: str = "black"  # "black", "white", "dark_gray", or "#RRGGBB"
    fit_mode: Literal["contain", "cover", "stretch"] = "contain"


class PptComposer:
    """Composes a PPT presentation from image data.

    Streams slides one at a time — does not accumulate all images in memory.
    Uses the pptx_writer adapter for python-pptx operations.
    """

    def __init__(self, config: Optional[PptConfig] = None):
        from academy.adapters.tools.pptx_writer import (
            create_presentation,
            SLIDE_DIMENSIONS,
        )

        self._config = config or PptConfig()
        dims = SLIDE_DIMENSIONS.get(
            self._config.aspect_ratio,
            SLIDE_DIMENSIONS["16:9"],
        )
        self._prs = create_presentation(dims[0], dims[1])
        self._slide_count = 0

    def add_slide(self, image_bytes: bytes) -> None:
        """Add a single slide with the given image.

        Args:
            image_bytes: PNG or JPEG image bytes.
        """
        from academy.adapters.tools.pptx_writer import add_slide

        add_slide(
            self._prs,
            image_bytes,
            background_color=self._config.background,
            fit_mode=self._config.fit_mode,
        )
        self._slide_count += 1

    @property
    def slide_count(self) -> int:
        return self._slide_count

    def finalize(self) -> bytes:
        """Finalize and return PPTX file bytes.

        Returns:
            PPTX file as bytes.

        Raises:
            ValueError: If no slides were added.
        """
        if self._slide_count == 0:
            raise ValueError("No slides added to presentation")

        from academy.adapters.tools.pptx_writer import save_to_bytes
        return save_to_bytes(self._prs)


def compose_ppt(
    slides_data: List[Tuple[bytes, int]],
    config: Optional[PptConfig] = None,
) -> bytes:
    """Compose a PPT from a list of (image_bytes, slide_number) tuples.

    Processes one slide at a time to minimize memory usage.

    Args:
        slides_data: List of (image_bytes, slide_number) tuples.
        config: PPT configuration.

    Returns:
        PPTX file bytes.
    """
    if not slides_data:
        raise ValueError("No slides data provided")

    composer = PptComposer(config)
    for image_bytes, _slide_num in slides_data:
        composer.add_slide(image_bytes)

    return composer.finalize()

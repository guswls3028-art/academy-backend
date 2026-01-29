# apps/worker/ai_worker/ai/omr/meta_px.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class PageScale:
    """
    Convert meta(mm) -> px.
    Boundary rule (fixed):
      - meta(mm) is assets single truth
      - px conversion is worker responsibility
      - aligned/warped image must represent the full page
    """
    sx: float
    sy: float
    img_w: int
    img_h: int

    def mm_to_px_point(self, x_mm: float, y_mm: float) -> Tuple[int, int]:
        x = int(round(float(x_mm) * self.sx))
        y = int(round(float(y_mm) * self.sy))
        x = _clamp(x, 0, self.img_w - 1)
        y = _clamp(y, 0, self.img_h - 1)
        return x, y

    def mm_to_px_len_x(self, v_mm: float) -> int:
        return max(1, int(round(float(v_mm) * self.sx)))

    def mm_to_px_len_y(self, v_mm: float) -> int:
        return max(1, int(round(float(v_mm) * self.sy)))


def build_page_scale_from_meta(
    *,
    meta: Dict[str, Any],
    image_size_px: Tuple[int, int],
) -> PageScale:
    """
    Build scaler from template meta.
    meta page size is mm. image_size_px is (width, height).
    """
    img_w, img_h = int(image_size_px[0]), int(image_size_px[1])

    page = meta.get("page") or {}
    size = page.get("size") or {}
    page_w_mm = float(size.get("width") or 0.0)
    page_h_mm = float(size.get("height") or 0.0)

    if img_w <= 0 or img_h <= 0:
        raise ValueError("invalid image_size_px")
    if page_w_mm <= 0.0 or page_h_mm <= 0.0:
        raise ValueError("invalid meta page size")

    sx = img_w / page_w_mm
    sy = img_h / page_h_mm
    return PageScale(sx=float(sx), sy=float(sy), img_w=img_w, img_h=img_h)

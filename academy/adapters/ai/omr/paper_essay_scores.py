"""Locate the printed bubble outlines on paper score grids after full-page alignment."""

from __future__ import annotations

import cv2
import numpy as np


def _outline_centers(gray, scale, bounds):
    """Collect oval centres in millimetres. Work only inside the requested print area."""
    left, top, right, bottom = bounds
    x0, y0 = scale.mm_to_px_point(left, top)
    x1, y1 = scale.mm_to_px_point(right, bottom)
    if x1 <= x0 or y1 <= y0:
        return np.empty((0, 2))
    density = 8
    crop = cv2.resize(
        gray[y0:y1, x0:x1],
        (
            max(2, round((x1 - x0) / scale.sx * density)),
            max(2, round((y1 - y0) / scale.sy * density)),
        ),
        interpolation=cv2.INTER_AREA,
    )
    ink = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 35, 12)
    contours, _ = cv2.findContours(ink, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    centers = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not (2.7 * density <= w <= 4.8 * density and 4.0 * density <= h <= 6.4 * density):
            continue
        if not 0.54 <= cv2.contourArea(contour) / (w * h) <= 0.90:
            continue
        centers.append((x0 / scale.sx + (x + w / 2) / density, y0 / scale.sy + (y + h / 2) / density))
    return np.asarray(centers, dtype=float).reshape(-1, 2)


def _registered_grid(gray, scale, choices):
    expected = np.asarray([(b["center"]["x"], b["center"]["y"]) for b in choices])
    lower, upper = expected.min(axis=0), expected.max(axis=0)
    detected = _outline_centers(gray, scale, (lower[0] - 12, lower[1] - 9, upper[0] + 12, upper[1] + 9))
    if len(detected) < 5 or len(detected) > 80:
        return None
    offsets = detected[:, None, :] - expected
    votes = offsets.reshape(-1, 2)
    votes = votes[(np.abs(votes[:, 0]) < 9) & (np.abs(votes[:, 1]) < 6)]
    if not len(votes):
        return None
    distances = np.sum((offsets[None, :, :, :] - votes[:, None, None, :]) ** 2, axis=-1)
    nearest = distances.min(axis=1)
    matches = nearest < 0.95**2
    counts = matches.sum(axis=1)
    # Prefer the tightest consensus; aliases one row or column away must lose.
    best = int(np.argmin((9 - counts) * 10 + np.minimum(nearest, 1).sum(axis=1)))
    used = matches[best]
    if counts[best] < 5 or len(set(np.where(used)[0] % 3)) < 3 or len(set(np.where(used)[0] // 3)) < 3:
        return None
    competing = np.linalg.norm(votes - votes[best], axis=1) > 2
    if np.any(counts[competing] >= counts[best]):
        return None
    nearest_ids = np.argmin(distances[best], axis=0)
    translation = np.median((detected[nearest_ids] - expected)[used], axis=0)
    centers = expected + translation
    centers[used] = detected[nearest_ids[used]]
    return centers


def _read_registered_row(gray, scale, choices, centers):
    marked = []
    for choice, (x, y) in zip(choices, centers):
        cx, cy = scale.mm_to_px_point(x, y)
        rx = max(2, round(choice["radius_x"] * scale.sx))
        ry = max(2, round(choice["radius_y"] * scale.sy))
        sample = gray[cy - ry : cy + ry + 1, cx - rx : cx + rx + 1]
        if sample.shape != (2 * ry + 1, 2 * rx + 1):
            return "ambiguous", None
        yy, xx = np.ogrid[-ry : ry + 1, -rx : rx + 1]
        core = sample[(xx / rx) ** 2 + (yy / ry) ** 2 <= 0.65**2]
        paper = float(np.percentile(sample, 90))
        if paper < 150:
            return "ambiguous", None
        dark = float(np.mean(core < paper - 55))
        if dark >= 0.70:
            marked.append(int(choice["label"]))
        elif dark >= 0.43 or np.mean(core < paper - 18) >= 0.58:
            return "ambiguous", None
    if not marked:
        return "blank", None
    if 0 in marked and len(marked) > 1:
        return "ambiguous", None
    return "ok", sum(marked)


def _objective_rows_match(gray, scale, meta):
    for column in meta.get("columns", []):
        questions = column["questions"]
        if not questions:
            continue
        first = questions[0]["choices"]
        xs = [p["center"]["x"] for p in first]
        ys = [q["choices"][0]["center"]["y"] for q in questions]
        # Widen the ROI for page curl while excluding the adjacent column.
        points = _outline_centers(gray, scale, (min(xs) - 5, 11, max(xs) + 7, 205))
        if not len(points):
            return False
        candidates = sorted(points[:, 1])
        groups = []
        for y in candidates:
            if not groups or y - groups[-1][0] > 1.8:
                groups.append([])
            groups[-1].append(y)
        rows = []
        for group in groups:
            near = points[np.abs(points[:, 1] - np.median(group)) < 1.0, 0]
            centers = []
            for x in sorted(near):
                if not centers or x - centers[-1] > 2.0:
                    centers.append(x)
            if len(centers) >= 3:
                rows.append(float(np.median(group)))
        # Handwriting may touch individual rows. Every observable row must map to
        # a distinct expected row, with evidence across almost the whole column.
        if len(rows) < np.ceil(len(ys) * 0.85):
            return False
        residuals = np.abs(np.subtract.outer(rows, ys))
        nearest = residuals.argmin(axis=1)
        if len(set(nearest)) != len(rows) or np.max(residuals.min(axis=1)) > 2.2:
            return False
    return True


def _header_is_unprinted(gray, scale, marks):
    if not marks:
        return False
    xs = [mark["x"] for mark in marks]
    ys = [mark["y"] for mark in marks]
    x0, y0 = scale.mm_to_px_point(min(xs) - 2, min(ys) - 2)
    x1, y1 = scale.mm_to_px_point(max(xs) + 2, max(ys) + 1)
    header = gray[y0:y1, x0:x1]
    return bool(header.size and np.percentile(header, 50) >= 155 and np.mean(header < 135) < 0.018)


def _has_extra_bubble_rows(gray, scale, meta, registered):
    """Exclude more MC columns or extra essay grids that share some expected rows."""
    last_column = meta["columns"][-1]["questions"]
    roi = last_column[0]["roi"]
    left = roi["x"] + roi["w"] + 2
    points = _outline_centers(gray, scale, (left, 12, scale.img_w / scale.sx, 205))
    if not len(points):
        return False
    if len(points) > 1000:
        return True
    expected = np.concatenate(registered)
    residual = points[:, None, :] - expected
    extra = points[np.min(np.sum(residual**2, axis=2), axis=1) > 1.3**2]
    for x, y in extra:
        row = extra[np.abs(extra[:, 1] - y) < 0.85, 0] - x
        for pitch in row[(row >= 4.2) & (row <= 7.0)]:
            if np.any(np.abs(row - 2 * pitch) < 0.75):
                return True
    return False


def detect_paper_essay_scores(*, meta, prepared_images, alignment_confirmed):
    """Read contiguous paper forms only when the blank header and physical grid agree."""
    if not alignment_confirmed or not meta.get("allow_unsigned_essay_score_grid") or not meta.get("columns"):
        return []
    scale, gray, _, _ = prepared_images
    if not _header_is_unprinted(gray, scale, meta.get("essay_score_layout_marks")):
        return []
    if not _objective_rows_match(gray, scale, meta):
        return []
    results = []
    registered = []
    for question in meta["essay_score_questions"]:
        choices = question["choices"]
        centers = _registered_grid(gray, scale, choices)
        if centers is None:
            # A partial shape match cannot identify an unsigned paper.
            return []
        registered.append(centers)
        status, score = _read_registered_row(gray, scale, choices, centers)
        results.append(
            {
                "version": "essay_score_marks.paper.v1",
                "question_number": question["question_number"],
                "status": status,
                "score": score,
            }
        )
    return results if registered and not _has_extra_bubble_rows(gray, scale, meta, registered) else []

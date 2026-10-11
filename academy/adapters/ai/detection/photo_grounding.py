"""Ground photo question crops in observed OCR blocks, not estimated VLM pixels."""
from __future__ import annotations

import json
import math
import re


PHOTO_GROUNDING_PROMPT = """한국어 시험지 사진에서 독립된 인쇄 문항만 분리하세요. 사진과 OCR 블록을 함께 읽으세요.
OCR id는 글자가 실제 존재하는 좌표입니다. OCR에서 손글씨 때문에 문항 번호가 사라지거나 잘못 읽혀도 사진에서 확인하세요. 본문의 실험 단계 1/2, 선택지, 손글씨를 독립 문항으로 만들지 마세요. 뒤에 깔린 다른 종이의 잘린 문제는 제외하세요.
여러 자리 문항 번호의 일부만 OCR된 조각을 전체 번호로 복사하지 마세요. 첫 줄의 인쇄 번호 전체를 사진에서 읽고, 이웃 문항과 번호가 크게 도약하면 사진을 다시 확인하세요. 이웃 번호는 재확인 단서이며 사진에 없는 번호를 만들어 채우는 근거가 아닙니다.
모든 OCR 조각을 문항에 배정하지 마세요. 문항의 시작점과 실제 공유 관계만 반환하면 서버가 같은 열의 다음 문항 앞까지 본문/그림/표/선택지를 포함합니다. 공유 자료 [7~8]처럼 실제로 자료를 공유하는 묶음은 각 문항에 상호 shared_with를 표시하고, 공통 자료가 시작하는 OCR id를 동일한 context_id로 반환하세요. 공유 자료가 없으면 context_id=null입니다.
문항 첫 줄을 담은 OCR 블록 id를 anchor_id에 반드시 반환하세요. 문항 번호가 가려져 첫 줄 일부만 읽혔으면 첫 줄 본문 블록을 사용합니다. 해당 문항이 새로 시작하는 인쇄 본문 줄이어야 하며, 다른 문항의 보기/후반부를 새로운 문항으로 만들지 마세요.
상단 학년/학교/시험명/문항수, 하단 저작권/페이지번호/다음 면에 계속, 손글씨 풀이/채점은 제외하세요. OCR 좌표는 [ymin,xmin,ymax,xmax] 0~1000입니다. 같은 열 안에서는 위에서 아래로, 왼쪽 열에서 오른쪽 열 순서로 읽습니다.
페이지 전체의 마지막 문항까지 빠짐없이 반환하세요. 문항이 없는 페이지는 problems=[]입니다.
JSON만 반환: {"confidence":0.0부터1.0,"problems":[{"number":정수,"anchor_id":정수,"shared_with":[정수,...],"context_id":정수또는null}]}.
OCR 블록:
"""


def photo_ocr_blocks(blocks, width: int, height: int) -> list[dict]:
    """Keep one stable ID per original OCR block; reject unusable observations."""
    if width <= 0 or height <= 0 or not blocks or len(blocks) > 500:
        raise ValueError("Photo OCR block count/dimensions unavailable")
    result = []
    for block in blocks:
        coords = [float(getattr(block, key)) for key in ("x0", "y0", "x1", "y1")]
        if not all(math.isfinite(v) for v in coords):
            raise ValueError("Non-finite photo OCR coordinate")
        x0, y0, x1, y1 = coords
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Empty photo OCR block")
        result.append({"text": str(block.text), "bbox": (x0, y0, x1, y1)})
    return result


def photo_grounding_prompt(blocks: list[dict], width: int, height: int) -> str:
    observations = []
    for index, block in enumerate(blocks):
        x0, y0, x1, y1 = block["bbox"]
        observations.append({"id": index, "text": block["text"], "box_2d": [
            round(y0 * 1000 / height), round(x0 * 1000 / width),
            round(y1 * 1000 / height), round(x1 * 1000 / width),
        ]})
    return PHOTO_GROUNDING_PROMPT + json.dumps(observations, ensure_ascii=False, separators=(",", ":"))


def _non_body(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if re.match(r"^\d{1,3}[.)]", compact):
        return False
    return any(word in compact for word in (
        "문항수", "선택형점수", "출제원안", "학년도", "과목명", "시험문제의저작권", "다음면에계속",
    )) or bool(re.match(r"^총\(?\d+\)?면중", compact))


def _covered(block: tuple, region: tuple) -> bool:
    x0, y0, x1, y1 = block
    a, b, c, d = region
    intersection = max(0, min(x1, c) - max(x0, a)) * max(0, min(y1, d) - max(y0, b))
    return intersection >= (x1 - x0) * (y1 - y0) * .90


def _footer(block: dict, height: int) -> bool:
    compact = re.sub(r"\s+", "", block["text"])
    return block["bbox"][1] > height * .8 and (
        any(word in compact for word in ("고등학", "다음면에계", "답안지의해당란", "기입했는지확인"))
        or bool(re.fullmatch(r"끝|다음[ㄱ-ㅎ]", compact)))


def _assign_photo_content(regions: list[dict], blocks: list[dict], width: int, height: int) -> None:
    """Partition observations at semantic starts, independently of model block lists."""
    columns = []
    for region in sorted(regions, key=lambda r: blocks[r["anchor_id"]]["bbox"][0]):
        x = blocks[region["anchor_id"]]["bbox"][0]
        if not columns or x - columns[-1][0] > width * .22:
            columns.append((x, []))
        columns[-1][1].append(region)
    by_number = {r["number"]: r for r in regions}
    first_number, last_number = min(by_number), max(by_number)

    def column_edge(index, y):
        # Perspective makes the gutter slope. Follow observed starts instead
        # of assigning the rightmost choices to the neighbouring column.
        points = sorted((blocks[r["anchor_id"]]["bbox"][1],
                         blocks[r["anchor_id"]]["bbox"][0]) for r in columns[index][1])
        x = points[0][1] if y <= points[0][0] else points[-1][1]
        for (y0, x0), (y1, x1) in zip(points, points[1:]):
            if y0 <= y <= y1 and y1 > y0:
                x = x0 + (x1 - x0) * (y - y0) / (y1 - y0)
                break
        return x - 8

    def in_column(index, box):
        x0, y0, x1, y1 = box
        y = (y0 + y1) / 2
        lower = columns[0][0] - width * .035 if index == 0 else column_edge(index, y)
        upper = column_edge(index + 1, y) if index + 1 < len(columns) else width
        return lower <= (x0 + x1) / 2 < upper

    for column_index, (left, members) in enumerate(columns):
        for block in blocks:
            printed = re.match(r"^\s*(\d{1,3})\s*[.)]\s*([가-힣])", block["text"])
            if (printed and int(printed[1]) not in by_number
                    and max(1, first_number - 3) <= int(printed[1]) <= last_number + 3
                    and abs(block["bbox"][0] - left) <= width * .05
                    and len(re.findall(r"[가-힣]", block["text"])) >= 8):
                raise ValueError("Photo grounding omitted a printed question")
        groups = {}
        for region in members:
            key = tuple(sorted({region["number"], *region["shared_with"]}))
            if any(by_number[n] not in members for n in key):
                raise ValueError("Shared photo material crosses columns")
            groups.setdefault(key, []).append(region)
        starts = []
        for key, group in groups.items():
            anchor_ids = {r["anchor_id"] for r in group}
            context_ids = {r["context_id"] for r in group if r["context_id"] is not None}
            if len(context_ids) > 1:
                raise ValueError("Inconsistent shared photo context")
            if len(group) > 1:
                # Printed range headers remain a source of truth if the model
                # identifies the group but omits its optional context reference.
                pattern = rf"^\[?{min(key)}[~～∼-]{max(key)}\]"
                context_ids.update(i for i, b in enumerate(blocks)
                                   if re.match(pattern, re.sub(r"\s+", "", b["text"]))
                                   and in_column(column_index, b["bbox"]))
            elif context_ids:
                raise ValueError("Unshared photo context")
            if any(not in_column(column_index, blocks[i]["bbox"])
                   or blocks[i]["bbox"][1] > min(blocks[a]["bbox"][1] for a in anchor_ids)
                   or _non_body(blocks[i]["text"]) for i in context_ids):
                raise ValueError("Invalid shared photo context")
            y = min(blocks[i]["bbox"][1] for i in anchor_ids | context_ids)
            # OCR may split a slanted first line into boxes whose top edges
            # differ. Include overlapping prose on that same printed line.
            for anchor_id in anchor_ids | context_ids:
                ax0, ay0, ax1, ay1 = blocks[anchor_id]["bbox"]
                for block in blocks:
                    x0, y0, x1, y1 = block["bbox"]
                    if (x0 >= ax0 and y0 < ay0 and y1 >= ay0 + (ay1 - ay0) * .25
                            and in_column(column_index, block["bbox"])
                            and len(re.findall(r"[가-힣]", block["text"])) >= 8
                            and not _non_body(block["text"])):
                        y = min(y, y0)
            starts.append((y, group))
        starts.sort(key=lambda item: item[0])
        for index, (top, group) in enumerate(starts):
            bottom = starts[index + 1][0] if index + 1 < len(starts) else math.inf
            if any(blocks[r["anchor_id"]]["bbox"][1] >= bottom for r in group):
                raise ValueError("Interleaved shared photo material")
            ids = set()
            for i, block in enumerate(blocks):
                x0, y0, x1, y1 = block["bbox"]
                if not (in_column(column_index, block["bbox"]) and top <= (y0 + y1) / 2 < bottom):
                    continue
                if _non_body(block["text"]) or _footer(block, height):
                    continue
                ids.add(i)
            if any(r["anchor_id"] not in ids for r in group):
                raise ValueError("Photo anchor outside its column interval")
            content = [blocks[i]["bbox"] for i in ids]
            bbox = (min(b[0] for b in content), min(b[1] for b in content),
                    max(b[2] for b in content), max(b[3] for b in content))
            for region in group:
                region["ids"] = ids
                region["content_bbox"] = bbox


def grounded_photo_regions(data: dict, blocks: list[dict], width: int, height: int) -> list[dict]:
    """Validate the entire page and derive source-pixel crops from observed content.

    Missing/contradictory starts are errors, never a shorter successful page.
    Padding is resolved here against neighbouring content, so the crop writer
    must not add its unrelated generic percentage padding again.
    """
    raw = data.get("problems") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("Photo grounding contains no complete questions")
    regions = []
    numbers = set()
    for p in raw:
        if not isinstance(p, dict) or type(p.get("number")) is not int or p["number"] <= 0:
            raise ValueError("Invalid grounded question number")
        if p["number"] in numbers:
            raise ValueError("Duplicate grounded question number")
        numbers.add(p["number"])
        anchor = p.get("anchor_id")
        shared = p.get("shared_with", [])
        context = p.get("context_id")
        if (type(anchor) is not int or not 0 <= anchor < len(blocks)
                or (context is not None and (type(context) is not int or not 0 <= context < len(blocks)))
                or not isinstance(shared, list)
                or any(type(n) is not int or n <= 0 or n == p["number"] for n in shared)):
            raise ValueError("Invalid photo OCR reference")
        if _non_body(blocks[anchor]["text"]):
            raise ValueError("Photo question anchored in a header")
        if re.match(r"^(이에대한|이것에대한|위에대한)", re.sub(r"\s+", "", blocks[anchor]["text"])):
            raise ValueError("Photo question anchored in a continuation")
        regions.append({"number": p["number"], "anchor_id": anchor, "context_id": context,
                        "shared_with": sorted(set(shared))})

    if len({r["anchor_id"] for r in regions}) != len(regions):
        raise ValueError("Duplicate photo question anchor")

    by_number = {r["number"]: r for r in regions}
    for r in regions:
        for number in r["shared_with"]:
            other = by_number.get(number)
            if (other is None or r["number"] not in other["shared_with"]
                    or {r["number"], *r["shared_with"]} != {number, *other["shared_with"]}):
                raise ValueError("Asymmetric shared photo material")
    _assign_photo_content(regions, blocks, width, height)
    for r in regions:
        for other in regions:
            if other is r or other["number"] in r["shared_with"]:
                continue
            if r["ids"] & other["ids"] or _covered(blocks[other["anchor_id"]]["bbox"], r["content_bbox"]):
                raise ValueError("Photo crop contains another question")

    first_y = min(blocks[r["anchor_id"]]["bbox"][1] for r in regions)
    left_edge = min(blocks[r["anchor_id"]]["bbox"][0] for r in regions) - width * .07
    for block in blocks:
        x0, y0, x1, y1 = block["bbox"]
        # Substantial printed prose outside every returned region is a missing
        # question/body. Small background fragments, handwriting formulas and
        # page furniture do not establish a missing foreground question.
        if (y1 < first_y or x0 < left_edge or x1 - x0 < width * .14
                or len(re.findall(r"[가-힣]", block["text"])) < 8
                or _non_body(block["text"]) or _footer(block, height)):
            continue
        if not any(_covered(block["bbox"], r["content_bbox"]) for r in regions):
            raise ValueError("Photo grounding omitted printed content")

    for r in regions:
        x0, y0, x1, y1 = r["content_bbox"]
        anchor = blocks[r["anchor_id"]]
        ax0, ay0, ax1, ay1 = anchor["bbox"]
        printed_number = re.match(r"^\s*" + str(r["number"]) + r"(?:\D|$)", anchor["text"])
        left_pad = 8 if printed_number else min(width * .035, (ay1 - ay0) * 1.5)
        crop = [max(0, x0 - left_pad), max(0, y0 - 8), min(width, x1 + 8), min(height, y1 + 8)]
        for other in regions:
            if other is r or other["number"] in r["shared_with"]:
                continue
            for index in other["ids"]:
                a, b, c, d = blocks[index]["bbox"]
                if b < crop[3] and d > crop[1]:
                    if c <= x0:
                        crop[0] = max(crop[0], c + 1)
                    if a >= x1:
                        crop[2] = min(crop[2], a - 1)
                if a < crop[2] and c > crop[0]:
                    if d <= y0:
                        crop[1] = max(crop[1], d + 1)
                    if b >= y1:
                        crop[3] = min(crop[3], b - 1)
        # Never reduce the observed content to satisfy a padding preference.
        crop = [min(crop[0], x0), min(crop[1], y0), max(crop[2], x1), max(crop[3], y1)]
        r["bbox"] = (math.floor(crop[0]), math.floor(crop[1]),
                     math.ceil(crop[2]) - math.floor(crop[0]), math.ceil(crop[3]) - math.floor(crop[1]))
    # A shared material group has exactly one display crop, including padding.
    for r in regions:
        if r["shared_with"]:
            group = [by_number[n] for n in {r["number"], *r["shared_with"]}]
            x0 = min(p["bbox"][0] for p in group)
            y0 = min(p["bbox"][1] for p in group)
            x1 = max(p["bbox"][0] + p["bbox"][2] for p in group)
            y1 = max(p["bbox"][1] + p["bbox"][3] for p in group)
            for p in group:
                p["bbox"] = (x0, y0, x1 - x0, y1 - y0)
    return regions

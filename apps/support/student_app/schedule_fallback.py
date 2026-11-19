import re
from datetime import time


def parse_lecture_start_time(lecture_time: str) -> time | None:
    """Read the first clock time from a legacy free-text lecture schedule."""
    match = re.search(r"(\d+):(\d+)", lecture_time)
    if match is None:
        return None
    hour, minute = match.groups()
    if len(hour) > 2 or len(minute) != 2:
        return None
    try:
        return time(int(hour), int(minute))
    except ValueError:
        return None

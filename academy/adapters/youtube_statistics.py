"""Read public statistics from the official API; never send a supplied URL or log keys."""
import json
import re
import time
from datetime import datetime

import requests


class StatisticsUnavailable(Exception):
    pass


def fetch_youtube_statistics(video_ids, api_key):
    ids = list(dict.fromkeys(video_ids))
    if not api_key or not 1 <= len(ids) <= 50 or any(not re.fullmatch(r"[A-Za-z0-9_-]{11}", item) for item in ids):
        raise StatisticsUnavailable()
    try:
        deadline = time.monotonic() + 12
        with requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"id": ",".join(ids), "part": "snippet,statistics", "fields": "items(id,snippet/publishedAt,statistics/viewCount)"},
            headers={"X-Goog-Api-Key": api_key}, timeout=(3, 5), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code != 200:
                raise StatisticsUnavailable()
            body = bytearray()
            for chunk in response.iter_content(8192):
                body.extend(chunk)
                if len(body) > 262144 or time.monotonic() > deadline:
                    raise StatisticsUnavailable()
            data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise StatisticsUnavailable()
        result = {}
        for item in data["items"]:
            video_id = item["id"]
            if video_id not in ids or video_id in result:
                raise StatisticsUnavailable()
            count = item.get("statistics", {}).get("viewCount")
            if count is not None and (not isinstance(count, str) or not re.fullmatch(r"[0-9]{1,19}", count) or int(count) > 9223372036854775807):
                raise StatisticsUnavailable()
            published = datetime.fromisoformat(item["snippet"]["publishedAt"].replace("Z", "+00:00"))
            if published.tzinfo is None:
                raise StatisticsUnavailable()
            result[video_id] = {"view_count": int(count) if count is not None else None, "published_at": published}
        return result
    except (requests.RequestException, ValueError, TypeError, AttributeError, KeyError):
        raise StatisticsUnavailable() from None

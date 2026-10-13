"""Bounded read-only YouTube oEmbed transport, independent of application policy."""
import json
import re
import time

import requests


class MetadataUnavailable(Exception):
    pass


def fetch_youtube_metadata(video_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise MetadataUnavailable()
    try:
        deadline = time.monotonic() + 12
        with requests.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=(3, 5), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code != 200:
                raise MetadataUnavailable()
            body = bytearray()
            for chunk in response.iter_content(8192):
                body.extend(chunk)
                if len(body) > 65536 or time.monotonic() > deadline:
                    raise MetadataUnavailable()
            data = json.loads(body)
        title, channel = data.get("title"), data.get("author_name")
        if not isinstance(title, str) or not title.strip() or not isinstance(channel, str) or not channel.strip():
            raise MetadataUnavailable()
        return {"source_title": title[:500], "channel_title": channel[:200],
                "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        raise MetadataUnavailable() from exc

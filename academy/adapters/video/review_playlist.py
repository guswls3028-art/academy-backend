"""Read one server-signed CDN playlist without forwarding browser credentials."""

import requests


def read_review_playlist(url):
    try:
        response = requests.get(url, timeout=(3, 8), allow_redirects=False)
    except requests.RequestException:
        return None
    if response.status_code == 200 and response.text.startswith("#EXTM3U"):
        return response.text
    return None

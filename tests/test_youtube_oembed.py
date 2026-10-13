import json
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase

from academy.adapters.youtube_oembed import MetadataUnavailable, fetch_youtube_metadata


class YouTubeTransportTests(SimpleTestCase):
    @patch("academy.adapters.youtube_oembed.requests.get")
    def test_bounded_lookup_fixed_destination_and_failure(self, get):
        response = MagicMock()
        get.return_value.__enter__.return_value = response
        response.status_code = 200
        response.iter_content.return_value = [json.dumps({"title": "제목", "author_name": "두각", "thumbnail_url": "http://private/secret"}).encode()]
        result = fetch_youtube_metadata("xgjL2HyrdFM")
        self.assertEqual(result["thumbnail_url"], "https://i.ytimg.com/vi/xgjL2HyrdFM/hqdefault.jpg")
        self.assertEqual(get.call_args.args[0], "https://www.youtube.com/oembed")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        response.status_code = 302
        with self.assertRaises(MetadataUnavailable):
            fetch_youtube_metadata("xgjL2HyrdFM")
        response.status_code = 200
        response.iter_content.return_value = [b"x" * 65537]
        with self.assertRaises(MetadataUnavailable):
            fetch_youtube_metadata("xgjL2HyrdFM")
        get.side_effect = requests.Timeout()
        with self.assertRaises(MetadataUnavailable):
            fetch_youtube_metadata("xgjL2HyrdFM")

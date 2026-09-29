"""Security-patched DRF bounds text bodies without buffering file uploads."""

import json

from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import path
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class ParserProbe(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        if request.content_type.startswith("multipart/"):
            upload = request.FILES["source_files"]
            return Response({
                "size": upload.size,
                "spooled": isinstance(upload, TemporaryUploadedFile),
                "label": request.data["label"],
            })
        return Response({"label": request.data["label"]})


urlpatterns = [path("parser-probe/", ParserProbe.as_view())]


@override_settings(
    ROOT_URLCONF=__name__,
    MIDDLEWARE=[],  # Exercise Django's HTTP exception boundary without tenant fixtures.
    DATA_UPLOAD_MAX_MEMORY_SIZE=2_621_440,
    FILE_UPLOAD_MAX_MEMORY_SIZE=1_048_576,
)
class RequestSizeBoundaryTests(SimpleTestCase):
    def test_normal_korean_json_remains_readable(self):
        response = self.client.post(
            "/parser-probe/",
            data=json.dumps({"label": "클리닉 주관식 검수"}, ensure_ascii=False),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"label": "클리닉 주관식 검수"})

    def test_oversized_json_and_form_are_http_400(self):
        text = "x" * 2_621_441
        for content_type, body in (
            ("application/json", json.dumps({"label": text})),
            ("application/x-www-form-urlencoded", "label=" + text),
        ):
            with self.subTest(content_type=content_type):
                response = self.client.post("/parser-probe/", data=body, content_type=content_type)
                self.assertEqual(response.status_code, 400)

    def test_larger_multipart_file_still_streams_to_disk(self):
        size = 3 * 1024 * 1024
        with SimpleUploadedFile("source.bin", b"x" * size) as upload:
            response = self.client.post(
                "/parser-probe/", data={"source_files": upload, "label": "원본 자료"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"size": size, "spooled": True, "label": "원본 자료"})

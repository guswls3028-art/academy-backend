"""Security-patched DRF bounds text bodies without buffering file uploads."""

import json

from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import path
from rest_framework import serializers
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


class RowSerializer(serializers.Serializer):
    score = serializers.IntegerField(min_value=0)


class BulkValidationProbe(ParserProbe):
    def post(self, request):
        serializer = RowSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


urlpatterns = [
    path("parser-probe/", ParserProbe.as_view()),
    path("bulk-probe/", BulkValidationProbe.as_view()),
]


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

    def test_non_text_json_charsets_are_rejected_without_decoding(self):
        for charset in ("zlib_codec", "bz2_codec", "base64_codec", "rot_13"):
            with self.subTest(charset=charset):
                response = self.client.generic(
                    "POST", "/parser-probe/", data=b'{"label":"ok"}',
                    content_type=f"application/json; charset={charset}",
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn("Unsupported charset", response.json()["detail"])

    def test_oversized_content_type_is_rejected_before_parser_selection(self):
        response = self.client.generic(
            "POST", "/parser-probe/", data=b'{"label":"ok"}',
            content_type='application/json; padding="' + "x" * 256 + '"',
        )
        self.assertEqual(response.status_code, 415)

    def test_long_accept_token_is_skipped_and_valid_json_remains_usable(self):
        long_token = 'application/json; padding="' + "x" * 300 + '"'
        for accept, expected in ((long_token, 406), (long_token + ", application/json", 200)):
            with self.subTest(expected=expected):
                response = self.client.post(
                    "/parser-probe/", data='{"label":"ok"}', content_type="application/json",
                    HTTP_ACCEPT=accept,
                )
                self.assertEqual(response.status_code, expected)

    def test_bulk_validation_keeps_existing_per_row_error_array(self):
        response = self.client.post(
            "/bulk-probe/", data=json.dumps([{"score": 7}, {"score": -1}]),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        errors = response.json()
        self.assertIsInstance(errors, list)
        self.assertEqual(errors[0], {})
        self.assertEqual(len(errors[1]["score"]), 1)

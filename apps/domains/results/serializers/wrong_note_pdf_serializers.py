# apps/domains/results/serializers/wrong_note_pdf_serializers.py
from __future__ import annotations

from rest_framework import serializers


class WrongNotePDFStatusSerializer(serializers.Serializer):
    """
    오답노트 PDF Job 상태 조회 응답

    ✅ 프론트 폴링용 최소 필드
    - status: PENDING/RUNNING/DONE/FAILED
    - file_url: DONE일 때 다운로드 URL
    - error_message: FAILED일 때 표시
    """
    job_id = serializers.IntegerField()
    status = serializers.CharField()
    file_path = serializers.CharField(allow_blank=True)
    file_url = serializers.CharField(allow_blank=True, allow_null=True)
    error_message = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
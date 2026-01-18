# PATH: apps/domains/results/serializers/admin_clinic_target.py
"""
역할
- Admin/Teacher용 "클리닉 대상자" 리스트 응답 계약을 고정한다.

설계 계약 (중요)
- Clinic 대상자 선정/판단은 results 도메인의 단일 진실이다.
- enrollment_id 기준(단일 진실)으로 내려준다.
- 프론트의 ClinicTarget 타입과 1:1로 맞춘다.

보류된 기능 (명시)
- reason의 세부 판정(점수/신뢰도)은 서비스에서 보수적으로 판정한다.
  (프로젝트마다 LOW_CONFIDENCE 신호가 Attempt.meta에 있을 수도, ResultFact.meta에 있을 수도 있어 방어 구현)
"""

from rest_framework import serializers


class AdminClinicTargetSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()
    session_title = serializers.CharField()

    reason = serializers.ChoiceField(choices=["score", "confidence"])

    exam_score = serializers.FloatField()
    cutline_score = serializers.FloatField()

    created_at = serializers.DateTimeField()

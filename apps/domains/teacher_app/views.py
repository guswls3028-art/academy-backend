"""
Teacher App BFF Views.
모바일 앱에 필요한 집계 데이터를 단일 응답으로 제공.
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff


class NotificationSummaryView(APIView):
    """
    GET: 미처리 알림 건수 집계.
    - qna_pending: 답변 없는 Q&A
    - registration_pending: 대기 중 등록요청
    - clinic_pending: 대기 중 클리닉 예약
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant

        # Q&A 미답변
        from apps.domains.community.models import PostEntity
        qna_pending = (
            PostEntity.objects.filter(
                tenant=tenant,
                post_type="qna",
                status="published",
            )
            .annotate(reply_count=_count_replies())
            .filter(reply_count=0)
            .count()
        )

        # 등록요청 대기
        from apps.domains.students.models import StudentRegistrationRequest
        registration_pending = StudentRegistrationRequest.objects.filter(
            tenant=tenant,
            status="pending",
        ).count()

        # 클리닉 예약 대기
        from apps.domains.clinic.models import SessionParticipant
        clinic_pending = SessionParticipant.objects.filter(
            tenant=tenant,
            status="pending",
        ).count()

        total = qna_pending + registration_pending + clinic_pending

        return Response({
            "total": total,
            "qna_pending": qna_pending,
            "registration_pending": registration_pending,
            "clinic_pending": clinic_pending,
        })


def _count_replies():
    """PostEntity에 대한 reply 수 서브쿼리"""
    from django.db.models import Count
    return Count("replies")

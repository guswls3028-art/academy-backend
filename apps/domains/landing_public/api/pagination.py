"""landing_public 도메인 전용 pagination (P1 audit 2026-05-14).

이전: viewset의 default pagination → settings.PAGE_SIZE=20 사용. frontend는 12로
보내지만 page_size_query_param 미정 → 무시 → totalPages 잘못 계산 → 끝 페이지 빈 결과.

본 클래스: frontend 와 일관 12 + page_size query 허용 (max 50).
board/review viewset 에 pagination_class 지정.
"""
from rest_framework.pagination import PageNumberPagination


class LandingPublicPagination(PageNumberPagination):
    page_size = 12
    page_size_query_param = "page_size"
    max_page_size = 50

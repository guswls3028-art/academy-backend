from rest_framework.pagination import PageNumberPagination


class AcademyPageNumberPagination(PageNumberPagination):
    """Default pagination with the client-visible page_size contract enabled."""

    page_size_query_param = "page_size"
    max_page_size = 500

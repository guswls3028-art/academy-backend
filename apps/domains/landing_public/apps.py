from django.apps import AppConfig


class LandingPublicConfig(AppConfig):
    """랜딩 공개 커뮤니티 도메인 (자유게시판 / 수강후기 / 외부 노출 컨텐츠).

    family-only community 도메인(`apps.domains.community`)과 본질이 다름:
      - 비로그인 외부 학부모도 read OK
      - 평점 / 사진 / 검증 뱃지 / 학원장 승인 워크플로우
      - 모더레이션 / 외부 공개 toggle 학원장 단독 권한
    내부 자료(공지/QnA/자료실/상담)와 데이터 격리.
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains.landing_public"
    verbose_name = "Landing Public Community"

    def ready(self):
        import apps.domains.landing_public.signals  # noqa: F401

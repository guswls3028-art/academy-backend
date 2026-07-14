"""폐기된 테넌트별 카카오 템플릿 검수 명령의 안전 경계."""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "제품 정책상 비활성화된 레거시 명령"

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        raise CommandError(
            "신규 카카오 템플릿 검수는 제품 정책상 영구 비활성화되었습니다. "
            "기존 승인 알림톡 봉투와 사용자 문구를 사용하세요."
        )

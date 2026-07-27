from django.core.management.base import BaseCommand

from apps.core.services.platform_push import dispatch_platform_push_batch


class Command(BaseCommand):
    help = "Dispatch durable platform inbox Web Push notifications."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200)
        parser.add_argument("--silent", action="store_true")

    def handle(self, *args, **options):
        result = dispatch_platform_push_batch(limit=options["limit"])
        if not options["silent"]:
            self.stdout.write(
                "platform push: "
                + " ".join(f"{key}={value}" for key, value in result.items())
            )

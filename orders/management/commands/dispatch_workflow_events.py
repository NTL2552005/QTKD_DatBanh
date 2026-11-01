from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from orders.workflow_events import MAX_BATCH_SIZE, dispatch_workflow_events


class Command(BaseCommand):
    help = "Send a bounded batch of outbox events to the configured workflow webhook."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help=f"Maximum events per run (1-{MAX_BATCH_SIZE}, default 20).",
        )

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= MAX_BATCH_SIZE:
            raise CommandError(
                f"--limit must be between 1 and {MAX_BATCH_SIZE}."
            )
        if not settings.WORKFLOW_ENABLED:
            self.stdout.write(
                self.style.WARNING(
                    "WORKFLOW_ENABLED is off; pending events are unchanged."
                )
            )
            return
        try:
            result = dispatch_workflow_events(limit=options["limit"])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {result['processed']} workflow event(s). "
                "Delivered means webhook accepted the event, not email delivery."
            )
        )

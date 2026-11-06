from django.core.management.base import BaseCommand, CommandError

from orders.review_classification import MAX_BATCH_SIZE, dispatch_review_classification_events


class Command(BaseCommand):
    help = "Dispatch pending review-classification events to the configured n8n webhook."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help=f"Maximum events per run (1-{MAX_BATCH_SIZE}, default 20).",
        )
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Requeue failed events before dispatching.",
        )

    def handle(self, *args, **options):
        if not 1 <= options["limit"] <= MAX_BATCH_SIZE:
            raise CommandError(f"--limit must be between 1 and {MAX_BATCH_SIZE}.")
        try:
            result = dispatch_review_classification_events(
                limit=options["limit"],
                retry_failed=options["retry_failed"],
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        message = (
            f"Processed {result['processed']} event(s): "
            f"{result['sent']} accepted by n8n, "
            f"{result['pending']} scheduled for retry, "
            f"{result['failed']} failed."
        )
        if result["pending"] or result["failed"]:
            self.stdout.write(self.style.WARNING(message))
        else:
            self.stdout.write(self.style.SUCCESS(message))

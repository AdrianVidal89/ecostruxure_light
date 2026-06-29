"""Import POCs from the M365 PoC Follow-up .xlsx export.

Usage:
    python manage.py import_pocs path/to/query.xlsx --user admin [--dry-run]
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.pocs.importer import import_pocs_from_xlsx


class Command(BaseCommand):
    help = "Import POCs from an M365 PoC Follow-up Excel export."

    def add_arguments(self, parser):
        parser.add_argument("xlsx", help="Path to the .xlsx file.")
        parser.add_argument(
            "--user",
            default="admin",
            help="Username recorded as creator of imported POCs (default: admin).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate and count without writing anything.",
        )

    def handle(self, *args, **opts):
        User = get_user_model()
        try:
            user = User.objects.get(username=opts["user"])
        except User.DoesNotExist:
            raise CommandError(f"User '{opts['user']}' not found.")

        with open(opts["xlsx"], "rb") as fh:
            stats = import_pocs_from_xlsx(fh, user, dry_run=opts["dry_run"])

        mode = "DRY-RUN — " if opts["dry_run"] else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}created={stats['created']} updated={stats['updated']} "
                f"skipped={stats['skipped']} errors={len(stats['errors'])}"
            )
        )
        for err in stats["errors"]:
            self.stderr.write(self.style.WARNING(f"  {err}"))

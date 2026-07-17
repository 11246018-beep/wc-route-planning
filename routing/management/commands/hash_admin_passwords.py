from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from routing.security import is_hashed_password


class Command(BaseCommand):
    help = "Hash existing plaintext Django admin/manager passwords without changing already-hashed values."

    def handle(self, *args, **options):
        updated_count = 0
        skipped_count = 0

        for user in User.objects.all().order_by("username"):
            password = user.password or ""
            if not password or is_hashed_password(password):
                skipped_count += 1
                continue

            user.set_password(password)
            user.save(update_fields=["password"])
            updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Admin password hashing complete. Updated: {updated_count}, skipped: {skipped_count}."
            )
        )

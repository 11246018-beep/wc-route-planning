from django.core.management.base import BaseCommand

from routing.models import Driver
from routing.security import hash_driver_password, is_hashed_password


class Command(BaseCommand):
    help = "Hash existing plaintext driver passwords without changing already-hashed values."

    def handle(self, *args, **options):
        updated_count = 0
        skipped_count = 0

        for driver in Driver.objects.all().order_by("driver_code"):
            password = driver.password or ""
            if not password or is_hashed_password(password):
                skipped_count += 1
                continue

            driver.password = hash_driver_password(password)
            driver.save(update_fields=["password"])
            updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Driver password hashing complete. Updated: {updated_count}, skipped: {skipped_count}."
            )
        )

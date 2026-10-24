from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from accounts.models import User
from audit.services import record_audit


class Command(BaseCommand):
    help = "Create the first Tamirabot Super Admin using interactive password entry."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(role=User.Role.SUPER_ADMIN, is_active=True).exists():
            raise CommandError("An active Super Admin already exists.")

        password = getpass("Password (minimum 12 characters): ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            raise CommandError("Passwords do not match.")

        user = User(
            username=options["username"],
            email=options["email"],
            role=User.Role.SUPER_ADMIN,
            is_staff=True,
        )
        try:
            validate_password(password, user)
        except ValidationError as exc:
            raise CommandError(f"Password is not strong enough: {exc}") from exc
        user.set_password(password)
        try:
            user.full_clean()
        except ValidationError as exc:
            raise CommandError(f"Could not create Super Admin: {exc}") from exc
        try:
            user.save()
        except IntegrityError as exc:
            raise CommandError(f"Could not create Super Admin: {exc}") from exc
        record_audit(
            actor=user,
            action="user.super_admin_seeded",
            instance=user,
            after={"username": user.username, "email": user.email, "role": user.role},
        )
        self.stdout.write(self.style.SUCCESS(f"Created Super Admin: {user.username}"))

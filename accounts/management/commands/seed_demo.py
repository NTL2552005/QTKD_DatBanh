import os

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Tạo các tài khoản demo BiBi Cake nếu chưa tồn tại."

    def handle(self, *args, **options):
        password = os.getenv("DEMO_PASSWORD")
        if not password:
            raise CommandError(
                "Chua cau hinh DEMO_PASSWORD. Hay them mat khau demo vao file .env "
                "roi chay lai lenh."
            )

        demo_accounts = (
            ("customer_demo", "customer@bibicake.local", "Khách hàng", User.Role.CUSTOMER),
            ("staff_demo", "staff@bibicake.local", "Nhân viên", User.Role.STAFF),
            ("owner_demo", "owner@bibicake.local", "Chủ cửa hàng", User.Role.OWNER),
        )
        for username, email, name, role in demo_accounts:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": email,
                    "first_name": name,
                    "role": role,
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
                self.stdout.write(f"Created demo account: {username} ({role}).")
            else:
                self.stdout.write(f"Kept existing account unchanged: {username}.")

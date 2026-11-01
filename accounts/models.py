from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Khách hàng"
        STAFF = "STAFF", "Nhân viên"
        OWNER = "OWNER", "Chủ cửa hàng"

    role = models.CharField(
        "vai trò",
        max_length=10,
        choices=Role.choices,
        default=Role.CUSTOMER,
    )

    def __str__(self):
        return self.get_full_name() or self.username

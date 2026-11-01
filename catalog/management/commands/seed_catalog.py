from decimal import Decimal

from django.core.management.base import BaseCommand

from catalog.models import CakeTemplate, Category


class Command(BaseCommand):
    help = "Tạo danh mục và mẫu bánh minh họa nếu chưa tồn tại."

    demo_catalog = (
        (
            "Bánh sinh nhật",
            "Mẫu bánh tham khảo cho những buổi tiệc sinh nhật.",
            (
                (
                    "Bánh kem dâu tươi",
                    "Mẫu minh họa: cốt bánh mềm, kem tươi và dâu theo mùa.",
                    Decimal("350000"),
                ),
                (
                    "Bánh sinh nhật hoa kem",
                    "Mẫu minh họa: trang trí hoa kem màu pastel.",
                    Decimal("480000"),
                ),
            ),
        ),
        (
            "Bánh kỷ niệm",
            "Mẫu bánh tham khảo cho dịp kỷ niệm và lời chúc đặc biệt.",
            (
                (
                    "Bánh kỷ niệm trái tim",
                    "Mẫu minh họa: tạo hình trái tim với lời chúc tùy chọn.",
                    Decimal("520000"),
                ),
                (
                    "Bánh kỷ niệm tối giản",
                    "Mẫu minh họa: phong cách trang trí tối giản, thanh lịch.",
                    Decimal("450000"),
                ),
            ),
        ),
        (
            "Bánh cupcake",
            "Các mẫu cupcake tham khảo cho tiệc nhỏ và buổi gặp mặt.",
            (
                (
                    "Cupcake kem vani",
                    "Mẫu minh họa: cupcake vani phủ kem mềm.",
                    Decimal("45000"),
                ),
                (
                    "Cupcake chocolate",
                    "Mẫu minh họa: cupcake chocolate trang trí kem.",
                    Decimal("50000"),
                ),
            ),
        ),
    )

    def handle(self, *args, **options):
        created_categories = 0
        created_cakes = 0
        for category_name, category_description, cakes in self.demo_catalog:
            category, category_created = Category.objects.get_or_create(
                name=category_name,
                defaults={"description": category_description},
            )
            created_categories += int(category_created)
            for name, description, price in cakes:
                _, cake_created = CakeTemplate.objects.get_or_create(
                    category=category,
                    name=name,
                    defaults={
                        "description": description,
                        "reference_price": price,
                        "is_available": category.is_active,
                    },
                )
                created_cakes += int(cake_created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Created {created_categories} categories and {created_cakes} cakes; "
                "existing data was preserved."
            )
        )

from django.core.management.base import BaseCommand
from django.db import transaction

from catalog.models import CakeTemplate


class Command(BaseCommand):
    help = "Điền gợi ý tư vấn demo chưa xác nhận cho catalog mẫu."

    demo_advisory_data = {
        "Bánh kem dâu tươi": {
            "suitable_occasions": "Sinh nhật",
            "flavor_profile": "Dâu tươi",
        },
        "Bánh kỷ niệm trái tim": {
            "suitable_occasions": "Kỷ niệm",
            "style_tags": "Tạo hình trái tim",
        },
        "Bánh kỷ niệm tối giản": {
            "suitable_occasions": "Kỷ niệm",
            "style_tags": "Tối giản, thanh lịch",
        },
        "Bánh sinh nhật hoa kem": {
            "suitable_occasions": "Sinh nhật",
            "style_tags": "Hoa kem màu pastel",
        },
        "Cupcake chocolate": {
            "flavor_profile": "Chocolate",
            "style_tags": "Cupcake trang trí kem",
        },
        "Cupcake kem vani": {
            "flavor_profile": "Vani",
            "style_tags": "Cupcake phủ kem mềm",
        },
    }

    @transaction.atomic
    def handle(self, *args, **options):
        updated_count = 0
        skipped_count = 0
        missing_names = []

        for cake_name, suggested_fields in self.demo_advisory_data.items():
            cake = CakeTemplate.objects.select_for_update().filter(
                name=cake_name
            ).first()
            if cake is None:
                missing_names.append(cake_name)
                continue
            if cake.advisory_data_verified:
                skipped_count += 1
                continue

            changed_fields = []
            for field, value in suggested_fields.items():
                if not getattr(cake, field):
                    setattr(cake, field, value)
                    changed_fields.append(field)

            if changed_fields:
                cake.save(update_fields=(*changed_fields, "updated_at"))
                updated_count += 1
            else:
                skipped_count += 1

        summary = (
            f"Updated {updated_count} demo cake records; skipped {skipped_count}; "
            f"missing {len(missing_names)}."
        )
        if missing_names:
            summary += " Missing names: " + ", ".join(missing_names)
        self.stdout.write(self.style.SUCCESS(summary))

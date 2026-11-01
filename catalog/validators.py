import warnings

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
ALLOWED_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}


def validate_catalog_image(uploaded_file):
    if uploaded_file.size > MAX_IMAGE_BYTES:
        raise ValidationError("Ảnh tải lên không được vượt quá 5 MiB.")

    try:
        uploaded_file.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(uploaded_file)
            image_format = image.format
            width, height = image.size
            if image_format not in ALLOWED_FORMATS:
                raise ValidationError("Chỉ chấp nhận ảnh JPEG, PNG hoặc WebP.")
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise ValidationError("Kích thước ảnh vượt quá giới hạn cho phép.")
            image.load()
        uploaded_file.catalog_image_extension = ALLOWED_FORMATS[image_format]
    except ValidationError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValidationError("Tệp tải lên không phải ảnh hợp lệ hoặc ảnh quá lớn.") from exc
    finally:
        uploaded_file.seek(0)

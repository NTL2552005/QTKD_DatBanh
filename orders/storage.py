from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.functional import cached_property
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateOrderPhotoStorage(FileSystemStorage):
    def __init__(self):
        super().__init__(location=None, base_url=None)

    @cached_property
    def base_location(self):
        return str(settings.PRIVATE_MEDIA_ROOT)


private_order_photo_storage = PrivateOrderPhotoStorage()

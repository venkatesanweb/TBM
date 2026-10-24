from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_EDGE = 1600
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
IMAGE_EXTENSIONS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


def validate_image(upload):
    if upload.size > MAX_IMAGE_BYTES:
        raise ValidationError("Image must be no larger than 5 MB.")
    try:
        upload.seek(0)
        with Image.open(upload) as image:
            if image.format not in ALLOWED_IMAGE_FORMATS:
                raise ValidationError("Use a JPEG, PNG, or WebP image.")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValidationError("Image dimensions must not exceed 20 megapixels.")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("The uploaded file is not a valid image.") from exc
    finally:
        upload.seek(0)


def resize_image(upload):
    validate_image(upload)
    upload.seek(0)
    with Image.open(upload) as source:
        image_format = source.format
        image = ImageOps.exif_transpose(source)
        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE), Image.Resampling.LANCZOS)
        if image_format == "JPEG" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        output = BytesIO()
        image.save(output, format=image_format, optimize=True)

    upload.name = f"{uuid4().hex}{IMAGE_EXTENSIONS[image_format]}"
    upload.file = BytesIO(output.getvalue())
    upload.size = len(output.getvalue())
    upload.seek(0)
    return upload


def image_snapshot(field_file):
    if not field_file:
        return None
    return field_file.name

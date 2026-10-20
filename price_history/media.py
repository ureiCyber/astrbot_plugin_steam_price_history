"""Bound chart uploads and snapshot their contents before adapter serialization."""
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

MAX_IMAGE_BYTES = 450 * 1024
MAX_IMAGE_SIZE = (1080, 1600)
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_SOURCE_PIXELS = 16_000_000


def prepare_image(path: str | Path) -> bytes:
    """Return a complete RGB JPEG, not raw pixels, suitable for Image.fromBytes.

    A small upload reduces pressure on protocol clients, but cannot guarantee a
    third-party messaging service accepts or retains the image.
    """
    with Path(path).open("rb") as handle:
        source = handle.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError("Chart input exceeds size limit")
    try:
        with Image.open(BytesIO(source)) as original:
            if original.width * original.height > MAX_SOURCE_PIXELS:
                raise ValueError("Chart input exceeds pixel limit")
            original.load()
            # Alpha is composited rather than turned into arbitrary black pixels.
            rgba = ImageOps.exif_transpose(original).convert("RGBA")
            image = Image.new("RGB", rgba.size, "#111827")
            image.paste(rgba, mask=rgba.getchannel("A"))
    except Image.DecompressionBombError as exc:
        raise ValueError("Chart input exceeds pixel limit") from exc
    image.thumbnail(MAX_IMAGE_SIZE, Image.Resampling.LANCZOS)
    while True:
        for quality in (88, 80, 70, 60):
            output = BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True, subsampling=0)
            payload = output.getvalue()
            if len(payload) <= MAX_IMAGE_BYTES:
                return payload
        # Extremely noisy covers still obey the hard upload budget.
        image = image.resize(
            (max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)),
            Image.Resampling.LANCZOS,
        )

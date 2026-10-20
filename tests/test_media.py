from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from price_history.media import prepare_image, MAX_IMAGE_BYTES, MAX_IMAGE_SIZE


class MediaTests(unittest.TestCase):
    def test_jpeg_is_bounded_and_survives_source_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.png"
            Image.effect_noise((2000, 2200), 100).convert("RGB").save(path)
            payload = prepare_image(path)
            path.unlink()
            self.assertLessEqual(len(payload), MAX_IMAGE_BYTES)
            with Image.open(BytesIO(payload)) as result:
                result.load()
                self.assertEqual(result.format, "JPEG")
                self.assertEqual(result.mode, "RGB")
                self.assertLessEqual(result.width, MAX_IMAGE_SIZE[0])
                self.assertLessEqual(result.height, MAX_IMAGE_SIZE[1])

    def test_transparent_background_composited(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "alpha.png"
            Image.new("RGBA", (40, 40), (255, 0, 0, 0)).save(path)
            with Image.open(BytesIO(prepare_image(path))) as result:
                self.assertLess(sum(abs(a-b) for a,b in zip(result.getpixel((20,20)), (17,24,39))), 8)

    def test_missing_corrupt_empty_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chart.png"
            with self.assertRaises(OSError):
                prepare_image(path)
            for value in (b"", b"not a chart"):
                path.write_bytes(value)
                with self.assertRaises(OSError):
                    prepare_image(path)

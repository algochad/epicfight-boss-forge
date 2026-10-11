import _path  # noqa
import struct
import unittest
import zlib

from bossforge_core import noise
from bossforge_core.pngio import Canvas, png_size, write_png


class NoiseTests(unittest.TestCase):
    def test_vnoise_deterministic_and_bounded(self):
        vals = [noise.vnoise(x * 0.37, x * 0.11, x * 0.73, 5) for x in range(200)]
        self.assertEqual(vals, [noise.vnoise(x * 0.37, x * 0.11, x * 0.73, 5) for x in range(200)])
        self.assertTrue(all(0.0 <= v <= 1.0 for v in vals))
        self.assertNotEqual(noise.vnoise(0.5, 0.5, 0.5, 1), noise.vnoise(0.5, 0.5, 0.5, 2))

    def test_worley_f1_le_f2(self):
        for i in range(50):
            f1, f2 = noise.worley(i * 0.31, i * 0.17, i * 0.05, 3)
            self.assertLessEqual(f1, f2)


class PngTests(unittest.TestCase):
    def test_png_structure_and_size_roundtrip(self):
        c = Canvas(5, 3)
        c.fill((1, 2, 3, 255))
        data = write_png(None, c)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(png_size(data), (5, 3))
        pos, idat = 8, b""
        while pos < len(data):
            n = struct.unpack(">I", data[pos:pos + 4])[0]
            if data[pos + 4:pos + 8] == b"IDAT":
                idat += data[pos + 8:pos + 8 + n]
            pos += 12 + n
        self.assertEqual(len(zlib.decompress(idat)), 3 * (5 * 4 + 1))

    def test_downsample_is_alpha_weighted(self):
        c = Canvas(2, 2)
        c.set(0, 0, (200, 100, 50, 255))
        c.set(1, 0, (200, 100, 50, 255))
        # bottom row stays fully transparent black: must not darken the color
        out = c.downsampled(2)
        r, g, b, a = out.get(0, 0)
        self.assertEqual((r, g, b), (200, 100, 50))
        self.assertEqual(a, 128)


if __name__ == "__main__":
    unittest.main()

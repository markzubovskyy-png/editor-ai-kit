import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "editor-ai-kit" / "skills" / "colour-grade" / "scripts"))

import grade  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class TestLutGeneration(unittest.TestCase):
    def test_lut_has_33_cubed_rows_in_range(self):
        for look in grade.LOOKS:
            rows = grade.build_lut("rec709", look, 1.0, 0.0)
            self.assertEqual(len(rows), 33 ** 3, look)
            self.assertTrue(all(0.0 <= v <= 1.0 for r in rows for v in r), look)

    def test_rec709_on_rec709_is_identity(self):
        size = 9
        rows = grade.build_lut("rec709", "rec709", 1.0, 0.0, size=size)
        grid = [i / (size - 1) for i in range(size)]
        expected = [(r, g, b) for b in grid for g in grid for r in grid]
        for got, want in zip(rows, expected):
            self.assertTrue(close(got, want), (got, want))

    def test_strength_zero_is_identity_for_every_look(self):
        for look in grade.LOOKS:
            for px in [(0.1, 0.2, 0.3), (0.5, 0.5, 0.5), (0.9, 0.4, 0.1)]:
                out = grade.grade_pixel(px, "rec709", look, 0.0, 0.0)
                self.assertTrue(close(out, px), (look, px, out))

    def test_red_changes_fastest(self):
        rows = grade.build_lut("rec709", "rec709", 1.0, 0.0, size=5)
        self.assertTrue(close(rows[1], (0.25, 0.0, 0.0)))
        self.assertTrue(close(rows[5], (0.0, 0.25, 0.0)))
        self.assertTrue(close(rows[25], (0.0, 0.0, 0.25)))

    def test_warm_is_warmer_and_cold_is_cooler(self):
        grey = (0.5, 0.5, 0.5)
        warm = grade.grade_pixel(grey, "rec709", "warm", 1.0, 0.0)
        cold = grade.grade_pixel(grey, "rec709", "cold", 1.0, 0.0)
        self.assertGreater(warm[0], warm[2])
        self.assertGreater(cold[2], cold[0])

    def test_cinematic_adds_contrast(self):
        lo = grade.luma(grade.grade_pixel((0.3, 0.3, 0.3), "rec709", "cinematic", 1.0, 0.0))
        hi = grade.luma(grade.grade_pixel((0.7, 0.7, 0.7), "rec709", "cinematic", 1.0, 0.0))
        self.assertGreater(hi - lo, 0.4)

    def test_exposure_brightens(self):
        base = grade.grade_pixel((0.4, 0.4, 0.4), "rec709", "rec709", 1.0, 0.0)
        up = grade.grade_pixel((0.4, 0.4, 0.4), "rec709", "rec709", 1.0, 1.0)
        self.assertGreater(up[0], base[0])

    def test_unknown_look_raises(self):
        with self.assertRaises(ValueError):
            grade.apply_look([0.5, 0.5, 0.5], "sepia", 1.0)


class TestColourMaths(unittest.TestCase):
    def test_slog3_middle_grey(self):
        # S-Log3 puts 18% grey at code value 420/1023
        self.assertAlmostEqual(grade.slog3_to_linear(420 / 1023), 0.18, places=3)

    def test_lab_round_trip(self):
        for px in [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.2, 0.5, 0.8), (0.9, 0.3, 0.1)]:
            back = grade.lab_to_rgb(grade.rgb_to_lab(px))
            self.assertTrue(close(back, px, 2e-3), (px, back))

    def test_match_to_itself_changes_nothing(self):
        pixels = [(0.2, 0.3, 0.4), (0.6, 0.5, 0.4), (0.8, 0.7, 0.2), (0.1, 0.1, 0.1)]
        f = grade.match_transfer(pixels, pixels, 1.0)
        for px in pixels:
            self.assertTrue(close(f(px), px, 2e-3))

    def test_match_moves_toward_a_warmer_reference(self):
        src = [(0.4, 0.4, 0.5), (0.5, 0.5, 0.6), (0.3, 0.3, 0.4)]
        ref = [(0.7, 0.5, 0.3), (0.8, 0.6, 0.4), (0.6, 0.4, 0.2)]
        f = grade.match_transfer(src, ref, 1.0)
        out = f((0.4, 0.4, 0.5))
        self.assertGreater(out[0] - out[2], 0.4 - 0.5)

    def test_guess_input(self):
        flat = [(0.35 + 0.002 * i, 0.36 + 0.002 * i, 0.34 + 0.002 * i) for i in range(100)]
        punchy = [(1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 1.0, 1.0), (1.0, 1.0, 1.0)] * 25
        self.assertEqual(grade.guess_input(flat)[0], "slog3")
        self.assertEqual(grade.guess_input(punchy)[0], "rec709")

    def test_auto_exposure_leaves_rec709_alone(self):
        self.assertEqual(grade.auto_exposure([(0.2, 0.2, 0.2)], "rec709"), 0.0)


class TestCubeFiles(unittest.TestCase):
    def test_write_and_read_cube(self):
        rows = grade.build_lut("rec709", "warm", 1.0, 0.0)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "warm.cube"
            grade.write_cube(rows, path, "warm")
            text = path.read_text()
            self.assertIn("LUT_3D_SIZE 33", text)
            size, back = grade.read_cube(path)
            self.assertEqual(size, 33)
            self.assertEqual(len(back), 33 ** 3)
            self.assertTrue(close(back[1234], rows[1234], 1e-5))


class TestStyleDefaults(unittest.TestCase):
    def test_example_style(self):
        d = grade.style_defaults(str(ROOT / "examples" / "brand" / "style.md"))
        self.assertEqual(d["look"], "cinematic")
        self.assertIsNone(d["reference"])

    def test_reference_relative_to_style(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "ref.jpg").write_bytes(b"x")
            style = Path(d) / "style.md"
            style.write_text("- Grade look: warm\n- Grade reference: ref.jpg\n")
            out = grade.style_defaults(str(style))
            self.assertEqual(out["look"], "warm")
            self.assertEqual(out["reference"], str(Path(d) / "ref.jpg"))

    def test_missing_style(self):
        self.assertEqual(grade.style_defaults("/nope/style.md"), {"look": None, "reference": None})


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not on PATH")
class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.clip = Path(cls.tmp.name) / "test clip.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x568:r=10:d=1",
                        "-pix_fmt", "yuv420p", str(cls.clip)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_options_still_is_a_2x2_grid(self):
        out = Path(self.tmp.name) / "graded"
        grade.main(["options", str(self.clip), "--out", str(out), "--still-width", "200"])
        still = out / "test clip_options.jpg"
        self.assertTrue(still.exists())
        err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(still)], capture_output=True, text=True).stderr
        self.assertIn("400x", err)

    def test_render_one_look(self):
        out = Path(self.tmp.name) / "graded2"
        grade.main(["render", str(self.clip), "--look", "warm", "--out", str(out), "--still-width", "200"])
        for name in ("test clip_warm.mp4", "test clip_warm.cube", "test clip_warm_before-after.jpg"):
            self.assertTrue((out / name).stat().st_size > 0, name)

    def test_render_without_a_look_asks_for_one(self):
        with self.assertRaises(SystemExit) as cm:
            grade.main(["render", str(self.clip), "--out", str(Path(self.tmp.name) / "g3")])
        self.assertIn("no look chosen", str(cm.exception))


if __name__ == "__main__":
    unittest.main()

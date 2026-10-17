import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "editor-ai-kit" / "skills" / "motion-render" / "scripts"))

import render as render_mod  # noqa: E402


class TestStyleParsing(unittest.TestCase):
    def test_load_style_parses_all_fields(self):
        style_md = ROOT / "examples" / "brand" / "style.md"
        style = render_mod.load_style(str(style_md))
        self.assertEqual(style["primary_color"], "#2F3B1F")
        self.assertEqual(style["secondary_color"], "#CFAE6B")
        self.assertEqual(style["primary_font"], "Poppins")
        self.assertEqual(style["secondary_font"], "Inter")

    def test_load_style_missing_file_falls_back_to_defaults(self):
        style = render_mod.load_style("/nonexistent/style.md")
        self.assertEqual(style["primary_color"], render_mod.DEFAULT_PRIMARY)
        self.assertEqual(style["secondary_color"], render_mod.DEFAULT_SECONDARY)

    def test_load_style_none_path(self):
        style = render_mod.load_style(None)
        self.assertEqual(style["primary_color"], render_mod.DEFAULT_PRIMARY)

    def test_hex_to_rgb(self):
        self.assertEqual(render_mod.hex_to_rgb("#2F3B1F"), (47, 59, 31))
        self.assertEqual(render_mod.hex_to_rgb("fff"), (255, 255, 255))

    def test_resolve_color_named(self):
        style = render_mod.load_style(None)
        self.assertEqual(render_mod.resolve_color("primary", style), render_mod.hex_to_rgb(render_mod.DEFAULT_PRIMARY))
        self.assertEqual(render_mod.resolve_color("secondary", style), render_mod.hex_to_rgb(render_mod.DEFAULT_SECONDARY))
        self.assertEqual(render_mod.resolve_color("#ABCDEF", style), render_mod.hex_to_rgb("#ABCDEF"))


class TestConfigParsing(unittest.TestCase):
    def test_load_json_config(self):
        config_path = ROOT / "examples" / "sample.json"
        config = render_mod.load_config(str(config_path))
        self.assertEqual(config["width"], 1080)
        self.assertEqual(config["height"], 1920)
        self.assertEqual(config["style"], "hand-drawn")
        self.assertGreaterEqual(len(config["graphics"]), 3)
        types = {g["type"] for g in config["graphics"]}
        self.assertTrue(types.issubset({
            "text_callout", "arrow", "circle", "underline",
            "number_counter", "checklist_tick",
        }))

    def test_config_duration_explicit(self):
        self.assertEqual(render_mod.config_duration({"duration": 5.0}), 5.0)

    def test_config_duration_from_graphics(self):
        config = {"graphics": [{"end": 1.0}, {"end": 3.2}]}
        self.assertEqual(render_mod.config_duration(config), 3.7)

    def test_config_duration_empty(self):
        self.assertEqual(render_mod.config_duration({}), 2.5)


class TestTimingHelpers(unittest.TestCase):
    def test_progress_bounds(self):
        self.assertEqual(render_mod.progress(0.0, 1.0, 2.0), 0.0)
        self.assertEqual(render_mod.progress(2.0, 1.0, 2.0), 1.0)
        self.assertEqual(render_mod.progress(1.5, 1.0, 2.0), 0.5)
        self.assertEqual(render_mod.progress(5.0, 1.0, 2.0), 1.0)

    def test_progress_instant(self):
        self.assertEqual(render_mod.progress(0.5, 1.0, 1.0), 0.0)
        self.assertEqual(render_mod.progress(1.0, 1.0, 1.0), 1.0)

    def test_alpha_envelope_fade_in_out(self):
        self.assertEqual(render_mod.alpha_envelope(-1, 0, 1), 0.0)
        self.assertGreater(render_mod.alpha_envelope(0.1, 0, 1, fade=0.15), 0.0)
        self.assertEqual(render_mod.alpha_envelope(0.5, 0, 1), 1.0)
        self.assertLess(render_mod.alpha_envelope(1.1, 0, 1, fade=0.15), 1.0)
        self.assertEqual(render_mod.alpha_envelope(2.0, 0, 1, fade=0.15), 0.0)

    def test_ease_out_monotonic(self):
        vals = [render_mod.ease_out(u) for u in (0.0, 0.25, 0.5, 0.75, 1.0)]
        self.assertEqual(vals[0], 0.0)
        self.assertEqual(vals[-1], 1.0)
        self.assertTrue(all(b >= a for a, b in zip(vals, vals[1:])))


class TestWobble(unittest.TestCase):
    def test_wobble_preserves_point_count_family(self):
        pts = [(0, 0), (100, 0), (100, 100)]
        out = render_mod.wobble(pts, seed=42)
        self.assertGreaterEqual(len(out), 2)

    def test_wobble_deterministic_for_same_seed(self):
        pts = [(0, 0), (50, 50), (100, 0)]
        a = render_mod.wobble(pts, seed=7)
        b = render_mod.wobble(pts, seed=7)
        self.assertTrue((a == b).all())

    def test_wobble_differs_for_different_seed(self):
        pts = [(0, 0), (50, 50), (100, 0)]
        a = render_mod.wobble(pts, seed=1)
        b = render_mod.wobble(pts, seed=2)
        self.assertFalse((a == b).all())


class TestUnknownGraphicType(unittest.TestCase):
    def test_unknown_type_raises(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (10, 10))
        draw = ImageDraw.Draw(img)
        style = render_mod.load_style(None)
        with self.assertRaises(ValueError):
            render_mod.render_graphic(draw, {"type": "nonsense"}, 0.0, style, 30, False)


class TestDrawTimeAndSalt(unittest.TestCase):
    def test_draw_key_overrides_share(self):
        self.assertAlmostEqual(render_mod.draw_time({"start": 0, "end": 4, "draw": 0.5}, 0.8), 0.5)
        self.assertAlmostEqual(render_mod.draw_time({"start": 0, "end": 4}, 0.8), 3.2)

    def test_stable_salt_is_deterministic(self):
        self.assertEqual(render_mod.stable_salt([540, 820]), render_mod.stable_salt([540, 820]))
        self.assertTrue(0 <= render_mod.stable_salt("x") < 97)


class TestExamples(unittest.TestCase):
    TYPES = {"text_callout", "arrow", "circle", "underline", "number_counter", "checklist_tick"}

    def test_every_example_config_uses_known_types(self):
        configs = list((ROOT / "examples" / "configs").glob("*.json")) + list((ROOT / "examples").glob("sample*.json"))
        self.assertGreaterEqual(len(configs), 5)
        for path in configs:
            cfg = render_mod.load_config(str(path))
            self.assertEqual((cfg["width"], cfg["height"]), (1080, 1920), path.name)
            for g in cfg["graphics"]:
                self.assertIn(g["type"], self.TYPES, path.name)

    def test_ideas_have_a_config_each_and_quote_the_script(self):
        ideas = json.loads((ROOT / "examples" / "ideas.json").read_text())["ideas"]
        script = (ROOT / "examples" / "script.txt").read_text()
        for idea in ideas:
            self.assertTrue((ROOT / "examples" / "configs" / f"{idea['id']}.json").exists(), idea["id"])
            self.assertIn(idea["line"], script.replace("\n", " "))
            self.assertIn(idea["type"], self.TYPES)


if __name__ == "__main__":
    unittest.main()

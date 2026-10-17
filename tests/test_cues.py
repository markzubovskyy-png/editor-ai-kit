import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "editor-ai-kit" / "skills" / "edit-handoff" / "scripts"))

import handoff as handoff_mod  # noqa: E402


class TestCueSheet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_seconds_to_timecode(self):
        self.assertEqual(handoff_mod.seconds_to_timecode(0), "00:00:00.000")
        self.assertEqual(handoff_mod.seconds_to_timecode(65.5), "00:01:05.500")
        self.assertEqual(handoff_mod.seconds_to_timecode(3661.25), "01:01:01.250")

    def test_load_ideas_dict_wrapped(self):
        ideas_path = self.tmp_path / "ideas.json"
        ideas_path.write_text(json.dumps({"ideas": [{"line": "a"}, {"line": "b"}]}))
        ideas = handoff_mod.load_ideas(str(ideas_path))
        self.assertEqual(len(ideas), 2)

    def test_load_ideas_bare_list(self):
        ideas_path = self.tmp_path / "ideas.json"
        ideas_path.write_text(json.dumps([{"line": "a"}]))
        ideas = handoff_mod.load_ideas(str(ideas_path))
        self.assertEqual(len(ideas), 1)

    def test_build_cues_without_render_dir(self):
        ideas = [{"id": "moment-1", "line": "hello", "timecode": 1.5, "type": "text_callout"}]
        cues = handoff_mod.build_cues(ideas, None)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["line"], "hello")
        self.assertEqual(cues[0]["file"], "")

    def test_build_cues_matches_render_file(self):
        render_dir = self.tmp_path / "render"
        render_dir.mkdir()
        (render_dir / "moment-1.mov").write_bytes(b"fake")
        ideas = [{"id": "moment-1", "line": "hello", "timecode": 0, "type": "text_callout"}]
        cues = handoff_mod.build_cues(ideas, render_dir)
        self.assertEqual(cues[0]["file"], "render/moment-1.mov")

    def test_match_prefers_mov_and_ignores_longer_ids(self):
        render_dir = self.tmp_path / "render"
        render_dir.mkdir()
        for name in ("moment-1_preview.mp4", "moment-1.webm", "moment-1.mov", "moment-10.mov"):
            (render_dir / name).write_bytes(b"x")
        self.assertEqual(handoff_mod.match_render_file(render_dir, {"id": "moment-1"}), "render/moment-1.mov")
        (render_dir / "moment-1.mov").unlink()
        self.assertEqual(handoff_mod.match_render_file(render_dir, {"id": "moment-1"}), "render/moment-1.webm")
        self.assertIsNone(handoff_mod.match_render_file(render_dir, {"id": "moment-2"}))

    def test_run_writes_cues_and_zip(self):
        ideas_path = self.tmp_path / "ideas.json"
        ideas_path.write_text(json.dumps({"ideas": [
            {"id": "moment-1", "line": "one", "timecode": 0, "type": "text_callout"},
            {"id": "moment-2", "line": "two", "timecode": 1, "type": "arrow"},
        ]}))
        render_dir = self.tmp_path / "render"
        render_dir.mkdir()
        (render_dir / "moment-1.mov").write_bytes(b"fake-mov")
        out_dir = self.tmp_path / "handoff"

        result = handoff_mod.run(str(ideas_path), str(render_dir), str(out_dir))

        self.assertEqual(result["cues"], 2)
        self.assertTrue(Path(result["cues_md"]).exists())
        self.assertTrue(Path(result["cues_csv"]).exists())
        self.assertTrue(Path(result["zip"]).exists())

        with zipfile.ZipFile(result["zip"]) as zf:
            names = zf.namelist()
        self.assertIn("cues.md", names)
        self.assertIn("cues.csv", names)
        self.assertTrue(any("moment-1.mov" in n for n in names))

        csv_text = Path(result["cues_csv"]).read_text()
        self.assertIn("one", csv_text)
        self.assertIn("two", csv_text)


if __name__ == "__main__":
    unittest.main()

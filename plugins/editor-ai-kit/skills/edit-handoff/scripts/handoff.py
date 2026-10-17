#!/usr/bin/env python3
"""
editor-ai-kit edit-handoff

Packs a motion-ideas output (ideas.json) and the rendered overlay files into
a cue sheet an editor can drop straight into CapCut or Premiere, plus a zip
of everything.

Usage:
    python3 handoff.py --ideas ideas.json --render-dir ./render --out ./handoff

Writes:
    <out>/cues.md     human-readable cue sheet
    <out>/cues.csv     line,timecode,file,type,notes
    <out>/handoff.zip  cues + every file under --render-dir
"""
from __future__ import annotations

import argparse
import csv
import json
import zipfile
from pathlib import Path


def seconds_to_timecode(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def load_ideas(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        ideas = data.get("ideas", [])
    else:
        ideas = data
    return ideas


PREFERRED_EXT = [".mov", ".webm", ".mp4", ".png", ".gif"]


def match_render_file(render_dir: Path, idea: dict):
    """Find the rendered file for an idea by its id (or slug).

    A file matches when its name is the id itself or the id followed by "_"
    (so moment-1 matches moment-1.mov and moment-1_preview.mp4 but never
    moment-10.mov). When several formats exist the editing master wins:
    .mov (ProRes with alpha), then .webm, then .mp4. Returns the path relative
    to render_dir, prefixed with "render/" to match the layout inside the zip,
    or None when nothing matches (the editor fills it in)."""
    if not render_dir or not render_dir.exists():
        return None
    key = str(idea.get("id") or idea.get("slug") or idea.get("line_ref") or "").strip().lower()
    if not key:
        return None
    hits = []
    for f in render_dir.rglob("*"):
        if not f.is_file():
            continue
        stem = f.stem.lower()
        if stem == key or stem.startswith(key + "_"):
            hits.append(f)
    if not hits:
        return None

    def rank(f):
        ext = f.suffix.lower()
        preview = f.stem.lower().endswith("_preview")
        return (PREFERRED_EXT.index(ext) if ext in PREFERRED_EXT else len(PREFERRED_EXT), preview, f.name)

    best = sorted(hits, key=rank)[0]
    return str(Path("render") / best.relative_to(render_dir))


def build_cues(ideas, render_dir: Path):
    cues = []
    for i, idea in enumerate(ideas, start=1):
        line = idea.get("line") or idea.get("script_line") or idea.get("text") or ""
        timecode_raw = idea.get("timecode")
        if timecode_raw is None:
            timecode_raw = idea.get("start", 0)
        if isinstance(timecode_raw, str):
            timecode = timecode_raw
        else:
            timecode = seconds_to_timecode(timecode_raw)
        gtype = idea.get("type") or idea.get("graphic_type") or "overlay"
        rendered = match_render_file(render_dir, idea) if render_dir else None
        cues.append({
            "index": i,
            "line": line,
            "timecode": timecode,
            "type": gtype,
            "file": rendered or "",
            "notes": idea.get("style_note") or idea.get("notes") or "",
        })
    return cues


def write_markdown(cues, out_path: Path):
    lines = ["# Cue sheet", "", "| # | Timecode | Line | Type | File | Notes |", "|---|---|---|---|---|---|"]
    for c in cues:
        lines.append(f"| {c['index']} | {c['timecode']} | {c['line']} | {c['type']} | {c['file']} | {c['notes']} |")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_csv(cues, out_path: Path):
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["index", "timecode", "line", "type", "file", "notes"])
        writer.writeheader()
        for c in cues:
            writer.writerow(c)


def write_zip(cues_md: Path, cues_csv: Path, render_dir: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(cues_md, cues_md.name)
        zf.write(cues_csv, cues_csv.name)
        if render_dir and render_dir.exists():
            for f in render_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, Path("render") / f.relative_to(render_dir))


def run(ideas_path, render_dir, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    render_dir = Path(render_dir) if render_dir else None

    ideas = load_ideas(ideas_path)
    cues = build_cues(ideas, render_dir)

    cues_md = out_dir / "cues.md"
    cues_csv = out_dir / "cues.csv"
    write_markdown(cues, cues_md)
    write_csv(cues, cues_csv)

    zip_path = out_dir / "handoff.zip"
    write_zip(cues_md, cues_csv, render_dir, zip_path)

    return {"cues": len(cues), "cues_md": str(cues_md), "cues_csv": str(cues_csv), "zip": str(zip_path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a cue sheet + zip from motion-ideas output and rendered overlays.")
    parser.add_argument("--ideas", required=True, help="Path to ideas.json from motion-ideas")
    parser.add_argument("--render-dir", default=None, help="Directory with rendered overlay files")
    parser.add_argument("--out", default="./handoff", help="Output directory")
    args = parser.parse_args(argv)

    result = run(args.ideas, args.render_dir, args.out)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

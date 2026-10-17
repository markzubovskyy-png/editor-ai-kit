#!/usr/bin/env python3
"""
editor-ai-kit motion-render

A small, dependency-light renderer that turns a JSON/YAML graphics config
into a transparent 1080x1920 overlay, ready to drop on top of any clip:

  - <name>.mov   ProRes 4444 with alpha (editing-grade, big file)
  - <name>.webm  VP9 with alpha (small, good for messaging apps)
  - <name>_preview.mp4  the same overlay composited on black, so you can
    scrub it anywhere without an alpha-aware player

Dependencies: Python 3.9+, Pillow, numpy, and ffmpeg on PATH. Nothing else.

Config format (JSON or YAML, see examples/sample.json):
{
  "width": 1080, "height": 1920, "fps": 30,
  "style": "hand-drawn" | "clean",
  "graphics": [
    {"type": "text_callout", "text": "...", "x": 540, "y": 400,
     "start": 0.2, "end": 2.4, "size": 72, "color": "primary"},
    {"type": "arrow", "from": [200, 600], "to": [700, 900],
     "start": 0.5, "end": 1.8, "color": "secondary"},
    {"type": "circle", "center": [540, 960], "radius": 220,
     "start": 0.0, "end": 2.0, "draw": 0.8},
    {"type": "underline", "from": [300, 1200], "to": [780, 1200],
     "start": 1.0, "end": 1.6},
    {"type": "number_counter", "from": 0, "to": 5, "prefix": "",
     "suffix": " steps", "x": 540, "y": 700, "start": 0.0, "end": 1.5},
    {"type": "checklist_tick", "items": ["Brand", "Ideas", "Render"],
     "x": 200, "y": 500, "start": 0.0, "end": 3.0}
  ]
}

Every graphic takes "start" and "end" in seconds. Optional "draw" (seconds)
sets how long the draw on or count up takes, then the graphic holds until
"end" and fades out.

Colours and fonts come from brand/style.md (see brand-style skill). Any
graphic can override "color" with a literal hex string instead of
"primary"/"secondary".
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    import numpy as np
except ImportError:  # pragma: no cover, numpy is needed for the hand-drawn wobble
    np = None

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_FPS = 30
DEFAULT_STYLE = "hand-drawn"
DEFAULT_PRIMARY = "#2F3B1F"     # dark olive, a safe default if style.md is missing
DEFAULT_SECONDARY = "#CFAE6B"   # warm tan accent
BOIL_FRAMES = 3                 # frames per "boil" step at 30fps = ~10fps line jitter, like a marker

FONT_CACHE_DIR = Path(os.environ.get("EDITOR_AI_KIT_FONT_CACHE", str(Path.home() / ".cache" / "editor-ai-kit" / "fonts")))

# A short list of well-known OFL (redistributable) Google Fonts we can fetch
# on demand. We never ship font binaries in this repo; we either use a system
# font the editor already has, or pull one of these at render time and cache it.
GOOGLE_FONT_REPO = "https://raw.githubusercontent.com/google/fonts/main/ofl"
GOOGLE_FONT_FAMILIES = {
    "inter": ("inter", "Inter[opsz,wght].ttf"),
    "poppins": ("poppins", "Poppins-Bold.ttf"),
    "montserrat": ("montserrat", "Montserrat[wght].ttf"),
}

SYSTEM_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:\\Windows\\Fonts\\arialbd.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]

_FONT_CACHE: dict = {}


# --------------------------------------------------------------------------
# Style + font helpers
# --------------------------------------------------------------------------
def hex_to_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def load_style(style_path):
    """Parse brand/style.md. Looks for simple `Key: value` lines; anything
    missing falls back to a safe default so the renderer never hard-fails
    just because style.md is incomplete."""
    style = {
        "primary_color": DEFAULT_PRIMARY,
        "secondary_color": DEFAULT_SECONDARY,
        "primary_font": None,
        "secondary_font": None,
    }
    if not style_path:
        return style
    p = Path(style_path)
    if not p.exists():
        return style
    text = p.read_text(encoding="utf-8")
    patterns = {
        "primary_color": r"(?im)^\s*[-*]?\s*primary colou?r\s*[:\-]\s*(#?[0-9a-fA-F]{3,8})",
        "secondary_color": r"(?im)^\s*[-*]?\s*secondary colou?r\s*[:\-]\s*(#?[0-9a-fA-F]{3,8})",
        "primary_font": r"(?im)^\s*[-*]?\s*primary font\s*[:\-]\s*([^\n]+)",
        "secondary_font": r"(?im)^\s*[-*]?\s*secondary font\s*[:\-]\s*([^\n]+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, text)
        if m:
            style[key] = m.group(1).strip()
    return style


def resolve_color(value, style):
    if value in (None, "primary"):
        return hex_to_rgb(style["primary_color"])
    if value == "secondary":
        return hex_to_rgb(style["secondary_color"])
    if isinstance(value, str) and value.startswith("#"):
        return hex_to_rgb(value)
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return hex_to_rgb(style["primary_color"])


def _try_fetch_google_font(name: str):
    key = name.strip().lower()
    if key not in GOOGLE_FONT_FAMILIES:
        return None
    folder, filename = GOOGLE_FONT_FAMILIES[key]
    dest = FONT_CACHE_DIR / filename
    if dest.exists():
        return str(dest)
    url = f"{GOOGLE_FONT_REPO}/{folder}/{filename}"
    try:
        FONT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=4) as resp:
            data = resp.read()
        dest.write_bytes(data)
        return str(dest)
    except Exception:
        return None


def find_font_path(name_hint):
    """Best-effort font resolution: a system font matching the hint, a cached
    or freshly fetched Google Font, then any system fallback, then None
    (caller uses Pillow's bitmap default font)."""
    if name_hint:
        hint = name_hint.strip().lower()
        fetched = _try_fetch_google_font(hint)
        if fetched:
            return fetched
        for candidate in SYSTEM_FONT_CANDIDATES:
            if hint.split()[0] in candidate.lower() and Path(candidate).exists():
                return candidate
    for candidate in SYSTEM_FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def get_font(name_hint, size):
    key = (name_hint, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    path = find_font_path(name_hint)
    try:
        font = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# --------------------------------------------------------------------------
# Hand-drawn wobble (adapted, generic, no client assets)
# --------------------------------------------------------------------------
def stable_salt(value):
    """Same salt on every run and every machine (Python's hash() is randomised per process)."""
    return zlib.crc32(str(value).encode("utf-8")) % 97


def boil_seed(t, fps, salt=0):
    return int(t * fps) // BOIL_FRAMES * 7919 + salt * 104729


def resample(pts, step=6.0):
    pts = np.asarray(pts, dtype=np.float64)
    if len(pts) < 2:
        return pts
    seg = np.sqrt(((pts[1:] - pts[:-1]) ** 2).sum(1))
    total = float(np.concatenate([[0], np.cumsum(seg)])[-1])
    n = max(2, int(total / step) + 1)
    cum = np.concatenate([[0], np.cumsum(seg)])
    s = np.linspace(0, total, n)
    return np.stack([np.interp(s, cum, pts[:, 0]), np.interp(s, cum, pts[:, 1])], axis=1)


def wobble(pts, seed, amp=3.0, freq=0.006):
    """Displace a polyline along its normal with smooth low-frequency noise,
    so repeated draws of the same shape look hand-drawn rather than identical."""
    rng = np.random.default_rng(seed)
    p = resample(pts)
    if len(p) < 2:
        return p
    d = np.gradient(p, axis=0)
    nrm = np.stack([-d[:, 1], d[:, 0]], axis=1)
    norms = np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-6)
    nrm = nrm / norms
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(p, axis=0), axis=1))])
    off = np.zeros(len(p))
    for k in range(3):
        f = freq * (1 + k * 1.7) * rng.uniform(0.7, 1.3)
        off += amp / (k + 1) * np.sin(s * f * 2 * math.pi + rng.uniform(0, 2 * math.pi))
    q = p + nrm * off[:, None]
    q += rng.normal(0, amp * 0.03, q.shape)
    return q


def draw_stroke(draw, pts, width, color, frac=1.0):
    """Draw a polyline, optionally only the first `frac` of it (draw-on reveal)."""
    pts = list(pts)
    n = len(pts)
    if n < 2 or frac <= 0:
        return
    m = max(2, int(math.ceil(n * min(max(frac, 0.0), 1.0))))
    pts = pts[:m]
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        draw.line([(x0, y0), (x1, y1)], fill=color, width=width)
        r = width / 2
        draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=color)


# --------------------------------------------------------------------------
# Easing + timing
# --------------------------------------------------------------------------
def ease_out(u):
    u = min(max(u, 0.0), 1.0)
    return 1 - (1 - u) ** 3


def progress(t, start, end):
    """0..1 reveal progress for a graphic active between start and end."""
    if end <= start:
        return 1.0 if t >= start else 0.0
    return min(max((t - start) / (end - start), 0.0), 1.0)


def draw_time(g, default_share):
    """How long the draw on (or count up) takes. Optional "draw" key in seconds;
    otherwise a share of the graphic's on screen time. Setting "draw" lets a
    graphic finish drawing early and then hold until "end"."""
    start, end = g.get("start", 0), g.get("end", 1)
    if "draw" in g:
        return max(float(g["draw"]), 0.01)
    return max(end - start, 0.01) * default_share


def alpha_envelope(t, start, end, fade=0.15):
    """Fade in at start, hold, fade out just after end."""
    if t < start:
        return 0.0
    if t <= end:
        return min(1.0, (t - start) / max(fade, 1e-6))
    if t <= end + fade:
        return max(0.0, 1.0 - (t - end) / fade)
    return 0.0


# --------------------------------------------------------------------------
# Graphic renderers (each draws onto a fresh transparent frame)
# --------------------------------------------------------------------------
def render_text_callout(draw, g, t, style, fps):
    a = alpha_envelope(t, g.get("start", 0), g.get("end", 1))
    if a <= 0.004:
        return
    text = str(g.get("text", ""))
    size = int(g.get("size", 72))
    color = resolve_color(g.get("color", "primary"), style) + (int(255 * a),)
    font = get_font(style.get("primary_font"), size)
    x, y = g.get("x", DEFAULT_WIDTH // 2), g.get("y", DEFAULT_HEIGHT // 2)
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x - w / 2, y - h / 2), text, font=font, fill=color)


def render_arrow(draw, g, t, style, fps, hand_drawn):
    start, end = g.get("start", 0), g.get("end", 1)
    frac = progress(t, start, start + draw_time(g, 0.7))
    a = alpha_envelope(t, start, end)
    if a <= 0.004 or frac <= 0:
        return
    fx, fy = g.get("from", [200, 600])
    tx, ty = g.get("to", [700, 900])
    color = resolve_color(g.get("color", "primary"), style) + (int(255 * a),)
    width = int(g.get("width", 10))
    pts = [(fx, fy), (tx, ty)]
    if hand_drawn:
        pts = wobble(pts, boil_seed(t, fps, salt=stable_salt(g.get("from"))), amp=4.0)
    draw_stroke(draw, pts, width, color, frac=1.0 if frac >= 1 else frac)
    if frac >= 0.999:
        ang = math.atan2(ty - fy, tx - fx)
        head = 22
        for da in (0.5, -0.5):
            hx = tx - head * math.cos(ang + da)
            hy = ty - head * math.sin(ang + da)
            draw.line([(tx, ty), (hx, hy)], fill=color, width=width)


def render_circle(draw, g, t, style, fps, hand_drawn):
    start, end = g.get("start", 0), g.get("end", 1)
    frac = progress(t, start, start + draw_time(g, 0.8))
    a = alpha_envelope(t, start, end)
    if a <= 0.004 or frac <= 0:
        return
    cx, cy = g.get("center", [DEFAULT_WIDTH // 2, DEFAULT_HEIGHT // 2])
    r = g.get("radius", 200)
    width = int(g.get("width", 10))
    color = resolve_color(g.get("color", "primary"), style) + (int(255 * a),)
    n = 90
    angles = [2 * math.pi * i / n for i in range(n + 1)]
    pts = [(cx + r * math.cos(ang), cy + r * math.sin(ang)) for ang in angles]
    if hand_drawn:
        pts = wobble(pts, boil_seed(t, fps, salt=stable_salt(g.get("center"))), amp=5.0)
    draw_stroke(draw, pts, width, color, frac=frac)


def render_underline(draw, g, t, style, fps, hand_drawn):
    start, end = g.get("start", 0), g.get("end", 1)
    frac = progress(t, start, start + draw_time(g, 1.0))
    a = alpha_envelope(t, start, end)
    if a <= 0.004 or frac <= 0:
        return
    fx, fy = g.get("from", [300, 1200])
    tx, ty = g.get("to", [780, 1200])
    width = int(g.get("width", 12))
    color = resolve_color(g.get("color", "secondary"), style) + (int(255 * a),)
    pts = [(fx, fy), (tx, ty)]
    if hand_drawn:
        pts = wobble(pts, boil_seed(t, fps, salt=stable_salt(g.get("from"))), amp=3.0)
    draw_stroke(draw, pts, width, color, frac=frac)


def render_number_counter(draw, g, t, style, fps):
    start, end = g.get("start", 0), g.get("end", 1)
    a = alpha_envelope(t, start, end)
    if a <= 0.004:
        return
    u = ease_out(progress(t, start, start + draw_time(g, 1.0)))
    v0, v1 = g.get("from", 0), g.get("to", 100)
    value = v0 + (v1 - v0) * u
    is_int = isinstance(v0, int) and isinstance(v1, int)
    text = f"{g.get('prefix', '')}{int(round(value)) if is_int else round(value, 1)}{g.get('suffix', '')}"
    size = int(g.get("size", 96))
    color = resolve_color(g.get("color", "primary"), style) + (int(255 * a),)
    font = get_font(style.get("primary_font"), size)
    x, y = g.get("x", DEFAULT_WIDTH // 2), g.get("y", DEFAULT_HEIGHT // 2)
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x - w / 2, y - h / 2), text, font=font, fill=color)


def render_checklist_tick(draw, g, t, style, fps):
    start, end = g.get("start", 0), g.get("end", 1)
    items = g.get("items", [])
    if not items:
        return
    x, y = g.get("x", 200), g.get("y", 500)
    size = int(g.get("size", 48))
    gap = int(g.get("gap", size * 1.6))
    color = resolve_color(g.get("color", "primary"), style)
    font = get_font(style.get("secondary_font") or style.get("primary_font"), size)
    per_item = draw_time(g, 1.0) / max(len(items), 1)
    for i, label in enumerate(items):
        item_start = start + i * per_item
        item_end = item_start + per_item * 0.8
        a = alpha_envelope(t, item_start, max(item_end, end))
        if a <= 0.004:
            continue
        iy = y + i * gap
        c = color + (int(255 * a),)
        box = size
        draw.rounded_rectangle([x, iy, x + box, iy + box], radius=box * 0.2, outline=c, width=4)
        u = ease_out(progress(t, item_start, item_end))
        if u > 0:
            p1 = (x + box * 0.22, iy + box * 0.55)
            p2 = (x + box * 0.42, iy + box * 0.78)
            p3 = (x + box * 0.8, iy + box * 0.25)
            pts = [p1, p2, p3]
            n_draw = 2 if u < 0.5 else 3
            draw_stroke(draw, pts[:n_draw + 1] if n_draw < len(pts) else pts, max(4, box // 8), c, frac=1.0)
        draw.text((x + box + 24, iy + box * 0.1), label, font=font, fill=c)


RENDERERS_HAND_AWARE = {"arrow", "circle", "underline"}


def render_graphic(draw, g, t, style, fps, hand_drawn):
    gtype = g.get("type")
    if gtype == "text_callout":
        render_text_callout(draw, g, t, style, fps)
    elif gtype == "arrow":
        render_arrow(draw, g, t, style, fps, hand_drawn)
    elif gtype == "circle":
        render_circle(draw, g, t, style, fps, hand_drawn)
    elif gtype == "underline":
        render_underline(draw, g, t, style, fps, hand_drawn)
    elif gtype == "number_counter":
        render_number_counter(draw, g, t, style, fps)
    elif gtype == "checklist_tick":
        render_checklist_tick(draw, g, t, style, fps)
    else:
        raise ValueError(f"Unknown graphic type: {gtype!r}")


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------
def load_config(path):
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith((".yml", ".yaml")):
        return parse_simple_yaml(text)
    return json.loads(text)


def parse_simple_yaml(text):
    """Tiny YAML subset loader so this skill stays dependency-light (no
    PyYAML requirement). Supports the flat/nested structure our configs use.
    If the editor's config is more complex than this, they can write JSON
    instead; both are first-class."""
    try:
        import yaml  # use it if it happens to be installed, it's more correct
        return yaml.safe_load(text)
    except ImportError:
        pass
    # Fallback: ask the editor to use JSON; most agents will anyway.
    raise RuntimeError(
        "YAML config given but PyYAML is not installed. Install pyyaml, or "
        "write the config as JSON (same structure) instead."
    )


def config_duration(config):
    if "duration" in config:
        return float(config["duration"])
    end_times = [g.get("end", 0) for g in config.get("graphics", [])]
    return max(end_times, default=2.0) + 0.5


# --------------------------------------------------------------------------
# Frame generation + ffmpeg encode
# --------------------------------------------------------------------------
def render_frames(config, style, outdir: Path):
    width = int(config.get("width", DEFAULT_WIDTH))
    height = int(config.get("height", DEFAULT_HEIGHT))
    fps = int(config.get("fps", DEFAULT_FPS))
    hand_drawn = config.get("style", DEFAULT_STYLE) == "hand-drawn"
    duration = config_duration(config)
    n_frames = max(1, int(duration * fps))
    outdir.mkdir(parents=True, exist_ok=True)
    for i in range(n_frames):
        t = i / fps
        frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(frame)
        for g in config.get("graphics", []):
            render_graphic(draw, g, t, style, fps, hand_drawn)
        frame.save(outdir / f"frame_{i:05d}.png")
    return n_frames, fps, width, height


def run(cmd):
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}):\n{result.stdout}")
    return result.stdout


def encode_outputs(frames_dir: Path, fps: int, out_basename: str, outdir: Path):
    outdir.mkdir(parents=True, exist_ok=True)
    pattern = str(frames_dir / "frame_%05d.png")

    mov_path = outdir / f"{out_basename}.mov"
    run([
        "ffmpeg", "-y", "-framerate", str(fps), "-i", pattern,
        "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le",
        "-vendor", "apl0", str(mov_path),
    ])

    webm_path = outdir / f"{out_basename}.webm"
    run([
        "ffmpeg", "-y", "-framerate", str(fps), "-i", pattern,
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
        "-b:v", "0", "-crf", "30", str(webm_path),
    ])

    preview_path = outdir / f"{out_basename}_preview.mp4"
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920",
        "-framerate", str(fps), "-i", pattern,
        "-filter_complex", "[0:v][1:v]overlay=shortest=1,format=yuv420p",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(preview_path),
    ])
    return mov_path, webm_path, preview_path


def render(config_path, style_path, out_dir, out_basename="overlay", keep_frames=False):
    config = load_config(config_path)
    style = load_style(style_path)
    with tempfile.TemporaryDirectory(prefix="editor-ai-kit-frames-") as tmp:
        frames_dir = Path(tmp)
        n_frames, fps, width, height = render_frames(config, style, frames_dir)
        out_dir = Path(out_dir)
        mov, webm, preview = encode_outputs(frames_dir, fps, out_basename, out_dir)
        if keep_frames:
            kept = out_dir / f"{out_basename}_frames"
            shutil.copytree(frames_dir, kept, dirs_exist_ok=True)
    return {
        "frames": n_frames, "fps": fps, "width": width, "height": height,
        "mov": str(mov), "webm": str(webm), "preview": str(preview),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description="Render a motion graphics config into transparent overlays.")
    parser.add_argument("config", help="Path to the graphics config (JSON or YAML)")
    parser.add_argument("--style", default=None, help="Path to brand/style.md")
    parser.add_argument("--out", default="./render", help="Output directory")
    parser.add_argument("--name", default="overlay", help="Output file basename")
    parser.add_argument("--keep-frames", action="store_true", help="Also keep the PNG frame sequence")
    args = parser.parse_args(argv)

    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found on PATH. Install ffmpeg and try again.", file=sys.stderr)
        return 1

    result = render(args.config, args.style, args.out, args.name, args.keep_frames)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

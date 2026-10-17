#!/usr/bin/env python3
"""editor-ai-kit colour-grade

Grade clips with one of four looks, or match a reference still. Every grade is
baked into a .cube 3D LUT (generated right here, no LUT files to download) and
applied with ffmpeg's lut3d filter, so what the editor sees in the options
still is exactly what they get in the render.

  python3 grade.py options clip.mp4 [more clips or folders]
      One 2x2 still per clip: 1 WARM, 2 COLD, 3 CINEMATIC, 4 REC709.

  python3 grade.py render clip.mp4 --look cinematic
  python3 grade.py render clip.mp4 --look all
  python3 grade.py render clip.mp4 --match reference.jpg
      Full graded video(s) + the .cube used + a before/after still.

  python3 grade.py lut --look warm --out warm.cube
      Just the LUT, for Premiere, DaVinci Resolve, Final Cut or CapCut.

Looks
  warm       golden, soft, a little richer
  cold       cool blue teal mids and shadows, restrained colour
  cinematic  contrast curve, teal shadows, warm highlights, calmer cyans
  rec709     clean and neutral. The safe default: just a correct conversion.

Options
  --input auto|slog3|rec709  what the footage is. auto guesses from contrast
                             (log footage looks flat and grey). slog3 = Sony
                             S-Log3 / S-Gamut3.Cine. Anything already looking
                             normal is rec709.
  --exposure STOPS           nudge exposure, e.g. -0.5 or 0.5. Default: auto
                             for log footage, 0 for rec709.
  --strength 0..1            how hard the look (or match) is applied.
                             Default 1 for looks, 0.75 for --match.
  --style brand/style.md     read the team's default look / reference still
                             from the brand style file (lines "Grade look:"
                             and "Grade reference:").
  --out DIR                  default ./graded
  --chat-copy                also write a copy under 45 MB, small enough to
                             send back through a Telegram bot (50 MB limit).
  --prores                   write ProRes 422 HQ .mov masters instead of .mp4.

Needs Python 3.9+ and ffmpeg on PATH. numpy is NOT required. Pillow is used
for the labels on the options still if it is installed; without it the still
is built by ffmpeg alone and the tiles stay in the same order, unlabelled.
The last line printed is a JSON summary of everything written.
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
from pathlib import Path

SIZE = 33                                   # LUT grid, 33^3 is the common size every NLE reads
LOOKS = ["warm", "cold", "cinematic", "rec709"]
LABELS = {"warm": "1  WARM", "cold": "2  COLD", "cinematic": "3  CINEMATIC", "rec709": "4  REC709"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mxf", ".mts", ".avi", ".mkv", ".webm"}
LUMA = (0.2126, 0.7152, 0.0722)
CHAT_LIMIT_MB = 45                          # Telegram bots can send files up to 50 MB

# S-Gamut3.Cine to Rec.709 primaries (linear light), from Sony's technical summary
GAMUT = (
    (1.6269474, -0.5401385, -0.0868089),
    (-0.1785155, 1.4179409, -0.2394254),
    (-0.0444361, -0.1959200, 1.2403561),
)


# ── colour maths (pure Python, one RGB triple at a time) ─────────────────────

def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def luma(rgb):
    return rgb[0] * LUMA[0] + rgb[1] * LUMA[1] + rgb[2] * LUMA[2]


def slog3_to_linear(x):
    x = x * 1023.0
    if x >= 171.2102946929:
        return (10.0 ** ((x - 420.0) / 261.5)) * 0.19 - 0.01
    return (x - 95.0) * 0.01125 / (171.2102946929 - 95.0)


def filmic(x):
    """Narkowicz ACES fit: soft highlight shoulder, gentle toe."""
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e))


def oetf709(x):
    x = max(x, 0.0)
    return 4.5 * x if x < 0.018 else 1.099 * x ** 0.45 - 0.099


def eotf709(v):
    v = clamp(v)
    return v / 4.5 if v < 0.081 else ((v + 0.099) / 1.099) ** (1 / 0.45)


def to_display(rgb, src, exposure):
    """Camera code values to Rec.709 display values."""
    gain = 2.0 ** exposure
    if src == "slog3":
        lin = [slog3_to_linear(c) for c in rgb]
        lin = [max(sum(m * c for m, c in zip(row, lin)), 0.0) * gain for row in GAMUT]
        return [oetf709(filmic(c * 0.8)) for c in lin]
    if exposure == 0:
        return [clamp(c) for c in rgb]
    return [clamp(oetf709(eotf709(c) * gain)) for c in rgb]


def add(rgb, k, w, vec):
    return [c + k * w * v for c, v in zip(rgb, vec)]


def sat(rgb, amount):
    y = luma(rgb)
    return [y + (c - y) * amount for c in rgb]


def s_curve(rgb, amount):
    y = luma(rgb)
    s = clamp(y + amount * (y - 0.5) * (1 - abs(2 * y - 1)))
    return [c + (s - y) for c in rgb]


def tame_cyan(rgb, k, amount=0.38):
    """Water and skies go neon cyan after a log conversion; pull them back to a deep teal."""
    cyan = clamp((min(rgb[1], rgb[2]) - rgb[0]) * 3.0)
    y = luma(rgb)
    rgb = [y + (c - y) * (1 - k * amount * cyan) for c in rgb]
    return add(rgb, k, cyan, (0.0, -0.025, 0.005))


def apply_look(rgb, name, k):
    if name == "rec709" or k == 0:
        return [clamp(c) for c in rgb]
    y = luma(rgb)
    shadow = clamp(1 - y * 2.2)
    high = clamp((y - 0.45) * 1.8)
    mid = 1 - abs(2 * y - 1)
    if name == "cinematic":
        rgb = s_curve(rgb, k * 0.55)
        rgb = add(rgb, k, shadow, (-0.025, 0.006, 0.022))
        rgb = add(rgb, k, high, (0.03, 0.012, -0.03))
        rgb = tame_cyan(rgb, k)
        rgb = sat(rgb, 1 - k * 0.05)
        rgb = [c * (1 - k * 0.03) + k * 0.015 for c in rgb]
    elif name == "warm":
        rgb = s_curve(rgb, k * 0.35)
        rgb = tame_cyan(rgb, k, 0.3)
        rgb = add(rgb, k, mid, (0.06, 0.022, -0.055))
        rgb = add(rgb, k, high, (0.035, 0.015, -0.04))
        rgb = add(rgb, k, shadow, (0.02, 0.004, -0.018))
        rgb = sat(rgb, 1 + k * 0.08)
        rgb = [c * (1 - k * 0.02) + k * 0.01 for c in rgb]
    elif name == "cold":
        rgb = s_curve(rgb, k * 0.45)
        rgb = add(rgb, k, mid, (-0.045, 0.0, 0.045))
        rgb = add(rgb, k, shadow, (-0.03, 0.01, 0.04))
        rgb = add(rgb, k, high, (-0.015, 0.0, 0.018))
        rgb = sat(rgb, 1 - k * 0.18)
    else:
        raise ValueError(f"unknown look {name!r}, pick one of {', '.join(LOOKS)}")
    return [clamp(c) for c in rgb]


# Lab (D65), used to match a reference still
_M = ((0.4124, 0.3576, 0.1805), (0.2126, 0.7152, 0.0722), (0.0193, 0.1192, 0.9505))
_MI = ((3.2406, -1.5372, -0.4986), (-0.9689, 1.8758, 0.0415), (0.0557, -0.2040, 1.0570))
_WHITE = (0.95047, 1.0, 1.08883)


def rgb_to_lab(rgb):
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (clamp(v) for v in rgb)]
    xyz = [sum(m * c for m, c in zip(row, lin)) / w for row, w in zip(_M, _WHITE)]
    f = [v ** (1 / 3) if v > 0.008856 else 7.787 * v + 16 / 116 for v in xyz]
    return [116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])]


def lab_to_rgb(lab):
    fy = (lab[0] + 16) / 116
    f = (fy + lab[1] / 500, fy, fy - lab[2] / 200)
    xyz = [(v ** 3 if v > 0.2069 else (v - 16 / 116) / 7.787) * w for v, w in zip(f, _WHITE)]
    lin = [max(sum(m * c for m, c in zip(row, xyz)), 0.0) for row in _MI]
    return [clamp(c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055) for c in lin]


def lab_stats(pixels):
    labs = [rgb_to_lab(p) for p in pixels]
    n = max(len(labs), 1)
    mean = [sum(l[i] for l in labs) / n for i in range(3)]
    std = [math.sqrt(sum((l[i] - mean[i]) ** 2 for l in labs) / n) + 1e-6 for i in range(3)]
    return mean, std


def match_transfer(src_pixels, ref_pixels, k):
    """Reinhard style mean/std transfer in Lab, clamped so it nudges the clip
    toward the reference instead of repainting it. Returns rgb -> rgb, which
    is pointwise, so it bakes straight into the LUT."""
    ma, sa = lab_stats(src_pixels)
    mb, sb = lab_stats(ref_pixels)
    lo_s, hi_s = (0.7, 0.6, 0.6), (1.4, 1.6, 1.6)
    lim = (15, 12, 12)
    scale = [min(max(b / a, lo), hi) for a, b, lo, hi in zip(sa, sb, lo_s, hi_s)]
    shift = [min(max(b - a, -m), m) for a, b, m in zip(ma, mb, lim)]

    def f(rgb):
        lab = rgb_to_lab(rgb)
        out = [(v - m) * s + m + d for v, m, s, d in zip(lab, ma, scale, shift)]
        return lab_to_rgb([v + k * (o - v) for v, o in zip(lab, out)])
    return f


def grade_pixel(rgb, src, look, k, exposure, extra=None):
    out = apply_look(to_display(rgb, src, exposure), look, k)
    if extra is not None:
        out = extra(out)
    return [clamp(c) for c in out]


def build_lut(src="rec709", look="rec709", k=1.0, exposure=0.0, extra=None, size=SIZE):
    """Rows in .cube order (red changes fastest, then green, then blue)."""
    grid = [i / (size - 1) for i in range(size)]
    return [grade_pixel((r, g, b), src, look, k, exposure, extra) for b in grid for g in grid for r in grid]


def write_cube(rows, path, title, size=SIZE):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f'TITLE "{title}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n')
        f.writelines(f"{p[0]:.6f} {p[1]:.6f} {p[2]:.6f}\n" for p in rows)
    return path


def read_cube(path):
    size, rows = None, []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("LUT_3D_SIZE"):
            size = int(line.split()[1])
        elif line and (line[0].isdigit() or line[0] in "-."):
            rows.append([float(v) for v in line.split()])
    return size, rows


# ── reading the style file ───────────────────────────────────────────────────

def style_defaults(style_path):
    """Optional lines in brand/style.md: 'Grade look: cinematic' and
    'Grade reference: brand/grade-reference.jpg' (relative to style.md)."""
    out = {"look": None, "reference": None}
    if not style_path or not Path(style_path).exists():
        return out
    text = Path(style_path).read_text(encoding="utf-8")
    m = re.search(r"(?im)^\s*[-*]?\s*grade look\s*:\s*([a-z0-9]+)", text)
    if m and m.group(1).lower() in LOOKS:
        out["look"] = m.group(1).lower()
    m = re.search(r"(?im)^\s*[-*]?\s*grade reference\s*:\s*(\S.*?)\s*$", text)
    if m and m.group(1).lower() not in ("none", "-", ""):
        ref = Path(m.group(1))
        if not ref.is_absolute():
            ref = Path(style_path).parent / ref
        if ref.exists():
            out["reference"] = str(ref)
    return out


# ── ffmpeg helpers ───────────────────────────────────────────────────────────

def ffmpeg_bin():
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg not found on PATH. Install it (brew install ffmpeg, apt install ffmpeg, "
                         "or ffmpeg.org) and try again.")
    return exe


def run(cmd, cwd=None):
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=cwd)
    if p.returncode:
        raise SystemExit(f"ffmpeg failed: {' '.join(map(str, cmd))}\n{p.stderr[-1500:]}")
    return p


_ENCODERS = None


def encoders():
    global _ENCODERS
    if _ENCODERS is None:
        out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        _ENCODERS = set(re.findall(r"^\s*V\S*\s+(\S+)", out, re.M))
    return _ENCODERS


def h264_args(bitrate_k=None, crf=16):
    enc = encoders()
    if "libx264" in enc:
        if bitrate_k:
            return ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{bitrate_k}k", "-maxrate", f"{bitrate_k}k",
                    "-bufsize", f"{bitrate_k * 2}k", "-pix_fmt", "yuv420p"]
        return ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if "h264_videotoolbox" in enc:
        return ["-c:v", "h264_videotoolbox", "-b:v", f"{bitrate_k or 20000}k", "-pix_fmt", "yuv420p"]
    return ["-c:v", "mpeg4", "-q:v", "2", "-pix_fmt", "yuv420p"]


def probe(path):
    err = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-i", str(path)],
                         capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    wh = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    return {
        "duration": int(dur[1]) * 3600 + int(dur[2]) * 60 + float(dur[3]) if dur else 0.0,
        "width": int(wh[1]) if wh else 0, "height": int(wh[2]) if wh else 0,
        "audio": "Audio:" in err,
    }


def read_ppm(path):
    data = Path(path).read_bytes()
    m = re.match(rb"P6\s+(\d+)\s+(\d+)\s+(\d+)\s", data)
    w, h = int(m[1]), int(m[2])
    raw = data[m.end(): m.end() + w * h * 3]
    px = [(raw[i] / 255.0, raw[i + 1] / 255.0, raw[i + 2] / 255.0) for i in range(0, len(raw), 3)]
    return w, h, px


def grab_png(path, t, out_png, width=720):
    """One frame as a full range RGB PNG (what the options still is built from)."""
    run([ffmpeg_bin(), "-nostdin", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={width}:-2:in_range=auto:out_range=full,format=rgb24", str(out_png)])


def sample_pixels(path, times, width=120, is_image=False):
    px = []
    with tempfile.TemporaryDirectory(prefix="eak-grade-") as d:
        for i, t in enumerate(times):
            ppm = os.path.join(d, f"s{i}.ppm")
            cmd = [ffmpeg_bin(), "-nostdin", "-v", "error", "-y"]
            cmd += ["-i", str(path)] if is_image else ["-ss", f"{t:.2f}", "-i", str(path)]
            cmd += ["-frames:v", "1", "-vf", f"scale={width}:-2:in_range=auto:out_range=full,format=rgb24", ppm]
            run(cmd)
            px += read_ppm(ppm)[2]
    return px


def sample_times(path, n=3):
    d = probe(path)["duration"] or 1.0
    return [d * (i + 1) / (n + 1) for i in range(n)]


def percentile(values, q):
    v = sorted(values)
    if not v:
        return 0.0
    idx = min(len(v) - 1, max(0, int(round(q / 100 * (len(v) - 1)))))
    return v[idx]


def guess_input(px):
    """S-Log3 sits in roughly 0.09 to 0.65 with muted colour; normal footage uses the full range."""
    ys = [luma(p) for p in px]
    lo, hi = percentile(ys, 1), percentile(ys, 99.5)
    chroma = sum(abs(c - y) for p, y in zip(px, ys) for c in p) / max(len(px) * 3, 1)
    src = "slog3" if lo > 0.07 and hi < 0.80 and chroma < 0.08 else "rec709"
    return src, {"p1": round(lo, 3), "p99_5": round(hi, 3), "chroma": round(chroma, 3)}


def auto_exposure(px, src):
    """Log footage: the stops that put the brightest 0.5% near 0.90 (skies keep detail), but never
    leave the median under 0.30 (dark rooms get lifted). Rec.709 was already exposed, leave it."""
    if src != "slog3":
        return 0.0
    sample = px[:: max(1, len(px) // 3000)]
    evs = [round(-2.5 + 0.1 * i, 1) for i in range(46)]
    best_hi, best_err, ev_mid = 0.0, 9.0, evs[-1]
    found_mid = False
    for ev in evs:
        ys = [luma(to_display(p, src, ev)) for p in sample]
        hi, med = percentile(ys, 99.5), percentile(ys, 50)
        if abs(hi - 0.90) < best_err:
            best_hi, best_err = ev, abs(hi - 0.90)
        if not found_mid and med >= 0.30:
            ev_mid, found_mid = ev, True
    return float(max(best_hi, ev_mid))


# ── commands ─────────────────────────────────────────────────────────────────

def expand(sources):
    files = []
    for s in sources:
        p = Path(s).expanduser()
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXT and not f.name.startswith("."))
        elif p.is_file():
            files.append(p)
        else:
            raise SystemExit(f"not found: {s}")
    if not files:
        raise SystemExit("no video files found")
    return files


def plan(path, a):
    px = sample_pixels(path, sample_times(path))
    src, stats = (a.input, {}) if a.input != "auto" else guess_input(px)
    ev = a.exposure if a.exposure is not None else auto_exposure(px, src)
    return px, src, stats, ev


def apply_cube_to_png(cube, src_png, out_png):
    """lut3d gets the LUT by bare filename from its own folder, so paths with
    spaces, colons or quotes never need filtergraph escaping."""
    cube = Path(cube)
    run([ffmpeg_bin(), "-nostdin", "-v", "error", "-y", "-i", str(Path(src_png).resolve()),
         "-vf", f"format=rgb24,lut3d=file={cube.name}:interp=tetrahedral", str(Path(out_png).resolve())],
        cwd=str(cube.parent))


def label_tiles(tiles, names, sheet):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return False
    ims = [Image.open(t).convert("RGB") for t in tiles]
    w, h = ims[0].size
    out = Image.new("RGB", (w * 2, h * 2))
    size = max(18, h // 14)
    font = None
    for cand in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:\\Windows\\Fonts\\arialbd.ttf"):
        if os.path.exists(cand):
            try:
                font = ImageFont.truetype(cand, size)
                break
            except OSError:
                pass
    if font is None:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:
            font = ImageFont.load_default()
    for i, (im, name) in enumerate(zip(ims, names)):
        d = ImageDraw.Draw(im, "RGBA")
        bar = int(size * 1.8)
        d.rectangle([0, h - bar, w, h], fill=(0, 0, 0, 150))
        d.text((int(size * 0.7), h - bar + (bar - size) // 2 - 2), LABELS[name], font=font, fill=(255, 255, 255))
        out.paste(im, ((i % 2) * w, (i // 2) * h))
    out.save(sheet, quality=90)
    return True


def options(files, a):
    out_dir = Path(a.out)
    results = []
    for path in files:
        px, src, stats, ev = plan(path, a)
        base = path.stem
        with tempfile.TemporaryDirectory(prefix="eak-options-") as d:
            frame = Path(d) / "frame.png"
            grab_png(path, (probe(path)["duration"] or 0.0) * 0.5, frame, width=a.still_width)
            tiles = []
            for name in LOOKS:
                cube = write_cube(build_lut(src, name, a.strength, ev), Path(d) / f"{name}.cube", name)
                tile = Path(d) / f"{name}.png"
                apply_cube_to_png(cube, frame, tile)
                tiles.append(str(tile))
            sheet = out_dir / f"{base}_options.jpg"
            labelled = label_tiles(tiles, LOOKS, sheet)
            if not labelled:
                ins = []
                for t in tiles:
                    ins += ["-i", t]
                run([ffmpeg_bin(), "-nostdin", "-v", "error", "-y", *ins, "-filter_complex",
                     "[0][1]hstack[top];[2][3]hstack[bot];[top][bot]vstack", "-q:v", "3", str(sheet)])
        results.append({"clip": str(path), "input": src, "exposure": ev, "detect": stats,
                        "options_still": str(sheet), "labelled": labelled,
                        "order": [LABELS[n] for n in LOOKS]})
        print(f"options {sheet.name} (input={src}, ev={ev})", file=sys.stderr)
    return results


def chat_copy(video, info, out):
    dur = max(info["duration"], 1.0)
    audio_k = 128 if info["audio"] else 0
    kbps = int(min(8000, CHAT_LIMIT_MB * 8000 / dur - audio_k))
    kbps = max(kbps, 300)
    cmd = [ffmpeg_bin(), "-nostdin", "-v", "error", "-y", "-i", str(video),
           "-vf", "scale=-2:'min(1920,ih)'", *h264_args(bitrate_k=kbps), "-movflags", "+faststart"]
    cmd += ["-c:a", "aac", "-b:a", "128k"] if info["audio"] else ["-an"]
    run(cmd + [str(out)])
    return out


def render(files, a):
    out_dir = Path(a.out)
    looks = LOOKS if a.look == "all" else [a.look]
    ref_px = sample_pixels(a.match, [0], width=160, is_image=True) if a.match else None
    results = []
    for path in files:
        px, src, stats, ev = plan(path, a)
        info = probe(path)
        base = path.stem
        jobs = [("matched", "rec709")] if a.match else [(n, n) for n in looks]
        for tag, look_name in jobs:
            extra = None
            k = a.strength
            if a.match:
                k_match = a.strength if a.strength_set else 0.75
                graded = [grade_pixel(p, src, "rec709", 1.0, ev) for p in px[:: max(1, len(px) // 6000)]]
                extra = match_transfer(graded, ref_px[:: max(1, len(ref_px) // 6000)], k_match)
                k = 1.0
            cube = out_dir / f"{base}_{tag}.cube"
            write_cube(build_lut(src, look_name, k, ev, extra), cube, f"{base} {tag} input={src} ev={ev}")
            ext = ".mov" if a.prores else ".mp4"
            out = (out_dir / f"{base}_{tag}{ext}").resolve()
            vf = (f"scale=in_range=auto:out_range=full,format=rgb48le,lut3d=file={cube.name}:interp=tetrahedral,"
                  f"scale=out_range=tv")
            if a.prores:
                venc = ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0"]
            else:
                venc = h264_args() + ["-movflags", "+faststart"]
            cmd = [ffmpeg_bin(), "-nostdin", "-v", "error", "-y", "-i", str(path.resolve()),
                   "-map", "0:v:0", "-map", "0:a?", "-vf", vf + (",format=yuv422p10le" if a.prores else ",format=yuv420p"),
                   *venc, "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                   "-color_range", "tv"]
            cmd += ["-c:a", "aac", "-b:a", "192k"] if info["audio"] else []
            run(cmd + [str(out)], cwd=str(out_dir))
            with tempfile.TemporaryDirectory(prefix="eak-ba-") as d:
                before = Path(d) / "before.png"
                after = Path(d) / "after.png"
                grab_png(path, (info["duration"] or 0.0) * 0.5, before, width=a.still_width)
                apply_cube_to_png(cube, before, after)
                ba = out_dir / f"{base}_{tag}_before-after.jpg"
                run([ffmpeg_bin(), "-nostdin", "-v", "error", "-y", "-i", str(before), "-i", str(after),
                     "-filter_complex", "[0][1]hstack", "-q:v", "3", str(ba.resolve())])
            r = {"clip": str(path), "input": src, "exposure": ev, "detect": stats, "look": tag,
                 "video": str(out), "lut": str(cube.resolve()), "before_after": str(ba.resolve()),
                 "size_mb": round(out.stat().st_size / 1e6, 1)}
            if a.chat_copy:
                small = (out_dir / f"{base}_{tag}_chat.mp4").resolve()
                chat_copy(out, info, small)
                r["chat_copy"] = str(small)
                r["chat_copy_mb"] = round(small.stat().st_size / 1e6, 1)
            results.append(r)
            print(f"rendered {out.name} ({r['size_mb']} MB, input={src}, ev={ev})", file=sys.stderr)
    return results


def lut_only(a):
    out = Path(a.lut_out or f"{a.look}.cube")
    if a.look == "all":
        raise SystemExit("lut writes one look at a time; pick warm, cold, cinematic or rec709")
    src = "rec709" if a.input == "auto" else a.input
    ev = a.exposure or 0.0
    write_cube(build_lut(src, a.look, a.strength, ev), out, f"editor-ai-kit {a.look} input={src} ev={ev}")
    return [{"look": a.look, "input": src, "exposure": ev, "lut": str(out.resolve())}]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["options", "render", "lut"])
    p.add_argument("src", nargs="*", help="video files or folders of videos")
    p.add_argument("--look", choices=LOOKS + ["all"])
    p.add_argument("--match", help="reference still to match (a screenshot or frame the editor sent)")
    p.add_argument("--input", choices=["auto", "slog3", "rec709"], default="auto")
    p.add_argument("--exposure", type=float)
    p.add_argument("--strength", type=float)
    p.add_argument("--style", help="brand/style.md with optional 'Grade look:' and 'Grade reference:' lines")
    p.add_argument("--out", default="graded")
    p.add_argument("--lut-out", help="where `lut` writes the .cube (default <look>.cube)")
    p.add_argument("--chat-copy", action="store_true", help=f"also write a copy under {CHAT_LIMIT_MB} MB for chat")
    p.add_argument("--prores", action="store_true", help="ProRes 422 HQ .mov masters instead of H.264 .mp4")
    p.add_argument("--still-width", type=int, default=720)
    a = p.parse_args(argv)
    a.strength_set = a.strength is not None
    a.strength = 1.0 if a.strength is None else max(0.0, min(1.0, a.strength))
    return a


def main(argv=None):
    a = parse_args(argv)
    ffmpeg_bin()
    if a.cmd == "lut":
        if not a.look:
            raise SystemExit("pick a look: --look warm|cold|cinematic|rec709")
        print(json.dumps({"results": lut_only(a)}))
        return 0
    if not a.src:
        raise SystemExit("give at least one clip or folder")
    if a.cmd == "render" and not a.look and not a.match:
        d = style_defaults(a.style)
        a.match = d["reference"]
        a.look = None if a.match else d["look"]
        if not a.look and not a.match:
            raise SystemExit("no look chosen. Use --look warm|cold|cinematic|rec709|all or --match ref.jpg "
                             "(or add 'Grade look:' to brand/style.md). Run `options` first to compare.")
    Path(a.out).mkdir(parents=True, exist_ok=True)
    files = [f.resolve() for f in expand(a.src)]
    res = options(files, a) if a.cmd == "options" else render(files, a)
    print(json.dumps({"out": str(Path(a.out).resolve()), "results": res}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

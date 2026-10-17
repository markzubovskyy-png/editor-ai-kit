---
name: motion-render
description: Use when an editor picks motion graphics ideas and wants them rendered. Renders each one as a transparent 1080x1920 overlay (.mov ProRes 4444 with alpha, .webm VP9 with alpha, and a preview .mp4) in a clean or hand drawn boiling line style, using the team's brand colours and fonts.
---

# motion-render

Real code: `scripts/render.py`. It turns a small JSON config into a
transparent overlay the editor drops on top of any clip. Needs Python 3.9+,
Pillow, numpy and ffmpeg on PATH (`pip3 install pillow numpy`).

## When to use

After motion-ideas, when the editor answers with the ideas they want
("love 1 and 3, make them hand drawn"), or when someone hands over a graphics
brief directly.

## How to use it

1. Turn each chosen idea into a config file named after its id, for example
   `jobs/<job>/configs/moment-1.json`. The schema is in the docstring at the
   top of `scripts/render.py`. Worked examples: `examples/configs/` and
   `examples/sample.json` in the repo.
   - `"style": "hand-drawn"` gives a marker line that redraws with fresh
     wobble every few frames (a boiling line). `"style": "clean"` gives
     steady lines. Default to the vibe in `brand/style.md` unless the
     editor asked for something else.
   - Colours: use `"primary"` and `"secondary"` so they come from
     `brand/style.md`. A literal hex works too.
   - Six graphic types: `text_callout`, `arrow`, `circle`, `underline`,
     `number_counter`, `checklist_tick`. Never put a number on screen that
     isn't in the script.
2. Render:
   ```bash
   python3 scripts/render.py jobs/<job>/configs/moment-1.json \
     --style brand/style.md --out jobs/<job>/render --name moment-1
   ```
3. Three files land in `jobs/<job>/render/`:
   - `moment-1.mov`: ProRes 4444 with alpha, for Premiere, Final Cut and
     DaVinci Resolve.
   - `moment-1.webm`: VP9 with alpha, small, good for chat and CapCut.
   - `moment-1_preview.mp4`: the overlay on black, plays anywhere.

## Sending it back over Telegram

Reply with the preview `.mp4` first so the editor can check it on their
phone, then the `.webm`. Telegram bots can send files up to 50 MB each. If a
`.mov` is bigger, say so and offer edit-handoff's zip or a shared folder
instead.

## Fonts

The repo ships no font files. The renderer tries a free Google Font matching
`brand/style.md` (downloaded once and cached in
`~/.cache/editor-ai-kit/fonts`), then a system font, then Pillow's built in
font, so rendering never hard fails.

## Verify before replying

- All three files exist and are not empty.
- `ffprobe` on the `.mov` shows a `yuva*` pixel format (alpha) and
  `1080x1920`.
- `ffprobe` on the `.webm` shows `1080x1920` and the stream tag
  `alpha_mode=1`. ffmpeg's built in VP9 decoder reports `yuv420p` and
  carries the alpha separately; that is normal.
- Look at a frame of `_preview.mp4`: does the graphic match its line?

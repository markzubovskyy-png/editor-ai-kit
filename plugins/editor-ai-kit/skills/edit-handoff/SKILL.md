---
name: edit-handoff
description: Use when an editor says "pack everything for the edit" or wants a cue sheet. Packs the rendered motion graphics and the ideas into cues.md, cues.csv (line, timecode, file) and one zip that drops straight into CapCut or Premiere.
---

# edit-handoff

The last step of a job. Takes `ideas.json` from motion-ideas and the files
from motion-render (and colour-grade, if any) and packs them into something
the editor drops straight into their timeline.

## When to use

The editor texts "pack everything for the edit", "send me the files", or
asks for a cue sheet.

## What to run

```bash
python3 scripts/handoff.py --ideas jobs/<job>/ideas.json \
  --render-dir jobs/<job>/render --out jobs/<job>/handoff
```

It writes:

- `cues.md`: a table (timecode, line, graphic type, file, notes) the
  editor can scan in ten seconds.
- `cues.csv`: the same data for a spreadsheet.
- `handoff.zip`: both cue sheets plus every file under `--render-dir`.

Files are matched to ideas by the idea's `id` in the filename, which is why
motion-render uses `--name moment-1`. If no file matches, the file column is
left blank for the editor to fill in. That is not an error.

To include graded clips, copy them into the render folder first, or run
handoff a second time with `--render-dir jobs/<job>/graded`.

## Sending it back over Telegram

Paste the `cues.md` table into the reply (it reads fine on a phone), then
attach `handoff.zip`. Telegram bots can send files up to 50 MB. If the zip is
bigger, send the `.webm` overlays and the cue sheet instead, and tell the
editor where the full zip sits on the brain.

## Verify

Open `cues.md`, check the row count matches the ideas in `ideas.json`, and
check the zip really contains the files (`unzip -l handoff.zip`).

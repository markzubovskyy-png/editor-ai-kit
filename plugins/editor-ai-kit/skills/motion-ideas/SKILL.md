---
name: motion-ideas
description: Use when an editor or the founder sends a script or transcript and asks for motion graphics ideas. Proposes 3 to 5 numbered moments, each tied to an exact line, in the team's brand style, and saves them as ideas.json for motion-render and edit-handoff.
---

# motion-ideas

Turns a script or transcript into a short, numbered list of motion graphic
moments, each tied to the exact line that triggers it. Usually the request
comes from an editor texting the brain on Telegram ("here's the script, give
me motion graphics ideas"), so keep the reply short enough to read on a phone.

## When to use

Someone pastes or attaches a script or transcript and asks for motion
graphics ideas, a graphics pass, or "what should we animate here".

## Inputs

- The script or transcript. On Telegram it may arrive as text, as a file
  (use the Telegram plugin's `download_attachment` tool, then read it), or
  as a timed transcript with timecodes.
- `brand/style.md` from the brand-style skill. If it's missing, say so and
  ask the founder to run brand-style. Don't invent a look.

## What to produce

3 to 5 moments. For each one:

- The exact line it attaches to, quoted word for word.
- A timecode if the transcript has timing, otherwise "when they say X".
- One motion-render graphic type: `text_callout`, `arrow`, `circle`,
  `underline`, `number_counter` or `checklist_tick`.
- A one line style note that matches the vibe in `brand/style.md`.

Never invent numbers, stats or claims. If the script says "a lot more"
without a figure, use a `text_callout`, not a `number_counter` with a made
up value.

## Reply format (for chat)

Number the ideas so the editor can answer "love 1 and 3, make them hand
drawn":

```
1. "Most founders try to edit with AI themselves. Don't." (0:00.4)
   text callout: big DON'T, hand drawn underline
2. ...
```

## File format

Save `ideas.json` in a job folder (for example `jobs/<date>-<video>/`):

```json
{
  "ideas": [
    {
      "id": "moment-1",
      "line": "Most founders try to edit with AI themselves. Don't.",
      "timecode": 0.4,
      "type": "text_callout",
      "style_note": "big DON'T lands on the word, hand drawn underline under it"
    }
  ]
}
```

`timecode` is seconds. Leave it out if unknown. The `id` is what
motion-render's `--name` and edit-handoff use to match files, so keep them
as `moment-1`, `moment-2` and so on. A worked example lives in
`examples/script.txt` and `examples/ideas.json` in the repo.

## Verify

Read `ideas.json` back. Every `line` must be a real quote from the input,
every `type` one of the six above, and there must be no invented numbers.

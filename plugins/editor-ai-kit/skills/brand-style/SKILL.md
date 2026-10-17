---
name: brand-style
description: Use when the founder wants to teach the editing brain their look, or when another editor-ai-kit skill can't find brand/style.md. Runs a short interview and writes one style file (colours, fonts, reference creators, motion vibe, default colour grade) that every graphic and every grade reads first.
---

# brand-style

This is step 3 of the setup: teach the brain your look. The brain is the
founder's own computer running Claude Code with this kit. Editors never run
this skill themselves. They text the brain over Telegram, and everything they
get back reads the file this skill writes. One file, one look, for every
editor.

## When to use

- The founder says "set up our brand style", "learn our look" or similar.
- Any other editor-ai-kit skill can't find `brand/style.md`.
- The founder wants to change the look. Edit the file here once and every
  editor gets the new look on their next message.

## Where the file lives

Use one folder on the founder's computer as the brain's home, for example
`~/editing-brain/`, and always start Claude Code from there (the
telegram-setup skill does the same). Write the file to
`~/editing-brain/brand/style.md`, or `./brand/style.md` in whatever folder
Claude Code was started in. Ask once if unsure.

## What to ask

Keep it short. Plain language, one question at a time:

1. Two brand colours. Hex codes if they have them. Otherwise ask for a
   screenshot or a link to the logo and pull the hex values from it.
2. Two fonts: one for big text, one for everything else. A name is enough.
   If it's a paid font the renderer can't use, pick the closest free Google
   Font and say so.
3. Two or three creators whose motion graphics they like, with links if
   possible, so ideas match the vibe instead of guessing.
4. The motion vibe in one line: clean, or hand drawn and scrappy. This maps
   to motion-render's two styles (`clean` and `hand-drawn`).
5. The default colour grade: warm, cold, cinematic or rec709 (clean and
   neutral). If they don't know, write rec709 and suggest running
   colour-grade options on one of their clips to pick.
6. Optional: a reference still for grading (a frame from a video whose
   colour they love). Save it as `brand/grade-reference.jpg`.

## What to write

Create `brand/style.md` with exactly these labels (the scripts parse them):

```markdown
# Brand style

- Primary color: #2F3B1F
- Secondary color: #CFAE6B
- Primary font: Poppins
- Secondary font: Inter
- Vibe: hand-drawn, scrappy, marker-on-paper
- Reference creators: @creator1, @creator2
- Grade look: cinematic
- Grade reference: grade-reference.jpg
```

If something is still unknown, write a safe default instead of leaving the
line out, and tell the founder which lines are placeholders. Write
`Grade reference: none` when there is no reference still. The reference
path is relative to `style.md`.

## Verify

Read the file back and show it to the founder. Check that both colours are
valid hex (`#` plus 3 or 6 hex digits), the grade look is one of warm, cold,
cinematic or rec709, and the reference still exists if one is set.

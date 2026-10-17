---
name: telegram-setup
description: Use when the founder wants their editors or managers to reach the editing brain from Telegram. Walks the founder through BotFather and Anthropic's official Claude Code Telegram channel plugin on their own computer, allowlists each editor's Telegram user ID, and explains what editors can ask. Editors install nothing.
---

# telegram-setup

Step 4 of the setup: create a Telegram bot and give your editors access.

The model is **one brain**. Claude Code, this kit, `brand/style.md` and your
playbook all live on the founder's own computer. Editors and managers do not
get their own Claude Code. They text a Telegram bot, the bot passes the
message into the Claude Code session running on the founder's computer, and
the files come back in the same chat. Same style for every editor, and any
change the founder makes reaches all of them at once.

This uses Anthropic's own Telegram channel plugin, not a custom bot server.
Source of truth: https://code.claude.com/docs/en/channels and
https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/telegram
Channels are a research preview, so if a command below doesn't match what
Claude Code shows, check those two pages before guessing.

## Before you start

- Claude Code on the founder's computer, logged in with a claude.ai account
  (or a Console API key). On a claude.ai Team or Enterprise plan an Owner has
  to switch on channels first under Organization settings, Claude Code,
  Channels. Pro and Max accounts can skip this.
- Bun installed (the plugin runs on it). Check with `bun --version`. If it's
  missing: `curl -fsSL https://bun.sh/install | bash`
- One folder that is the brain's home, for example `~/editing-brain/`, with
  `brand/style.md` inside it (run brand-style first). Always start Claude
  Code from this folder.

## Founder steps (on your own computer)

1. **Create the bot.** In Telegram, open [@BotFather](https://t.me/BotFather),
   send `/newbot`, pick a display name and a username ending in `bot`.
   Copy the token. Treat it like a password: never commit it, never post it
   in a group.

2. **Install the plugin.** Run `claude` in a terminal, then at its prompt:
   ```
   /plugin install telegram@claude-plugins-official
   ```
   Choose the user scope when asked. If it says
   `Marketplace "claude-plugins-official" not found`, run
   `/plugin marketplace add anthropics/claude-plugins-official` and try
   again. If it says `Run /reload-plugins to apply.`, run `/reload-plugins`.

3. **Give it the token.**
   ```
   /telegram:configure <token from BotFather>
   ```
   This saves it to `~/.claude/channels/telegram/.env`. Typing it there
   yourself is fine; don't ask the assistant to store it anywhere else.

4. **Start the brain with Telegram switched on.** Exit Claude Code, then:
   ```bash
   cd ~/editing-brain
   claude --channels plugin:telegram@claude-plugins-official
   ```
   Messages only arrive while this session is open.

5. **Add yourself first.** DM your bot. It replies with a pairing code. In
   Claude Code run:
   ```
   /telegram:access pair <code>
   ```

6. **Add each editor and manager.** Two ways, both fine:
   - Pairing: they DM the bot, send you the code it gives them, and you run
     `/telegram:access pair <code>`.
   - By user ID: they message [@userinfobot](https://t.me/userinfobot), send
     you the number it replies with, and you run
     `/telegram:access allow <their user id>`.

   Remove someone with `/telegram:access remove <user id>`. Run
   `/telegram:access` on its own to see who has access.

7. **Lock it down.** Once everyone is in:
   ```
   /telegram:access policy allowlist
   ```
   From then on the bot ignores anyone you haven't added.

8. **Keep the computer on while the team works.** If the computer sleeps or
   the session closes, the bot goes quiet. On a Mac, run `caffeinate -i` in a
   second terminal tab or turn off sleep in System Settings. Leave the
   Claude Code window open.

9. **Stop it waiting on you.** If Claude Code hits a permission prompt while
   you're away, the session waits until you answer. Allow the commands the
   kit uses ahead of time in `~/editing-brain/.claude/settings.json`:
   ```json
   {
     "permissions": {
       "allow": ["Bash(python3:*)", "Bash(ffmpeg:*)", "Bash(unzip:*)"]
     }
   }
   ```
   Anyone on the allowlist can talk to your computer through this session,
   so only add people you trust.

## What the editor does

Nothing to install. They open Telegram, message the bot, and get files back
in the same chat. That's it.

Good to tell them:

- Scripts can be pasted as text.
- Clips: Telegram lets the bot download files up to 20 MB. For longer
  clips, send a short export or a download link instead.
- Files the bot sends back are capped at 50 MB each, which is why grades
  come back as a chat sized copy and the full quality file stays on the
  brain.

## What your editor can ask

- "here's the script, give me motion graphics ideas" (motion-ideas)
- "love 1 and 3, make them hand drawn" (motion-render)
- "render idea 2 clean, in our colours" (motion-render)
- "colour options for this clip" (colour-grade sends the 4 up still)
- "go with cinematic" or "match this frame" plus a screenshot (colour-grade)
- "pack everything for the edit" (edit-handoff sends the cue sheet and zip)

## Optional: an editor with their own AI

A founder can give an editor their own Claude subscription instead. It works,
but that's a second brain: their style file and prompts will drift from
yours unless you copy every change across by hand.

## Verify

DM the bot from an allowlisted account. The message should show up in the
running Claude Code session as a Telegram channel event, and the reply should
arrive in Telegram. If the bot doesn't answer, check the session was started
with `--channels plugin:telegram@claude-plugins-official`, then check
`/telegram:access` lists the sender.

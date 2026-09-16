# claude-memory-bookstore

**English** · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [한국어](README.ko.md) · [日本語](README.ja.md)

A persistent memory system for [Claude Code](https://claude.com/claude-code) that scales to
hundreds of files and millions of characters, while only ever loading a tiny slice into context
automatically.

I've been running this for a few months across dozens of long-running projects. As of the last
time I checked: **2.5M characters stored, and only 0.8% of that gets read into context
automatically per session.** That's not a bug — it's the entire design.

**Re-measured six weeks later: 6.44M characters across 921 files, and the auto-loaded index is
now 0.26%.** The store grew 2.6x while the always-on context cost went down by a factor of three.
The section on what changed is near the bottom, including the one ratio that got worse.

## The problem this solves

Claude Code sessions have a limited context window. A memory store doesn't. If you try to make
an AI assistant "remember everything" by feeding it the full history every time, you get the
opposite of what you wanted: it reads the wrong parts, misses the important bits, and gets slower
than having no memory at all.

The fix isn't retrieving more. It's loading less by default, and teaching the system where to
look instead of what to memorize.

## Architecture: three layers

```
Layer 1 — Index (always loaded)          0.26% of total volume, 100% load frequency
Layer 2 — Triggers & rules (on demand)    13% of total volume, loaded when grepped
Layer 3 — Source of truth (on demand)     87% of total volume, loaded when opened
```

**Layer 1 — `MEMORY.md`.** One line per memory: "file exists + one-line hook." It doesn't store
knowledge, it stores *where to find knowledge*. This is the only file Claude Code loads
automatically into every new session.

**Layer 2 — trigger table + rules.** A hand-maintained inverted index mapping keywords
("triggers" — the phrases you'd naturally type to recall something) to source files. This file
gets large (100K+ characters in my real one), so the discipline is: `grep` first, then read by
line range. Reading it in full will silently truncate and you'll lose the back half without
noticing.

**Layer 3 — source of truth.** One file per project/topic, one log per handoff, one decision
record per major call. All conclusions live here. The two layers above are just projections of
this layer — if they ever disagree with layer 3, layer 3 wins.

## Read path: how a keyword finds knowledge

1. You type a trigger word in any session — a project codename, an incident name, whatever
   phrase you'd naturally reach for.
2. Claude greps the trigger table for it (not a full read — see layer 2 above).
3. It gets back a pointer plus a one-line status: `status trigger-A · trigger-B → source-file
   [hard fact]`. The bracket only holds what's needed right at the moment of the grep hit:
   rollback commands, hard numbers, a verdict.
4. It opens the source file and works from there. Judgments cached in the index don't count as
   authoritative — only the source file does.

## Write path: three automatic gates

**Gate 0 — backup + version layer** (`hooks/memory_backup.sh`, runs on every `PostToolUse`
`Write|Edit`): mirrors the memory directory to a backup location and commits it to git on every
write. If the backup drive isn't mounted, it fails silently and sends one deduplicated alert
instead of retrying forever — then catches up automatically once the drive is back.

**Gate 1 — stock gate** (`hooks/memory_size_gate.py`): warns you (via whatever notification
channel you wire up) when the index crosses a size threshold, or when the trigger table grows
too much in a single day. Three checks, each fires at most once per day so it doesn't turn into
noise you learn to ignore.

**Gate 2 — flow gate** (same script, newer addition): the stock gate alone wasn't enough. A
component breakdown of the index's growth showed **new lines added vs. old lines getting fatter
was roughly 4-7:1** — meaning periodic cleanup only ever fixes a symptom that reappears the next
day. The flow gate catches the problem at the moment of writing instead: it inspects the diff of
what you just added to the index and, if a new line is too long, or the same source file is now
indexed by two different lines, or you added a new event pointer without retiring an old one, it
sends that feedback back into the *same Claude session* that just wrote it — via the
`PostToolUse` hook's `hookSpecificOutput.additionalContext` field, not `exit 2` (which only
surfaces to the human, never reaches the model's context).

## Conflict resolution

When two memories disagree, resolution order is: your explicit instruction (always wins) >
source-of-truth file > trigger-table projection > index hook line > old log snapshot. Same-tier
conflicts resolve by newer date. If it can't be resolved that way, the system asks rather than
guessing.

## What changed in six weeks

The three layers held. What I added after the first commit:

**An archive tier** (`_archive_index.md`). Finished projects and cold lines don't get deleted,
they get demoted: the index line moves to a separate file while the source file stays where it
is. Deleting would lose the "we already tried this" answer, which is the one I reach for most.

**A partitioned index.** The index hit its own ceiling. One project had grown to 66 index lines,
two thirds of the file, for work I only touch on some days. Those moved out to
`_matrix_index.md` and the main index kept a single line pointing at it.

**Project-based filing instead of event-based.** The index started as a running log: one line
per incident, appended forever. Refiling by project (one archive file per project, with new
incidents appended inside it rather than to the index) took the index from 24,349 to 20,760
characters without dropping anything.

**Measure in characters, not bytes.** `wc -c` misdiagnosed the index size three times before I
caught it. CJK text is 3 bytes per character, so a byte count reads three times too high. Every
threshold in the size gate now uses `len(open(path, encoding='utf-8').read())`.

### What layer 2 costs

Layer 2 didn't hold its ratio. The trigger table is now 13% of total volume, up from the ~6%
quoted above, at 849,747 characters in one file.

Close to half of that is retired on purpose: 400,240 characters (47%) sit under an
"archive / settled" heading, holding projects that finished, got dropped, or were superseded.
Their trigger words stay indexed. Retiring a trigger here means demoting it to a bookmark
instead of deleting it, because months later you still type the old codename, and that is when
you need the system to answer "that one's finished, look at X now."

So the growth is a known cost of that choice. What the flow gate still misses is the other half:
a line that earns its place and then keeps earning more of it every month. I have no hit data on
trigger words, so I can't separate a line that's load-bearing from one that's merely old.

The grep-first discipline still works at this size.

## Setup

1. Copy `hooks/memory_backup.sh` and `hooks/memory_size_gate.py` into `~/.claude/hooks/`, and
   edit the placeholder paths at the top of each (backup destination, env file for notifications,
   memory directory).
2. Wire the backup hook into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.file_path // empty' | /bin/bash $HOME/.claude/hooks/memory_backup.sh",
            "timeout": 120,
            "async": true
          }
        ]
      }
    ]
  }
}
```

3. Create your memory directory (see `demo/` for the file layout) and start writing `MEMORY.md`
   entries as one-line hooks, with the actual detail living in per-topic files next to it.
4. If you want Telegram alerts for the backup/size gates, drop `TG_TOKEN` / `TG_CHAT_ID` into an
   env file and point `TG_ENV` at it — that file should never be committed (see `.gitignore`).

## What's in `demo/`

Fully fictional example files (a fake "widget launch" project) showing the expected format —
`MEMORY.md` index entries, a trigger-table excerpt, and one source-of-truth topic file. Nothing
in this repository is a real project, client, or account; the numbers in this README (6.44M
characters, 0.26%, three layers) come from my own real usage but the file contents you can browse
are all placeholders.

## License

MIT.

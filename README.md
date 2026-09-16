# claude-memory-bookstore

**English** · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [한국어](README.ko.md) · [日本語](README.ja.md)

A persistent memory system for [Claude Code](https://claude.com/claude-code) that scales to
hundreds of files and millions of characters, while only ever loading a tiny slice into context
automatically.

I've been running this for a few months across dozens of long-running projects. As of the last
time I checked: **2.5M characters stored, and only 0.8% of that gets read into context
automatically per session.** That is the design, not a bug in it.

**Re-measured six weeks later: 6.44M characters across 921 markdown files, and the
auto-loaded index is 0.26%.** Measured the same way, the store is about 2.3x bigger (the 2.5M
above was rounded; it was 2.81M on the day of the first commit). The index itself went from
18.8K to 16.8K characters, so the ratio fell mostly because the denominator grew. The section
near the bottom covers what changed, including the one ratio that got worse.

## The problem this solves

Claude Code sessions have a limited context window. A memory store doesn't. If you try to make
an AI assistant "remember everything" by feeding it the full history every time, you get the
opposite of what you wanted: it reads the wrong parts, misses the important bits, and gets slower
than having no memory at all.

The fix is to load less by default, and to teach the system where to look rather than what
to memorize.

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
gets large (about 850K characters in my real one), so the discipline is: `grep` first, then read by
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

The three layers held. Two of the mechanisms below existed at the first commit but weren't in
the README; the rest are new.

**Archive tier** (`_archive_index.md`, since July). Finished projects and cold lines are
demoted, not deleted: the index line moves to this file, the source file stays put. A demotion
is accepted only if everything that left `MEMORY.md` landed in the archive file. Without that
check, demotion and deletion look identical in the data.

**Project dossiers** (`projects/<name>.md`, late August). The index had become a running log,
one line per handoff, audit or decision, appended forever; 38% of its links pointed at dated log
files. Each long-running project now has one dossier with its own timeline, and new logs are
appended there instead of to the index. Filing five projects this way took the index from 24,349
to 20,782 characters with no links lost.

**Partitioned index** (`_matrix_index.md`, late August). The index hit its ceiling twice in one
month (Claude Code stops reading at 24,400 characters). One project group held 66 of 113 pointer
lines and 57% of the characters, so it moved into its own index file and the main index keeps
one line pointing at it. The one-time drop wasn't the point: about half of new lines now route
into the partition instead of into the always-loaded file.

**The write channel was a blind spot.** The hooks in this repo fire on `PostToolUse` for
`Write|Edit`. Writes made through `Bash` — python scripts, `sed`, redirects — never triggered
the backup, the git commit or the size gate, and those are exactly the batch edits the gate most
needed to see. It went unnoticed for weeks. The fix is a `Bash` matcher that recognises memory
paths, plus a scheduled job that commits anything left dirty. If you copy the hooks, copy that
lesson too.

**Measure in characters, not bytes.** `wc -c` misdiagnosed the index size more than once; the
log says four times. Mixed Chinese and English runs about 1.6 to 1.9 bytes per character, so a
byte count reads 60-90% high and trips a 24,400 threshold when the real figure is near 15,000.
Every threshold in the size gate uses `len(open(path, encoding='utf-8').read())`, and has since
before the first commit.

### What layer 2 costs

Layer 2 didn't hold its ratio. The trigger table is 13% of total volume, up from 6% at the first
commit, at 849,747 characters in one file.

Retired entries aren't the cause. The "archive / settled" section at the bottom holds about
1,600 characters, twenty-odd lines, each a bookmark that answers "that one's finished, look at X
now". Retirement here means demoting to a bookmark rather than deleting, because months later
you still type the old codename.

The cause is fat cards. 733 entries, median 741 characters; the 111 entries over 2,000
characters are 49% of the file, and the largest is 17,604. The table's own rule is one line of
status plus hard facts, with details left in the source file. That rule was enforced by hand
once in July, taking the file from 107K to 73K, and the fat grew back within two months. The
stock gate flags the day a new entry crosses 800 characters; an entry that is already fat and
keeps growing trips nothing. The flow gate doesn't inspect this file at all, it only diffs
`MEMORY.md`.

I haven't extracted hit data for trigger words yet, though it exists: every grep against the
table is recorded in the session transcripts, 874 of them across 190 sessions in the last month.
Until that's done I can't tell a load-bearing card from one that is merely old.

Grep-first still works at this size. What fails instead is word choice: of 14 phrases I'd
naturally type, 5 returned nothing, all of them hitting entries written that day in system names
rather than the words I actually say.

(Figures measured 2026-09-16.)

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

# How work gets handed to Freebuff

> **Owner:** this file is the source of truth for the task-handoff loop. Project rules
> for the executor → `../../stratoscan/knowledge.md`. Architecture → `../../CLAUDE.md`.

Claude runs on a cloud VM. Freebuff runs on your laptop. **The two cannot reach each
other** — no shared filesystem, no shared localhost, no network route. `localhost` on
the VM is the VM's own loopback, not yours.

So GitHub is the bus, and **a spec file is the message**. Claude does not "tell"
Freebuff anything directly. It writes a file, commits it, and pushes. You pull, and
point Freebuff at the file.

```
Claude (cloud VM)                    you (laptop)                Freebuff (laptop)
      │                                   │                             │
      │ writes .claude/tasks/NNN-*.md     │                             │
      │ git push ──────────► GitHub ──────┤ git pull                    │
      │                                   │ ─── "read NNN-*.md" ──────► │
      │                                   │                             │ edits files
      │                                   │                             │ commits
      │                        GitHub ◄───┤ git push ◄──────────────────┘
      │ git fetch ◄──────────────────────┘                             │
      │ reviews the diff                                                │
      │ merges, or writes notes and pushes them back ───────────────────┘
```

Nothing here is automatic. You are the relay, and that is deliberate: every change
Freebuff makes gets read by Claude before it merges.

---

## Running a task

```bash
git pull
cd stratoscan
freebuff
```

Then, to Freebuff:

> Read `.claude/tasks/004-populate-volume.md` and do exactly what it says.

When the spec's **Verify** block exits 0:

```bash
git push -u origin task/004-populate-volume
```

Then tell Claude the branch name. It fetches, reviews the diff, and either merges or
sends notes back as another pushed file.

---

## Running several tasks at once

Do **not** run two Freebuff sessions against the same working tree. They will
overwrite each other's edits and you will not be able to tell which agent produced
what. Give each task its own checkout:

```bash
scripts/worktree.sh add 004 005 006     # three isolated checkouts + branches
```

That creates `../st-004`, `../st-005`, `../st-006`, each on its own branch, each a
real checkout of the same repository. Run one Freebuff per directory, in parallel.

```bash
scripts/worktree.sh list                # what's live
scripts/worktree.sh rm 004              # tear one down after it merges
```

**Never parallelise two specs that touch the same file.** A merge conflict between two
agents that cannot talk to each other costs more than the time you saved. Each spec's
`Files` section is the check — if two specs overlap there, run them in sequence.

---

## What a spec must contain

Every section is mandatory. The two that are easiest to skip are the two that matter
most, so they are explained here rather than left to taste.

```markdown
# NNN — <imperative title>
## Goal        one paragraph: what "done" looks like
## Files       exact repo-relative paths; nothing outside this list may change
## Pattern     ONE worked before/after on a real file from this repo
## Non-goals   explicit: no drive-by refactors, no reformatting, no renames
## Baseline    which suites already fail before you start
## Verify      exact commands that must exit 0
## Done        push branch task/NNN-slug; do not merge
```

**Non-goals** exists because a fast model's instinct is to improve things it passes on
the way. Left unstated, you get an unrelated refactor bundled into a one-line fix.

**Baseline** exists because a delegated model that meets a pre-existing failure assumes
it caused it and "fixes" something unrelated. Naming the already-red suites up front
prevents an entire class of wasted work.

**Verify** is the whole basis of delegation. Only give away work whose failure a command
can detect. If you cannot write that command, the task is not ready to hand over.

---

## Current baseline

As of commit `2b8f4b4`:

| Check | State |
|---|---|
| Backend suites | **40/40** — `for t in tests/test_*.py; do python -m "tests.$(basename "$t" .py)"; done` |
| `ruff check .` | clean |
| Frontend | **205/205** vitest, typecheck / lint / format clean |
| Env template | complete — `validate_env_template()` returns `[]` |

If a Freebuff branch lowers any of those numbers, it broke something. That comparison
is most of the review.

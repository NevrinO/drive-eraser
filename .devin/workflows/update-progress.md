---
description: Update progress.txt at the end of a work session
---

# Update Progress Workflow

Update `progress.txt` in the workspace root at the end of each work session to maintain cross-session continuity.

## When to Run

- At the end of a coding session before closing the conversation
- When the user says "wrap up", "we're done for today", or similar
- Before committing a significant batch of changes

## Steps

1. **Read the current `progress.txt`** to see what's there.

2. **Update the following sections:**

   - **Last Updated**: Set to today's date
   - **Current Focus**: What you're currently working on (or "between tasks" if nothing is in progress)
   - **Last Completed**: Brief summary of what was accomplished this session
   - **Known Blockers**: Anything blocking progress (or "None")
   - **Next Planned Work**: What should be tackled next session
   - **Session History**: Append a one-line entry with date and brief summary

3. **Keep it concise** — the entire file should stay under 50 lines. This is a quick-reference file, not a detailed log.

4. **Do not delete historical entries** from Session History unless the file exceeds 40 lines. If it does, trim older entries (keep the most recent 10).

## Rules

1. **Always read before writing** — don't overwrite information you don't intend to.
2. **Be specific** — "Fixed bug in disk_ops.py" not "Fixed some bugs".
3. **Update immediately** — don't wait until the next session to update progress.
4. **This file is not committed to git** — it's a working file for cross-session continuity. Add it to .gitignore if it keeps getting staged.

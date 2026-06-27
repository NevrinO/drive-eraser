---
description: Clean up completed issues in code_concerns.md (collapse or purge)
---

# Cleanup Completed Concerns

Clean up `[COMPLETED]` issues in `code_concerns.md`. Two modes:

- **Default (collapse)**: Keep heading lines, remove body fields (Line, Issue, Impact, Suggestion, Depends-on, Related). Reduces file size while preserving the issue map.
- **PURGE**: Remove completed issues entirely (heading + body). Use when the completed list is no longer needed for reference.

## Invocation

```
/cleanup          — collapse completed issues to header-only
/cleanup PURGE    — remove completed issues entirely
```

## Step 1: Run the Cleanup Script

Run the Python script:

```bash
// turbo
python scripts/cleanup_concerns.py --input code_concerns.md
```

For purge mode (only when user explicitly includes "PURGE"):

```bash
// turbo
python scripts/cleanup_concerns.py --input code_concerns.md --purge
```

## Step 2: Verify

After running the script:

1. Read the modified `code_concerns.md` to spot-check a few collapsed/purged sections.
2. Run the triage script to confirm it still parses correctly:

```bash
// turbo
python scripts/triage_concerns.py --input code_concerns.md --output scripts/triage_output.json
```

3. Confirm the pending issue count matches expectations (completed count should drop to 0 since collapsed issues have no body to parse, but the `completed` flag is still detected from the heading).

## Step 3: Report

Report to the user:
- How many issues were collapsed/purged
- Current file size vs previous (optional)
- Whether the triage script still runs cleanly

## Rules

1. **Never run purge mode without explicit user confirmation** — the user must include "PURGE" in their request.
2. **Always preserve ## file section headers** — even if all issues under them are completed.
3. **No backup file** — rely on git for recovery.
4. **Re-runnable** — safe to run multiple times. Collapsed headers are idempotent (already collapsed issues are skipped). Purge is idempotent (already removed issues stay removed).
5. **Parser compatibility** — `triage_concerns.py` handles collapsed headers (empty bodies) correctly. Completed issues are filtered out during grouping regardless of body content.

---
auto_execution_mode: 0
description: Parse and group code_concerns.md issues into fixable batches with dependency awareness
---
You are a triage analyst. Your job is to parse `code_concerns.md`, group issues by pattern/root-cause, identify dependencies, and present actionable fix batches to the user. You do NOT fix issues — you analyze and present options.

## Invocation

```
/triage
```

## Step 1: Run the Triage Script

Run the Python triage script to get structured grouping data:

```bash
python scripts/triage_concerns.py --input code_concerns.md --output scripts/triage_output.json
```

Then read `scripts/triage_output.json`.

## Step 2: Review and Refine Groups

The script uses keyword matching — it will have false positives and missed groupings. Before presenting to the user, review each group:

1. **Read the `issue_text` and `suggestion` for each issue** in the JSON to verify the pattern assignment makes sense.
2. **Merge groups** that are really the same root cause (e.g., "concurrency_lock" and "toctou" might overlap if the fix is the same).
3. **Split groups** where issues share a keyword but have different fix shapes (e.g., two "dead code" issues where one is a Python function and one is CSS — different skills needed).
4. **Rename groups** to be descriptive and actionable (e.g., "TOCTOU: os.path.exists() pre-checks" not just "toctou").
5. **Check for issues the script missed grouping** — scan the "Ungrouped" entries and see if any share a pattern with a named group.
6. **Verify dependency chains** — read the referenced issues to confirm the ordering makes sense. Add any chains the script missed by scanning for phrases like "also addresses", "same pattern as", "do this first", "before any other".

## Step 3: Present Summary to User

Present a summary table in the chat:

```
# Triage Summary

**Stats**: N pending issues, M completed, K groups identified

## Groups

| ID | Pattern | Issues | Weight | Session | Action | Dependencies |
|---|---|---|---|---|---|---|
| G1 | TOCTOU: os.path.exists() pre-checks | 8 | 16 | medium | batch_fix | — |
| G2 | Dead CSS cleanup | 12 | 12 | medium | batch_fix | — |
| G3 | File org: extract shared utils | 1 | 8 | small | plan | — (unblocks G4-G7) |
| ... | ... | ... | ... | ... | ... | ... |

## Dependency Chains
- **G3 must be done before G4, G5, G6, G7** — extracting shared utils unblocks admin_routes.py splits

## Ungrouped (review individually)
- [list any issues that didn't fit a pattern, with file + difficulty]
```

## Step 4: Interactive Q&A

Ask the user which group(s) they want to tackle. Use the `ask_user_question` tool with the groups as options. Include the group ID, pattern name, issue count, and session size in each option's label and description.

If there are more than 4 groups, present them in batches or ask the user to specify by group ID.

Also ask whether they want to:
- **Fix now** (for batch_fix groups) — proceed to Step 5
- **Generate a plan** (for plan groups, or if user prefers) — proceed to Step 6
- **Just review** — end here, user will come back later

## Step 5: Batch Fix (Trivial/Low/Medium groups)

For groups with `action: batch_fix`:

1. **Present each issue** in the group with file:line citation and the fix suggestion.
2. **Confirm with the user** — "I'll fix these N issues. The general approach is: [fix_shape]. Proceed?"
3. **Fix all issues** in the group, following the fix_shape pattern.
4. **After fixing**, mark each completed issue in `code_concerns.md` by appending `[COMPLETED]` to the issue's heading line:

   ```
   ### A3: [COMPLETED] [Advisory] `os.path.exists()` TOCTOU in Background Thread (Concurrency)
   ```

5. **Report** what was fixed and what remains.

## Step 6: Generate Plan (High/Investigation groups)

For groups with `action: plan`, or when the user requests a plan:

1. **Read each issue's full context** — read the referenced file lines to understand the current code.
2. **Create a plan file** at `fix-plan.md` in the workspace root with the following structure:

```markdown
# Fix Plan: [Group Name]

## Overview
- **Group ID**: G3
- **Issues**: ORG5, ORG1, ORG2, ORG3, ORG4
- **Total weight**: 40
- **Estimated sessions**: 3-4

## Dependency Order
1. ORG5 (extract shared utils) — MUST be first
2. ORG1 (remove duplicate template CRUD) — after ORG5
3. ORG2 (move enclosure endpoints) — after ORG5
4. ORG3 (move SMART endpoints) — after ORG5
5. ORG4 (move kill_all_jobs) — after ORG5

## Step 1: [Title]
- **Issue(s)**: ORG5
- **File(s)**: backend/routes/admin_routes.py, backend/routes/_shared.py (new)
- **What to do**: [detailed instructions]
- **Verification**: [how to verify the change works]
- **On completion**: Mark ORG5 as [COMPLETED] in code_concerns.md

## Step 2: [Title]
...

## Completion Checklist
- [ ] Step 1 complete — ORG5 marked [COMPLETED]
- [ ] Step 2 complete — ORG1 marked [COMPLETED]
- ...
```

3. **Present the plan** to the user in chat with a summary.
4. **Ask the user** if they want to proceed with step 1 now, or save the plan for later.

## Rules

1. **Never fix issues during triage** — this workflow is analysis and planning only, except when the user explicitly approves a batch fix in Step 5.
2. **Always run the script first** — don't manually parse `code_concerns.md`. The script provides the structured base; your job is to refine it.
3. **Respect difficulty caps** — don't combine groups into a session larger than 20 weight points without user approval.
4. **Dependency awareness is critical** — if a group has dependencies, call them out prominently. Never suggest fixing a dependent group before its prerequisite.
5. **Mark completions accurately** — only mark `[COMPLETED]` after the fix is actually applied and verified.
6. **Re-runnable** — this workflow can be run multiple times. It will skip `[COMPLETED]` issues and re-group remaining ones.
7. **Be honest about uncertainty** — if the script's grouping seems wrong, say so. The user needs to trust the groupings to make decisions.

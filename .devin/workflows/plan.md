---
auto_execution_mode: 0
description: Structured investigation and plan builder before implementing non-trivial changes
---
You are a principal engineer building a thorough implementation plan. Your goal is to fully understand the problem before proposing any code changes. **Do not write or edit code during this workflow.** The output is a plan, not an implementation.

## When to Use

Invoke this workflow for any non-trivial task: bug fixes that require tracing data flow, feature additions, refactors, architectural changes, or anything touching multiple files. Skip for trivial changes (typo fixes, single-line constant updates, comment-only changes).

## Phase 1: Problem Definition (Mandatory)

Before reading any code, clearly state:

1. **What the user asked for** — restate the request in your own words to confirm understanding.
2. **What success looks like** — how will we know the change is complete and correct?
3. **What constraints exist** — security requirements, backwards compatibility, performance, deployment environment (Ubuntu 26.04 production, Windows dev).
4. **What is explicitly out of scope** — things tangentially related that should NOT be touched.

If any of the above are ambiguous, ask the user before proceeding. Do not guess.

## Phase 2: Investigation (Mandatory — spend the most time here)

This is the core phase. Do NOT rush through it to get to implementation. The goal is to understand the problem so thoroughly that the implementation plan writes itself.

### 2a. Read the relevant code

- Use `code_search` to find all files related to the request.
- Read the full files, not just snippets — context matters.
- Read any config files, schemas, or data structures involved.
- Read test files that cover the affected code — understand what behavior is currently tested.

### 2b. Trace the data flow

For bug fixes, trace from the symptom back to the root cause:
- Where does the bad data/state originate?
- What transformations happen along the way?
- Where does it surface as the observed symptom?
- Is the fix best applied at the source, mid-flow, or at the display layer? (Prefer upstream fixes — Lesson #25)

For features, trace the existing flow that the new feature extends:
- What is the current end-to-end path?
- Where does the new functionality slot in?
- What existing patterns should the new code follow?

### 2c. Map all affected files and call sites

- Grep for all callers of any function you plan to modify.
- Grep for all references to any variable, class, or constant you plan to change.
- List every file that will need changes, directly or indirectly.
- Identify any cross-file contracts (return types, API schemas, CSS class names, event names) that could break.

### 2d. Check guardrails

- Read `.devin/rules/lessons-quick-ref.md` for applicable rules.
- Read `docs/SECURITY_DEVIATIONS.md` for documented deviations that might be relevant.
- Read `code_concerns.md` for existing findings on the files you'll touch.
- If a lesson-learned rule applies, note it and how the plan accounts for it.

### 2e. Enumerate edge cases and failure modes

Explicitly list:
- **Empty/null/zero inputs** — what happens with empty lists, None values, 0, ""?
- **Concurrent access** — could two threads/processes hit this code simultaneously?
- **Error paths** — what happens if a dependency fails mid-operation?
- **Large inputs** — could size cause memory or timeout issues? (Lesson #8)
- **State transitions** — what if the system is in an unexpected state when this runs?
- **Permission/auth boundaries** — does this change who can access what?

### 2f. Identify test strategy

- Which existing tests cover the affected code?
- What new tests are needed?
- What edge cases from 2e need test coverage?
- Can tests be run on Windows dev machine, or do they need the Ubuntu server?

## Phase 3: Plan Construction

Only after Phase 2 is complete, build the plan. Present it as:

### Implementation Plan

```
# Plan: [title]

## Problem Summary
[1-2 sentence summary of what was found during investigation]

## Root Cause (for bug fixes) / Rationale (for features)
[Explanation of why the issue exists or why the approach was chosen]

## Affected Files
| File | Change | Reason |
|---|---|---|
| path/to/file.py | [what changes] | [why] |

## Implementation Steps
1. [step] — [which file, what change, why]
2. [step] — ...
(order steps by dependency — earlier steps must not depend on later ones)

## Edge Cases Addressed
- [edge case] → [how the plan handles it]

## Test Plan
- [test to add/modify] — [what it verifies]
- [existing test to run] — [why]

## Guardrails Checked
- Lesson #N: [how this plan complies or why it deviates]

## Risks
- [risk] → [mitigation or why it's acceptable]

## Out of Scope
- [explicitly listed items not being changed]
```

## Phase 4: User Review

Present the plan to the user. **Do not begin implementation until the user approves.** If the user requests changes to the plan, update it and re-present.

## Rules

1. **No code changes during planning** — this workflow produces a plan only.
2. **Investigation depth scales with complexity** — a 2-file bug fix needs less investigation than a cross-module feature, but both need all phases completed.
3. **Verify findings** — read the actual code before claiming something about it. Do not rely on memory or assumptions.
4. **Parallelize investigation** — read multiple files, grep multiple patterns simultaneously for efficiency.
5. **Cite line numbers** — when referencing code in the plan, use `@file:line` format.
6. **Be honest about uncertainty** — if you can't determine something from the code, say so and ask the user.
7. **Don't over-engineer the plan** — keep steps minimal and focused. The plan should be directly actionable, not aspirational.
8. **Check for CRITIQUE.md** — if it exists, address its findings in the plan before any new work.

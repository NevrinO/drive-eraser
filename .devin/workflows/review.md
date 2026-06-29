---
auto_execution_mode: 0
description: Review active code changes for bugs, regressions, security issues, and improvements
---
You are a principal software engineer performing a thorough review of active code changes. Your goal is to verify the changes fix the intended problem without introducing new issues.

## Pre-Review Context Gathering

Before reviewing, gather context in parallel:

1. **Read `docs/SECURITY_DEVIATIONS.md`** — check for documented deliberate deviations. Do not flag documented deviations as issues.
2. **Read `.devin/rules/lessons-quick-ref.md`** — load guardrails for known failure patterns (SQL injection, TOCTOU, concurrency, regex anchors, etc.). For full details on a specific rule, read `.devin/rules/lessons-learned.md`.
3. **Read `code_concerns.md`** — check for existing findings on the files being reviewed to avoid duplicates and maintain sequential ID numbering.
4. **Identify what changed** — use `git diff` or `@[working-changes]` to see exactly what was modified. Read the full changed files for context, not just the diff hunks.
5. **Grep for callers** of any changed functions — verify the changes don't break call sites.

## Review Focus

### 1. Did the change fix the intended problem?
- Trace the data flow from the change to the symptom it was meant to fix
- Verify the fix addresses the root cause, not a surface symptom (Lesson #25)
- Check if the fix is complete or partial (e.g., fixed one code path but missed another)

### 2. Did the change introduce new issues?
- **Logic errors**: Incorrect conditions, wrong variable references, off-by-one errors
- **Edge cases**: Empty lists, None values, zero-capacity drives, empty strings, zero as a valid value (Lesson #86)
- **Null/undefined references**: DOM elements, API response fields, optional parameters
- **Race conditions**: Check-then-act patterns, TOCTOU, shared state without locks, status updates outside locks (Lesson #5)
- **Security vulnerabilities**: Input validation gaps, command injection, path traversal, timing attacks (`==` vs `hmac.compare_digest`), secret/PII logging
- **Resource leaks**: File handles, subprocesses, DB connections not closed in error paths
- **API contract violations**: Changed return structures that break callers, missing fields, inconsistent error shapes
- **Caching issues**: Stale cache after config change, cache key collisions, caching failure states (Lesson #32)
- **Pattern violations**: Does the change follow established codebase conventions? (e.g., all admin routes use auth decorator, all device operations acquire device lock)

### 3. Cross-file impact
- **Import changes**: Did removed imports leave orphaned references? Did added imports create circular dependencies?
- **Schema changes**: If field names or data structures changed, are all read/write paths updated? (Lesson #53)
- **CSS class changes**: If CSS classes were renamed/removed, are all JS/HTML consumers updated? (Lesson #66)
- **Event handlers**: If DOM structure changed, are event listeners still attached correctly? (Lesson #24)

## Output Format

### Critical Findings (must fix before committing)

```
### 1. [Title] (Category) — [file:line]
[What's wrong]
[Impact]
[Suggested fix]
```

### Advisory Findings (can defer)

```
### N. [Title] (Category) — [file:line]
[What's wrong]
[Impact]
[Suggested fix]
```

### Verdict

```
## Verdict: [PASS / PASS WITH ADVISORIES / FAIL — N critical issues must be fixed]
```

## Writing Findings to code_concerns.md

If any findings are actionable and should be tracked for future fixing (not trivial fixes you can apply now), append them to `code_concerns.md` using the canonical format:

```markdown
### [ID]: [Critical|Advisory] Title — Difficulty: [Trivial|Low|Medium|High|Investigation] — Category: [Category]
- **Line**: N (or N-M)
- **Issue**: [what's wrong]
- **Impact**: [consequence]
- **Suggestion**: [how to fix]
- **Depends-on**: [issue ID(s) that must be fixed first, or "none"]
- **Related**: [issue ID(s) that are related but not blocking, or "none"]
```

Use sequential IDs continuing from the last entry in `code_concerns.md`. Use `C` prefix for Critical, `A` prefix for Advisory. Read the file first to find the last ID number.

After writing, run format validation:

```bash
// turbo
python scripts/normalize_concerns.py --input code_concerns.md --check
```

## Rules

1. **Verify before reporting** — read the actual lines referenced. Do not report speculative or low-confidence findings.
2. **Check `SECURITY_DEVIATIONS.md`** before flagging anything as a security flaw. Documented deviations are acknowledged, not flagged.
3. **Check `lessons-quick-ref.md`** for applicable guardrails. Reference the lesson number when applicable.
4. **Cite line numbers** for every finding using the `@file:line` format.
5. **Report pre-existing bugs** found during review — they may not be part of the current change but are still important for code quality.
6. **Don't duplicate** existing findings in `code_concerns.md`. Read it first.
7. **Be specific** — "security issue" is useless. "Timing attack on cookie comparison at line 37 using `==` instead of `hmac.compare_digest`" is useful.
8. **Severity calibration**: Critical = security vulnerability, data corruption, race condition, crash, broken functionality. Advisory = performance, code quality, consistency, maintainability.
9. **If exploring the codebase**, call multiple tools in parallel for increased efficiency. Do not spend too much time exploring.
10. **Remember that if you were given a specific git commit**, it may not be checked out and local code states may be different.
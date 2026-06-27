---
auto_execution_mode: 0
description: Comprehensive dual code review and YAGNI audit for a single file
---
You are a principal engineer performing a comprehensive review of a single file. This workflow runs two reviews in sequence: a **Code Review** (security, architecture, concurrency, correctness) and a **YAGNI Review** (over-engineering, dead code, unnecessary complexity). Results are written to `code_concerns.md`.

## Invocation

```
/deep-review @[file/path]
```

Or for multiple files:
```
/deep-review @[file1] @[file2] @[file3]
```

## Pre-Review Context Gathering

Before reviewing, gather context in parallel:

1. **Read the target file** completely.
2. **Read `docs/SECURITY_DEVIATIONS.md`** — check for documented deliberate deviations that explain non-standard patterns. Do not flag documented deviations as flaws.
3. **Read `.devin/rules/lessons-learned.md`** — check against known guardrails (SQL injection, TOCTOU, concurrency, regex anchors, etc.).
4. **Read `code_concerns.md`** — check for existing findings on this file to avoid duplicates and maintain sequential ID numbering.
5. **Grep for callers** of key functions in the file — determine which functions are actively used vs dead code.
6. **Grep for related patterns** — e.g., if the file uses `get_device_lock`, check if all similar functions use it consistently.

## Part 1: Code Review

Analyze the file systematically through each lens below. Classify every finding as **[Critical]** or **[Advisory]** and assign a **Difficulty** tag to each finding using the following scale:

- **Trivial** — Single-line change, no logic change (e.g., delete dead CSS, fix a z-index value, rename a variable)
- **Low** — Small localized change, 1-5 lines, no cross-file impact (e.g., add a missing lock, swap `==` for `hmac.compare_digest`, add a try/except around one call)
- **Medium** — Multi-line change within a single function/file, may require understanding surrounding logic (e.g., extract a helper, restructure lock scope, add input validation with error handling)
- **High** — Cross-file or architectural change, requires updating multiple call sites or coordinating changes across modules (e.g., move endpoints between route files, refactor shared utilities, change return type contracts)
- **Investigation** — Cannot be estimated without deeper analysis; root cause or correct fix approach is unclear (e.g., race condition with multiple contributing factors, deadlock potential needing reproduction)

### Security
- **Timing attacks**: String comparisons on tokens, passwords, or hashes using `==` instead of `hmac.compare_digest`.
- **Information disclosure**: Error messages, stack traces, or internal details returned to clients. `str(e)` in API responses.
- **Input validation**: Are all inputs from untrusted sources validated? Device paths, file paths, user-supplied strings.
- **Secrets in code**: Hardcoded passwords, API keys, passphrases not from config.
- **Secret/PII logging**: Are passphrases, tokens, user data, or sensitive config values being logged?
- **Authentication consistency**: Are all sensitive endpoints protected? Compare against `SECURITY_DEVIATIONS.md` for documented exceptions.
- **Command injection**: User input flowing into subprocess commands. Check `shell=True` usage.
- **Path traversal**: User input in file paths without validation.

### Concurrency
- **Race conditions**: Check-then-act patterns, TOCTOU on files (`os.path.exists` before `open`).
- **Missing locks**: Shared mutable state accessed without synchronization.
- **Lock scope**: Locks held too long (blocking I/O under lock) or too short (released before operation completes).
- **Thread safety**: Global variables modified without locks. Background threads without stop mechanisms.
- **Deadlock potential**: Multiple locks acquired in inconsistent order across functions.

### Error Handling
- **Error swallowing**: `except: pass`, bare `except Exception` that silently discard errors. Systematically scan for these.
- **Uncaught exceptions**: Operations that can raise but aren't wrapped (e.g., `int()` on user input, `json.loads()` on untrusted data).
- **Resource leaks in error paths**: File handles, subprocesses, DB connections not closed when exceptions occur. Check `finally` blocks.
- **Inconsistent error returns**: Functions with similar purposes returning different error dict shapes.
- **Bare `except` vs specific exceptions**: Catching too broadly can mask real bugs.

### Architecture
- **Pattern consistency**: Does the file follow patterns established elsewhere in the codebase? (e.g., all verification functions acquire device lock — does this one too?)
- **Return value contracts**: Do similar functions return the same structure? Document mismatches.
- **Import hygiene**: Unused imports, circular import risk, imports inside functions (lazy imports) that could be module-level.
- **Module-level side effects**: Database init, thread starts, network connections at import time.

### Performance
- **Unnecessary allocations**: Large temporary objects (e.g., `b'\x00' * len(data)` instead of `any(memoryview())`).
- **Hot path I/O**: File reads, JSON parsing, or schema validation on every request without caching.
- **Unbounded collections**: Lists/dicts that grow without limits. Per Lesson #9, enforce size limits.
- **Redundant operations**: Re-reading data that was already available, re-computing values unnecessarily.
- **Subprocess spawning**: Large numbers of subprocess calls without batching or limits.

### Correctness
- **Integer division by zero**: `capacity // num_chunks` where `num_chunks` could be 0.
- **Numeric overflow**: Large drive sizes (TB+) multiplied or shifted without overflow protection.
- **Off-by-one errors**: Range bounds, slice endpoints, index access.
- **Edge cases**: Empty lists, None values, zero-capacity drives, empty strings.

### Resource Management
- **Subprocess cleanup**: Are `Popen` objects always killed and waited on in all code paths (including exceptions)?
- **File handle cleanup**: Are `open()` calls in `with` blocks? Are temp files cleaned up?
- **Thread lifecycle**: Are daemon threads started without join? Are stop events properly signaled?
- **Database connections**: Are connections closed in finally blocks?

### Test Coverage
- **Untested functions**: Are there exported functions with no corresponding test?
- **Untested error paths**: Are error branches covered by tests?
- **Untested edge cases**: Zero-capacity drives, empty inputs, concurrent access.

## Part 2: YAGNI Review

Identify over-engineering and unnecessary complexity. Do NOT code, just explain.

### What to Flag
- **Dead code**: Functions, classes, or variables defined but never called. Use grep to verify zero callers.
- **Duplicate code**: Two functions with >80% overlap that could be consolidated.
- **Speculative abstractions**: Plugin architectures, extension points, or parameterized functions where hardcoded values would suffice.
- **Defensive overkill**: Circular reference detection on data guaranteed to be JSON-serializable. Array summarization for collections that never grow large. Excessive logging for low-risk operations.
- **Premature generalization**: Code written for "future use cases" that may never materialize.
- **Unused configuration options**: Policy keys that are loaded but never read by any code path.

### What NOT to Flag
- Security features (input validation, authentication, HTML escaping).
- Core business logic actively used.
- Required compliance or regulatory features.
- Performance optimizations for actual bottlenecks.
- Error handling for realistic failure modes.
- Patterns documented in `SECURITY_DEVIATIONS.md` as deliberate decisions.

## Part 3: File Size & Organization Review (files > 800 lines)

If the target file exceeds 800 lines, perform an organizational analysis. The goal is to keep files under 1000 lines so that an AI agent with a ~200k token context window can read the file, gather all necessary context (sibling files, lessons-learned, security deviations, callers), and act within a single context window. This is a best-effort goal — if a file is single-domain and splitting would make code harder to manage, note that and skip the split. Do NOT code, just explain.

### What to Analyze

1. **Domain mixing**: Does the file contain route handlers or functions from multiple unrelated domains? Map each function/route to its domain (e.g., "SMART diagnostics", "enclosure CRUD", "policy config").
2. **Existing sibling files**: List all sibling route files in the same directory. For each domain found in the target file, check if a sibling file already handles that domain — the functions may belong there.
3. **Duplicate endpoints**: Check if the target file defines endpoints that overlap with or duplicate endpoints in sibling files (e.g., CRUD for the same resource at different URL paths).
4. **Shared utilities trapped in domain files**: Are utility functions (auth decorators, validators, helpers) defined in this file but imported by multiple other files? These should live in a shared module (e.g., `routes/_shared.py` or `routes/auth.py`), not a domain-specific file.
5. **Line count breakdown**: Provide a table showing each domain group, its approximate line range, line count, and proposed destination.
6. **What stays**: Identify what genuinely belongs in the target file and estimate the post-refactor line count.

### What NOT to Flag
- Files under 800 lines (skip Part 3 entirely).
- Cohesive files that are large but single-domain (e.g., a 900-line parser with no separable concerns) — note this explicitly and explain why splitting would harm readability.
- Splits that would create files too small to justify (< 100 lines).
- Splits that would fracture a single cohesive function or class across files.

### Output

Present a table in the chat:

```
# File Organization Review: [file path] ([N] lines)

## Domain Breakdown
| Domain | Lines | Count | Proposed Destination |
|---|---|---|---|
| ... | ... | ... | ... |

## Findings
### ORG1. [Title]
[description, evidence, suggestion]

## Post-Refactor Estimate
[what stays, estimated line count]
```

## Output Format

### To the User (Chat)

Present two or three sections in the chat (Part 3 only if file > 800 lines):

```
# Code Review: [file path]

## Critical Findings
### 1. [Title] (Category) — Difficulty: [Trivial|Low|Medium|High|Investigation]
[file:line citation]
[description, impact, fix]

## Advisory Findings
### N. [Title] (Category) — Difficulty: [Trivial|Low|Medium|High|Investigation]
[same format]

# YAGNI Review: [file path]

## YAGNI Violations
### 1. [Title] — Difficulty: [Trivial|Low|Medium|High|Investigation]
[description]

## Not YAGNI Violations (Confirmed Necessary)
- [item]: [why it's needed]

## YAGNI Verdict
[summary]

# File Organization Review: [file path] ([N] lines)
[Only if file > 800 lines — see Part 3 above]
```

### To `code_concerns.md`

Append a new `## [file path]` section with all findings using the **canonical format**. This format is machine-parsed by `scripts/triage_concerns.py` — do not deviate from it.

#### Canonical Format (Critical/Advisory findings)

```markdown
### [ID]: [Critical|Advisory] Title — Difficulty: [Trivial|Low|Medium|High|Investigation] — Category: [Category]
- **Line**: N (or N-M)
- **Issue**: [what's wrong]
- **Impact**: [consequence]
- **Suggestion**: [how to fix]
- **Depends-on**: [issue ID(s) that must be fixed first, or "none"]
- **Related**: [issue ID(s) that are related but not blocking, or "none"]
```

#### Canonical Format (File Organization findings)

For File Organization findings (Part 3), add a `## File Organization` subsection within the file's `code_concerns.md` section, using `ORG` prefix with sequential numbering:

```markdown
## File Organization

### ORG1: Title — Difficulty: [Trivial|Low|Medium|High|Investigation] — Category: File Organization
- **Lines**: N-M
- **Issue**: [what's misplaced or duplicated]
- **Impact**: [maintenance burden, circular deps, etc.]
- **Suggestion**: [proposed destination file]
- **Depends-on**: [issue ID(s) that must be fixed first, or "none"]
- **Related**: [issue ID(s) that are related but not blocking, or "none"]
```

#### Field Requirements

- **Heading**: Must contain exactly `[ID]: [Critical|Advisory] Title — Difficulty: X — Category: Y`. The em-dash (`—`, U+2014) separates fields. Do not use hyphens (`-`) as separators in the heading.
- **Difficulty**: Mandatory. One of: Trivial, Low, Medium, High, Investigation.
- **Category**: Mandatory. One of: Security, Concurrency, Error Handling, Architecture, Performance, Correctness, Resource Management, Test Coverage, Dead Code, DRY, Code Quality, File Organization, CSS.
- **Depends-on**: Mandatory field. Use `none` if no dependencies. List issue IDs comma-separated (e.g., `ORG5, A45`).
- **Related**: Mandatory field. Use `none` if no related issues. List issue IDs comma-separated.
- **Line vs Lines**: Use `Line` for single line/range. Use `Lines` for ORG entries with ranges.

Use sequential IDs continuing from the last entry in `code_concerns.md`. Use `C` prefix for Critical, `A` prefix for Advisory, `ORG` prefix for File Organization. Read the file first to find the last ID number for each prefix.

#### Format Validation

After writing to `code_concerns.md`, run the normalizer to verify format compliance:

```bash
python scripts/normalize_concerns.py --input code_concerns.md --check
```

If the script reports any format violations, fix them before completing the workflow. The normalizer will also migrate any legacy-format entries it finds to the canonical format.

## Post-Completion Summary Table

After all findings are written to `code_concerns.md` and the format check passes, present a summary table in the chat as the final output. This gives the user a quick at-a-glance view of all findings without opening the file.

### Format

```markdown
# Deep Review Summary: [file path] ([N] lines)

| ID | Severity | Title | Category | Difficulty |
|---|---|---|---|---|
| C16 | Critical | Status chip class name mismatch | Correctness | Low |
| A41 | Advisory | `pulse-danger-btn` never referenced | Dead Code | Trivial |
| ... | ... | ... | ... | ... |

**Totals**: X Critical, Y Advisory — Z Trivial, W Low, V Medium, U High
```

### Rules for the Summary Table

1. List every finding appended to `code_concerns.md` for this review session.
2. Sort by severity (Critical first, then Advisory), then by ID number.
3. Keep the Title column concise (truncate to ~50 chars if needed).
4. Include a totals row with counts by severity and by difficulty.
5. If the file exceeded 800 lines and Part 3 was performed, include ORG entries in the table as well (Severity = "Org").
6. Present this table as the **last thing** in the chat response, after any detailed findings output.

## Rules

1. **Cite line numbers** for every finding using the `@file:line` format.
2. **Verify before reporting** — read the actual lines referenced. Do not report speculative or low-confidence findings.
3. **Check `SECURITY_DEVIATIONS.md`** before flagging anything as a security flaw. Documented deviations are acknowledged, not flagged.
4. **Check `lessons-learned.md`** for applicable guardrails. Reference the lesson number when applicable.
5. **Grep for callers** before flagging dead code. A function with zero grep hits outside its own file and tests is dead.
6. **Don't duplicate** existing findings in `code_concerns.md`. Read it first.
7. **Fix nothing** — this is analysis only. The user decides what to fix and when.
8. **Be specific** — "security issue" is useless. "Timing attack on cookie comparison at line 37 using `==` instead of `hmac.compare_digest`" is useful.
9. **Severity calibration**: Critical = security vulnerability, data corruption, race condition, crash. Advisory = performance, code quality, consistency, maintainability.
10. **Follow the canonical format exactly** — the heading must contain `Difficulty` and `Category` fields separated by em-dashes. The `Depends-on` and `Related` fields are mandatory (use `none` if not applicable). Run `python scripts/normalize_concerns.py --check` after writing to verify format compliance.

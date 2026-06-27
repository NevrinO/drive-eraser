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

Analyze the file systematically through each lens below. Classify every finding as **[Critical]** or **[Advisory]**.

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

## Output Format

### To the User (Chat)

Present two sections in the chat:

```
# Code Review: [file path]

## Critical Findings
### 1. [Title] (Category)
[file:line citation]
[description, impact, fix]

## Advisory Findings
### N. [Title] (Category)
[same format]

# YAGNI Review: [file path]

## YAGNI Violations
### 1. [Title]
[description]

## Not YAGNI Violations (Confirmed Necessary)
- [item]: [why it's needed]

## YAGNI Verdict
[summary]
```

### To `code_concerns.md`

Append a new `## [file path]` section with all findings using the existing format:

```markdown
### [ID]: [Critical|Advisory] [Title] (Category)
- **Line**: N (or N-M)
- **Issue**: [what's wrong]
- **Impact**: [consequence]
- **Suggestion**: [how to fix]
```

Use sequential IDs continuing from the last entry in `code_concerns.md`. Use `C` prefix for Critical, `A` prefix for Advisory. Read the file first to find the last ID number.

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

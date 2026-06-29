---
trigger: always_on
---

# Lessons Learned — Quick Reference

This is a compact summary of all rules from `lessons-learned.md`. The 5 rules below are expanded because they are **process discipline** with no automated safety net — skipping them causes silent failures. All 107 rules are listed as one-liners in the Quick Reference section.

## Critical Process Rules (Expanded)

### Rule 1 — Append-Only for lessons-learned.md
New lessons must ALWAYS be appended to the END of `lessons-learned.md`. Never insert in the middle. The file has a File Maintenance Rule at the top stating this. Inserting in the middle requires renumbering all subsequent rules and creates maintenance burden. Before adding a lesson, read the file to find the last rule number, then append with the next sequential number. This is also documented in `critic-actor-protocol.md` under "CRITICAL APPEND-ONLY RULE".

### Rule 3 — Verification Before Claiming Completion
Never claim fixes are complete in CRITIQUE.md resolution logs or document critical findings without verifying the actual code changes. When writing a resolution log claiming "Added X at line Y" or "Fixed function Z", read the file to verify the change actually exists before writing the log. When acting as the Critic Agent, before writing a finding in CRITIQUE.md, read the specific lines of code referenced in the finding to verify the issue actually exists. False claims of completed fixes waste review time and erode trust. After making edits, always read the affected lines to confirm the changes were applied before updating documentation.

### Rule 48 — Code Extraction Best Practices
When extracting code into new modules, three sub-rules apply:
1. **Remove Original Code**: Delete the moved handlers from the source file in the same commit. Leaving both old and new route registrations makes the new files dead code that never executes. Before marking extraction complete: (1) delete moved code from source, (2) remove unused imports from source, (3) verify app starts without errors, (4) confirm at least one extracted endpoint responds.
2. **Clean Stale Imports**: After extraction, audit both source and destination for orphaned imports. Run linting or grep each import symbol to confirm it's still used.
3. **No Initialization Side-Effects**: Each new file must reproduce only its extracted behavior — never add new auto-initialization (DOMContentLoaded, immediate calls) that wasn't in the original. Diff combined new files against original and flag any net-new top-level statements.

### Rule 75 — Raw Strings for \Z in Regex/Docstrings
In regex, `\Z` is the correct anchor for strict end-of-string. Never change it to `\\Z` — that breaks the regex. The SyntaxWarning occurs because `\Z` is not a recognized Python escape sequence in regular string literals. The ONLY correct fix is to use a raw string prefix (`r"..."` or `r"""..."""`). This tells Python's parser to treat backslashes literally, which is exactly what regex needs. For docstrings containing `\Z`, use raw triple-quotes: `r"""...\Z..."""`. NEVER "escape" the backslash to `\\Z` — that is technically inaccurate because the regex engine reads a single backslash. NEVER change the regex pattern itself. Change the string literal type instead.

### Rule 107 — Always Read Affected Lines After Every Edit
After every edit operation, immediately read the modified lines to verify the change was applied correctly — both syntactically and semantically. No exceptions, even for trivial changes. Edit tools can silently fail to match, match the wrong location, or produce unexpected output when surrounding context is ambiguous. Skipping the verification read saves 2 seconds but costs hours when a bad edit ships undetected. The verification read should confirm: (1) the old text was replaced, (2) the new text is in the expected location, (3) surrounding code is intact and not mangled. Even `multi_edit` operations where one edit depends on another should be verified after each edit in the sequence, not just at the end.

---

## Quick Reference (All Rules)

**File Maintenance**
- **1**: Always append new lessons to END of lessons-learned.md — never insert in middle *(see expanded above)*
- **2**: Apply new lessons to code changes in the same commit — don't add a lesson describing a bug without fixing the bug
- **3**: Verify fixes before claiming completion — read affected lines before writing resolution logs *(see expanded above)*

**SQL & Database**
- **4**: Never accept raw unvalidated strings for SQL column definitions — validate against allowlist or split into strictly validated components; reject trailing tokens
- **10**: Avoid confusing/redundant SQL query patterns with duplicated parameters
- **27**: Add default value guardrails for optional schema fields in persistence layer (dict.get with default or DEFAULT constraint)

**Concurrency & Race Conditions**
- **5**: Don't assume single-process usage is safe — use locks, transactions, atomic operations; prevent TOCTOU, status-update-outside-lock, and thread-registration-before-start issues
- **101**: Global signal flags reset per-operation create cross-operation race — use per-operation threading.Event or generation counter
- **103**: Thread reference nullification after join-outside-lock must use identity check (`if thread is old_thread`)
- **105**: Module-level dict caches without locks have non-atomic multi-key updates — protect with threading.Lock

**HTML & Parsing**
- **6**: Don't use regex to parse HTML — use BeautifulSoup or lxml
- **14**: Use proper bracket matching for JSON embedded in binary data — track string state, not naive rfind()

**Comparisons**
- **7**: Don't rely on json.dumps()/str() comparisons for objects/arrays — handle non-serializable types, circular refs

**Security & DoS Prevention**
- **8**: Enforce size limits on user input, collections (max 100 items), API responses (max 1000), and JSON bodies (64KB max before parsing)
- **12**: Validate device paths against strict regex whitelist before use in commands; validate extracted/derived paths too; use full composite format string validation
- **15**: Use `\Z` (not `$`) for strict end-of-string in validation regexes — `$` matches before trailing `\n`
- **40**: Validate resolved file paths are within expected directory (path traversal prevention via abspath + commonprefix)
- **77**: Validate string enum values against explicit allowlist before use
- **79**: Validate input data before mutating in-memory objects or storage (validate-then-mutate, not mutate-then-validate)

**Crypto**
- **13**: Use current crypto standards (PBKDF2 ≥100k iterations), shared constants in single location, never hardcode different values in read vs write paths

**Caching**
- **17**: Don't snapshot lazy/TTL cache values via module-level imports — use get_x() function calls at use time
- **32**: Use caching consistently at all call sites; never cache failure states; dedicated cache var per function; coordinate in-flight tasks with cache invalidation; invalidate derived-data caches when source mappings change

**Authentication**
- **18**: All admin endpoints must use consistent auth, cookie names, /api/admin/ path; conditional auth for local vs remote; decorator order: auth before rate limiting; apply security decorators across ALL route modules

**Imports & Modules**
- **19**: Verify imports exist and are complete when adding code or extracting modules — run syntax check before committing
- **74**: Prevent circular imports when centralizing module initialization — trace import graph, use deferred registration or lazy imports

**Flask & Routes**
- **20**: Don't define multiple route handlers for same path — use single decorator with methods list
- **31**: Maintain consistent REST API patterns across HTTP methods for same resource
- **73**: Documentation must match implementation — verify security controls actually exist before documenting them

**Integrity**
- **21**: Validate integrity checks (SHA256, signatures) on every read, not just write

**Dependencies**
- **22**: Don't switch from pinned (`==`) to minimum (`>=`) dependency versions without safeguards (CI/CD, update policy)
- **23**: Document build dependency fixes with specific error, OS version, and reason

**DOM & Events**
- **24**: Don't attach duplicate event listeners; consolidate in one location; check for existing global handlers; clean up modal listeners; check document.readyState
- **35**: Include null checks for all new DOM element references — match existing pattern
- **69**: Use data attributes for DOM state detection, not visual content (textContent, innerHTML); check actual modal visibility class (`.open` not `!hidden`)
- **81**: Never use inline event handlers (onclick) with CSP — use data attributes + addEventListener

**Bug Fixing**
- **25**: Investigate root cause before fixing — trace data flow source to display; prefer minimal upstream fixes

**Numbering & IDs**
- **26**: Apply numbering scheme changes to every producer and consumer (backend, frontend, config, tests, docs)
- **52**: Ensure sufficient entropy in generated identifiers; validate transformed results have minimum length

**UI State**
- **30**: UI state must prioritize operational states (RUNNING, FAILED) over config states; don't display unknown values as known defaults
- **62**: Synchronize all UI button states across mode switches — disable controls not applicable to new mode
- **67**: UI previews must accurately reflect runtime configuration or clearly indicate simplified representation
- **89**: UI controls must match backend policy semantics — trace which code paths the policy actually controls
- **96**: UI action buttons must match backend eligibility logic — parameterize filters for manual vs automatic paths
- **100**: WebSocket state updates must refresh all active UI surfaces including open modals

**Error Handling**
- **33**: Use consistent error handling patterns for similar operations; wrap JSON parsing in try-catch even in error paths
- **91**: Don't implement timeouts via cooperative checks around blocking I/O — use subprocess timeout, non-blocking I/O, or separate worker process

**Numeric Validation**
- **34**: Validate extracted numeric values are within reasonable bounds (e.g., slot numbers 0-9999)
- **55**: Guard against division by zero in mathematical conversion functions — validate divisors before operation
- **59**: Validate numeric conversions from user input — isNaN() in JS, try-except in Python; 0 is a valid value
- **86**: Use explicit null/undefined checks for numeric values in JS — 0 is falsy, don't use truthiness checks

**API Contracts**
- **39**: Verify API endpoint data types/formats before implementing client code (DB IDs vs friendly IDs vs slugs)
- **71**: Standardize return structures across function families — same field names and types
- **104**: When adding a key to all return paths, audit every return statement including early returns and nested conditionals

**Resource Immutability**
- **41**: Enforce resource immutability consistently across ALL modification operations (DELETE, POST, PUT, import)

**Async/Await**
- **42**: Never use `await` without declaring function as `async` — SyntaxError at runtime

**Edit Verification**
- **43**: Verify edit operations produce syntactically valid code — read modified section after editing
- **107**: Always read affected lines after every edit — no exceptions *(see expanded above)*

**State Variables**
- **44**: Declare all state variables at module level with explicit initialization — never rely on implicit declaration
- **45**: Verify initialization code is at correct scope level — not nested inside unrelated conditionals
- **46**: Verify global state dependencies are initialized before use — pass as param or document precondition
- **47**: Verify variable references and scope when editing functions — check typos and lexical scope
- **60**: Use descriptive module-specific names for global state variables to prevent cross-module confusion
- **61**: Preserve non-serializable types (Sets, Maps) when updating state — don't use shallow spread
- **87**: Use `global var_name` before assigning to module-level globals in Python functions — Python treats assigned names as local

**Code Extraction**
- **48**: Code extraction requires removing original code, cleaning stale imports, and no new side-effects *(see expanded above)*

**Defensive Programming**
- **49**: Prefer simple declarative solutions over complex defensive patterns — use defer attribute, existing null checks

**Script Dependencies**
- **50**: Document cross-file variable references in non-module `<script>` tags — add comments or move to shared utils file

**Single Source of Truth**
- **51**: Never duplicate authoritative config/data between frontend and backend — derive from API response

**Validation Consistency**
- **28**: Validate all input paths equally (ID lookup vs inline object, reference vs direct value)
- **29**: Validate post-transformation results still meet original contract (e.g., skip_positions reducing count below minimum)
- **54**: Validate original input before transformation, not transformed output
- **90**: Sync validation logic changes with tests and comments — update assertions and docs together

**Debug Artifacts**
- **56**: Remove debugging console.log/temp code before committing

**User Feedback**
- **57**: Provide explicit user feedback when transformations reduce available data (e.g., "3 devices skipped")

**API Caching (Frontend)**
- **58**: Add caching/flags to prevent redundant API calls on UI navigation (tab switches)

**String Normalization**
- **88**: Apply same string normalization to both sides before comparing — don't compare normalized vs raw

**Subprocess Management**
- **64**: Handle subprocess version capture and lifecycle robustly; store Popen refs; explicitly terminate jobs
- **92**: Low-priority background tasks must not hold same locks as destructive operations — use separate lock scopes

**DRY**
- **65**: Don't copy-paste identical logic blocks — extract shared helpers

**CSS**
- **66**: Follow established CSS class patterns; verify all usages across JS/HTML before changing classes

**Install Scripts**
- **68**: Install scripts must create all required files (including hash/signature files) and export env vars for subprocesses

**Rate Limiting**
- **63**: Use shared storage for rate limiting in multi-worker deployments — memory:// only safe for single-worker

**Background Tasks**
- **84**: Coordinate background thread and frontend polling to prevent concurrent update conflicts
- **85**: Filter DB queries by current state for background tasks — don't query all historical records
- **94**: Background workers must verify entity still exists before writing final state — use generation token or presence check
- **97**: Background worker threads must have exception handlers — unhandled exceptions leave entity stuck in non-terminal status
- **98**: Manual endpoints using discover_drives() must pass running_devices set for safety checks
- **99**: Dynamic semaphore resize must recreate after active workers drain — not just when set_concurrency finds no active workers

**Policy Schema**
- **93**: Every policy schema field must be consumed by code — dead schema fields mislead operators

**Manual Endpoints**
- **95**: Manual endpoints must reuse automatic eligibility checks — extract shared helper

**Testing**
- **76**: Clean up SQLite connections in test fixtures — gc.collect(), PRAGMA wal_checkpoint(TRUNCATE)
- **102**: Update tests when renaming/removing module-level attributes — grep test directory for old names

**Miscellaneous**
- **70**: Enforce total limits when iterating over dynamic system resources, not just per-item limits
- **72**: Use identical retry logic across all passes of multi-pass operations
- **80**: Populate all fields in returned data structures — no placeholder nulls that mislead consumers
- **82**: Don't hardcode values defined as constants elsewhere — import and reuse
- **83**: Verify value is array before calling .map()/.filter()/.forEach() — use Array.isArray()
- **106**: Update docstrings/comments when changing fix approaches — grep for references to old approach

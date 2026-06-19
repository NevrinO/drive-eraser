---
trigger: always_on
---

# Project Lessons Learned & Guardrails

This file contains generalized architectural guardrails derived from past agent mistakes. The Coding Agent must strictly adhere to these rules.

**File Maintenance Rule**: When adding new lessons to this file, always append them at the end. Do not insert lessons in the middle, as this requires renumbering all subsequent rules. The rules do not need to be in any particular order - they are a collection of guardrails that should be applied regardless of position.

### 1. SQL Security & Column Modification
- **Rule**: Never accept a raw, unvalidated string for SQL column definitions or parameters.
- **Guardrail**: If dynamic schema modifications are required, validate parameters against an allowlist, or split parameters so that type definitions and names are strictly validated via regex and escaped. Never assume a regex that matches `[a-zA-Z]` is safe if it allows raw unquoted input trailing it.

### 2. Concurrency & Race Conditions
- **Rule**: Do not assume "single-process usage" is safe.
- **Guardrail**: If the codebase uses threads, locks, or runs in a multi-worker environment (like Flask/Gunicorn), always write thread-safe operations. Use locks, transactions, or native database atomic operations (e.g., `IF NOT EXISTS`). For schema modifications like `ALTER TABLE`, wrap in try-except to handle duplicate column errors from concurrent operations. For file-based state management, use file locking (e.g., `fcntl.flock()` on Unix, `msvcrt.locking()` on Windows, or the `filelock` library) around the entire load-modify-save sequence. Atomic file writes (tempfile + rename) only prevent partial file corruption, not lost updates from concurrent modifications.
- **TOCTOU Prevention**: Time-of-check to time-of-use race conditions occur when file existence checks and subsequent operations are not atomic. The correct fix is to remove the pre-check entirely and handle exceptions from the actual operation. Wrapping `os.path.exists()` or `os.path.isdir()` in try-except does not eliminate the race condition—it only prevents crashes from the check itself. The atomic operation is the actual file/directory access (e.g., `os.listdir()`), not the existence check. Use the pattern: `try: operation() except OSError: handle_error()` without any pre-check. For file upload/delete operations, also use atomic patterns like writing to a temporary file then using `os.rename()` (atomic on POSIX) or file locking mechanisms.

### 3. HTML Parsing
- **Rule**: Do not use regular expressions to parse HTML.
- **Guardrail**: Always use a robust parser like BeautifulSoup (`bs4`) or `lxml` to extract elements, body text, or attributes to prevent fragility with nested tags, multiline elements, or script tags.

### 4. Object & Array Comparisons
- **Rule**: Do not rely on naive `json.dumps()` or `str()` comparisons for arbitrary arrays or objects.
- **Guardrail**: Account for non-serializable types (datetimes, sets), circular references, and key sorting. Use try/except fallbacks or deep-comparison helper functions to avoid runtime crashes.

### 5. Size Limits for DoS Prevention
- **Rule**: Always enforce size limits on user input, collections, and API responses.
- **Guardrail**: For any endpoint accepting arrays (e.g., job_ids, filters), enforce a reasonable maximum size (e.g., 100 items) to prevent memory exhaustion and long-running queries. For bulk operations, enforce limits on total collection size (e.g., 100 items total). For API responses that return collections, enforce reasonable maximum sizes (e.g., 1000 items) to prevent memory exhaustion, long response times, or network bandwidth exhaustion. Return a 400 error or truncate with pagination if limits would be exceeded. Also enforce size limits before parsing JSON from untrusted sources (e.g., 64KB maximum).

### 6. Date Range Validation
- **Rule**: When accepting date range filters, validate logical consistency.
- **Guardrail**: If both start_date and end_date are provided, ensure start_date <= end_date to prevent inverted ranges that produce no results or confusing behavior.

### 7. SQL Query Clarity
- **Rule**: Avoid confusing or redundant query patterns that duplicate parameters without clear intent.
- **Guardrail**: When searching multiple columns with the same value set, document the intent clearly or validate which column should be searched based on input format. Duplicate parameter lists without clear purpose make code hard to maintain and debug.

### 8. Recursive Processing & Circular References
- **Rule**: When implementing recursive traversal of data structures (lists, dicts, trees), always add cycle detection.
- **Guardrail**: Use a `visited` set or similar mechanism to track processed objects and prevent infinite recursion on circular references. Failing to do so will cause `RecursionError` at runtime when encountering self-referential structures (e.g., `a = []; a.append(a)` or `d = {}; d['self'] = d`).

### 9. Device Path Validation
- **Rule**: Never accept raw device paths from user input or internal APIs without validation.
- **Guardrail**: Validate device paths against a strict regex whitelist (e.g., `^/dev/[a-z]+[0-9]*$`) before using in command construction. This prevents path traversal, command injection, and accidental access to sensitive devices. Apply validation at the ingestion point, not just at the point of use, even when data comes from trusted discovery APIs. When determining device types from device paths (e.g., NVMe vs SATA), use regex patterns that match the actual naming conventions rather than substring checks like `"nvme" in device_path.lower()`. Use strict patterns like `^/dev/nvme[0-9]+(n[0-9]+)?(p[0-9]+)?$` for NVMe device detection to ensure accuracy.

### 10. Cryptographic Parameter Standards
- **Rule**: Use current cryptographic standards for key derivation and hashing, and ensure consistency across all code paths.
- **Guardrail**: For PBKDF2, use at least 100,000 iterations (NIST recommendation), not 10,000. Define PBKDF2 iteration counts, salts, algorithms, and other cryptographic parameters as shared constants in a single location. Never hardcode different values in read vs. write paths. Parameter mismatches silently break security features and are extremely difficult to debug in production.

### 11. JSON Parsing with Delimiters
- **Rule**: When parsing JSON embedded in binary data, ensure correct delimiter matching and ignore delimiters inside string literals.
- **Guardrail**: Use proper bracket matching algorithms (e.g., counting nested braces) to find the correct opening delimiter for a given closing delimiter. Naive `rfind()` can match the wrong opening brace when multiple JSON objects exist. If implementing a custom JSON scanner, track string state (toggle on unescaped `"`) and only count `{`, `}`, `[`, `]` when outside of strings. Better yet, use a proper JSON streaming parser or extract the object using a library that handles JSON semantics correctly. Naive byte-level brace counting will fail on real-world data containing braces in string values.

### 12. Strict Full-String Anchors in Validation Regexes
- **Rule**: For input-validation/whitelist regexes, never anchor with `$` when you mean strict end-of-string.
- **Guardrail**: In Python, `$` also matches just before a trailing `\n`, so a value like `/dev/sda\n` passes a `^...$` whitelist. Use `\Z` for a strict end anchor (or `re.fullmatch`), and explicitly reject `\n`/`\r` for path-like inputs. This applies to device paths, identifiers, and any security-sensitive whitelist. Note that different regex engines support different anchors (JavaScript does not support `\z`), so verify anchors are supported by the target language.

### 13. Preserve API Contracts When Centralizing / Refactoring
- **Rule**: When replacing a function body with a delegated/centralized implementation, the new behavior must honor the original signature and documented contract.
- **Guardrail**: If a wrapper advertises parameters (e.g., `fallbacks`, `env_var`) they must still take effect, or the parameters should be removed. Preserve the original error contract: if callers expect `None` for "not found", do not regress to raising an uncaught exception (e.g., `KeyError` from an unguarded dict lookup). Guard centralized lookups against unknown keys.

### 14. Caching Effectiveness Across Import Styles
- **Rule**: A lazy/TTL cache is defeated when consumers bind its result once via `from module import VALUE`.
- **Guardrail**: If a value is meant to be re-resolved over time (TTL, hot-reload), expose it through a function call (`get_x()`) and require call sites to invoke it at use time. Snapshotting via module-level `from ... import` (including values produced by module `__getattr__`) freezes the value at import and silently bypasses the cache's refresh semantics, producing inconsistent behavior across modules.

### 15. Authentication Consistency
- **Rule**: All admin endpoints must follow the same authentication pattern, use consistent cookie/session names, and be under the `/api/admin/` path.
- **Guardrail**: When adding new admin endpoints (e.g., file upload, configuration changes), always verify they include the same authentication checks as existing admin routes. In Flask applications, this typically means checking admin session cookies or using a decorator. Missing authentication on admin endpoints is a critical security vulnerability that allows unauthorized access to administrative functions. Place all administrative functions under `/api/admin/` rather than directly under `/api/` to ensure they automatically inherit global authentication middleware and follow the established security architecture.
- **Refactoring Consistency**: Authentication mechanisms (cookie names, session keys, token headers) are part of the security contract between client and server. When extracting routes into new files, verify that authentication checks use the same cookie/key names as the original implementation. Inconsistencies (e.g., checking "session_token" in one route but "admin_session" in another) cause authentication bypass or legitimate access denial. Audit all authentication paths after refactoring to ensure consistency.

### 16. Import Verification
- **Rule**: Never add code that uses modules without verifying the imports exist, and ensure imports are complete when extracting code.
- **Guardrail**: When adding new functionality that requires standard library or third-party modules, immediately add the corresponding import statement at the top of the file. Run a syntax check or linting tool before committing. When extracting code into new files during refactoring, verify all imports are copied to the new file. Before marking a refactoring task complete, run a syntax check or import verification on all new files. Missing imports cause immediate runtime failures (NameError) that are easily preventable.


### 17. Flask Route Definition Best Practices
- **Rule**: Do not define multiple route handlers for the same endpoint path.
- **Guardrail**: When an endpoint needs to handle multiple HTTP methods, use a single `@app.route()` decorator with all methods listed (e.g., `methods=["GET", "POST", "DELETE"]`) and dispatch within the handler function using `request.method`. Duplicate route definitions for the same path lead to code duplication, authentication logic duplication, and maintenance issues.

### 18. Complete Integrity Validation Chains
- **Rule**: Integrity checks must be validated on every read, not just written on save.
- **Guardrail**: When implementing file integrity validation (e.g., SHA256 hashes, signatures), the validation must occur in the read path as well as the write path. Writing a hash file without reading and validating it on load provides no protection against tampering. If validation fails on read, log a security-relevant warning and either reject the load or fall back to a known-good default with an error message. Never implement partial security measures that provide no actual protection—either implement the complete security measure or remove the partial implementation entirely.

### 19. Dependency Version Management for Reproducibility
- **Rule**: Do not change from pinned versions (`==`) to minimum versions (`>=`) without establishing a dependency update process.
- **Guardrail**: If allowing dependency updates is desired, implement safeguards first:
  - Create a documented dependency update policy (when to update, how to test)
  - Add automated testing against latest dependency versions in CI/CD
  - Consider using dependency management tools (pip-tools, poetry) to separate development constraints from production locks
  - Document specific library features used and their version requirements
  - For production systems, prefer pinned versions unless there is a clear, tested process for handling updates

### 20. Document Build Dependency Fixes
- **Rule**: When adding system-level build dependencies (e.g., `python3-dev`) to fix installation failures, document the specific error and OS version in comments or changelog.
- **Guardrail**: Installation script changes should include inline comments explaining:
  - The specific error that prompted the change
  - The OS version/distribution where the error occurred
  - Why the dependency resolves the issue (e.g., "Pillow 11.x requires build headers on Ubuntu 26.04")

### 21. Event Listener Management
- **Rule**: Never attach duplicate event listeners to the same DOM element, and avoid registering specific listeners for elements already handled by global handlers.
- **Guardrail**: When refactoring code to use initialization functions, ensure all event listener attachments are consolidated in a single location. Duplicate listeners cause handlers to execute multiple times, leading to unpredictable behavior (e.g., double submissions, concurrent animations). When adding interactive elements (e.g., modal close buttons), check if a global handler already exists for that pattern (e.g., `data-close-modal` attribute). If a global handler handles the element, do not add a specific event listener. Document the dependency on the global handler in a comment if the specific listener is intentionally omitted.

### 22. Root Cause Investigation Over Surface Fixes
- **Rule**: Always investigate the root cause of an issue before implementing fixes or adding debug logging.
- **Guardrail**: When a user reports a problem (e.g., "logo appears wrong size"), trace the data flow from source to display to find where the transformation occurs. Adding logging or surface-level fixes without understanding the underlying issue leads to incomplete solutions and technical debt. Follow the bug fixing discipline: identify root cause before implementing, prefer minimal upstream fixes over downstream workarounds.

### 23. Numbering Scheme Changes
- **Rule**: Numbering scheme changes (e.g., 1-indexed to 0-indexed IDs/display numbers) must be applied to every producer and consumer in the data flow.
- **Guardrail**: When changing identifiers such as `bay1` to `bay0`, audit backend generation, seed config, frontend default generation, manual creation flows, save payloads, sorting/display logic, tests, and documentation before marking the task complete. Partial migrations create off-by-one regressions and inconsistent persisted state.
- **Single-Layer Changes**: When changing indexing schemes within a single layer (frontend or backend), update all producers, consumers, and validation logic consistently. Even if the change doesn't cross layers, incomplete updates within the same layer create validation gaps and display inconsistencies.

### 24. Default Value Guardrails for Optional Schema Fields
- **Rule**: When adding new optional fields to database schemas, ensure there's a default value guardrail in the persistence layer.
- **Guardrail**: If a field is added to support new functionality (e.g., `job_type` for distinguishing job types), the persistence function must handle missing keys gracefully. Use `dict.get("field", default_value)` or add a DEFAULT constraint in the schema. Never rely on all code paths to set the new field—missing fields will insert NULL, breaking downstream logic that expects populated values.

### 25. Validation Completeness Across Multiple Input Paths
- **Rule**: When data can be provided via multiple mechanisms (e.g., ID lookup vs inline object, reference vs direct value), validation must cover all paths equally.
- **Guardrail**: If a validation function only checks data when provided through one path (e.g., template_id lookup), but the data can also be provided through another path (e.g., inline template object), the validation gap creates a security and consistency vulnerability. Either validate all paths, document the limitation clearly, or reject unsupported paths at the entry point.

### 26. Post-Transformation Contract Validation
- **Rule**: When applying transformations that reduce available data (e.g., skip_positions, filters, exclusions), validate that the result still meets the original contract.
- **Guardrail**: If a function is expected to return N items but a transformation (like skipping positions) can reduce the count, add post-transformation validation to ensure the result meets minimum requirements. For example, if bay_count=8 is requested but skip_positions eliminates 9 positions, the function should raise an error rather than silently returning fewer items.

### 27. UI State Priority and Unknown Value Handling
- **Rule**: UI state rendering must prioritize operational states over configuration/metadata states, and unknown values should not be displayed as known defaults.
- **Guardrail**: When rendering UI elements that display drive or system states:
  - Never display unknown/placeholder values as if they were known (e.g., defaulting "unknown" drive type to "HDD" badge). Only render badges/labels when the value is explicitly known.
  - Establish a clear state priority hierarchy where critical operational states (RUNNING, FAILED, LOCKED) take precedence over configuration states (UNCONFIGURED). Configuration warnings should be additive (e.g., corner badges, border styles) rather than state overrides that hide active operations.
  - When adding conditional state logic, place higher-priority checks last or use explicit priority ordering to prevent lower-priority states from masking critical information.

### 28. REST API Consistency Across HTTP Methods
- **Rule**: Maintain consistent API patterns across all HTTP methods for the same resource.
- **Guardrail**: When designing CRUD endpoints, use consistent parameter passing mechanisms. If POST/PUT use JSON request bodies for resource identifiers and data, DELETE should also use JSON bodies (or follow a documented, consistent pattern). Avoid mixing query parameters and request bodies for the same resource type, as this creates confusing APIs and can lead to information leakage through server logs. Document any intentional deviations clearly.

### 29. Cache Coherence Across Multiple Cached Calls
- **Rule**: When calling multiple cached functions that may return related data, ensure cache consistency to avoid stale/inconsistent responses.
- **Guardrail**: If a response combines data from multiple cached sources (e.g., controller list and device list), either use a single source of truth, pass cached data between functions, or disable caching for one of the calls to ensure temporal consistency. Never assume separate caches with independent TTLs will remain synchronized during a single request.

### 30. Consistent Error Handling Patterns
- **Rule**: Use consistent error handling patterns for similar operations across the codebase.
- **Guardrail**: When the same operation (e.g., SMART data retrieval, file parsing) appears in multiple places, use identical error handling patterns. If one path sets a null/error field and another uses silent pass, API consumers cannot distinguish between "no data" and "error occurred". Standardize on either explicit error fields or consistent null patterns to aid debugging and API contract clarity. This includes JSON parsing errors—wrap `json.loads()` calls in try/except blocks consistently across all database load functions.

### 31. Validation of Extracted Numeric Values
- **Rule**: Always validate numeric values extracted from strings or unstructured data.
- **Guardrail**: When extracting numbers via regex, parsing, or string manipulation (e.g., slot numbers from slot IDs), validate that the result is within reasonable bounds for the domain. Handle conversion exceptions gracefully and reject values outside expected ranges (e.g., slot numbers should be 0-9999, not arbitrary integers). Unvalidated extracted numbers can cause display issues or logic errors downstream.

### 32. DOM Element Null Check Completeness
- **Rule**: When adding new DOM element references, always include null checks for all new elements.
- **Guardrail**: If a codebase has an existing pattern of checking DOM element existence (e.g., lines 14-16 in auditLedger.js), any new element references must follow the same pattern. Missing null checks for new elements while checking old ones creates inconsistent error handling and runtime crashes when elements are missing from the DOM. Either check all elements or check none consistently.

### 33. Complete UI Control Implementation
- **Rule**: When adding new UI controls, ensure all corresponding event handlers and state management are implemented.
- **Guardrail**: If a new UI element is added (e.g., a button, input, or toggle), verify that all necessary event listeners are attached and state variables are declared. Missing event handlers or undeclared state variables cause runtime errors. Use a checklist: (1) element reference, (2) event listener, (3) state variable (if needed), (4) validation logic, (5) error handling.

### 34. Verification Before Claiming Completion
- **Rule**: Never claim fixes are complete in CRITIQUE.md resolution logs or document critical findings without verifying the actual code changes.
- **Guardrail**: When writing a resolution log claiming "Added X at line Y" or "Fixed function Z", read the file to verify the change actually exists before writing the log. When acting as the Critic Agent, before writing a finding in CRITIQUE.md, read the specific lines of code referenced in the finding to verify the issue actually exists. False claims of completed fixes waste review time and erode trust. After making edits, always read the affected lines to confirm the changes were applied before updating documentation.

### 35. State Management Lifecycle on Context Changes
- **Rule**: Clear transient UI state when the user context changes (filters, navigation, data refresh).
- **Guardrail**: Selection state, temporary flags, or user-modifiable data that depends on the current view should be cleared when the view changes (e.g., filtering a list, switching tabs, refreshing data). Persisting state across context changes creates confusing UX where selections refer to items no longer visible or relevant. Either clear state on context change or explicitly document that persistence is intentional.

### 36. Client-Side Validation to Match Backend Limits
- **Rule**: Enforce client-side limits that match backend API constraints to provide immediate user feedback.
- **Guardrail**: If the backend enforces a maximum (e.g., 100 items for DoS prevention), the frontend should enforce the same limit before making the API call. This prevents users from selecting many items only to have their request rejected, and provides immediate, actionable feedback. The frontend limit should be slightly more conservative than the backend limit to account for any edge cases.

### 37. API Contract Verification Before Implementation
- **Rule**: Verify the expected data types and formats for API endpoints before implementing client code.
- **Guardrail**: When implementing client code that calls an API, verify whether the endpoint expects database IDs, friendly IDs, slugs, or other identifier types. Sending the wrong ID type will cause API failures. Read the backend route handler or API documentation to confirm the expected format before writing the client-side implementation.

### 38. Path Traversal Prevention in File Serving
- **Rule**: Never serve files directly from user-controlled or database-stored paths without validation.
- **Guardrail**: When serving files based on paths from untrusted sources (JSON data, user input, database records), validate that the resolved absolute path is within the expected directory. Use `os.path.abspath()` on both the file path and the allowed base directory, then verify with `os.path.commonprefix()` that the file path is a subdirectory of the base directory. This prevents path traversal attacks (e.g., `../../../etc/passwd`) that could expose sensitive server files.

### 39. Default Resource Immutability Consistency
- **Rule**: When a system designates certain resources as immutable (e.g., default templates, system configurations), all modification operations must enforce this protection consistently.
- **Guardrail**: If one operation (e.g., DELETE) prevents modification of default resources, all other operations that could modify those resources (e.g., POST, PUT, import) must also include the same protection. Inconsistent enforcement creates security vulnerabilities where attackers can bypass immutability through a less-protected code path. Either reject operations that would modify defaults, skip default resources with a warning, or require explicit confirmation/override flags.

### 40. Async/Await Syntax Requirement in JavaScript
- **Rule**: Never use `await` without declaring the function as `async`.
- **Guardrail**: When adding `await` calls to a function, ensure the function is declared with the `async` keyword. JavaScript will throw a SyntaxError at runtime if `await` is used in a non-async function. This is a fundamental syntax requirement that prevents the code from executing at all.

### 41. Verify Edit Operation Output for Syntax Validity
- **Rule**: Always verify that edit operations produce syntactically valid code, especially when modifying existing functions.
- **Guardrail**: After using the edit tool to modify code, read the modified section to ensure the output is valid syntax. Corrupted edits (e.g., mangled string literals, broken function signatures, incomplete lines) will cause the entire file to fail loading. This is particularly important when editing within existing functions where line boundaries can be ambiguous. If an edit produces invalid syntax, revert and use a more specific old_string with more surrounding context to ensure uniqueness.

### 42. Declare All State Variables at Module Level
- **Rule**: All module-level state variables must be explicitly declared before use, not implicitly created through assignment.
- **Guardrail**: When adding new functionality that requires persistent state (e.g., mappingMode, manualMappings, selectedDevice), declare these variables at the module level with explicit initialization (e.g., `let mappingMode = 'pattern'`). Never rely on implicit declaration through assignment in functions, as this creates implicit globals and makes the code fragile to refactoring. Follow the existing pattern in the codebase (e.g., lines 109-119 in adminPanel.js) for consistency.

### 43. Validate Conditional Block Placement
- **Rule**: Ensure initialization code is placed at the correct scope level, not nested inside unrelated conditionals.
- **Guardrail**: When adding initialization code (e.g., resetting state variables), verify it runs in all required code paths. If initialization is placed inside a conditional block (e.g., `if (mappingPreview)`), it will not execute when that condition is false. Initialization that should always run must be outside conditionals. Review the control flow to ensure initialization occurs at the appropriate point in the lifecycle.

### 44. Verify Global State Dependencies Before Use
- **Rule**: Before using global state variables in new functions, verify they are properly initialized and populated.
- **Guardrail**: When writing functions that depend on global state (e.g., `localBayMapCopy`), ensure the state is either: (1) passed as a parameter, (2) initialized in a well-defined lifecycle method before the function is called, or (3) documented as a required precondition. Never assume global state exists without verification. If the state has complex initialization requirements, add a comment documenting when and how it is populated.

### 45. Verify Variable References and Scope When Editing Functions
- **Rule**: When adding code blocks to existing functions, verify all referenced variables exist and are in scope.
- **Guardrail**: Before inserting validation, error handling, or logic blocks into an existing function, check that every variable referenced in the new code is either: (1) already declared in the function scope, (2) passed as a parameter, or (3) a global variable that exists in the module. Typos in variable names (e.g., `localBayMapState` vs `localBayMapCopy`) and scope errors (referencing variables outside their lexical scope) cause runtime failures that are easily preventable by reviewing the function's existing variable declarations before editing.


### 46. Code Extraction Best Practices
- **Rule**: When extracting code into new modules (blueprints, sub-packages, etc.), the original module MUST have the extracted code deleted in the same commit, and all side-effects must be preserved exactly.
- **Remove Original Code**: If you extract Flask routes into Blueprint files but leave the original `@app.route()` decorators in the monolithic module, the original routes register first and shadow the blueprints — making the new files dead code that never executes. Before marking an extraction refactor complete: (1) delete the moved handlers from the source file, (2) remove the now-unused imports from the source file, (3) verify the application starts without errors, (4) confirm at least one extracted endpoint responds via its new handler. Never ship "both old and new" route registrations simultaneously.
- **Clean Stale Imports**: After extracting code into new modules, audit both source and destination files for stale imports. When deleting functions from a module during extraction, some imports become orphaned (e.g., a utility only used by the moved code). These stale imports waste startup time, obscure the module's actual dependencies, and fail linting. After completing any extraction refactor, run a linting tool or manually grep each import symbol to confirm it is still used in the remaining code. This applies to both the source file and the destination files (e.g., an import copied to a new file but never called there).
- **No Initialization Side-Effects**: When splitting a monolithic file into smaller modules, each new file must reproduce only the behavior of its extracted section — never add new auto-initialization or lifecycle hooks that weren't present in the original. A common mistake is adding `DOMContentLoaded` handlers or immediate function calls in a newly extracted module "for convenience," which changes when code runs relative to authentication flows, dependency loading, or application state. Before committing a split: diff the combined new files against the original and flag any top-level statements that are net-new. Every auto-executing line must trace back to equivalent code in the original module.

### 47. Avoid Unnecessary Defensive Programming Patterns
- **Rule**: Prefer simple, declarative solutions over complex defensive patterns when simpler alternatives exist.
- **Guardrail**: When defensive programming patterns (e.g., DOMContentLoaded checks, null checks) are added to handle edge cases, first verify if a simpler solution exists (e.g., using the `defer` attribute on script tags, existing null checks at module load time). Complex defensive patterns that duplicate code across multiple locations create maintenance burden and technical debt. Use the simplest solution that actually solves the problem, and document why the defensive pattern is necessary if no simpler alternative exists.


### 48. Explicit Dependencies Between Non-Module Script Files
- **Rule**: When splitting browser-side scripts loaded via `<script>` tags (non-ES-module), cross-file variable references must be documented and centralized.
- **Guardrail**: `const`/`let` declarations at global scope in non-module scripts are shared across files via the global lexical environment, but the dependency is invisible — there is no `import` statement to make it explicit. If File B uses a `const` declared in File A, (a) add a comment in File B noting the dependency, or (b) move the shared declaration into a utilities file that both depend on. This prevents silent breakage when `<script>` tag order is changed. Any shared DOM reference used by multiple split files should live in a single "shared references" file loaded first.

### 49. Single Source of Truth for Authoritative Configuration
- **Rule**: Never duplicate authoritative configuration or data lists between frontend and backend.
- **Guardrail**: When the backend defines the authoritative source for data (e.g., default templates, system constants, allowed values), the frontend should derive this information from the API response rather than maintaining its own hardcoded list. Duplication creates maintenance burden and drift risk—if the backend list changes but the frontend list is not updated, inconsistencies occur. The API should expose the authoritative data (e.g., include an `is_default` boolean field), and the frontend should use this field for UI logic.

### 50. Collision Probability in Generated Identifiers
- **Rule**: When generating human-readable identifiers with random components, ensure sufficient entropy to prevent collisions.
- **Guardrail**: For identifiers that include random suffixes (e.g., UUID-based friendly IDs), calculate the collision probability based on expected volume. With 4 hex characters (65,536 combinations), collisions become likely if more than ~8,000 items are generated per day (birthday paradox). Use at least 6 hex characters (16,777,216 combinations) for daily-rotated identifiers, or implement collision detection with retry logic. Document the collision probability assumptions in code comments.

### 51. Complete Field Name Refactoring Across All Data Paths
- **Rule**: When renaming fields or identifiers, update all read and write paths consistently in a single operation.
- **Guardrail**: Partial field name refactoring creates schema mismatches where data is written with the new name but read with the old name (or vice versa). Before marking a refactoring complete, grep the entire codebase for all occurrences of the old field name, including: form submissions, API payloads, database queries, template rendering, default value assignments, and HTML form field names. If backward compatibility is needed during migration, add explicit fallback logic (e.g., `data.new_field || data.old_field`) and document the transition period. Never ship partial schema migrations.

### 52. Validation Consistency During Transformations
- **Rule**: When validating input that undergoes transformation (e.g., parsing, conversion, mapping), validate the original input before transformation, not the transformed output. When changing the domain or range of a numbering scheme, update all validation logic to match the new domain.
- **Pre-Transformation Validation**: If validation checks the transformed data instead of the original input, type mismatches can render validation ineffective. For example, validating an array of objects when the input was an array of numbers will fail to catch invalid numeric ranges. Always validate the raw input at the point of entry, then apply transformations. If validation logic must reference transformed data, use distinct variable names to avoid shadowing the original input and causing confusion about which data is being validated.
- **Domain Change Updates**: If a numbering scheme's upper bound changes from value A to value B (e.g., from `bay_count` to `rows * cols`), every validation that checks against that bound must be updated. Validation gaps create situations where the UI displays values that validation rejects, or vice versa. Audit all validation logic when changing numbering domains, including input validation, range checks, and error messages.

### 53. Guard Against Division by Zero in Mathematical Conversion Functions
- **Rule**: All functions performing division or modulo operations must validate that divisors are non-zero before the operation.
- **Guardrail**: Mathematical conversion functions (e.g., coordinate transformations, index calculations) that accept divisor parameters must include guard clauses to reject zero or negative values. Even if the current caller validates inputs, the function is a reusable utility that may be called from other contexts in the future. Throw a clear error message (e.g., "Columns must be greater than 0") rather than allowing the operation to proceed with Infinity, NaN, or incorrect results.

### 54. Remove Debugging Artifacts Before Committing
- **Rule**: Never commit debugging console.log statements or temporary debug code to production.

### 55. User Feedback for Data Reduction During Transformations

### 56. State Update Functions Must Preserve Non-Serializable Types
- **Rule**: When updating state objects that contain non-serializable types (Sets, Maps, custom objects), use deep merging or explicit field preservation rather than shallow spread operators.
- **Guardrail**: The spread operator (`{ ...state, ...newState }`) creates a shallow copy and does not preserve Set or Map references. If the newState does not include these fields, they become undefined. Either: (1) explicitly preserve non-serializable fields in the spread, (2) use deep merging that handles these types correctly, or (3) only update specific fields rather than replacing the entire state object. This is critical for state management in browser applications where Sets are used for tracking selections.

### 57. UI Button State Synchronization Across Mode Switches
- **Rule**: When implementing mode switches (e.g., pattern vs manual mode), all UI state including button enable/disable states must be synchronized with the new mode.
- **Guardrail**: Mode switches should reset or disable any UI controls that are not applicable to the new mode. For example, if an "Undo" button is only meaningful in pattern mode, it must be disabled when switching to manual mode. Leaving stateful UI controls enabled across mode switches creates inconsistent state where user actions can produce confusing or invalid results. All button states, selection states, and validation states should be reviewed when adding mode switching logic.

### 56. Authentication Parameter Consistency Across Multiple Mechanisms
- **Rule**: When multiple authentication mechanisms exist in the same application, they must use identical default values and parameter sources.
- **Guardrail**: If the application has both a global authentication middleware (e.g., `@app.before_request`) and route-specific authentication decorators, they must derive their authentication parameters from the same source with the same defaults. Inconsistent default values (e.g., one mechanism uses "eraser123" as default passphrase, another uses "") create security vulnerabilities where authentication succeeds via one path but fails via another. Define authentication parameters as shared constants in a single location, or ensure all mechanisms call the same configuration retrieval function with identical default values.

### 56. Decorator Order for Security-Critical Middleware
- **Rule**: Authentication and authorization decorators must always be applied before rate limiting decorators.
- **Guardrail**: In Flask applications, decorators are applied bottom-to-top (the last decorator executes first). If `@limiter.limit()` is placed before `@require_admin_auth`, rate limits are enforced on unauthenticated requests, allowing attackers to exhaust the quota and deny service to legitimate users. Always place authentication decorators above rate limiting decorators in the source code so they execute first. This applies to any security-critical middleware (CORS, rate limiting, logging) that should only apply to authenticated requests.

### 57. Shared Storage for Rate Limiting in Multi-Worker Deployments
- **Rule**: Rate limiting state must be stored in a shared backend when using multi-worker deployments.
- **Guardrail**: In-memory rate limit storage (`storage_uri="memory://"`) is only safe for single-worker deployments. With multiple workers (gunicorn, uWSGI), each worker maintains its own rate limit state, allowing attackers to bypass limits by distributing requests across workers. For production deployments, use shared storage backends like Redis or Memcached. If in-memory storage must be used, document this as a known limitation and enforce single-worker mode in the deployment configuration.

### 56. Subprocess Version Capture Error Handling
- **Rule**: When capturing version information from external tools via subprocess, distinguish between different failure modes and validate meaningful output.
- **Guardrail**: Functions that call external commands (e.g., `hdparm -V`, `nvme --version`) must:
  - Catch specific exceptions (TimeoutExpired, FileNotFoundError, PermissionError) separately from generic Exception
  - Log the specific failure reason when a tool fails
  - Validate that output matches expected patterns before using it
  - Distinguish between "command not found", "command failed", "timeout", and "permission denied"
  - Consider caching versions to avoid repeated subprocess calls
  - Add validation that meaningful versions were captured before including them in audit trails or certificates

### 57. Cache Failure State Handling
- **Rule**: Never cache failure states (None, error values) in TTL-based caches.
- **Guardrail**: When implementing caching with TTL, only update the cache on successful data acquisition. If an operation fails (subprocess timeout, file I/O error, exception), do not cache the failure state. Caching failures causes the cache to return invalid data for the entire TTL period, even if the underlying issue is transient. The cache contract should be: return valid cached data, or perform a fresh scan on miss. Never cache None or error results. Move cache update logic inside the success path, not in finally blocks or exception handlers.

### 58. Cache Consistency Across Function Calls
- **Rule**: When adding caching to a function, ensure all call sites use the caching consistently.
- **Guardrail**: If a function is modified to support caching via a `use_cache` parameter, all existing call sites must be updated to pass `use_cache=True` (or the appropriate default). Leaving call sites that bypass the cache defeats the purpose of the optimization and creates hot paths that still perform expensive operations. Audit all call sites when adding caching to ensure consistent usage.

### 59. User Feedback for Data Reduction During Transformations
- **Rule**: When transformations reduce available data (e.g., filtering, skipping invalid items), always provide explicit user feedback about what was excluded.
- **Guardrail**: If a transformation skips items (e.g., missing bays, data mismatches, validation failures), track and report the count and reason for skipped items to the user. Silent data reduction creates confusing UX where users don't understand why expected items are missing from results. Always provide visible feedback (e.g., "Warning: 3 device(s) skipped due to missing bays") rather than silently proceeding with reduced data.

### 60. Avoid Code Duplication - DRY Principle
- **Rule**: Never copy-paste identical logic blocks across multiple functions.
- **Guardrail**: When the same logic appears in multiple functions (e.g., enclosure-based mapping in three pattern functions), extract it into a shared helper function. Code duplication creates maintenance burden where bug fixes must be applied in multiple locations. Before adding new code, check if similar logic already exists and can be reused or refactored into a common utility. This is especially important for complex logic with multiple edge cases or validation steps.

### 61. CSS Class Consistency for UI Element Visibility
- **Rule**: When implementing UI element visibility control, use the CSS class pattern established for that element type in the codebase.
- **Guardrail**: Different UI elements use different visibility patterns (e.g., modals use `.open` class with `.modal { display: none; }` and `.modal.open { display: block; }`, while footers/overlays use `.hidden` class). Before adding visibility control to an element, verify the CSS class definitions in the stylesheet. Mixing visibility patterns (e.g., using `.hidden` on a modal when the established pattern is `.open`) causes the element to not display correctly. Always grep the CSS file to confirm the correct class names and their semantics before using them.

### 62. HTML Form Constraints Must Match JavaScript Validation
- **Rule**: HTML form validation attributes (min, max, pattern) must match JavaScript validation logic to provide consistent user feedback.
- **Guardrail**: When adding client-side validation, ensure HTML form attributes and JavaScript validation use the same rules. If JavaScript validates a number must be between 1 and 100, the HTML input should have min="1" and max="100". Mismatched validation causes confusing UX where the browser allows values that JavaScript rejects, or vice versa.

### 63. UI Preview Consistency with Configuration
- **Rule**: UI previews must accurately reflect the actual configuration and behavior that will be used at runtime, or clearly indicate when using a simplified representation.
- **Guardrail**: When implementing preview components (e.g., template previews, configuration visualizations), the preview should generally respect all relevant configuration parameters (traversal order, numbering schemes, etc.) and match the backend logic exactly. However, simplified representations are acceptable if:
  - The distinction is clearly documented or indicated to users (e.g., "Reference numbering for configuration" vs "Actual erasure order")
  - The operational behavior (animation, backend) respects the actual configuration
  - The simplified representation serves a clear UX purpose (e.g., consistent reference numbering for input fields)
  - The animation or dynamic preview shows the actual runtime behavior
  Hardcoding preview behavior to a fixed order without clear indication creates misleading UX. When using simplified representations, add visual indicators or documentation to clarify the difference between reference displays and operational behavior.

### 64. Environment Variable Visibility in Subprocess Heredocs
- **Rule**: Bash variables are not automatically visible to subprocess heredocs unless explicitly exported.
- **Guardrail**: When using bash heredocs to execute Python or other subprocesses that need access to bash variables (e.g., `CONFIG_DIR`, `INSTALL_DIR`), export the variables before the heredoc using `export VAR_NAME`. The subprocess will not inherit shell variables that are only set but not exported. Hardcoded defaults in subprocess code are fragile and prone to drift from the actual bash configuration. Follow the pattern: `export VAR_NAME; subprocess << EOF; ... $VAR_NAME ...; EOF; unset VAR_NAME`.

### 65. Install Scripts Must Create All Required Backend Files
- **Rule**: When install scripts create configuration files that the backend reads, they must create all required files (including auxiliary files like hashes, signatures, indexes) in the exact format expected by the backend.
- **Guardrail**: If the backend expects a configuration file plus an integrity hash file (e.g., `config.json` and `config.json.sha256`), the install script must create both. Creating only the primary file causes the backend to reject it or fall back to defaults, making the install script's work ineffective. Before adding file creation to install scripts, read the backend's load function to identify all required files and their formats.

### 66. Shell Script TOCTOU Prevention
- **Rule**: Even in shell scripts and install utilities, avoid check-then-write patterns that create TOCTOU race conditions.
- **Guardrail**: When writing shell scripts that modify files, do not check file validity then write based on that check. Instead, attempt the operation directly and handle errors. For file creation, use atomic patterns (write to temp file then rename) without pre-checks. For validation, attempt to parse/process the file directly rather than checking existence first. This applies even to single-user scenarios—defensive coding prevents future issues when the script is used in different contexts.

### 67. Complete UI Option Distribution Across All Relevant Controls
- **Rule**: When adding new options to dropdowns or selects, the option must be added to all UI locations where that choice is relevant.
- **Guardrail**: If a new traversal preset, template type, or configuration option is added to one part of the UI (e.g., template creation form), it must also be added to all other UI controls that reference the same concept (e.g., bay mapping traversal select, template application dialog). Partial implementation creates confusing UX where users can create resources with certain options but cannot apply or use them elsewhere. Audit the entire codebase for all references to the option type before marking the feature complete.

### 68. Apply Lessons to Code Changes in the Same Commit
- **Rule**: When adding a new lesson to lessons-learned.md, verify that the concurrent code changes do not violate that lesson.
- **Guardrail**: If a code change introduces a pattern that a new lesson is meant to prevent, the code must be fixed to comply with the lesson before the commit is complete. Adding a lesson that describes a bug without fixing the bug creates technical debt and confusion. When adding guardrails, audit the concurrent changes for violations and fix them in the same operation.

### 69. Function Contract Preservation During Modifications
- **Rule**: When modifying a function to support new use cases, preserve the original contract for all existing callers or update all call sites consistently.
- **Guardrail**: If a function modification changes the semantics (e.g., deriving grid dimensions from partial inputs instead of accepting full dimensions as parameters), this breaks the contract for existing callers. Either: (1) make the change backward-compatible by adding optional parameters with sensible defaults, (2) create a new function with a different name for the new use case, or (3) update all call sites to pass the required data. Never infer critical data from partial inputs when the full data is available from the caller—this creates fragile functions that fail with edge cases. When adding optional parameters to existing functions, ensure all call sites that rely on the old behavior are updated or the default preserves the old semantics.

### 70. DOM State Detection Should Use Data Attributes, Not Visual Content
- **Rule**: When detecting DOM element state (e.g., skipped cells, selected items, disabled elements), use data attributes rather than inspecting visual content or text.
- **Guardrail**: Checking `textContent`, `innerHTML`, or visual markers (e.g., `cell.textContent === "×"`) couples state detection to the visual representation, making code fragile to UI changes. Instead, set `data-*` attributes during rendering (e.g., `data-skipped="true"`) and check these attributes in event handlers and utility functions. This decouples state logic from presentation and allows UI changes without breaking behavior.

### 71. Enforce Total Limits When Iterating Over Dynamic System Resources
- **Rule**: When iterating over dynamic system resources (e.g., SCSI hosts, PCI devices, filesystem entries), always enforce a total limit across all iterations, not just per-item limits.
- **Guardrail**: Per-item limits (e.g., 24 slots per host) are insufficient when the number of items is unbounded (e.g., 100+ hosts). Always add a total limit check that breaks out of all loops when the limit is reached. This prevents memory exhaustion and long-running operations in environments with many resources. The pattern is: `if len(collection) >= MAX_TOTAL: break` in both inner and outer loops.

### 72. Validate Composite Format Strings with Proper Regex Patterns
- **Rule**: When validating strings that contain multiple concatenated components (e.g., "pci-{addr}-scsi-{host}:0:{slot}:0"), use strict regex patterns that validate the entire structure, not just prefix checks.
- **Guardrail**: Checking only that a string starts with a prefix (e.g., `startswith('pci-')`) is insufficient for security and correctness. Use full regex patterns that validate each component in the correct format and position. For composite formats, the regex should match the entire string structure with proper anchors (e.g., `^pci-[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]-scsi-\d+:0:\d+:0\Z`). This prevents malformed or malicious strings from passing validation.

### 73. Standardize Return Structures Across Function Families
- **Rule**: Functions that perform similar operations (e.g., mapping functions, validation functions) must return consistent data structures with the same field names and types.
- **Guardrail**: When multiple functions in the same family return different structures (e.g., one returns `{mapping, skippedCount, mismatchCount}` while another returns `{mapping, skippedCount, emptySlotCount}`), API consumers cannot handle the results uniformly. This creates fragile code that breaks when the fallback path is taken. Define a standard return structure for the function family and ensure all functions conform to it, including all relevant fields with appropriate defaults (e.g., `mismatchCount: 0` if not applicable).

### 74. Retry Logic Consistency Within Multi-Pass Operations
- **Rule**: When a function performs multiple passes over the same data with the same operation (e.g., reading chunks from a device), all passes must use identical retry and error handling logic.
- **Guardrail**: If the first pass of an operation uses retry logic with progressive delays to handle transient errors (e.g., drives needing time to become readable after crypto sanitize), any subsequent passes that perform the same operation must use the same retry pattern. Inconsistent retry logic creates race conditions where transient errors cause false failures in later passes but would have been retried in earlier passes. Extract retry logic into a helper function or ensure all passes share the same retry-with-delays pattern.

### 75. Conditional Authentication for Admin Endpoints
- **Rule**: Admin endpoints must implement conditional authentication that distinguishes between local and remote access.
- **Guardrail**: When securing administrative functions, check the request source IP and apply authentication conditionally:
  - Local requests (127.0.0.1, ::1, or local network subnets) should be allowed without authentication for on-premises convenience
  - Remote requests must require authentication via session cookies, tokens, or other secure mechanisms
  - This pattern applies to all admin routes (policy, configuration, sensitive data exports, etc.)
  - Implement as a decorator or middleware to ensure no route is missed
  - Never skip authentication entirely on admin endpoints—this allows unauthorized remote access to system configuration and sensitive data

### 76. Consistent Security Decorator Application Across All Route Modules
- **Rule**: When adding security decorators (authentication, rate limiting) to routes, apply them consistently across ALL route modules, not just the main api_routes.py file.
- **Guardrail**: Flask applications often split routes into multiple blueprints (certificate_routes, template_routes, bay_mapping_routes, etc.). When implementing security controls, audit ALL route files, not just the main routes file. Missing decorators on blueprint routes creates security vulnerabilities where sensitive operations (bulk certificate generation, template management, bay mapping) are completely unprotected. Use grep to find all route definitions and verify each has the required decorators before marking the task complete.

### 77. Documentation Must Match Implementation
- **Rule**: When updating API contract documentation, verify that the documented security controls actually exist in the implementation.
- **Guardrail**: Do not document endpoints as having authentication or rate limiting if those decorators are not actually applied to the routes. Misleading documentation creates a false sense of security and may lead to security audits passing when the implementation is actually vulnerable. Before documenting security controls for an endpoint, verify the route has the required decorators in the source code. If implementation is incomplete, either defer documentation or document the actual state (e.g., "TODO: add authentication").

### 78. CSS Class Migration Requires Cross-File Usage Verification
- **Rule**: When consolidating or removing CSS classes, verify all usages across the entire codebase before deletion.
- **Guardrail**: CSS class consolidation (e.g., replacing `.status-ok` with `.status-badge--complete`) must include a comprehensive search for all usages of the old classes across all JavaScript, HTML, and template files. Removing a CSS class without updating all consumers causes functional regressions where UI elements lose their styling. Use grep to find all occurrences of the old class names before removing them from the CSS file. Either update all consumers to use the new classes, or keep the old classes as backward-compatible aliases.

### 79. Reject Trailing Tokens in Validated SQL Column Definitions
- **Rule**: When tokenizing and validating a SQL column definition string before interpolating it into a raw SQL statement, reject any unexpected trailing tokens after the validated components.
- **Guardrail**: Validating only the expected tokens (column name, type, DEFAULT keyword, default value) while ignoring extra tokens at the end allows injection via trailing SQL fragments (e.g., `col TEXT DEFAULT 0 ; DROP TABLE ...`). After validating the known parts, explicitly check that no additional tokens exist beyond what is expected. For a definition with DEFAULT, expect exactly 4 tokens; without DEFAULT, expect exactly 2. Raise an error if extra tokens are present.

### 80. Prevent Circular Imports When Centralizing Module Initialization
- **Rule**: When centralizing module initialization (e.g., registering Flask blueprints in app_config.py), avoid creating circular import dependencies.
- **Guardrail**: If a central module (e.g., app_config.py) imports from other modules at module level, those other modules must not import from the central module at module level. Circular imports cause ImportError or AttributeError at startup because Python cannot satisfy the import chain. Solutions:
  - Move the centralized imports into a function that is called after all modules are loaded (e.g., `register_blueprints(app)` called at the end of app_config.py or in app.py)
  - Use lazy imports within functions rather than at module level in the central module
  - Restructure so that dependent modules do not import from the central module at module level (move those imports into route functions)
  - Extract shared utilities into a separate module that both can import from without circularity
Before centralizing initialization, trace the import graph to ensure no cycles exist.

### 81. Validate Extracted Device Paths Before Use
- **Rule**: When extracting or deriving device paths from validated input (e.g., extracting controller from namespace), the extracted path must also be validated before use.
- **Guardrail**: Validation of the original input does not guarantee the extracted/derived value is safe. Any path used in command construction must pass `validate_device_path()` regardless of its origin. For example, when extracting `/dev/nvme0` from `/dev/nvme0n1` via regex, the extracted controller path must be validated before being passed to subprocess commands. This defense-in-depth approach prevents security regressions if the extraction logic changes or if new code paths bypass the original validation. Apply this principle to all derived values: extracted substrings, transformed paths, or computed identifiers used in security-sensitive contexts.

### 82. Subprocess Lifecycle Management for Job Termination
- **Rule**: When implementing job termination features, store subprocess references and explicitly terminate them rather than just updating status flags.
- **Guardrail**: Updating a job's status to "failed" or "killed" in memory/database does not stop the actual subprocess executing the work. The job execution loop must check for termination signals, and the termination endpoint must:
  1. Store the subprocess `Popen` object in the job dict (e.g., `job["_process"] = process`)
  2. When terminating: check `process.poll() is None` to verify it's still running
  3. Call `process.terminate()` followed by `process.wait(timeout=N)` with fallback to `process.kill()`
  4. Ensure the job execution loop checks the same termination condition as the admin endpoint

### 83. Raw Strings for Docstrings Containing Regex Escape Sequences
- **Rule**: When writing docstrings or comments that reference regex escape sequences like `\Z`, use raw string literals to avoid SyntaxWarnings.
- **Guardrail**: Python's string parser treats backslashes as escape characters. Writing `\Z` in a regular string literal triggers a SyntaxWarning because `\Z` is not a recognized escape sequence. Use raw string prefixes (`r"""` or `r'`) for docstrings and comments that reference regex anchors. This tells Python to treat backslashes literally, which is what regex engines expect. Never escape the backslash in documentation (e.g., `\\Z`) as this is technically inaccurate—the regex engine reads a single backslash. The regex patterns themselves should always use raw strings (`r'pattern\Z'`), and any documentation describing them should also use raw strings.

### 84. Database Connection Cleanup in Test Fixtures
- **Rule**: Test fixtures that create or use SQLite databases must explicitly clean up connections to prevent ResourceWarnings.
- **Guardrail**: When writing pytest fixtures that initialize databases or use `init_wipe_db()`, the fixture's finally block must:
  1. Call `gc.collect()` to trigger garbage collection of pending connections
  2. Open a connection to the test database and run `PRAGMA wal_checkpoint(TRUNCATE)` to close WAL files
  3. Optionally run `PRAGMA optimize` to help SQLite clean up
  4. Wrap this in try-except to handle cases where the database file doesn't exist
  This prevents unclosed connection warnings that originate from SQLite's connection pooling and WAL mode. The backend code should use context managers (`with closing(sqlite3.connect(...))`), but test fixtures need explicit cleanup because connections may be held by Flask, Werkzeug, or mock objects during test execution.

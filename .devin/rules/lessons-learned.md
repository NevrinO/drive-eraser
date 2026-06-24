---
trigger: always_on
---

# Project Lessons Learned & Guardrails

This file contains generalized architectural guardrails derived from past agent mistakes. The Coding Agent must strictly adhere to these rules.

## Rules About This File

### 1. File Maintenance Rule
- **Rule**: This file is editable and should be maintained as the project evolves. New lessons must always be appended to the end of the file. Do not insert lessons in the middle, as this requires renumbering all subsequent rules.
- **Guardrail**: When adding a new lesson, read the file to find the last rule number, then append with the next sequential number. The rules do not need to be in any particular order - they are a collection of guardrails that should be applied regardless of position.

### 2. Append-Only Rule for lessons-learned.md
- **Rule**: When adding new lessons to lessons-learned.md, always append them to the end. Never insert lessons in the middle.
- **Guardrail**: This rule is also documented in critic-actor-protocol.md under "CRITICAL APPEND-ONLY RULE" for the Critic Agent. Violating this rule creates duplicate rule numbers and makes the file difficult to maintain.

### 3. Apply Lessons to Code Changes in the Same Commit
- **Rule**: When adding a new lesson to lessons-learned.md, verify that the concurrent code changes do not violate that lesson.
- **Guardrail**: If a code change introduces a pattern that a new lesson is meant to prevent, the code must be fixed to comply with the lesson before the commit is complete. Adding a lesson that describes a bug without fixing the bug creates technical debt and confusion. When adding guardrails, audit the concurrent changes for violations and fix them in the same operation.

### 4. Verification Before Claiming Completion
- **Rule**: Never claim fixes are complete in CRITIQUE.md resolution logs or document critical findings without verifying the actual code changes.
- **Guardrail**: When writing a resolution log claiming "Added X at line Y" or "Fixed function Z", read the file to verify the change actually exists before writing the log. When acting as the Critic Agent, before writing a finding in CRITIQUE.md, read the specific lines of code referenced in the finding to verify the issue actually exists. False claims of completed fixes waste review time and erode trust. After making edits, always read the affected lines to confirm the changes were applied before updating documentation.

## General Rules

### 5. SQL Security & Column Modification
- **Rule**: Never accept a raw, unvalidated string for SQL column definitions or parameters.
- **Guardrail**: If dynamic schema modifications are required, validate parameters against an allowlist, or split parameters so that type definitions and names are strictly validated via regex and escaped. Never assume a regex that matches `[a-zA-Z]` is safe if it allows raw unquoted input trailing it.

### 6. Concurrency & Race Conditions
- **Rule**: Do not assume "single-process usage" is safe.
- **Guardrail**: If the codebase uses threads, locks, or runs in a multi-worker environment (like Flask/Gunicorn), always write thread-safe operations. Use locks, transactions, or native database atomic operations (e.g., `IF NOT EXISTS`). For schema modifications like `ALTER TABLE`, wrap in try-except to handle duplicate column errors from concurrent operations. For file-based state management, use file locking (e.g., `fcntl.flock()` on Unix, `msvcrt.locking()` on Windows, or the `filelock` library) around the entire load-modify-save sequence. Atomic file writes (tempfile + rename) only prevent partial file corruption, not lost updates from concurrent modifications.
- **TOCTOU Prevention**: Time-of-check to time-of-use race conditions occur when file existence checks and subsequent operations are not atomic. The correct fix is to remove the pre-check entirely and handle exceptions from the actual operation. Wrapping `os.path.exists()` or `os.path.isdir()` in try-except does not eliminate the race condition—it only prevents crashes from the check itself. The atomic operation is the actual file/directory access (e.g., `os.listdir()`), not the existence check. Use the pattern: `try: operation() except OSError: handle_error()` without any pre-check. For file upload/delete operations, also use atomic patterns like writing to a temporary file then using `os.rename()` (atomic on POSIX) or file locking mechanisms.
- **Shell Script TOCTOU Prevention**: Even in shell scripts and install utilities, avoid check-then-write patterns that create TOCTOU race conditions. When writing shell scripts that modify files, do not check file validity then write based on that check. Instead, attempt the operation directly and handle errors. For file creation, use atomic patterns (write to temp file then rename) without pre-checks. For validation, attempt to parse/process the file directly rather than checking existence first. This applies even to single-user scenarios—defensive coding prevents future issues when the script is used in different contexts.

### 7. HTML Parsing
- **Rule**: Do not use regular expressions to parse HTML.
- **Guardrail**: Always use a robust parser like BeautifulSoup (`bs4`) or `lxml` to extract elements, body text, or attributes to prevent fragility with nested tags, multiline elements, or script tags.

### 8. Object & Array Comparisons
- **Rule**: Do not rely on naive `json.dumps()` or `str()` comparisons for arbitrary arrays or objects.
- **Guardrail**: Account for non-serializable types (datetimes, sets), circular references, and key sorting. Use try/except fallbacks or deep-comparison helper functions to avoid runtime crashes.

### 9. Size Limits for DoS Prevention
- **Rule**: Always enforce size limits on user input, collections, and API responses.
- **Guardrail**: For any endpoint accepting arrays (e.g., job_ids, filters), enforce a reasonable maximum size (e.g., 100 items) to prevent memory exhaustion and long-running queries. For bulk operations, enforce limits on total collection size (e.g., 100 items total). For API responses that return collections, enforce reasonable maximum sizes (e.g., 1000 items) to prevent memory exhaustion, long response times, or network bandwidth exhaustion. Return a 400 error or truncate with pagination if limits would be exceeded. Also enforce size limits before parsing JSON from untrusted sources (e.g., 64KB maximum).

### 10. Date Range Validation
- **Rule**: When accepting date range filters, validate logical consistency.
- **Guardrail**: If both start_date and end_date are provided, ensure start_date <= end_date to prevent inverted ranges that produce no results or confusing behavior.

### 11. SQL Query Clarity
- **Rule**: Avoid confusing or redundant query patterns that duplicate parameters without clear intent.
- **Guardrail**: When searching multiple columns with the same value set, document the intent clearly or validate which column should be searched based on input format. Duplicate parameter lists without clear purpose make code hard to maintain and debug.

### 12. Recursive Processing & Circular References
- **Rule**: When implementing recursive traversal of data structures (lists, dicts, trees), always add cycle detection.
- **Guardrail**: Use a `visited` set or similar mechanism to track processed objects and prevent infinite recursion on circular references. Failing to do so will cause `RecursionError` at runtime when encountering self-referential structures (e.g., `a = []; a.append(a)` or `d = {}; d['self'] = d`).

### 13. Device Path Validation
- **Rule**: Never accept raw device paths from user input or internal APIs without validation.
- **Guardrail**: Validate device paths against a strict regex whitelist (e.g., `^/dev/[a-z]+[0-9]*$`) before using in command construction. This prevents path traversal, command injection, and accidental access to sensitive devices. Apply validation at the ingestion point, not just at the point of use, even when data comes from trusted discovery APIs. When determining device types from device paths (e.g., NVMe vs SATA), use regex patterns that match the actual naming conventions rather than substring checks like `"nvme" in device_path.lower()`. Use strict patterns like `^/dev/nvme[0-9]+(n[0-9]+)?(p[0-9]+)?$` for NVMe device detection to ensure accuracy.
- **Extracted Path Validation**: When extracting or deriving device paths from validated input (e.g., extracting a controller from a namespace), the extracted path must also be validated before use. Validation of the original input does not guarantee the extracted/derived value is safe. Any path used in command construction must pass `validate_device_path()` regardless of its origin. For example, when extracting `/dev/nvme0` from `/dev/nvme0n1` via regex, the extracted controller path must be validated before being passed to subprocess commands. This defense-in-depth approach prevents security regressions if the extraction logic changes or if new code paths bypass the original validation. Apply this principle to all derived values: extracted substrings, transformed paths, or computed identifiers used in security-sensitive contexts.

### 14. Cryptographic Parameter Standards
- **Rule**: Use current cryptographic standards for key derivation and hashing, and ensure consistency across all code paths.
- **Guardrail**: For PBKDF2, use at least 100,000 iterations (NIST recommendation), not 10,000. Define PBKDF2 iteration counts, salts, algorithms, and other cryptographic parameters as shared constants in a single location. Never hardcode different values in read vs. write paths. Parameter mismatches silently break security features and are extremely difficult to debug in production.

### 15. JSON Parsing with Delimiters
- **Rule**: When parsing JSON embedded in binary data, ensure correct delimiter matching and ignore delimiters inside string literals.
- **Guardrail**: Use proper bracket matching algorithms (e.g., counting nested braces) to find the correct opening delimiter for a given closing delimiter. Naive `rfind()` can match the wrong opening brace when multiple JSON objects exist. If implementing a custom JSON scanner, track string state (toggle on unescaped `"`) and only count `{`, `}`, `[`, `]` when outside of strings. Better yet, use a proper JSON streaming parser or extract the object using a library that handles JSON semantics correctly. Naive byte-level brace counting will fail on real-world data containing braces in string values.

### 16. Strict Full-String Anchors in Validation Regexes
- **Rule**: For input-validation/whitelist regexes, never anchor with `$` when you mean strict end-of-string.
- **Guardrail**: In Python, `$` also matches just before a trailing `\n`, so a value like `/dev/sda\n` passes a `^...$` whitelist. Use `\Z` for a strict end anchor (or `re.fullmatch`), and explicitly reject `\n`/`\r` for path-like inputs. This applies to device paths, identifiers, and any security-sensitive whitelist. Note that different regex engines support different anchors (JavaScript does not support `\z`), so verify anchors are supported by the target language.

### 17. Preserve API Contracts When Centralizing / Refactoring
- **Rule**: When replacing a function body with a delegated/centralized implementation, the new behavior must honor the original signature and documented contract.
- **Guardrail**: If a wrapper advertises parameters (e.g., `fallbacks`, `env_var`) they must still take effect, or the parameters should be removed. Preserve the original error contract: if callers expect `None` for "not found", do not regress to raising an uncaught exception (e.g., `KeyError` from an unguarded dict lookup). Guard centralized lookups against unknown keys.

### 18. Caching Effectiveness Across Import Styles
- **Rule**: A lazy/TTL cache is defeated when consumers bind its result once via `from module import VALUE`.
- **Guardrail**: If a value is meant to be re-resolved over time (TTL, hot-reload), expose it through a function call (`get_x()`) and require call sites to invoke it at use time. Snapshotting via module-level `from ... import` (including values produced by module `__getattr__`) freezes the value at import and silently bypasses the cache's refresh semantics, producing inconsistent behavior across modules.

### 19. Authentication Consistency
- **Rule**: All admin endpoints must follow the same authentication pattern, use consistent cookie/session names, and be under the `/api/admin/` path.
- **Guardrail**: When adding new admin endpoints (e.g., file upload, configuration changes), always verify they include the same authentication checks as existing admin routes. In Flask applications, this typically means checking admin session cookies or using a decorator. Missing authentication on admin endpoints is a critical security vulnerability that allows unauthorized access to administrative functions. Place all administrative functions under `/api/admin/` rather than directly under `/api/` to ensure they automatically inherit global authentication middleware and follow the established security architecture.
- **Refactoring Consistency**: Authentication mechanisms (cookie names, session keys, token headers) are part of the security contract between client and server. When extracting routes into new files, verify that authentication checks use the same cookie/key names as the original implementation. Inconsistencies (e.g., checking "session_token" in one route but "admin_session" in another) cause authentication bypass or legitimate access denial. Audit all authentication paths after refactoring to ensure consistency.
- **Parameter Consistency Across Mechanisms**: When multiple authentication mechanisms exist in the same application, they must use identical default values and parameter sources. If the application has both a global authentication middleware (e.g., `@app.before_request`) and route-specific authentication decorators, they must derive their authentication parameters from the same source with the same defaults. Inconsistent default values (e.g., one mechanism uses "eraser123" as default passphrase, another uses "") create security vulnerabilities where authentication succeeds via one path but fails via another. Define authentication parameters as shared constants in a single location, or ensure all mechanisms call the same configuration retrieval function with identical default values.
- **Conditional Authentication for Admin Endpoints**: Admin endpoints must implement conditional authentication that distinguishes between local and remote access. Check the request source IP and apply authentication conditionally: local requests (127.0.0.1, ::1, or local network subnets) should be allowed without authentication for on-premises convenience; remote requests must require authentication via session cookies, tokens, or other secure mechanisms. This applies to all admin routes (policy, configuration, sensitive data exports, etc.). Implement as a decorator or middleware to ensure no route is missed. Never skip authentication entirely on admin endpoints—this allows unauthorized remote access to system configuration and sensitive data.
- **Consistent Security Decorator Application Across Route Modules**: When adding security decorators (authentication, rate limiting) to routes, apply them consistently across ALL route modules, not just the main api_routes.py file. Flask applications often split routes into multiple blueprints (certificate_routes, template_routes, bay_mapping_routes, etc.). When implementing security controls, audit ALL route files, not just the main routes file. Missing decorators on blueprint routes creates security vulnerabilities where sensitive operations (bulk certificate generation, template management, bay mapping) are completely unprotected. Use grep to find all route definitions and verify each has the required decorators before marking the task complete.
- **Decorator Order for Security-Critical Middleware**: Authentication and authorization decorators must always be applied before rate limiting decorators. In Flask applications, decorators are applied bottom-to-top (the last decorator executes first). If `@limiter.limit()` is placed before `@require_admin_auth`, rate limits are enforced on unauthenticated requests, allowing attackers to exhaust the quota and deny service to legitimate users. Always place authentication decorators above rate limiting decorators in the source code so they execute first. This applies to any security-critical middleware (CORS, rate limiting, logging) that should only apply to authenticated requests.

### 20. Import Verification
- **Rule**: Never add code that uses modules without verifying the imports exist, and ensure imports are complete when extracting code.
- **Guardrail**: When adding new functionality that requires standard library or third-party modules, immediately add the corresponding import statement at the top of the file. Run a syntax check or linting tool before committing. When extracting code into new files during refactoring, verify all imports are copied to the new file. Before marking a refactoring task complete, run a syntax check or import verification on all new files. Missing imports cause immediate runtime failures (NameError) that are easily preventable.

### 21. Flask Route Definition Best Practices
- **Rule**: Do not define multiple route handlers for the same endpoint path.
- **Guardrail**: When an endpoint needs to handle multiple HTTP methods, use a single `@app.route()` decorator with all methods listed (e.g., `methods=["GET", "POST", "DELETE"]`) and dispatch within the handler function using `request.method`. Duplicate route definitions for the same path lead to code duplication, authentication logic duplication, and maintenance issues.

### 22. Complete Integrity Validation Chains
- **Rule**: Integrity checks must be validated on every read, not just written on save.
- **Guardrail**: When implementing file integrity validation (e.g., SHA256 hashes, signatures), the validation must occur in the read path as well as the write path. Writing a hash file without reading and validating it on load provides no protection against tampering. If validation fails on read, log a security-relevant warning and either reject the load or fall back to a known-good default with an error message. Never implement partial security measures that provide no actual protection—either implement the complete security measure or remove the partial implementation entirely.

### 23. Dependency Version Management for Reproducibility
- **Rule**: Do not change from pinned versions (`==`) to minimum versions (`>=`) without establishing a dependency update process.
- **Guardrail**: If allowing dependency updates is desired, implement safeguards first:
  - Create a documented dependency update policy (when to update, how to test)
  - Add automated testing against latest dependency versions in CI/CD
  - Consider using dependency management tools (pip-tools, poetry) to separate development constraints from production locks
  - Document specific library features used and their version requirements
  - For production systems, prefer pinned versions unless there is a clear, tested process for handling updates

### 24. Document Build Dependency Fixes
- **Rule**: When adding system-level build dependencies (e.g., `python3-dev`) to fix installation failures, document the specific error and OS version in comments or changelog.
- **Guardrail**: Installation script changes should include inline comments explaining:
  - The specific error that prompted the change
  - The OS version/distribution where the error occurred
  - Why the dependency resolves the issue (e.g., "Pillow 11.x requires build headers on Ubuntu 26.04")

### 25. Event Listener Management
- **Rule**: Never attach duplicate event listeners to the same DOM element, and avoid registering specific listeners for elements already handled by global handlers.
- **Guardrail**: When refactoring code to use initialization functions, ensure all event listener attachments are consolidated in a single location. Duplicate listeners cause handlers to execute multiple times, leading to unpredictable behavior (e.g., double submissions, concurrent animations). When adding interactive elements (e.g., modal close buttons), check if a global handler already exists for that pattern (e.g., `data-close-modal` attribute). If a global handler handles the element, do not add a specific event listener. Document the dependency on the global handler in a comment if the specific listener is intentionally omitted.
- **Modal Workflow Cleanup**: Implement proper cleanup for event listeners attached to modal elements to prevent memory leaks and duplicate handlers. When modal workflows attach event listeners (navigation buttons, form submissions), ensure there is a corresponding cleanup mechanism when the modal closes. Use tracked event listener systems (e.g., the `addTrackedEventListener` pattern in utils.js) or ensure listeners are attached once at module load and never re-attached. Duplicate listeners cause handlers to execute multiple times, leading to unexpected behavior and memory leaks.
- **DOM Ready State**: Ensure event listeners are attached regardless of DOM ready state when using DOMContentLoaded. If a script uses `DOMContentLoaded` to attach event listeners, the listener will not fire if the script loads after the DOM is already ready (deferred loading, dynamic injection). Check `document.readyState` before attaching the listener—if DOM is already "complete" or "interactive", attach handlers immediately. Alternatively, use the `defer` attribute on script tags to ensure they run after DOM parsing but before DOMContentLoaded fires.

### 26. Root Cause Investigation Over Surface Fixes
- **Rule**: Always investigate the root cause of an issue before implementing fixes or adding debug logging.
- **Guardrail**: When a user reports a problem (e.g., "logo appears wrong size"), trace the data flow from source to display to find where the transformation occurs. Adding logging or surface-level fixes without understanding the underlying issue leads to incomplete solutions and technical debt. Follow the bug fixing discipline: identify root cause before implementing, prefer minimal upstream fixes over downstream workarounds.

### 27. Numbering Scheme Changes
- **Rule**: Numbering scheme changes (e.g., 1-indexed to 0-indexed IDs/display numbers) must be applied to every producer and consumer in the data flow.
- **Guardrail**: When changing identifiers such as `bay1` to `bay0`, audit backend generation, seed config, frontend default generation, manual creation flows, save payloads, sorting/display logic, tests, and documentation before marking the task complete. Partial migrations create off-by-one regressions and inconsistent persisted state.
- **Single-Layer Changes**: When changing indexing schemes within a single layer (frontend or backend), update all producers, consumers, and validation logic consistently. Even if the change doesn't cross layers, incomplete updates within the same layer create validation gaps and display inconsistencies.
- **Domain Change Updates**: If a numbering scheme's upper bound changes from value A to value B (e.g., from `bay_count` to `rows * cols`), every validation that checks against that bound must be updated. Validation gaps create situations where the UI displays values that validation rejects, or vice versa. Audit all validation logic when changing numbering domains, including input validation, range checks, and error messages.

### 28. Default Value Guardrails for Optional Schema Fields
- **Rule**: When adding new optional fields to database schemas, ensure there's a default value guardrail in the persistence layer.
- **Guardrail**: If a field is added to support new functionality (e.g., `job_type` for distinguishing job types), the persistence function must handle missing keys gracefully. Use `dict.get("field", default_value)` or add a DEFAULT constraint in the schema. Never rely on all code paths to set the new field—missing fields will insert NULL, breaking downstream logic that expects populated values.

### 29. Validation Completeness Across Multiple Input Paths
- **Rule**: When data can be provided via multiple mechanisms (e.g., ID lookup vs inline object, reference vs direct value), validation must cover all paths equally.
- **Guardrail**: If a validation function only checks data when provided through one path (e.g., `template_id` lookup), but the data can also be provided through another path (e.g., inline template object), the validation gap creates a security and consistency vulnerability. Either validate all paths, document the limitation clearly, or reject unsupported paths at the entry point.

### 30. Post-Transformation Contract Validation
- **Rule**: When applying transformations that reduce available data (e.g., skip_positions, filters, exclusions), validate that the result still meets the original contract.
- **Guardrail**: If a function is expected to return N items but a transformation (like skipping positions) can reduce the count, add post-transformation validation to ensure the result meets minimum requirements. For example, if bay_count=8 is requested but skip_positions eliminates 9 positions, the function should raise an error rather than silently returning fewer items.

### 31. UI State Priority and Unknown Value Handling
- **Rule**: UI state rendering must prioritize operational states over configuration/metadata states, and unknown values should not be displayed as known defaults.
- **Guardrail**: When rendering UI elements that display drive or system states:
  - Never display unknown/placeholder values as if they were known (e.g., defaulting "unknown" drive type to "HDD" badge). Only render badges/labels when the value is explicitly known.
  - Establish a clear state priority hierarchy where critical operational states (RUNNING, FAILED, LOCKED) take precedence over configuration states (UNCONFIGURED). Configuration warnings should be additive (e.g., corner badges, border styles) rather than state overrides that hide active operations.
  - When adding conditional state logic, place higher-priority checks last or use explicit priority ordering to prevent lower-priority states from masking critical information.

### 32. REST API Consistency Across HTTP Methods
- **Rule**: Maintain consistent API patterns across all HTTP methods for the same resource.
- **Guardrail**: When designing CRUD endpoints, use consistent parameter passing mechanisms. If POST/PUT use JSON request bodies for resource identifiers and data, DELETE should also use JSON bodies (or follow a documented, consistent pattern). Avoid mixing query parameters and request bodies for the same resource type, as this creates confusing APIs and can lead to information leakage through server logs. Document any intentional deviations clearly.

### 33. Caching Best Practices
- **Rule**: When adding caching to a function, ensure all call sites use the caching consistently and never cache failure states.
- **Guardrail**: If a function is modified to support caching via a `use_cache` parameter, all existing call sites must be updated to pass `use_cache=True` (or the appropriate default). Leaving call sites that bypass the cache defeats the purpose of the optimization and creates hot paths that still perform expensive operations. Audit all call sites when adding caching to ensure consistent usage.
- **Cache Coherence Across Multiple Cached Calls**: When calling multiple cached functions that may return related data, ensure cache consistency to avoid stale/inconsistent responses. If a response combines data from multiple cached sources (e.g., controller list and device list), either use a single source of truth, pass cached data between functions, or disable caching for one of the calls to ensure temporal consistency. Never assume separate caches with independent TTLs will remain synchronized during a single request.
- **Cache Failure State Handling**: Never cache failure states (None, error values) in TTL-based caches. When implementing caching with TTL, only update the cache on successful data acquisition. If an operation fails (subprocess timeout, file I/O error, exception), do not cache the failure state. Caching failures causes the cache to return invalid data for the entire TTL period, even if the underlying issue is transient. The cache contract should be: return valid cached data, or perform a fresh scan on miss. Never cache None or error results. Move cache update logic inside the success path, not in finally blocks or exception handlers.

### 34. Consistent Error Handling Patterns
- **Rule**: Use consistent error handling patterns for similar operations across the codebase.
- **Guardrail**: When the same operation (e.g., SMART data retrieval, file parsing) appears in multiple places, use identical error handling patterns. If one path sets a null/error field and another uses silent pass, API consumers cannot distinguish between "no data" and "error occurred". Standardize on either explicit error fields or consistent null patterns to aid debugging and API contract clarity. This includes JSON parsing errors—wrap `json.loads()` calls in try/except blocks consistently across all database load functions.
- **Robust JSON Parsing in Error Paths**: Wrap JSON parsing in try-catch blocks even in error handling paths to prevent cascading failures. When handling API errors, code often attempts to parse the response body to extract error messages: `const data = await response.json(); throw new Error(data.error || "Failed")`. If the response body is not valid JSON or lacks the expected field, this throws a parsing error instead of the intended error message. Wrap JSON parsing in try-catch and provide fallback error messages to ensure users see meaningful feedback even when response parsing fails.

### 35. Validation of Extracted Numeric Values
- **Rule**: Always validate numeric values extracted from strings or unstructured data.
- **Guardrail**: When extracting numbers via regex, parsing, or string manipulation (e.g., slot numbers from slot IDs), validate that the result is within reasonable bounds for the domain. Handle conversion exceptions gracefully and reject values outside expected ranges (e.g., slot numbers should be 0-9999, not arbitrary integers). Unvalidated extracted numbers can cause display issues or logic errors downstream.

### 36. DOM Element Null Check Completeness
- **Rule**: When adding new DOM element references, always include null checks for all new elements.
- **Guardrail**: If a codebase has an existing pattern of checking DOM element existence (e.g., lines 14-16 in auditLedger.js), any new element references must follow the same pattern. Missing null checks for new elements while checking old ones creates inconsistent error handling and runtime crashes when elements are missing from the DOM. Either check all elements or check none consistently.

### 37. Complete UI Control Implementation
- **Rule**: When adding new UI controls, ensure all corresponding event handlers, state management, and option distribution are implemented.
- **Guardrail**: If a new UI element is added (e.g., a button, input, or toggle), verify that all necessary event listeners are attached and state variables are declared. Missing event handlers or undeclared state variables cause runtime errors. Use a checklist: (1) element reference, (2) event listener, (3) state variable (if needed), (4) validation logic, (5) error handling.
- **Option Distribution**: When adding new options to dropdowns or selects, the option must be added to all UI locations where that choice is relevant. If a new traversal preset, template type, or configuration option is added to one part of the UI (e.g., template creation form), it must also be added to all other UI controls that reference the same concept (e.g., bay mapping traversal select, template application dialog). Partial implementation creates confusing UX where users can create resources with certain options but cannot apply or use them elsewhere. Audit the entire codebase for all references to the option type before marking the feature complete.

### 38. State Management Lifecycle on Context Changes
- **Rule**: Clear transient UI state when the user context changes (filters, navigation, data refresh).
- **Guardrail**: Selection state, temporary flags, or user-modifiable data that depends on the current view should be cleared when the view changes (e.g., filtering a list, switching tabs, refreshing data). Persisting state across context changes creates confusing UX where selections refer to items no longer visible or relevant. Either clear state on context change or explicitly document that persistence is intentional.

### 39. Client-Side Validation Consistency
- **Rule**: Enforce client-side limits and HTML form constraints that match backend API constraints and JavaScript validation logic to provide immediate, consistent user feedback.
- **Guardrail**: If the backend enforces a maximum (e.g., 100 items for DoS prevention), the frontend should enforce the same limit before making the API call. This prevents users from selecting many items only to have their request rejected, and provides immediate, actionable feedback. The frontend limit should be slightly more conservative than the backend limit to account for any edge cases.
- **HTML Form Constraints**: HTML form validation attributes (min, max, pattern) must match JavaScript validation logic to provide consistent user feedback. When adding client-side validation, ensure HTML form attributes and JavaScript validation use the same rules. If JavaScript validates a number must be between 1 and 100, the HTML input should have min="1" and max="100". Mismatched validation causes confusing UX where the browser allows values that JavaScript rejects, or vice versa.

### 40. API Contract Verification Before Implementation
- **Rule**: Verify the expected data types and formats for API endpoints before implementing client code.
- **Guardrail**: When implementing client code that calls an API, verify whether the endpoint expects database IDs, friendly IDs, slugs, or other identifier types. Sending the wrong ID type will cause API failures. Read the backend route handler or API documentation to confirm the expected format before writing the client-side implementation.

### 41. Path Traversal Prevention in File Serving
- **Rule**: Never serve files directly from user-controlled or database-stored paths without validation.
- **Guardrail**: When serving files based on paths from untrusted sources (JSON data, user input, database records), validate that the resolved absolute path is within the expected directory. Use `os.path.abspath()` on both the file path and the allowed base directory, then verify with `os.path.commonprefix()` that the file path is a subdirectory of the base directory. This prevents path traversal attacks (e.g., `../../../etc/passwd`) that could expose sensitive server files.

### 42. Default Resource Immutability Consistency
- **Rule**: When a system designates certain resources as immutable (e.g., default templates, system configurations), all modification operations must enforce this protection consistently.
- **Guardrail**: If one operation (e.g., DELETE) prevents modification of default resources, all other operations that could modify those resources (e.g., POST, PUT, import) must also include the same protection. Inconsistent enforcement creates security vulnerabilities where attackers can bypass immutability through a less-protected code path. Either reject operations that would modify defaults, skip default resources with a warning, or require explicit confirmation/override flags.

### 43. Async/Await Syntax Requirement in JavaScript
- **Rule**: Never use `await` without declaring the function as `async`.
- **Guardrail**: When adding `await` calls to a function, ensure the function is declared with the `async` keyword. JavaScript will throw a SyntaxError at runtime if `await` is used in a non-async function. This is a fundamental syntax requirement that prevents the code from executing at all.

### 44. Verify Edit Operation Output for Syntax Validity
- **Rule**: Always verify that edit operations produce syntactically valid code, especially when modifying existing functions.
- **Guardrail**: After using the edit tool to modify code, read the modified section to ensure the output is valid syntax. Corrupted edits (e.g., mangled string literals, broken function signatures, incomplete lines) will cause the entire file to fail loading. This is particularly important when editing within existing functions where line boundaries can be ambiguous. If an edit produces invalid syntax, revert and use a more specific old_string with more surrounding context to ensure uniqueness.

### 45. Declare All State Variables at Module Level
- **Rule**: All module-level state variables must be explicitly declared before use, not implicitly created through assignment.
- **Guardrail**: When adding new functionality that requires persistent state (e.g., `mappingMode`, `manualMappings`, `selectedDevice`), declare these variables at the module level with explicit initialization (e.g., `let mappingMode = 'pattern'`). Never rely on implicit declaration through assignment in functions, as this creates implicit globals and makes the code fragile to refactoring. Follow the existing pattern in the codebase (e.g., lines 109-119 in adminPanel.js) for consistency.

### 46. Validate Conditional Block Placement
- **Rule**: Ensure initialization code is placed at the correct scope level, not nested inside unrelated conditionals.
- **Guardrail**: When adding initialization code (e.g., resetting state variables), verify it runs in all required code paths. If initialization is placed inside a conditional block (e.g., `if (mappingPreview)`), it will not execute when that condition is false. Initialization that should always run must be outside conditionals. Review the control flow to ensure initialization occurs at the appropriate point in the lifecycle.

### 47. Verify Global State Dependencies Before Use
- **Rule**: Before using global state variables in new functions, verify they are properly initialized and populated.
- **Guardrail**: When writing functions that depend on global state (e.g., `localBayMapCopy`), ensure the state is either: (1) passed as a parameter, (2) initialized in a well-defined lifecycle method before the function is called, or (3) documented as a required precondition. Never assume global state exists without verification. If the state has complex initialization requirements, add a comment documenting when and how it is populated.

### 48. Verify Variable References and Scope When Editing Functions
- **Rule**: When adding code blocks to existing functions, verify all referenced variables exist and are in scope.
- **Guardrail**: Before inserting validation, error handling, or logic blocks into an existing function, check that every variable referenced in the new code is either: (1) already declared in the function scope, (2) passed as a parameter, or (3) a global variable that exists in the module. Typos in variable names (e.g., `localBayMapState` vs `localBayMapCopy`) and scope errors (referencing variables outside their lexical scope) cause runtime failures that are easily preventable by reviewing the function's existing variable declarations before editing.

### 49. Code Extraction Best Practices
- **Rule**: When extracting code into new modules (blueprints, sub-packages, etc.), the original module MUST have the extracted code deleted in the same commit, and all side-effects must be preserved exactly.
- **Remove Original Code**: If you extract Flask routes into Blueprint files but leave the original `@app.route()` decorators in the monolithic module, the original routes register first and shadow the blueprints — making the new files dead code that never executes. Before marking an extraction refactor complete: (1) delete the moved handlers from the source file, (2) remove the now-unused imports from the source file, (3) verify the application starts without errors, (4) confirm at least one extracted endpoint responds via its new handler. Never ship "both old and new" route registrations simultaneously.
- **Clean Stale Imports**: After extracting code into new modules, audit both source and destination files for stale imports. When deleting functions from a module during extraction, some imports become orphaned (e.g., a utility only used by the moved code). These stale imports waste startup time, obscure the module's actual dependencies, and fail linting. After completing any extraction refactor, run a linting tool or manually grep each import symbol to confirm it is still used in the remaining code. This applies to both the source file and the destination files (e.g., an import copied to a new file but never called there).
- **No Initialization Side-Effects**: When splitting a monolithic file into smaller modules, each new file must reproduce only the behavior of its extracted section — never add new auto-initialization or lifecycle hooks that weren't present in the original. A common mistake is adding `DOMContentLoaded` handlers or immediate function calls in a newly extracted module "for convenience," which changes when code runs relative to authentication flows, dependency loading, or application state. Before committing a split: diff the combined new files against the original and flag any top-level statements that are net-new. Every auto-executing line must trace back to equivalent code in the original module.

### 50. Avoid Unnecessary Defensive Programming Patterns
- **Rule**: Prefer simple, declarative solutions over complex defensive patterns when simpler alternatives exist.
- **Guardrail**: When defensive programming patterns (e.g., DOMContentLoaded checks, null checks) are added to handle edge cases, first verify if a simpler solution exists (e.g., using the `defer` attribute on script tags, existing null checks at module load time). Complex defensive patterns that duplicate code across multiple locations create maintenance burden and technical debt. Use the simplest solution that actually solves the problem, and document why the defensive pattern is necessary if no simpler alternative exists.

### 51. Explicit Dependencies Between Non-Module Script Files
- **Rule**: When splitting browser-side scripts loaded via `<script>` tags (non-ES-module), cross-file variable references must be documented and centralized.
- **Guardrail**: `const`/`let` declarations at global scope in non-module scripts are shared across files via the global lexical environment, but the dependency is invisible — there is no `import` statement to make it explicit. If File B uses a `const` declared in File A, (a) add a comment in File B noting the dependency, or (b) move the shared declaration into a utilities file that both depend on. This prevents silent breakage when `<script>` tag order is changed. Any shared DOM reference used by multiple split files should live in a single "shared references" file loaded first.

### 52. Single Source of Truth for Authoritative Configuration
- **Rule**: Never duplicate authoritative configuration or data lists between frontend and backend.
- **Guardrail**: When the backend defines the authoritative source for data (e.g., default templates, system constants, allowed values), the frontend should derive this information from the API response rather than maintaining its own hardcoded list. Duplication creates maintenance burden and drift risk—if the backend list changes but the frontend list is not updated, inconsistencies occur. The API should expose the authoritative data (e.g., include an `is_default` boolean field), and the frontend should use this field for UI logic.

### 53. Collision Probability in Generated Identifiers
- **Rule**: When generating human-readable identifiers with random components, ensure sufficient entropy and validate transformed results.
- **Guardrail**: For identifiers that include random suffixes (e.g., UUID-based friendly IDs), calculate the collision probability based on expected volume. With 4 hex characters (65,536 combinations), collisions become likely if more than ~8,000 items are generated per day (birthday paradox). Use at least 6 hex characters (16,777,216 combinations) for daily-rotated identifiers, or implement collision detection with retry logic. Document the collision probability assumptions in code comments.
- **Post-Transformation Validation**: If user input undergoes transformation (regex replacement, sanitization, normalization) to produce an identifier, the transformation can produce an empty string or invalid value (e.g., input "!!!" becomes "" after removing non-alphanumeric chars). Always validate the transformed result has a minimum length and valid format before using it in API calls, database operations, or file operations. Provide user feedback if the input cannot produce a valid identifier.

### 54. Complete Field Name Refactoring Across All Data Paths
- **Rule**: When renaming fields or identifiers, update all read and write paths consistently in a single operation.
- **Guardrail**: Partial field name refactoring creates schema mismatches where data is written with the new name but read with the old name (or vice versa). Before marking a refactoring complete, grep the entire codebase for all occurrences of the old field name, including: form submissions, API payloads, database queries, template rendering, default value assignments, and HTML form field names. If backward compatibility is needed during migration, add explicit fallback logic (e.g., `data.new_field || data.old_field`) and document the transition period. Never ship partial schema migrations.
- **Consistent Field Naming Across Data Flow**: When data flows from backend to frontend, verify the actual field names returned by the API and use them consistently. If the backend uses `physical_slot_number` but the frontend code checks `physical_slot`, drives will not be correctly matched or displayed. Audit the API response structure and standardize field names across all consumers. Add defensive checks for both field names only during migration periods, not as permanent solutions.

### 55. Validation Consistency During Transformations
- **Rule**: When validating input that undergoes transformation (e.g., parsing, conversion, mapping), validate the original input before transformation, not the transformed output.
- **Guardrail**: If validation checks the transformed data instead of the original input, type mismatches can render validation ineffective. For example, validating an array of objects when the input was an array of numbers will fail to catch invalid numeric ranges. Always validate the raw input at the point of entry, then apply transformations. If validation logic must reference transformed data, use distinct variable names to avoid shadowing the original input and causing confusion about which data is being validated.

### 56. Guard Against Division by Zero in Mathematical Conversion Functions
- **Rule**: All functions performing division or modulo operations must validate that divisors are non-zero before the operation.
- **Guardrail**: Mathematical conversion functions (e.g., coordinate transformations, index calculations) that accept divisor parameters must include guard clauses to reject zero or negative values. Even if the current caller validates inputs, the function is a reusable utility that may be called from other contexts in the future. Throw a clear error message (e.g., "Columns must be greater than 0") rather than allowing the operation to proceed with Infinity, NaN, or incorrect results.

### 57. Remove Debugging Artifacts Before Committing
- **Rule**: Never commit debugging console.log statements or temporary debug code to production.
- **Guardrail**: Remove all debugging artifacts (console.log, debugger statements, temporary test code) before committing. Debugging statements left in production code can expose sensitive information, create performance issues, and confuse future maintainers. Use a pre-commit hook or linting rule to catch these if necessary.

### 58. User Feedback for Data Reduction During Transformations
- **Rule**: When transformations reduce available data (e.g., filtering, skipping invalid items), always provide explicit user feedback about what was excluded.
- **Guardrail**: If a transformation skips items (e.g., missing bays, data mismatches, validation failures), track and report the count and reason for skipped items to the user. Silent data reduction creates confusing UX where users don't understand why expected items are missing from results. Always provide visible feedback (e.g., "Warning: 3 device(s) skipped due to missing bays") rather than silently proceeding with reduced data.

### 59. Prevent Redundant API Calls on UI Navigation
- **Rule**: Add caching or flags to prevent redundant API calls when users navigate between UI sections.
- **Guardrail**: If a UI section triggers API calls on every navigation (e.g., tab clicks), users switching back and forth cause unnecessary API load. Add a flag to track if initialization has already occurred, check if data is already loaded before re-fetching, or implement a TTL-based cache. This reduces server load and improves responsiveness.

### 60. Validate parseInt Results Before Arithmetic
- **Rule**: Always validate that `parseInt` or `Number` conversions produce valid numeric values before using them in arithmetic.
- **Guardrail**: If user input or API data is converted via `parseInt()`, the result can be `NaN` if the input is null, undefined, or non-numeric. `NaN + anyValue` remains `NaN`, causing silent failures in downstream calculations. Add validation to check `isNaN()` or use a default value before using the result in arithmetic operations.

### 61. Avoid Global Variable Name Collisions Across Modules
- **Rule**: Use descriptive, module-specific names for global state variables to prevent confusion about ownership and synchronization.
- **Guardrail**: When multiple modules maintain local copies of the same data (e.g., `localEnclosures` in both driveManagement.js and enclosureManagement.js), the naming pattern creates confusion about which module "owns" the data and whether copies are synchronized. Use descriptive names (e.g., `workbenchEnclosures` vs `adminEnclosures`) or implement a shared state management pattern. If modules maintain independent copies, ensure both refresh from the API when needed and document the synchronization strategy.

### 62. State Update Functions Must Preserve Non-Serializable Types
- **Rule**: When updating state objects that contain non-serializable types (Sets, Maps, custom objects), use deep merging or explicit field preservation rather than shallow spread operators.
- **Guardrail**: The spread operator (`{ ...state, ...newState }`) creates a shallow copy and does not preserve Set or Map references. If the newState does not include these fields, they become undefined. Either: (1) explicitly preserve non-serializable fields in the spread, (2) use deep merging that handles these types correctly, or (3) only update specific fields rather than replacing the entire state object. This is critical for state management in browser applications where Sets are used for tracking selections.

### 63. UI Button State Synchronization Across Mode Switches
- **Rule**: When implementing mode switches (e.g., pattern vs manual mode), all UI state including button enable/disable states must be synchronized with the new mode.
- **Guardrail**: Mode switches should reset or disable any UI controls that are not applicable to the new mode. For example, if an "Undo" button is only meaningful in pattern mode, it must be disabled when switching to manual mode. Leaving stateful UI controls enabled across mode switches creates inconsistent state where user actions can produce confusing or invalid results. All button states, selection states, and validation states should be reviewed when adding mode switching logic.

### 64. Rate Limiting Best Practices
- **Rule**: Rate limiting must be implemented consistently with correct decorator order and shared storage appropriate for the deployment environment.
- **Guardrail**: Authentication and authorization decorators must always be applied before rate limiting decorators. In Flask applications, decorators are applied bottom-to-top (the last decorator executes first). If `@limiter.limit()` is placed before `@require_admin_auth`, rate limits are enforced on unauthenticated requests, allowing attackers to exhaust the quota and deny service to legitimate users. Always place authentication decorators above rate limiting decorators in the source code so they execute first. This applies to any security-critical middleware (CORS, rate limiting, logging) that should only apply to authenticated requests.
- **Shared Storage for Multi-Worker Deployments**: Rate limiting state must be stored in a shared backend when using multi-worker deployments. In-memory rate limit storage (`storage_uri="memory://"`) is only safe for single-worker deployments. With multiple workers (gunicorn, uWSGI), each worker maintains its own rate limit state, allowing attackers to bypass limits by distributing requests across workers. For production deployments, use shared storage backends like Redis or Memcached. If in-memory storage must be used, document this as a known limitation and enforce single-worker mode in the deployment configuration.

### 65. Subprocess Management
- **Rule**: When using external subprocesses, handle version capture and lifecycle management robustly.
- **Guardrail**: Functions that call external commands (e.g., `hdparm -V`, `nvme --version`) must catch specific exceptions (TimeoutExpired, FileNotFoundError, PermissionError) separately from generic Exception, log the specific failure reason, validate that output matches expected patterns, distinguish between "command not found", "command failed", "timeout", and "permission denied", consider caching versions to avoid repeated subprocess calls, and add validation that meaningful versions were captured before including them in audit trails or certificates.
- **Job Termination Lifecycle**: When implementing job termination features, store subprocess references and explicitly terminate them rather than just updating status flags. Updating a job's status to "failed" or "killed" in memory/database does not stop the actual subprocess executing the work. The job execution loop must check for termination signals, and the termination endpoint must: (1) store the subprocess `Popen` object in the job dict (e.g., `job["_process"] = process`), (2) check `process.poll() is None` to verify it's still running, (3) call `process.terminate()` followed by `process.wait(timeout=N)` with fallback to `process.kill()`, (4) ensure the job execution loop checks the same termination condition as the admin endpoint.

### 66. Avoid Code Duplication - DRY Principle
- **Rule**: Never copy-paste identical logic blocks across multiple functions.
- **Guardrail**: When the same logic appears in multiple functions (e.g., enclosure-based mapping in three pattern functions), extract it into a shared helper function. Code duplication creates maintenance burden where bug fixes must be applied in multiple locations. Before adding new code, check if similar logic already exists and can be reused or refactored into a common utility. This is especially important for complex logic with multiple edge cases or validation steps.

### 67. CSS Class Management
- **Rule**: When implementing UI element visibility or migrating CSS classes, follow the established pattern and verify all usages across the codebase.
- **Guardrail**: Different UI elements use different visibility patterns (e.g., modals use `.open` class with `.modal { display: none; }` and `.modal.open { display: block; }`, while footers/overlays use `.hidden` class). Before adding visibility control to an element, verify the CSS class definitions in the stylesheet. Mixing visibility patterns (e.g., using `.hidden` on a modal when the established pattern is `.open`) causes the element to not display correctly. Always grep the CSS file to confirm the correct class names and their semantics before using them.
- **Cross-File Usage Verification**: CSS class consolidation (e.g., replacing `.status-ok` with `.status-badge--complete`) must include a comprehensive search for all usages of the old classes across all JavaScript, HTML, and template files. Removing a CSS class without updating all consumers causes functional regressions where UI elements lose their styling. Use grep to find all occurrences of the old class names before removing them from the CSS file. Either update all consumers to use the new classes, or keep the old classes as backward-compatible aliases.

### 68. UI Preview Consistency with Configuration
- **Rule**: UI previews must accurately reflect the actual configuration and behavior that will be used at runtime, or clearly indicate when using a simplified representation.
- **Guardrail**: When implementing preview components (e.g., template previews, configuration visualizations), the preview should generally respect all relevant configuration parameters (traversal order, numbering schemes, etc.) and match the backend logic exactly. However, simplified representations are acceptable if:
  - The distinction is clearly documented or indicated to users (e.g., "Reference numbering for configuration" vs "Actual erasure order")
  - The operational behavior (animation, backend) respects the actual configuration
  - The simplified representation serves a clear UX purpose (e.g., consistent reference numbering for input fields)
  - The animation or dynamic preview shows the actual runtime behavior
  Hardcoding preview behavior to a fixed order without clear indication creates misleading UX. When using simplified representations, add visual indicators or documentation to clarify the difference between reference displays and operational behavior.

### 69. Install Script Best Practices
- **Rule**: Install scripts must create all required backend files and correctly export environment variables needed by subprocesses.
- **Guardrail**: When install scripts create configuration files that the backend reads, they must create all required files (including auxiliary files like hashes, signatures, indexes) in the exact format expected by the backend. If the backend expects a configuration file plus an integrity hash file (e.g., `config.json` and `config.json.sha256`), the install script must create both. Creating only the primary file causes the backend to reject it or fall back to defaults, making the install script's work ineffective. Before adding file creation to install scripts, read the backend's load function to identify all required files and their formats.
- **Environment Variable Visibility in Subprocess Heredocs**: Bash variables are not automatically visible to subprocess heredocs unless explicitly exported. When using bash heredocs to execute Python or other subprocesses that need access to bash variables (e.g., `CONFIG_DIR`, `INSTALL_DIR`), export the variables before the heredoc using `export VAR_NAME`. The subprocess will not inherit shell variables that are only set but not exported. Hardcoded defaults in subprocess code are fragile and prone to drift from the actual bash configuration. Follow the pattern: `export VAR_NAME; subprocess << EOF; ... $VAR_NAME ...; EOF; unset VAR_NAME`.

### 70. Function Contract Preservation During Modifications
- **Rule**: When modifying a function to support new use cases, preserve the original contract for all existing callers or update all call sites consistently.
- **Guardrail**: If a function modification changes the semantics (e.g., deriving grid dimensions from partial inputs instead of accepting full dimensions as parameters), this breaks the contract for existing callers. Either: (1) make the change backward-compatible by adding optional parameters with sensible defaults, (2) create a new function with a different name for the new use case, or (3) update all call sites to pass the required data. Never infer critical data from partial inputs when the full data is available from the caller—this creates fragile functions that fail with edge cases. When adding optional parameters to existing functions, ensure all call sites that rely on the old behavior are updated or the default preserves the old semantics.

### 71. DOM State Detection Should Use Data Attributes, Not Visual Content
- **Rule**: When detecting DOM element state (e.g., skipped cells, selected items, disabled elements), use data attributes rather than inspecting visual content or text.
- **Guardrail**: Checking `textContent`, `innerHTML`, or visual markers (e.g., `cell.textContent === "×"`) couples state detection to the visual representation, making code fragile to UI changes. Instead, set `data-*` attributes during rendering (e.g., `data-skipped="true"`) and check these attributes in event handlers and utility functions. This decouples state logic from presentation and allows UI changes without breaking behavior.

### 72. Enforce Total Limits When Iterating Over Dynamic System Resources
- **Rule**: When iterating over dynamic system resources (e.g., SCSI hosts, PCI devices, filesystem entries), always enforce a total limit across all iterations, not just per-item limits.
- **Guardrail**: Per-item limits (e.g., 24 slots per host) are insufficient when the number of items is unbounded (e.g., 100+ hosts). Always add a total limit check that breaks out of all loops when the limit is reached. This prevents memory exhaustion and long-running operations in environments with many resources. The pattern is: `if len(collection) >= MAX_TOTAL: break` in both inner and outer loops.

### 73. Validate Composite Format Strings with Proper Regex Patterns
- **Rule**: When validating strings that contain multiple concatenated components (e.g., "pci-{addr}-scsi-{host}:0:{slot}:0"), use strict regex patterns that validate the entire structure, not just prefix checks.
- **Guardrail**: Checking only that a string starts with a prefix (e.g., `startswith('pci-')`) is insufficient for security and correctness. Use full regex patterns that validate each component in the correct format and position. For composite formats, the regex should match the entire string structure with proper anchors (e.g., `^pci-[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-9a-fA-F]-scsi-\d+:0:\d+:0\Z`). This prevents malformed or malicious strings from passing validation.

### 74. Standardize Return Structures Across Function Families
- **Rule**: Functions that perform similar operations (e.g., mapping functions, validation functions) must return consistent data structures with the same field names and types.
- **Guardrail**: When multiple functions in the same family return different structures (e.g., one returns `{mapping, skippedCount, mismatchCount}` while another returns `{mapping, skippedCount, emptySlotCount}`), API consumers cannot handle the results uniformly. This creates fragile code that breaks when the fallback path is taken. Define a standard return structure for the function family and ensure all functions conform to it, including all relevant fields with appropriate defaults (e.g., `mismatchCount: 0` if not applicable).

### 75. Retry Logic Consistency Within Multi-Pass Operations
- **Rule**: When a function performs multiple passes over the same data with the same operation (e.g., reading chunks from a device), all passes must use identical retry and error handling logic.
- **Guardrail**: If the first pass of an operation uses retry logic with progressive delays to handle transient errors (e.g., drives needing time to become readable after crypto sanitize), any subsequent passes that perform the same operation must use the same retry pattern. Inconsistent retry logic creates race conditions where transient errors cause false failures in later passes but would have been retried in earlier passes. Extract retry logic into a helper function or ensure all passes share the same retry-with-delays pattern.

### 76. Documentation Must Match Implementation
- **Rule**: When updating API contract documentation, verify that the documented security controls actually exist in the implementation.
- **Guardrail**: Do not document endpoints as having authentication or rate limiting if those decorators are not actually applied to the routes. Misleading documentation creates a false sense of security and may lead to security audits passing when the implementation is actually vulnerable. Before documenting security controls for an endpoint, verify the route has the required decorators in the source code. If implementation is incomplete, either defer documentation or document the actual state (e.g., "TODO: add authentication").

### 77. Reject Trailing Tokens in Validated SQL Column Definitions
- **Rule**: When tokenizing and validating a SQL column definition string before interpolating it into a raw SQL statement, reject any unexpected trailing tokens after the validated components.
- **Guardrail**: Validating only the expected tokens (column name, type, DEFAULT keyword, default value) while ignoring extra tokens at the end allows injection via trailing SQL fragments (e.g., `col TEXT DEFAULT 0 ; DROP TABLE ...`). After validating the known parts, explicitly check that no additional tokens exist beyond what is expected. For a definition with DEFAULT, expect exactly 4 tokens; without DEFAULT, expect exactly 2. Raise an error if extra tokens are present.

### 78. Prevent Circular Imports When Centralizing Module Initialization
- **Rule**: When centralizing module initialization (e.g., registering Flask blueprints in app_config.py), avoid creating circular import dependencies.
- **Guardrail**: If a central module (e.g., app_config.py) imports from other modules at module level, those other modules must not import from the central module at module level. Circular imports cause ImportError or AttributeError at startup because Python cannot satisfy the import chain. Solutions:
  - Move the centralized imports into a function that is called after all modules are loaded (e.g., `register_blueprints(app)` called at the end of app_config.py or in app.py)
  - Use lazy imports within functions rather than at module level in the central module
  - Restructure so that dependent modules do not import from the central module at module level (move those imports into route functions)
  - Extract shared utilities into a separate module that both can import from without circularity
Before centralizing initialization, trace the import graph to ensure no cycles exist.

### 79. Raw Strings for Docstrings Containing Regex Escape Sequences
- **Rule**: When writing docstrings or comments that reference regex escape sequences like `\Z`, use raw string literals to avoid SyntaxWarnings.
- **Guardrail**: Python's string parser treats backslashes as escape characters. Writing `\Z` in a regular string literal triggers a SyntaxWarning because `\Z` is not a recognized escape sequence. Use raw string prefixes (`r"""` or `r'`) for docstrings and comments that reference regex anchors. This tells Python to treat backslashes literally, which is what regex engines expect. Never escape the backslash in documentation (e.g., `\\Z`) as this is technically inaccurate—the regex engine reads a single backslash. The regex patterns themselves should always use raw strings (`r'pattern\Z'`), and any documentation describing them should also use raw strings.

### 80. Database Connection Cleanup in Test Fixtures
- **Rule**: Test fixtures that create or use SQLite databases must explicitly clean up connections to prevent ResourceWarnings.
- **Guardrail**: When writing pytest fixtures that initialize databases or use `init_wipe_db()`, the fixture's finally block must:
  1. Call `gc.collect()` to trigger garbage collection of pending connections
  2. Open a connection to the test database and run `PRAGMA wal_checkpoint(TRUNCATE)` to close WAL files
  3. Optionally run `PRAGMA optimize` to help SQLite clean up
  4. Wrap this in try-except to handle cases where the database file doesn't exist
This prevents unclosed connection warnings that originate from SQLite's connection pooling and WAL mode. The backend code should use context managers (`with closing(sqlite3.connect(...))`), but test fixtures need explicit cleanup because connections may be held by Flask, Werkzeug, or mock objects during test execution.

### 81. Server-Side Validation Must Mirror New Client-Side Constraints
- **Rule**: When adding validation, length limits, or format constraints in the frontend, the same constraint must be enforced by the authoritative backend API or schema.
- **Guardrail**: Client-side validation improves UX but can be bypassed via direct API calls, browser DevTools, or scripted clients. Treat the backend as the authoritative source of truth for all data constraints. When tightening a frontend rule (e.g., minimum length, required format, allowed characters), update the corresponding backend schema (e.g., JSON Schema `minLength`, `pattern`, `enum`) and/or add an explicit validation check in the route handler. Keep client and server rules in sync; if they differ, either relax the frontend to match the backend or tighten the backend to match the frontend, and document the rationale. Add regression tests for both valid boundary values and invalid bypass attempts.

### 82. Complete Event Listener Attachment Implementation
- **Rule**: When adding event listener attachment checks, ensure the actual listener is attached, not just the tracking flag.
- **Guardrail**: A common pattern is to check if a listener is already attached using a dataset flag (e.g., `dataset.enclosureListener`) to prevent duplicate listeners. However, setting the flag without actually attaching the listener is a no-op that leaves the element non-functional. When implementing this pattern, the check must both set the flag AND attach the listener in the same conditional block. If the module-level code already handles listener attachment, the conditional check should be removed entirely rather than implemented as a no-op. Incomplete listener attachment causes silent failures where UI elements appear but do not respond to user interaction.

### 83. Dictionary/Collection Size Validation from User Input
- **Rule**: When accepting dictionaries or collections from user input, always enforce size limits before processing.
- **Guardrail**: User-provided dictionaries (e.g., custom_labels, custom_roles) can contain arbitrary numbers of entries. Without size limits, an attacker can send thousands of entries causing memory exhaustion or long-running operations. Enforce a reasonable maximum (e.g., 1000 entries) and return a 400 error if exceeded. This applies to any endpoint accepting JSON objects with unknown key counts, not just explicitly named "collections".

### 84. Safe Numeric Conversion from User Input
- **Rule**: When converting user-provided strings to numbers, always validate the conversion succeeds and the result is within expected bounds.
- **Guardrail**: Direct use of `int()` or `parseInt()` on user input without try-except can crash the request handler on invalid input. Wrap conversions in try-except blocks and validate the result is within a reasonable range for the domain (e.g., 0-9999 for slot numbers). Return a clear error message on conversion failure. This applies to both backend (Python int()) and frontend (JavaScript parseInt()) numeric conversions.

### 85. NaN Validation After JavaScript parseInt
- **Rule**: Always check for NaN after calling parseInt() on user input before using the result as an array index or object key.
- **Guardrail**: JavaScript parseInt() returns NaN when the input cannot be parsed as a number. Using NaN as an array index or object key creates invalid entries that break data structures. Always check `if (isNaN(result))` and handle the error case gracefully (e.g., skip the entry, log an error, return early). This is particularly important when parsing dataset attributes or form inputs.

### 86. String Content Validation from User Input
- **Rule**: When accepting string values from user input for display or storage, validate content constraints beyond basic type checking.
- **Guardrail**: Strings from user input should be validated for length (max characters), character set (no control characters, valid encoding), and format if applicable. Extremely long strings can break UI rendering or cause memory issues. Control characters can corrupt data or cause display issues. Add validation at the ingestion point (backend API) and return a 400 error for invalid values. Do not rely solely on frontend escaping for security.

### 87. Allowlist Validation for String Enum Values
- **Rule**: When accepting string values that must match a predefined set of options, validate against an allowlist before use.
- **Guardrail**: User-provided enum-like values (e.g., role="wipe|os|reserved", status="active|inactive") must be validated against an explicit allowlist. Never assume the value is valid based on frontend select options alone, as API calls can bypass UI constraints. Define the valid values as a constant set and check membership before using the value in logic or persisting to storage. Return a 400 error with the list of valid options for invalid values.

### 88. Fallback Rendering for Position-Based Layouts
- **Rule**: When implementing position-based rendering (grids, layouts, maps), always provide a fallback section for items with missing or invalid position data.
- **Guardrail**: If a rendering function filters items based on position validity (e.g., checking `Number.isInteger(pos.row)`), items that fail validation must not be silently dropped. Track these items and render them in a fallback section (e.g., "Unassigned Drives", "Items with Invalid Positions") with a visible header indicating why they are separate. Add console warnings for debugging. This prevents data loss in the UI when legacy data, malformed configurations, or missing metadata cause position validation to fail. The fallback ensures users can still interact with all items even if position mapping is incomplete.

### 89. Validate Before Mutate Pattern
- **Rule**: Always validate input data before applying mutations to in-memory objects or persistent storage.
- **Guardrail**: When processing API requests that update configuration or state, perform validation checks before modifying any data structures. Mutating an object (e.g., `current_policy[field] = payload[field]`) before validation (e.g., checking if strict_audit_mode requires a passphrase) violates the validate-then-mutate principle. Even if the mutation is not persisted on validation failure, the in-memory object is corrupted, which can cause confusion if the object is used elsewhere in the same request context or if error handling logic assumes the object is unchanged. Move all validation logic to the beginning of the request handler, before any field assignments or data structure modifications.

### 90. Complete Field Population When Extending Data Structures
- **Rule**: When adding new fields to data structures returned by functions, ensure those fields are actually populated from the data source.
- **Guardrail**: Adding fields to a return dictionary or object without implementing the logic to populate them creates dead code that misleads consumers. If a function returns a dictionary with fields like `sas_grown_defect_list`, `sas_scan_status`, etc., but always sets them to `None`, any code that tries to use these fields will not work as intended. Either: (1) implement the parsing logic to populate the fields from the source data (e.g., smartctl JSON output), (2) remove the placeholder fields entirely, or (3) add explicit comments marking them as "TODO: not yet implemented" with a clear plan for completion. Never ship half-implemented data structures that appear functional but always return null/None values.

### 91. Specific Device Name Pattern Validation
- **Rule**: Device name validation patterns must match the actual naming conventions of the target system, not generic character classes.
- **Guardrail**: When validating device names (e.g., SATA, NVMe), use patterns that match the actual system naming conventions, not overly permissive generic patterns. For Linux SATA devices, the pattern should be `^sd[a-z][0-9]*\Z` (e.g., sda, sdb1, sdc2), not `^[a-z]+[0-9]*\Z` which would accept invalid names like "abc123" or "xyz". Overly permissive patterns create security vulnerabilities by accepting inputs that are not valid device names but could bypass other validation checks. Always verify the actual naming convention of the target system before writing validation regexes.

### 92. Atomic Check-Then-Act for Resource Allocation
- **Rule**: When implementing resource allocation or state transitions that must be mutually exclusive, use atomic operations rather than check-then-act patterns.
- **Guardrail**: Check-then-act patterns (e.g., query database for active tests, then start a new test if none found) create TOCTOU race conditions where multiple concurrent requests can pass the check simultaneously. Use atomic operations instead: database-level unique constraints, application-level locks (e.g., Redis locks, threading.Lock), or conditional inserts (e.g., `INSERT ... WHERE NOT EXISTS`). For database operations, use transactions with row locks or unique constraints to ensure mutual exclusion. For in-memory state, use locks around the entire check-and-allocate sequence. Never rely on separate read and write operations for mutually exclusive resource allocation.

### 93. CSP-Compliant Event Handler Registration
- **Rule**: Never use inline event handlers (onclick, onsubmit, etc.) in HTML when Content Security Policy restricts script execution.
- **Guardrail**: When CSP includes `script-src 'self'` or similar restrictive directives, inline event handlers are blocked and cause runtime errors. Always use data attributes (e.g., `data-collapsible-toggle`, `data-smart-export`) combined with event delegation via `addEventListener()`. For dynamically generated HTML strings, store parameters in data attributes and handle events through a centralized event listener that reads the data attributes. This pattern works with CSP and is more maintainable than scattered inline handlers. Apply this consistently across all frontend JavaScript files.

### 94. Avoid Magic Numbers and Duplicate Constants
- **Rule**: Never hardcode values that are defined as constants elsewhere in the codebase.
- **Guardrail**: When a value is already defined as a constant (e.g., cache TTL, timeout values, limits), always import and use that constant rather than hardcoding the same value. Hardcoded duplicates create maintenance burden and inconsistency—if the constant is changed in the future, the hardcoded value will not be updated, leading to divergent behavior. Before adding a magic number, search the codebase to see if a constant already exists for that value. If not, define a new constant in an appropriate location (e.g., module-level or shared config file) and use it consistently across all call sites.

### 95. Defensive Array Type Checking Before Array Methods
- **Rule**: Always verify a value is an array before calling array methods like `.map()`, `.filter()`, or `.forEach()`.
- **Guardrail**: When data from APIs or external sources may be null, undefined, or a non-array type, calling array methods will throw runtime errors. Use `Array.isArray(value)` or check `value && Array.isArray(value) && value.length > 0` before using array methods. This is particularly important in frontend JavaScript where API responses may vary based on error conditions or backend changes. Never assume that because a field "should" be an array, it always will be. Add defensive checks at the point of use, not just at the data loading stage, to prevent cascading failures.

### 96. Background Thread and Frontend Polling Coordination
- **Rule**: When implementing background threads that update the same data as frontend polling, coordinate updates to prevent race conditions.
- **Guardrail**: If a background thread and frontend polling both update the same database records (e.g., SMART test status), implement a coordination mechanism to prevent concurrent update conflicts. Use database-level locking, optimistic locking with timestamp checking (UPDATE ... WHERE id = ? AND updated_at < ?), or designate a single updater (either background thread OR frontend polling, not both). Concurrent updates without coordination can cause lost updates, database locked errors, and inconsistent state. This is especially critical for SQLite which handles concurrent writes with locking that can lead to contention issues.

### 97. Filter Database Queries by Current State for Background Tasks
- **Rule**: Background threads should query only relevant records based on current system state, not all historical records.
- **Guardrail**: When background threads query databases for records to process (e.g., in-progress SMART tests), filter by current system state (e.g., only devices currently connected) rather than querying all records. Querying all records and then checking each one individually wastes CPU cycles and I/O on disconnected or irrelevant items. Either pass a list of currently active items to the query function, add a device existence check before processing, or create a specialized query function that returns only relevant records. This prevents scalability issues as the database grows.

### 98. JavaScript Truthiness and Zero Values
- **Rule**: When checking for presence of numeric values in JavaScript, use explicit null/undefined checks instead of truthiness checks.
- **Guardrail**: JavaScript treats 0, "", false, and NaN as falsy values. Using `if (value)` to check if a value exists will fail when the value is legitimately 0 or an empty string. Always use `if (value != null)` or `if (value !== null && value !== undefined)` when checking for presence of values that could be 0 or empty strings. This is particularly important for numeric fields like display numbers, counts, or indices where 0 is a valid value. The pattern `if (field && otherField)` will fail when field is 0, even if both fields are present and valid.

### 99. Global Variable Declaration Required When Assigning Module-Level Globals in Python Functions
- **Rule**: Any Python function that assigns to a module-level global variable MUST declare it with `global var_name` before the assignment.
- **Guardrail**: Python's compile-time scoping treats a name as local to a function if that name appears on the left-hand side of any assignment anywhere in the function body (including inside nested `if`/`with`/`try` blocks). This means even a single conditional assignment (e.g., `_thread = threading.Thread(...)` inside an `else:` branch) causes every read of that name earlier in the same function to raise `UnboundLocalError` at runtime — even when the branch is never taken on the first call. The fix is a `global _thread_name` declaration at the start of the function (or inside the controlling `with` block). This commonly bites background-thread management code where the thread reference is conditionally updated inside a lock block. Always add `global` declarations for module-level thread, lock, and event variables before the first assignment in any function.

### 100. Use the Correct DOM State Predicate When Checking Modal Visibility
- **Rule**: Always check the CSS class or attribute that the modal system actually uses for open/closed state — never use a class that is never applied.
- **Guardrail**: If modals are opened/closed by toggling an `.open` class (e.g., `openModal` adds `.open`, `closeModal` removes `.open`), then checking `!element.classList.contains('hidden')` is always `true` because `.hidden` is never added to modal elements. This makes any guard that depends on the check vacuously pass and causes downstream code to run unconditionally. Verify the actual toggle mechanism (class-based, attribute-based, style-based) by reading the `openModal`/`closeModal` helper functions before writing visibility checks. Use the positive form (`classList.contains('open')`) rather than the negated form (`!classList.contains('hidden')`) to avoid silent always-true conditions.

### 101. Never Share a Cache Variable Between Functions Returning Different Data Types
- **Rule**: Each cached function must have its own dedicated cache variable; never reuse the same module-level cache for multiple functions that return different data types.
- **Guardrail**: When multiple functions share a cache dict like `{'data': None, 'timestamp': 0}`, one function may store a `List[Dict]` while another stores an `int`. The next caller will receive the wrong type, causing `TypeError`, `AttributeError`, or silent logic errors. Always define a separate cache variable (and ideally a separate lock) per cached function. Name the cache explicitly after the function or data it holds (e.g., `_ENCLOSURE_HARDWARE_INFO_CACHE`).

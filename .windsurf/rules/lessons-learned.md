---
trigger: always_on
---

# Project Lessons Learned & Guardrails

This file contains generalized architectural guardrails derived from past agent mistakes. The Coding Agent must strictly adhere to these rules.

### 1. SQL Security & Column Modification
- **Rule**: Never accept a raw, unvalidated string for SQL column definitions or parameters.
- **Guardrail**: If dynamic schema modifications are required, validate parameters against an allowlist, or split parameters so that type definitions and names are strictly validated via regex and escaped. Never assume a regex that matches `[a-zA-Z]` is safe if it allows raw unquoted input trailing it.

### 2. Concurrency & Race Conditions
- **Rule**: Do not assume "single-process usage" is safe. 
- **Guardrail**: If the codebase uses threads, locks, or runs in a multi-worker environment (like Flask/Gunicorn), always write thread-safe operations. Use locks, transactions, or native database atomic operations (e.g., `IF NOT EXISTS`). For schema modifications like `ALTER TABLE`, wrap in try-except to handle duplicate column errors from concurrent operations.

### 3. HTML Parsing
- **Rule**: Do not use regular expressions to parse HTML.
- **Guardrail**: Always use a robust parser like BeautifulSoup (`bs4`) or `lxml` to extract elements, body text, or attributes to prevent fragility with nested tags, multiline elements, or script tags.

### 4. Object & Array Comparisons
- **Rule**: Do not rely on naive `json.dumps()` or `str()` comparisons for arbitrary arrays or objects.
- **Guardrail**: Account for non-serializable types (datetimes, sets), circular references, and key sorting. Use try/except fallbacks or deep-comparison helper functions to avoid runtime crashes.

### 5. Input Validation for DoS Prevention
- **Rule**: Always validate and limit the size of user-provided lists and collections.
- **Guardrail**: For any endpoint accepting arrays (e.g., job_ids, filters), enforce a reasonable maximum size (e.g., 100 items) to prevent memory exhaustion and long-running queries that could cause denial of service.

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
- **Rule**: Never accept raw device paths from user input without validation.
- **Guardrail**: Validate device paths against a strict regex whitelist (e.g., `^/dev/[a-z]+[0-9]*$`) before using in command construction. This prevents path traversal, command injection, and accidental access to sensitive devices.

### 10. JSON Parsing Size Limits
- **Rule**: Always enforce size limits before parsing JSON from untrusted sources.
- **Guardrail**: Check the byte length of JSON data before calling `json.loads()`. Enforce a reasonable maximum (e.g., 64KB) to prevent memory exhaustion and DoS attacks from maliciously large payloads.

### 11. Cryptographic Parameter Standards
- **Rule**: Use current cryptographic standards for key derivation and hashing.
- **Guardrail**: For PBKDF2, use at least 100,000 iterations (NIST recommendation), not 10,000. Keep cryptographic parameters aligned with current security best practices to prevent brute-force attacks.

### 12. JSON Parsing with Delimiters
- **Rule**: When parsing JSON embedded in binary data, ensure correct delimiter matching.
- **Guardrail**: Use proper bracket matching algorithms (e.g., counting nested braces) to find the correct opening delimiter for a given closing delimiter. Naive `rfind()` can match the wrong opening brace when multiple JSON objects exist.

### 13. Cryptographic Parameter Consistency
- **Rule**: When using key derivation or cryptographic hashing across multiple code paths (e.g., write and verify, sign and validate), all paths must use identical parameters.
- **Guardrail**: Define PBKDF2 iteration counts, salts, algorithms, and other cryptographic parameters as shared constants in a single location. Never hardcode different values in read vs. write paths. Parameter mismatches silently break security features and are extremely difficult to debug in production.

### 14. String-Aware JSON Delimiter Parsing
- **Rule**: When parsing JSON by scanning for structural delimiters, delimiters inside string literals must be ignored.
- **Guardrail**: If implementing a custom JSON scanner, track string state (toggle on unescaped `"`) and only count `{`, `}`, `[`, `]` when outside of strings. Better yet, use a proper JSON streaming parser or extract the object using a library that handles JSON semantics correctly. Naive byte-level brace counting will fail on real-world data containing braces in string values.

### 15. Strict Full-String Anchors in Validation Regexes
- **Rule**: For input-validation/whitelist regexes, never anchor with `$` when you mean strict end-of-string.
- **Guardrail**: In Python, `$` also matches just before a trailing `\n`, so a value like `/dev/sda\n` passes a `^...$` whitelist. Use `\Z` for a strict end anchor (or `re.fullmatch`), and explicitly reject `\n`/`\r` for path-like inputs. This applies to device paths, identifiers, and any security-sensitive whitelist.

### 16. Preserve API Contracts When Centralizing / Refactoring
- **Rule**: When replacing a function body with a delegated/centralized implementation, the new behavior must honor the original signature and documented contract.
- **Guardrail**: If a wrapper advertises parameters (e.g., `fallbacks`, `env_var`) they must still take effect, or the parameters should be removed. Preserve the original error contract: if callers expect `None` for "not found", do not regress to raising an uncaught exception (e.g., `KeyError` from an unguarded dict lookup). Guard centralized lookups against unknown keys.

### 17. Caching Effectiveness Across Import Styles
- **Rule**: A lazy/TTL cache is defeated when consumers bind its result once via `from module import VALUE`.
- **Guardrail**: If a value is meant to be re-resolved over time (TTL, hot-reload), expose it through a function call (`get_x()`) and require call sites to invoke it at use time. Snapshotting via module-level `from ... import` (including values produced by module `__getattr__`) freezes the value at import and silently bypasses the cache's refresh semantics, producing inconsistent behavior across modules.

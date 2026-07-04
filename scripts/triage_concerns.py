#!/usr/bin/env python3
"""Parse code_concerns.md, group issues by pattern/root-cause, output structured JSON.

Usage:
    python scripts/triage_concerns.py [--input code_concerns.md] [--output scripts/triage_output.json]
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


# ── Pattern detection rules ──────────────────────────────────────────────
# Each rule: (pattern_id, display_name, fix_shape, keyword_regexes, exclude_regexes)
# Keywords are matched (case-insensitive) against issue + suggestion + title text.
# Exclude regexes prevent false positives — if any exclude matches, the rule is skipped.
# Rules are ordered by specificity: most specific patterns first, generic ones last.
# The first matching rule (that doesn't hit an exclude) wins.
PATTERN_RULES = [
    # ── Highly specific patterns (check first to prevent false groupings) ──
    ("signal_flag_reset", "Signal flags never reset after handler",
     "Reset interruption flags at start of each operation, or use threading.Event",
     [r"flag.*never reset", r"never reset.*signal", r"_interrupted.*never", r"_shutdown_requested.*never",
      r"permanently disables", r"flag is never reset"],
     []),

    ("cache_failure_state", "Cache failure state cached (None/empty cached for full TTL)",
     "Move cache update inside success path only; never cache failure states",
     [r"caches failure", r"caches.*empty", r"caches.*None", r"cache.*failure state",
      r"cache.*None.*failure", r"cache.*empty.*failure"],
     []),

    ("inconsistent_return", "Inconsistent return types across code paths",
     "Return consistent types (e.g., always list) or raise custom exception",
     [r"inconsistent type", r"returns.*dict.*list", r"return.*\{\"error.*\}.*instead",
      r"returns.*\[\].*dict", r"inconsistent return"],
     []),

    ("atomic_write", "Non-atomic file writes",
     "Use tempfile + flush + fsync + os.replace() pattern",
     [r"non-atomic", r"atomic write", r"tempfile.*rename"],
     []),

    ("lock_race", "Lock race: two-phase lock acquisition / lock ordering",
     "Acquire all locks in nested scope in consistent order, or re-check inside the final lock scope",
     [r"two.phase.*lock", r"lock.*race", r"lock.*acquisition.*race", r"re-acquire.*lock",
      r"between.*lock.*scope", r"releases.*lock.*then"],
     []),

    ("toctou", "TOCTOU: os.path.exists() pre-checks",
     "Replace os.path.exists() + open/listdir with direct try/except (OSError, FileNotFoundError)",
     [r"os\.path\.exists", r"TOCTOU", r"time-of-check"],
     [r"lock.*race", r"two.phase.*lock", r"lock.*acquisition"]),

    ("timing_attack", "Timing attack: == instead of hmac.compare_digest",
     "Replace == with hmac.compare_digest for hash/token comparisons",
     [r"timing attack", r"hmac\.compare_digest", r"string comparison vulnerable"],
     []),

    ("resource_leak", "Resource leaks in error paths",
     "Use context managers or close resources in except/finally blocks",
     [r"resource leak", r"file handle", r"fd leak", r"temp director", r"not closed"],
     []),

    ("css_broken", "CSS: broken/missing class definitions",
     "Add missing CSS rules or update JS to use correct class names",
     [r"undefined.*css", r"missing.*css", r"css.*not.*defined", r"broken.*css",
      r"class.*not.*defined", r"no css rules exist", r"css.*not.*exist",
      r"missing.*\.btn--", r"missing.*\.gap-", r"missing.*\.mt-", r"z-index.*below"],
     []),

    ("info_disclosure", "Information disclosure: str(e) in API responses",
     "Replace str(e) in jsonify error responses with generic messages; log details server-side",
     [r"str\(e\)", r"information disclosure", r"error messages returned to clients"],
     []),

    ("concurrency_lock", "Concurrency: missing/improper locks",
     "Add locks around shared mutable state or restructure lock scope",
     [r"missing lock", r"without lock", r"lock scope", r"non-reentrant", r"deadlock", r"thread.safe",
      r"double lock", r"lock acquisition", r"re-acquire"],
     [r"flag.*never reset", r"permanently disables"]),

    ("memory_leak", "Unbounded dict/list growth (memory leak)",
     "Use WeakValueDictionary or add cleanup on release",
     [r"unbounded", r"memory leak", r"grows indefinitely", r"never removed"],
     []),

    # ── Moderate specificity ──
    ("import_hygiene", "Import hygiene: lazy/redundant imports",
     "Move imports to module level, remove duplicates",
     [r"lazy import", r"redundant import", r"import hygiene", r"__import__"],
     []),

    ("error_swallowing", "Error swallowing: bare except / silent failures",
     "Add logging to except blocks, catch specific exceptions",
     [r"swallow", r"except.*pass", r"silent.*(?:swallow|fail|error|except|disabled)", r"no logging"],
     [r"schema", r"additionalProperties", r"cross-module", r"coupling", r"duplicat"]),

    ("input_validation", "Input validation gaps",
     "Validate input types, formats, and enforce size limits",
     [r"input validation", r"not validated", r"no.*validation", r"size limit"],
     [r"stray line", r"EOF marker"]),

    # ── Generic patterns (check last — prone to false positives) ──
    ("perf_caching", "Performance: repeated config file reads without caching",
     "Add TTL cache or file-mtime-based cache for config file reads",
     [r"every.*(?:request|call|invocation|event).*(?:disk|policy|json|bay_map|config)",
      r"no caching", r"re-read.*(?:policy|config|json|bay_map|file)",
      r"disk i/o.*(?:every|each|frequent)",
      r"loaded on every", r"reloaded on every",
      r"reads.*from disk.*parses.*json"],
     [r"flag.*never reset", r"permanently disables", r"interrupted.*flag",
      r"_job_interrupted", r"_discovery_interrupted",
      r"double lock", r"lock acquisition", r"re-acquire",
      r"chunk", r"hash"]),

    ("dead_code", "Dead code: unused functions/classes/CSS",
     "Remove dead code or wire it into a caller if the feature was intended",
     [r"dead code", r"zero callers", r"never called", r"zero usage", r"dead css",
      r"dead variable", r"dead branch", r"dead selector", r"never referenced"],
     [r"handler.*inconsistency", r"wrong handler", r"save button handler",
      r"never reset", r"flag.*never"]),

    ("dry_violation", "DRY: duplicated logic across functions/files",
     "Extract shared helper function and update all call sites to use it",
     [r"duplicat", r"DRY", r"same logic", r"nearly identical"],
     []),

    ("file_org", "File organization: domain mixing / god modules",
     "Extract domains into separate route files; do shared-utils extraction first",
     [r"god module", r"domain mixing", r"file organization", r"should move",
      r"split.*(?:module|file|route|blueprint|class|\.py|\.js|into.*separate)",
      r"exceeds.*800.*line", r"file size.*exceeds"],
     []),
]

# Difficulty weights
# investigation capped at 8 (same as high) — the has_investigation flag forces action=plan,
# so the weight doesn't need to be 999. The old 999 distorted session_size reporting.
WEIGHTS = {"trivial": 1, "low": 2, "medium": 4, "high": 8, "investigation": 8}
MAX_WEIGHT = 20

# Broadened ID pattern to handle compound IDs like A-SD1, A-IH1, Y-SD2, F-IH1
ID_PATTERN = r"[ACFY](?:-[A-Z]+)?\d+|ORG\d+"

# Patterns that indicate a YAGNI/review entry is a "no issues found" confirmation,
# not an actionable issue. These are excluded from pending count and grouping.
NO_ACTION_PATTERNS = [
    r"no speculative abstractions found",
    r"no dead (?:html|code|css|function|selector)s? found",
    r"noted but not recommended for change",
    r"actively used.*not dead code",
    r"no orphaned or unused",
    r"all (?:functions|endpoints|containers).*actively (?:called|used)",
]

# Canonical heading pattern (same as normalize_concerns.py)
EM_DASH = "\u2014"
CANONICAL_HEADING = re.compile(
    r"^###\s+(?:\[COMPLETED\]\s*)?"
    r"(" + ID_PATTERN + r"):\s*"
    r"(?:\[(Critical|Advisory)\]\s+)?"
    r"(.+?)\s+"
    r"\u2014\s+Difficulty:\s*(\w+)\s+"
    r"\u2014\s+Category:\s*(.+?)\s*$",
    re.MULTILINE,
)

# Simple heading pattern (body-field format from deep-review workflow)
# Matches headings without inline difficulty/category — those fields are in the body
SIMPLE_HEADING = re.compile(
    r"^###\s+(?:\[COMPLETED\]\s*)?"
    r"(" + ID_PATTERN + r"):\s*"
    r"(?:\[(Critical|Advisory)\]\s+)?"
    r"(.+?)\s*$",
    re.MULTILINE,
)

# Category inference keywords (for body-field format without inline category)
_CATEGORY_RULES = [
    ("Security", [r"timing attack", r"hmac\.compare_digest", r"str\(e\)", r"information disclosure", r"input validation", r"command injection", r"path traversal", r"secret.*log", r"rate limit", r"xss", r"escape", r"auth", r"noopener", r"autocomplete"]),
    ("Concurrency", [r"race condition", r"toctou", r"os\.path\.exists", r"lock", r"thread.safe", r"deadlock", r"atomic", r"concurrent"]),
    ("Error Handling", [r"except.*pass", r"swallow", r"silent", r"uncaught", r"error.*return", r"bare.*except", r"error.*parsing"]),
    ("Architecture", [r"pattern consistency", r"return.*contract", r"import hygiene", r"module.level", r"dead import", r"unused import"]),
    ("Performance", [r"unnecessary alloc", r"hot path", r"every.*request", r"every.*call", r"unbounded", r"redundant", r"subprocess.*spawn", r"re-render", r"full grid", r"debounce"]),
    ("Correctness", [r"division by zero", r"overflow", r"off.by.one", r"edge case", r"empty list", r"none.*value", r"falsy", r"truthiness", r"zero.*valid", r"undefined.*handling"]),
    ("Resource Management", [r"subprocess.*cleanup", r"file handle", r"temp file", r"thread.*join", r"database.*connection", r"resource leak", r"pil.*leak", r"image.*leak", r"file.*leak"]),
    ("Dead Code", [r"dead code", r"zero callers", r"never called", r"zero usage", r"dead css", r"dead variable", r"unused.*param", r"never read", r"never referenced"]),
    ("DRY", [r"duplicat", r"DRY", r"same logic", r"nearly identical", r"repeated"]),
    ("Code Quality", [r"code smell", r"anti.pattern", r"redundant import", r"typo", r"stray", r"naming", r"mislabeled", r"zone.*comment"]),
    ("File Organization", [r"god module", r"domain mixing", r"file organization", r"should move", r"split", r"file size"]),
    ("CSS", [r"undefined.*css", r"missing.*css", r"css.*not.*defined", r"broken.*css", r"z-index", r"dead css", r"inline.*style", r"csp"]),
    ("Test Coverage", [r"no test", r"untested", r"test coverage", r"zero.*test"]),
    ("Accessibility", [r"aria", r"screen reader", r"focus trap", r"tabnabbing", r"accessib", r"noscript"]),
]


def _infer_category(title: str, issue_text: str, suggestion_text: str) -> str:
    """Infer category from issue text using keyword matching."""
    combined = (title + " " + issue_text + " " + suggestion_text).lower()
    for category, keywords in _CATEGORY_RULES:
        for kw in keywords:
            if re.search(kw, combined, re.IGNORECASE):
                return category
    return "Code Quality"


def parse_concerns(filepath: str) -> list[dict]:
    """Parse code_concerns.md into structured issue dicts.

    Supports two heading formats:
    1. Canonical: ### [ID]: [Severity] Title — Difficulty: X — Category: Y
    2. Body-field: ### [ID]: Title (with **Difficulty**: X in body)
    """
    text = Path(filepath).read_text(encoding="utf-8")
    issues = []

    # Find all ## File: headings (file sections) with their positions
    # Only match "## File:" headings — other ## headings (Critical Findings, etc.) are sub-sections
    file_headings = []
    for m in re.finditer(r"^## File:\s*[`']?([^`']+)[`']?", text, re.MULTILINE):
        file_headings.append((m.start(), m.group(1).strip()))

    # Find all ### headings (issue entries)
    all_headings = list(re.finditer(r"^###\s+.+$", text, re.MULTILINE))

    for j, heading_match in enumerate(all_headings):
        heading_line = heading_match.group(0)
        start = heading_match.start()
        end = all_headings[j + 1].start() if j + 1 < len(all_headings) else len(text)

        # Stop at --- separators
        sep = re.search(r"^---\s*$", text[start:end], re.MULTILINE)
        if sep:
            end = start + sep.start()

        body = text[heading_match.end():end].strip()

        # Try canonical format first
        canon_match = CANONICAL_HEADING.match(heading_line)
        if canon_match:
            issue_id = canon_match.group(1).strip()
            severity_raw = canon_match.group(2)
            title = canon_match.group(3).strip()
            difficulty_raw = canon_match.group(4).strip().lower()
            category = canon_match.group(5).strip()
        else:
            # Try simple (body-field) format
            simple_match = SIMPLE_HEADING.match(heading_line)
            if not simple_match:
                continue  # Not an issue heading (e.g., "### Critical Findings")

            issue_id = simple_match.group(1).strip()
            severity_raw = simple_match.group(2)
            title = simple_match.group(3).strip()

            # Extract difficulty from body
            diff_match = re.search(r"\*\*Difficulty\*\*:\s*(\w+)", body)
            difficulty_raw = diff_match.group(1).strip().lower() if diff_match else "medium"
            category = ""  # Will be inferred from body text

        # Determine severity
        if severity_raw:
            severity = severity_raw.lower()
        elif issue_id.startswith("C"):
            severity = "critical"
        else:
            severity = "advisory"

        # Check if completed
        completed = "[COMPLETED]" in heading_line

        # Find the nearest preceding ## heading for file section
        file_name = "unknown"
        for fh_pos, fh_name in file_headings:
            if fh_pos < start:
                file_name = fh_name
            else:
                break

        # Extract fields from body (handle both canonical and body-field formats)
        line_ref = ""
        issue_text = ""
        suggestion_text = ""
        depends_on = []
        related = []

        line_match = re.search(r"\*\*Lines?\*\*:\s*(.+)", body)
        if line_match:
            line_ref = line_match.group(1).strip()

        # Try **Issue** (canonical) then **Root Problem** (body-field)
        issue_match = re.search(r"\*\*Issue\*\*:\s*(.+?)(?=\*\*Impact\*\*|\*\*Suggestion\*\*|\*\*Fix\*\*|\Z)", body, re.DOTALL)
        if not issue_match:
            issue_match = re.search(r"\*\*Root Problem\*\*:\s*(.+?)(?=\*\*Fix\*\*|\*\*Suggestion\*\*|\*\*Depends-on\*\*|\*\*Related\*\*|\Z)", body, re.DOTALL)
        if issue_match:
            issue_text = issue_match.group(1).strip()

        # Try **Suggestion** (canonical) then **Fix** (body-field)
        suggestion_match = re.search(r"\*\*Suggestion\*\*:\s*(.+?)(?=\n- \*\*|\Z)", body, re.DOTALL)
        if not suggestion_match:
            suggestion_match = re.search(r"\*\*Fix\*\*:\s*(.+?)(?=\n- \*\*|\*\*Depends-on\*\*|\*\*Related\*\*|\Z)", body, re.DOTALL)
        if suggestion_match:
            suggestion_text = suggestion_match.group(1).strip()

        dep_match = re.search(r"\*\*Depends-on\*\*:\s*(.+)", body)
        if dep_match:
            dep_text = dep_match.group(1).strip()
            if dep_text.lower() != "none":
                depends_on = [d.strip() for d in dep_text.split(",") if d.strip()]

        rel_match = re.search(r"\*\*Related\*\*:\s*(.+)", body)
        if rel_match:
            rel_text = rel_match.group(1).strip()
            if rel_text.lower() != "none":
                related = [r.strip() for r in rel_text.split(",") if r.strip()]

        # Infer category if not in heading
        if not category:
            category = _infer_category(title, issue_text, suggestion_text)

        # Detect no-action entries (YAGNI "no issues found" confirmations)
        combined_text = (title + " " + issue_text + " " + body).lower()
        no_action = any(re.search(p, combined_text, re.IGNORECASE) for p in NO_ACTION_PATTERNS)

        issues.append({
            "id": issue_id,
            "title": title,
            "file": file_name,
            "line": line_ref,
            "severity": severity,
            "difficulty": difficulty_raw,
            "category": category,
            "completed": completed,
            "no_action": no_action,
            "issue_text": issue_text,
            "suggestion_text": suggestion_text,
            "depends_on": depends_on,
            "related": related,
            "raw_body": body,
        })

    return issues


def detect_patterns(issue: dict) -> list[str]:
    """Return list of matching pattern IDs for an issue.

    Checks exclude regexes first — if any exclude matches, the pattern is skipped.
    Rules are evaluated in priority order (most specific first).
    """
    combined = (issue.get("issue_text", "") + " " + issue.get("suggestion_text", "") + " " + issue.get("title", "")).lower()
    matched = []
    for rule in PATTERN_RULES:
        pattern_id, _name, _shape, keywords, excludes = rule

        # Check excludes first — if any exclude matches, skip this pattern
        if any(re.search(excl, combined, re.IGNORECASE) for excl in excludes):
            continue

        for kw in keywords:
            if re.search(kw, combined, re.IGNORECASE):
                matched.append(pattern_id)
                break
    return matched


def detect_dependencies(issues: list[dict]) -> list[dict]:
    """Detect dependencies using structured fields and text-based heuristics.

    Sources:
    1. Structured Depends-on field from canonical format
    2. Related field as soft dependencies
    3. Text-based detection: scan issue bodies for phrases like "after fixing X",
       "do this first", "same pattern as X" that indicate ordering

    Filters out chains where both sides are completed (noise reduction).
    Uses multi-valued lookup to handle duplicate IDs gracefully.
    """
    deps = []
    by_id = defaultdict(list)
    for i in issues:
        by_id[i["id"]].append(i)

    pending_ids = {i["id"] for i in issues if not i["completed"] and not i.get("no_action", False)}

    # Text-based dependency patterns: (regex, reason_template)
    # These scan issue_text + suggestion_text for references to other issue IDs
    # that imply ordering (do X first, after X, same pattern as X, etc.)
    TEXT_DEP_PATTERNS = [
        (r"(?:after|before)\s+(?:fixing|completing|addressing|tackling)\s+(?:issue\s+)?(" + ID_PATTERN + r")",
         "text-based ordering: {unblocks} should be done {direction} {prereq}"),
        (r"do\s+(?:this\s+)?first.*?(" + ID_PATTERN + r")",
         "text-based ordering: {prereq} should be done first (referenced by {unblocks})"),
        (r"same\s+pattern\s+as\s+(?:issue\s+)?(" + ID_PATTERN + r")",
         "text-based ordering: {prereq} shares a pattern with {unblocks}"),
        (r"(?:unblocks|prerequisite\s+for)\s+(?:issue\s+)?(" + ID_PATTERN + r")",
         "text-based ordering: {unblocks} unblocks {prereq}"),
    ]

    for issue in issues:
        # 1. Use structured depends_on field
        for dep_id in issue.get("depends_on", []):
            if dep_id in by_id:
                deps.append({
                    "must_do_first": dep_id,
                    "unblocks": issue["id"],
                    "reason": f"{issue['id']} explicitly depends on {dep_id}",
                })

        # 2. Use related field as soft dependencies
        for rel_id in issue.get("related", []):
            if rel_id in by_id:
                deps.append({
                    "must_do_first": rel_id,
                    "unblocks": issue["id"],
                    "reason": f"{issue['id']} is related to {rel_id} (soft dependency)",
                    "soft": True,
                })

        # 3. Text-based detection
        combined_text = (issue.get("issue_text", "") + " " + issue.get("suggestion_text", "") + " " + issue.get("raw_body", "")).lower()
        for pattern, reason_template in TEXT_DEP_PATTERNS:
            for m in re.finditer(pattern, combined_text, re.IGNORECASE):
                ref_id = m.group(1).strip()
                if ref_id in by_id and ref_id != issue["id"]:
                    direction = "after" if "after" in m.group(0).lower() else "before"
                    reason = reason_template.format(unblocks=issue["id"], prereq=ref_id, direction=direction)
                    # "after fixing X" means X must be done first
                    # "before fixing X" means current issue must be done first
                    if "after" in m.group(0).lower():
                        deps.append({"must_do_first": ref_id, "unblocks": issue["id"], "reason": reason, "soft": True})
                    elif "before" in m.group(0).lower():
                        deps.append({"must_do_first": issue["id"], "unblocks": ref_id, "reason": reason, "soft": True})
                    else:
                        deps.append({"must_do_first": ref_id, "unblocks": issue["id"], "reason": reason, "soft": True})

    # Filter: only include chains where at least one side has a pending issue
    deps = [d for d in deps if d["must_do_first"] in pending_ids or d["unblocks"] in pending_ids]

    # Deduplicate
    seen = set()
    unique_deps = []
    for d in deps:
        key = (d["must_do_first"], d["unblocks"])
        if key not in seen:
            seen.add(key)
            unique_deps.append(d)

    return unique_deps


def group_issues(issues: list[dict]) -> list[dict]:
    """Group issues by detected pattern, split by weight cap.

    Improvements over naive grouping:
    - Same-pattern groups split by weight cap get related_groups field linking them
    - Ungrouped issues try file-based grouping first, then category fallback
    - pattern_id is included in output for cross-referencing
    - no_action entries (YAGNI "no issues found") are excluded from grouping
    """
    # Exclude completed AND no_action entries from pending
    pending = [i for i in issues if not i["completed"] and not i.get("no_action", False)]

    # Assign patterns
    ungrouped = []
    pattern_buckets: dict[str, list[dict]] = defaultdict(list)

    for issue in pending:
        patterns = detect_patterns(issue)
        if patterns:
            # Assign to first matching pattern (priority order — most specific first)
            pattern_buckets[patterns[0]].append(issue)
        else:
            ungrouped.append(issue)

    # Build group objects, splitting by weight
    groups = []
    group_counter = 0
    # Track which groups share a pattern_id for related_groups linking
    pattern_group_map: dict[str, list[str]] = defaultdict(list)

    for pattern_id, pattern_issues in pattern_buckets.items():
        pattern_info = next(r for r in PATTERN_RULES if r[0] == pattern_id)
        _, display_name, fix_shape, _, _ = pattern_info

        # Sort by difficulty (easiest first)
        pattern_issues.sort(key=lambda i: WEIGHTS.get(i["difficulty"], 999))

        # Split into sub-batches if weight exceeds cap
        current_batch = []
        current_weight = 0
        batch_num = 0

        for issue in pattern_issues:
            w = WEIGHTS.get(issue["difficulty"], 999)
            if current_weight + w > MAX_WEIGHT and current_batch:
                group_counter += 1
                batch_num += 1
                gid = f"G{group_counter}"
                suffix = f" (batch {batch_num})" if batch_num > 1 else ""
                groups.append(_make_group(gid, display_name + suffix, fix_shape, current_batch, pattern_id=pattern_id))
                pattern_group_map[pattern_id].append(gid)
                current_batch = []
                current_weight = 0
            current_batch.append(issue)
            current_weight += w

        if current_batch:
            group_counter += 1
            batch_num += 1
            gid = f"G{group_counter}"
            suffix = f" (batch {batch_num})" if batch_num > 1 else ""
            groups.append(_make_group(gid, display_name + suffix, fix_shape, current_batch, pattern_id=pattern_id))
            pattern_group_map[pattern_id].append(gid)

    # Add related_groups field to groups that share a pattern_id
    for group in groups:
        pid = group.get("pattern_id", "")
        if pid and len(pattern_group_map[pid]) > 1:
            group["related_groups"] = [g for g in pattern_group_map[pid] if g != group["id"]]

    # Ungrouped issues: group by file first (same-file issues can often be fixed together),
    # then try category only for 3+ same-file same-category issues
    if ungrouped:
        file_buckets: dict[str, list[dict]] = defaultdict(list)
        for issue in ungrouped:
            file_buckets[issue["file"]].append(issue)

        for file_name, file_issues in file_buckets.items():
            file_issues.sort(key=lambda i: WEIGHTS.get(i["difficulty"], 999))

            # If 3+ issues in same file, try sub-grouping by category
            if len(file_issues) >= 3:
                cat_sub_buckets: dict[str, list[dict]] = defaultdict(list)
                for issue in file_issues:
                    cat = issue.get("category", "Unknown")
                    cat_sub_buckets[cat].append(issue)

                # Only use category sub-grouping if it produces at least one group of 2+
                has_cat_group = any(len(v) >= 2 for v in cat_sub_buckets.values())
                if has_cat_group:
                    for cat, cat_issues in cat_sub_buckets.items():
                        cat_issues.sort(key=lambda i: WEIGHTS.get(i["difficulty"], 999))
                        current_batch = []
                        current_weight = 0

                        for issue in cat_issues:
                            w = WEIGHTS.get(issue["difficulty"], 999)
                            if current_weight + w > MAX_WEIGHT and current_batch:
                                group_counter += 1
                                groups.append(_make_group(f"G{group_counter}", f"Misc: {cat} ({file_name})", "No common pattern detected — review individually", current_batch))
                                current_batch = []
                                current_weight = 0
                            current_batch.append(issue)
                            current_weight += w

                        if current_batch:
                            group_counter += 1
                            groups.append(_make_group(f"G{group_counter}", f"Misc: {cat} ({file_name})", "No common pattern detected — review individually", current_batch))
                    continue  # Skip default file grouping for this file

            # Default: group all same-file issues together
            current_batch = []
            current_weight = 0

            for issue in file_issues:
                w = WEIGHTS.get(issue["difficulty"], 999)
                if current_weight + w > MAX_WEIGHT and current_batch:
                    group_counter += 1
                    groups.append(_make_group(f"G{group_counter}", f"Ungrouped: {file_name}", "No common pattern detected — review individually", current_batch))
                    current_batch = []
                    current_weight = 0
                current_batch.append(issue)
                current_weight += w

            if current_batch:
                group_counter += 1
                groups.append(_make_group(f"G{group_counter}", f"Ungrouped: {file_name}", "No common pattern detected — review individually", current_batch))

    return groups


def _make_group(gid: str, name: str, fix_shape: str, issues: list[dict], pattern_id: str = "") -> dict:
    """Build a group dict from a list of issues."""
    weight = sum(WEIGHTS.get(i["difficulty"], 999) for i in issues)
    files = sorted(set(i["file"] for i in issues))
    breakdown = defaultdict(int)
    for i in issues:
        breakdown[i["difficulty"]] += 1

    if weight <= 8:
        session_size = "small"
    elif weight <= 16:
        session_size = "medium"
    elif weight <= 20:
        session_size = "large"
    else:
        session_size = "xlarge"

    has_investigation = any(i["difficulty"] == "investigation" for i in issues)
    has_high = any(i["difficulty"] == "high" for i in issues)

    if has_investigation:
        action = "plan"
    elif has_high:
        action = "plan"
    elif session_size in ("small", "medium"):
        action = "batch_fix"
    else:
        action = "batch_fix"

    result = {
        "id": gid,
        "pattern": name,
        "pattern_id": pattern_id,
        "fix_shape": fix_shape,
        "issues": [{"id": i["id"], "title": i["title"], "file": i["file"],
                     "line": i["line"], "severity": i["severity"],
                     "difficulty": i["difficulty"], "category": i.get("category", ""),
                     "suggestion": i["suggestion_text"][:200]} for i in issues],
        "issue_ids": [i["id"] for i in issues],
        "files": files,
        "weight": weight,
        "difficulty_breakdown": dict(breakdown),
        "session_size": session_size,
        "action": action,
    }
    return result


def _warn_duplicate_ids(issues: list[dict]) -> None:
    """Warn about duplicate issue IDs in the source file."""
    id_counts = defaultdict(int)
    for issue in issues:
        id_counts[issue["id"]] += 1
    for issue_id, count in id_counts.items():
        if count > 1:
            titles = [i["title"][:60] for i in issues if i["id"] == issue_id]
            print(f"WARNING: Duplicate issue ID '{issue_id}' found {count}x:", file=sys.stderr)
            for t in titles:
                print(f"  - {t}", file=sys.stderr)
            print(f"  Fix code_concerns.md to use unique IDs.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Triage code_concerns.md into grouped fix batches")
    parser.add_argument("--input", default="code_concerns.md", help="Path to code_concerns.md")
    parser.add_argument("--output", default=None, help="Output JSON path (default: stdout)")
    args = parser.parse_args()

    issues = parse_concerns(args.input)
    _warn_duplicate_ids(issues)
    completed = [i for i in issues if i["completed"]]
    no_action = [i for i in issues if i.get("no_action", False) and not i["completed"]]
    pending = [i for i in issues if not i["completed"] and not i.get("no_action", False)]

    groups = group_issues(issues)
    deps = detect_dependencies(issues)

    # Collect no-action issue IDs for reference
    no_action_ids = [i["id"] for i in no_action]

    result = {
        "total_issues": len(issues),
        "completed": len(completed),
        "no_action": len(no_action),
        "no_action_ids": no_action_ids,
        "pending": len(pending),
        "groups": groups,
        "dependency_chains": deps,
    }

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Written {len(groups)} groups ({len(pending)} pending, {len(no_action)} no-action, {len(completed)} completed) to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()

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

    ("toctou", "TOCTOU: os.path.exists() pre-checks",
     "Replace os.path.exists() + open/listdir with direct try/except (OSError, FileNotFoundError)",
     [r"os\.path\.exists", r"TOCTOU", r"time-of-check"],
     []),

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
     [r"god module", r"domain mixing", r"file organization", r"should move", r"split",
      r"exceeds.*800.*line", r"file size.*exceeds"],
     []),
]

# Difficulty weights
WEIGHTS = {"trivial": 1, "low": 2, "medium": 4, "high": 8, "investigation": 999}
MAX_WEIGHT = 20

# Canonical heading pattern (same as normalize_concerns.py)
EM_DASH = "\u2014"
CANONICAL_HEADING = re.compile(
    r"^###\s+(?:\[COMPLETED\]\s*)?"
    r"([AC]\d+|ORG\d+):\s*"
    r"(?:\[(Critical|Advisory)\]\s+)?"
    r"(.+?)\s+"
    r"\u2014\s+Difficulty:\s*(\w+)\s+"
    r"\u2014\s+Category:\s*(.+?)\s*$",
    re.MULTILINE,
)


def parse_concerns(filepath: str) -> list[dict]:
    """Parse code_concerns.md (canonical format) into structured issue dicts."""
    text = Path(filepath).read_text(encoding="utf-8")
    issues = []

    # Find all ## headings (file sections) with their positions
    file_headings = [(m.start(), m.group(1).strip()) for m in re.finditer(r"^## (.+)$", text, re.MULTILINE)]

    # Find all canonical ### headings
    for match in CANONICAL_HEADING.finditer(text):
        issue_id = match.group(1).strip()
        severity_raw = match.group(2)
        title = match.group(3).strip()
        difficulty_raw = match.group(4).strip().lower()
        category = match.group(5).strip()

        # Determine severity
        if severity_raw:
            severity = severity_raw.lower()
        elif issue_id.startswith("C"):
            severity = "critical"
        else:
            severity = "advisory"

        # Check if completed (look for [COMPLETED] in the full heading line)
        heading_line = match.group(0)
        completed = "[COMPLETED]" in heading_line

        # Extract the body (lines after the heading until next ### or ---)
        body_start = match.end()
        body_end = len(text)
        next_heading = re.search(r"^###\s+", text[body_start:], re.MULTILINE)
        if next_heading:
            body_end = body_start + next_heading.start()
        sep = re.search(r"^---\s*$", text[body_start:body_end], re.MULTILINE)
        if sep:
            body_end = body_start + sep.start()

        body = text[body_start:body_end].strip()

        # Extract fields from body
        line_ref = ""
        issue_text = ""
        suggestion_text = ""
        depends_on = []
        related = []

        line_match = re.search(r"\*\*Lines?\*\*:\s*(.+)", body)
        if line_match:
            line_ref = line_match.group(1).strip()

        issue_match = re.search(r"\*\*Issue\*\*:\s*(.+?)(?=\*\*Impact\*\*|\Z)", body, re.DOTALL)
        if issue_match:
            issue_text = issue_match.group(1).strip()

        suggestion_match = re.search(r"\*\*Suggestion\*\*:\s*(.+?)(?=\n- \*\*|\Z)", body, re.DOTALL)
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

        # Find the nearest preceding ## heading for file section
        file_name = "unknown"
        for fh_pos, fh_name in file_headings:
            if fh_pos < match.start():
                file_name = fh_name
            else:
                break

        issues.append({
            "id": issue_id,
            "title": title,
            "file": file_name,
            "line": line_ref,
            "severity": severity,
            "difficulty": difficulty_raw,
            "category": category,
            "completed": completed,
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
    """Detect dependencies using structured Depends-on field from canonical format.

    Filters out chains where both sides are completed (noise reduction).
    Uses multi-valued lookup to handle duplicate IDs gracefully.
    """
    deps = []
    by_id = defaultdict(list)
    for i in issues:
        by_id[i["id"]].append(i)

    pending_ids = {i["id"] for i in issues if not i["completed"]}

    for issue in issues:
        # Use structured depends_on field
        for dep_id in issue.get("depends_on", []):
            if dep_id in by_id:
                deps.append({
                    "must_do_first": dep_id,
                    "unblocks": issue["id"],
                    "reason": f"{issue['id']} explicitly depends on {dep_id}",
                })

        # Also use related field as soft dependencies
        for rel_id in issue.get("related", []):
            if rel_id in by_id:
                deps.append({
                    "must_do_first": rel_id,
                    "unblocks": issue["id"],
                    "reason": f"{issue['id']} is related to {rel_id} (soft dependency)",
                    "soft": True,
                })

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
    - Ungrouped issues try category-based grouping before falling back to file
    - pattern_id is included in output for cross-referencing
    """
    pending = [i for i in issues if not i["completed"]]

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

    # Ungrouped issues: try category-based grouping first, then file as fallback
    if ungrouped:
        # Try grouping by category
        category_buckets: dict[str, list[dict]] = defaultdict(list)
        for issue in ungrouped:
            cat = issue.get("category", "Unknown")
            category_buckets[cat].append(issue)

        # Only use category grouping if it produces groups of 2+;
        # single-issue categories fall through to file grouping
        category_grouped = set()
        for cat, cat_issues in category_buckets.items():
            if len(cat_issues) >= 2:
                cat_issues.sort(key=lambda i: WEIGHTS.get(i["difficulty"], 999))
                current_batch = []
                current_weight = 0

                for issue in cat_issues:
                    w = WEIGHTS.get(issue["difficulty"], 999)
                    if current_weight + w > MAX_WEIGHT and current_batch:
                        group_counter += 1
                        groups.append(_make_group(f"G{group_counter}", f"Misc: {cat}", "No common pattern detected — review individually", current_batch))
                        current_batch = []
                        current_weight = 0
                    current_batch.append(issue)
                    current_weight += w
                    category_grouped.add(issue["id"])

                if current_batch:
                    group_counter += 1
                    groups.append(_make_group(f"G{group_counter}", f"Misc: {cat}", "No common pattern detected — review individually", current_batch))

        # Remaining ungrouped: fall back to file grouping
        remaining = [i for i in ungrouped if i["id"] not in category_grouped]
        if remaining:
            file_buckets: dict[str, list[dict]] = defaultdict(list)
            for issue in remaining:
                file_buckets[issue["file"]].append(issue)

            for file_name, file_issues in file_buckets.items():
                file_issues.sort(key=lambda i: WEIGHTS.get(i["difficulty"], 999))
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
    pending = [i for i in issues if not i["completed"]]
    completed = [i for i in issues if i["completed"]]

    groups = group_issues(issues)
    deps = detect_dependencies(issues)

    result = {
        "total_issues": len(issues),
        "completed": len(completed),
        "pending": len(pending),
        "groups": groups,
        "dependency_chains": deps,
    }

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Written {len(groups)} groups ({len(pending)} pending issues) to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()

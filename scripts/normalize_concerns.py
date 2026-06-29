#!/usr/bin/env python3
"""Normalize code_concerns.md entries to canonical format and validate.

Modes:
    --check          Report violations without modifying the file
    --fix            Migrate legacy entries to canonical format and write back
    (no flags)       Same as --check

Canonical heading format:
    ### [ID]: [Critical|Advisory] Title — Difficulty: X — Category: Y
    ### ORG1: Title — Difficulty: X — Category: File Organization

Canonical body fields:
    - **Line**: N (or N-M)        [or **Lines** for ORG]
    - **Issue**: ...
    - **Impact**: ...
    - **Suggestion**: ...
    - **Depends-on**: ... (mandatory, use "none" if N/A)
    - **Related**: ... (mandatory, use "none" if N/A)
"""
import argparse
import re
import sys
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────

VALID_DIFFICULTIES = {"Trivial", "Low", "Medium", "High", "Investigation"}
VALID_CATEGORIES = {
    "Security", "Concurrency", "Error Handling", "Architecture",
    "Performance", "Correctness", "Resource Management", "Test Coverage",
    "Dead Code", "DRY", "Code Quality", "File Organization", "CSS",
}
EM_DASH = "\u2014"  # —

# Legacy heading patterns to detect and migrate
# Legacy heading patterns — ordered most-specific first
# Each entry: (name, compiled_regex, group_mapping)
# group_mapping maps field names to regex group indices (0-based after full match)
LEGACY_PATTERNS = [
    # Format C: ### [Critical] C18 — Title — Difficulty: Low  (severity-first, has difficulty)
    ("format_c",
     re.compile(r"^###\s+(?:\[COMPLETED\]\s*)?\[(Critical|Advisory)\]\s+([AC]\d+)\s+\u2014\s+(.+?)\s+\u2014\s+Difficulty:\s*(\w+)\s*$", re.MULTILINE),
     {"severity": 0, "id": 1, "title": 2, "difficulty": 3}),

    # Format B: ### [Critical] C10 — Title  (severity-first, no difficulty)
    ("format_b",
     re.compile(r"^###\s+(?:\[COMPLETED\]\s*)?\[(Critical|Advisory)\]\s+([AC]\d+)\s+\u2014\s+(.+?)\s*$", re.MULTILINE),
     {"severity": 0, "id": 1, "title": 2}),

    # Format A: ### A1: [Advisory] Title (Category)  (id-first, optional category in parens)
    ("format_a",
     re.compile(r"^###\s+(?:\[COMPLETED\]\s*)?([AC]\d+):\s*\[(Critical|Advisory)\]\s+(.+?)(?:\s*\(([^)]+)\))?\s*$", re.MULTILINE),
     {"id": 0, "severity": 1, "title": 2, "category": 3}),

    # Format D: ### ORG1: Title — Difficulty: Trivial  (ORG with difficulty)
    ("format_d",
     re.compile(r"^###\s+(?:\[COMPLETED\]\s*)?(ORG\d+):\s+(.+?)\s+\u2014\s+Difficulty:\s*(\w+)\s*$", re.MULTILINE),
     {"id": 0, "title": 1, "difficulty": 2}),

    # Format E: ### ORG1: Title  (ORG, no difficulty)
    ("format_e",
     re.compile(r"^###\s+(?:\[COMPLETED\]\s*)?(ORG\d+):\s+(.+?)\s*$", re.MULTILINE),
     {"id": 0, "title": 1}),
]

# Canonical heading pattern
CANONICAL_PATTERN = re.compile(
    r"^###\s+(?:(\[COMPLETED\]\s*)?)"
    r"([AC]\d+|ORG\d+):\s*"
    r"(?:\[(Critical|Advisory)\]\s+)?"
    r"(.+?)\s+"
    r"\u2014\s+Difficulty:\s*(\w+)\s+"
    r"\u2014\s+Category:\s*(.+?)\s*$",
    re.MULTILINE,
)


# ── Difficulty inference (for legacy entries without it) ──────────────────

_DIFFICULTY_RULES = [
    ("Investigation", [r"investigation", r"unclear", r"needs deeper analysis", r"root cause.*unclear", r"reproduction"]),
    ("High", [r"cross-file", r"update all.*call", r"move.*endpoint", r"migrate", r"architectural change", r"multiple.*module", r"refactor.*across"]),
    ("Medium", [r"extract.*helper", r"restructure", r"add.*validation.*error", r"context manager", r"multi-line", r"within a single function"]),
    ("Low", [r"add.*lock", r"add.*try/except", r"swap", r"replace.*with", r"add.*logging", r"add.*hmac", r"add.*limit", r"add.*cleanup", r"use.*weakvalue", r"change.*to", r"add.*validation"]),
    ("Trivial", [r"remove.*line", r"delete.*dead", r"rename", r"remove.*stray", r"remove.*redundant", r"remove.*duplicate.*rule", r"fix.*z-index", r"remove.*unused"]),
]


def infer_difficulty(issue_text: str, suggestion_text: str) -> str:
    combined = (issue_text + " " + suggestion_text).lower()
    for level, keywords in _DIFFICULTY_RULES:
        for kw in keywords:
            if re.search(kw, combined, re.IGNORECASE):
                return level
    return "Medium"


# ── Category inference (for legacy entries without it) ────────────────────

_CATEGORY_MAP = {
    "data corruption risk": "Correctness",
    "memory leak": "Resource Management",
    "same pattern": "DRY",
    "config drift": "Architecture",
    "signal handling": "Concurrency",
    "inconsistency": "Code Quality",
    "code duplication": "DRY",
    "dos prevention": "Security",
    "consistency": "Code Quality",
    "correctness/architecture": "Correctness",
    "architecture/dry": "Architecture",
    "concurrency": "Concurrency",
    "performance": "Performance",
    "error handling": "Error Handling",
}

_CATEGORY_RULES = [
    ("Security", [r"timing attack", r"hmac\.compare_digest", r"str\(e\)", r"information disclosure", r"input validation", r"ssrf", r"command injection", r"path traversal", r"secret.*log"]),
    ("Concurrency", [r"race condition", r"toctou", r"os\.path\.exists", r"lock", r"thread.safe", r"deadlock", r"atomic"]),
    ("Error Handling", [r"except.*pass", r"swallow", r"silent", r"uncaught", r"error.*return", r"bare.*except"]),
    ("Architecture", [r"pattern consistency", r"return.*contract", r"import hygiene", r"module.level.*side", r"factory pattern"]),
    ("Performance", [r"unnecessary alloc", r"hot path", r"every.*request", r"every.*call", r"unbounded", r"redundant", r"subprocess.*spawn"]),
    ("Correctness", [r"division by zero", r"overflow", r"off.by.one", r"edge case", r"empty list", r"none.*value"]),
    ("Resource Management", [r"subprocess.*cleanup", r"file handle", r"temp file", r"thread.*join", r"database.*connection"]),
    ("Dead Code", [r"dead code", r"zero callers", r"never called", r"zero usage", r"dead css"]),
    ("DRY", [r"duplicat", r"DRY", r"same logic", r"nearly identical"]),
    ("Code Quality", [r"code smell", r"anti.pattern", r"redundant import", r"__import__", r"typo", r"stray"]),
    ("File Organization", [r"god module", r"domain mixing", r"file organization", r"should move", r"split"]),
    ("CSS", [r"undefined.*css", r"missing.*css", r"css.*not.*defined", r"broken.*css", r"z-index", r"dead css"]),
]


def normalize_category(raw: str) -> str:
    """Normalize a legacy category string to a valid category."""
    # Strip lesson references and extra text
    cleaned = re.sub(r"\u2014.*", "", raw).strip()
    cleaned = re.sub(r"—.*", "", cleaned).strip()
    cleaned = re.sub(r".*?Lesson.*", "", cleaned).strip()
    # Check direct mapping
    key = cleaned.lower().strip()
    if key in _CATEGORY_MAP:
        return _CATEGORY_MAP[key]
    # Check if already valid
    for valid in VALID_CATEGORIES:
        if key == valid.lower():
            return valid
    # Try partial match
    for valid in VALID_CATEGORIES:
        if valid.lower() in key or key in valid.lower():
            return valid
    return None  # Signal that inference is needed


def infer_category(title: str, issue_text: str, suggestion_text: str, raw_category: str = None) -> str:
    # If we have a raw category, try to normalize it first
    if raw_category:
        normalized = normalize_category(raw_category)
        if normalized:
            return normalized
    # Fall back to keyword inference
    combined = (title + " " + issue_text + " " + suggestion_text).lower()
    for category, keywords in _CATEGORY_RULES:
        for kw in keywords:
            if re.search(kw, combined, re.IGNORECASE):
                return category
    return "Code Quality"


# ── Parsing ───────────────────────────────────────────────────────────────

def parse_file(text: str) -> list[dict]:
    """Parse all issue entries from code_concerns.md text.
    Works on the full text without splitting into sections.
    """
    entries = []

    # Find all ## headings (file sections) with their positions
    file_headings = [(m.start(), m.group(1).strip()) for m in re.finditer(r"^## (.+)$", text, re.MULTILINE)]

    # Find all ### headings (issue entries)
    heading_iter = list(re.finditer(r"^###\s+.+$", text, re.MULTILINE))

    for j, heading_match in enumerate(heading_iter):
        heading_line = heading_match.group(0)
        start = heading_match.start()
        end = heading_iter[j + 1].start() if j + 1 < len(heading_iter) else len(text)

        # Stop at --- separators
        sep = re.search(r"^---\s*$", text[start:end], re.MULTILINE)
        if sep:
            end = start + sep.start()

        body = text[heading_match.end():end].strip()

        # Find the nearest preceding ## heading for file section
        file_section = "unknown"
        for fh_pos, fh_name in file_headings:
            if fh_pos < start:
                file_section = fh_name
            else:
                break

        entry = {
            "file_section": file_section,
            "heading_line": heading_line,
            "body": body,
        }

        # Try to parse as canonical
        entry["canonical"] = parse_canonical(heading_line)
        if not entry["canonical"]:
            entry["canonical"] = None
            entry["legacy"] = parse_legacy(heading_line, body)
        else:
            entry["legacy"] = None

        entries.append(entry)

    return entries


def parse_canonical(heading: str) -> dict | None:
    """Try to parse a heading as canonical format."""
    m = CANONICAL_PATTERN.match(heading)
    if not m:
        return None
    completed = bool(m.group(1))
    issue_id = m.group(2)
    severity = m.group(3) or ("Advisory" if issue_id.startswith("A") else "Critical" if issue_id.startswith("C") else None)
    title = m.group(4)
    difficulty = m.group(5)
    category = m.group(6)
    return {
        "completed": completed,
        "id": issue_id,
        "severity": severity,
        "title": title,
        "difficulty": difficulty,
        "category": category,
    }


def parse_legacy(heading: str, body: str) -> dict | None:
    """Try to parse a heading as one of the legacy formats."""
    for _name, pattern, group_map in LEGACY_PATTERNS:
        m = pattern.match(heading)
        if not m:
            continue

        groups = m.groups()
        data = {}
        for field, idx in group_map.items():
            val = groups[idx] if idx < len(groups) else None
            if val is not None:
                data[field] = val.strip() if isinstance(val, str) else val

        issue_id = data.get("id")
        if not issue_id:
            continue

        # Extract issue/suggestion text for difficulty/category inference
        issue_text = ""
        suggestion_text = ""
        issue_m = re.search(r"\*\*Issue\*\*:\s*(.+?)(?=\*\*Impact\*\*|\Z)", body, re.DOTALL)
        if issue_m:
            issue_text = issue_m.group(1).strip()
        sug_m = re.search(r"\*\*Suggestion\*\*:\s*(.+?)(?=\n-|\n###|\Z)", body, re.DOTALL)
        if sug_m:
            suggestion_text = sug_m.group(1).strip()

        # Infer missing fields
        difficulty = data.get("difficulty")
        if not difficulty:
            difficulty = infer_difficulty(issue_text, suggestion_text)

        category = data.get("category")
        if category:
            # Try to normalize the existing category
            normalized = normalize_category(category)
            category = normalized if normalized else category
        if not category or category not in VALID_CATEGORIES:
            category = infer_category(data.get("title", ""), issue_text, suggestion_text, category)

        severity = data.get("severity")
        if not severity:
            severity = "Advisory" if issue_id.startswith("A") else "Critical" if issue_id.startswith("C") else "Advisory"

        # Clean title of any existing tags
        title = data.get("title", "")
        title = re.sub(r"\[(Critical|Advisory|COMPLETED)\]", "", title).strip()
        title = re.sub(r"\u2014\s*Difficulty:\s*\w+", "", title).strip()
        title = re.sub(r"\s+", " ", title).strip()

        return {
            "id": issue_id,
            "severity": severity,
            "title": title,
            "difficulty": difficulty,
            "category": category,
        }

    return None


# ── Body field extraction and validation ──────────────────────────────────

def extract_body_fields(body: str) -> dict:
    """Extract structured fields from body text."""
    fields = {}
    for key in ("Line", "Lines", "Issue", "Impact", "Suggestion", "Depends-on", "Related"):
        m = re.search(rf"\*\*{re.escape(key)}\*\*:\s*(.+?)(?=\n- \*\*|\Z)", body, re.DOTALL)
        if m:
            fields[key] = m.group(1).strip()
    return fields


def validate_entry(entry: dict) -> list[str]:
    """Return list of validation errors for an entry."""
    errors = []

    # Check if it's canonical or legacy
    if entry["canonical"]:
        c = entry["canonical"]

        # Validate difficulty
        if c["difficulty"] not in VALID_DIFFICULTIES:
            errors.append(f"{c['id']}: invalid difficulty '{c['difficulty']}'")

        # Validate category
        if c["category"] not in VALID_CATEGORIES:
            errors.append(f"{c['id']}: invalid category '{c['category']}'")

        # Check body fields
        fields = extract_body_fields(entry["body"])
        if "Depends-on" not in fields:
            errors.append(f"{c['id']}: missing 'Depends-on' field")
        if "Related" not in fields:
            errors.append(f"{c['id']}: missing 'Related' field")

    elif entry["legacy"]:
        l = entry["legacy"]
        errors.append(f"{l['id']}: legacy format — needs migration to canonical")

        # Check body fields
        fields = extract_body_fields(entry["body"])
        if "Depends-on" not in fields:
            errors.append(f"{l['id']}: missing 'Depends-on' field (will be added as 'none')")
        if "Related" not in fields:
            errors.append(f"{l['id']}: missing 'Related' field (will be added as 'none')")

    else:
        errors.append(f"unparseable heading: {entry['heading_line'][:80]}")

    return errors


# ── Migration ─────────────────────────────────────────────────────────────

def migrate_entry(entry: dict) -> tuple[str, str]:
    """Generate canonical heading and body for a legacy entry.
    Returns (new_heading, new_body).
    """
    data = entry["legacy"] or entry["canonical"]
    if not data:
        return entry["heading_line"], entry["body"]

    completed_prefix = "[COMPLETED] " if data.get("completed") else ""

    # Build canonical heading
    if data["id"].startswith("ORG"):
        new_heading = f"### {completed_prefix}{data['id']}: {data['title']} {EM_DASH} Difficulty: {data['difficulty']} {EM_DASH} Category: {data['category']}"
    else:
        new_heading = f"### {completed_prefix}{data['id']}: [{data['severity']}] {data['title']} {EM_DASH} Difficulty: {data['difficulty']} {EM_DASH} Category: {data['category']}"

    # Build canonical body — preserve existing fields, add missing ones
    body = entry["body"]
    fields = extract_body_fields(body)

    # Check if Depends-on and Related are missing
    if "Depends-on" not in fields:
        body += "\n- **Depends-on**: none"
    if "Related" not in fields:
        body += "\n- **Related**: none"

    return new_heading, body


def migrate_file(text: str, entries: list[dict]) -> str:
    """Migrate all legacy entries in the file text to canonical format.
    Uses direct string replacement to avoid offset issues.
    """
    for entry in entries:
        # Skip entries that are already canonical with all required fields
        if entry["canonical"] and not _has_missing_body_fields(entry):
            continue
        # Skip unparseable entries (non-issue headings like "Post-Refactor Estimate")
        if not entry["canonical"] and not entry["legacy"]:
            continue

        new_heading, new_body = migrate_entry(entry)
        old_heading = entry["heading_line"]
        old_body = entry["body"]

        # Replace heading (exact string match)
        if old_heading != new_heading:
            text = text.replace(old_heading, new_heading, 1)

        # Replace body only if we added Depends-on/Related fields
        if old_body != new_body:
            text = text.replace(old_body, new_body, 1)

    return text


def _has_missing_body_fields(entry: dict) -> bool:
    """Check if a canonical entry is missing required body fields."""
    fields = extract_body_fields(entry["body"])
    return "Depends-on" not in fields or "Related" not in fields


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Normalize and validate code_concerns.md format")
    parser.add_argument("--input", default="code_concerns.md", help="Path to code_concerns.md")
    parser.add_argument("--check", action="store_true", help="Check only, don't modify file")
    parser.add_argument("--fix", action="store_true", help="Migrate legacy entries and write back")
    args = parser.parse_args()

    text = Path(args.input).read_text(encoding="utf-8")
    entries = parse_file(text)

    # Validate all entries
    all_errors = []
    legacy_count = 0
    canonical_count = 0
    unparseable_count = 0

    for entry in entries:
        if entry["canonical"]:
            canonical_count += 1
        elif entry["legacy"]:
            legacy_count += 1
        else:
            unparseable_count += 1

        errors = validate_entry(entry)
        all_errors.extend(errors)

    # Summary
    total = len(entries)
    print(f"Entries: {total} total ({canonical_count} canonical, {legacy_count} legacy, {unparseable_count} unparseable)")

    if all_errors:
        print(f"\nViolations ({len(all_errors)}):")
        for err in all_errors:
            print(f"  - {err}")
    else:
        print("\nAll entries conform to canonical format.")

    # Fix mode
    if args.fix and (legacy_count > 0 or any("missing" in e for e in all_errors)):
        new_text = migrate_file(text, entries)
        Path(args.input).write_text(new_text, encoding="utf-8")
        print(f"\nMigrated {legacy_count} legacy entries to canonical format. File updated.")
    elif args.fix:
        print("\nNo migrations needed — all entries already canonical.")

    if all_errors and not args.fix:
        print(f"\nRun with --fix to migrate {legacy_count} legacy entries and add missing fields.")
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Clean up completed issues in code_concerns.md.

Two modes:
  default (collapse): Keep heading lines of completed issues, remove body fields.
  --purge:            Remove completed issues entirely (heading + body).

Preserves ## file section headers even if all issues under them are completed.
Modifies code_concerns.md in-place. No backup (rely on git).

Usage:
    python scripts/cleanup_concerns.py [--input code_concerns.md] [--purge]
"""
import argparse
import re
import sys
from pathlib import Path

EM_DASH = "\u2014"
CANONICAL_HEADING = re.compile(
    r"^###\s+(?:\[COMPLETED\]\s*)?"
    r"([AC]\d+|ORG\d+):\s*"
    r"(.+?)\s+"
    r"\u2014\s+Difficulty:\s*(\w+)\s+"
    r"\u2014\s+Category:\s*(.+?)\s*$",
    re.MULTILINE,
)


def collapse_completed(text: str) -> tuple[str, int]:
    """Collapse completed issues to header-only. Returns (new_text, count_collapsed)."""
    lines = text.split("\n")
    result = []
    i = 0
    collapsed = 0

    while i < len(lines):
        line = lines[i]

        # Check if this is a completed issue heading
        m = CANONICAL_HEADING.match(line)
        if m and "[COMPLETED]" in line:
            # Keep the heading
            result.append(line)
            i += 1
            # Collect body lines until next ### or --- or ## or EOF
            body_lines = []
            while i < len(lines):
                next_line = lines[i]
                if (next_line.startswith("### ") or
                    next_line.startswith("## ") or
                    next_line.strip() == "---"):
                    break
                body_lines.append(next_line)
                i += 1
            # Only count as collapsed if there was actual body content
            if any(bl.strip() for bl in body_lines):
                collapsed += 1
            # Skip trailing blank line if present (but not before --- or ##)
            if i < len(lines) and lines[i].strip() == "" and i + 1 < len(lines):
                peek = lines[i + 1]
                if peek.startswith("### ") or peek.startswith("## ") or peek.strip() == "---":
                    i += 1  # skip the blank line
        else:
            result.append(line)
            i += 1

    return "\n".join(result), collapsed


def purge_completed(text: str) -> tuple[str, int]:
    """Remove completed issues entirely. Returns (new_text, count_purged)."""
    lines = text.split("\n")
    result = []
    i = 0
    purged = 0

    while i < len(lines):
        line = lines[i]

        # Check if this is a completed issue heading
        m = CANONICAL_HEADING.match(line)
        if m and "[COMPLETED]" in line:
            i += 1
            # Skip body lines until next ### or --- or ## or EOF
            while i < len(lines):
                next_line = lines[i]
                if (next_line.startswith("### ") or
                    next_line.startswith("## ") or
                    next_line.strip() == "---"):
                    break
                i += 1
            # Skip trailing blank line if present (but not before --- or ##)
            if i < len(lines) and lines[i].strip() == "" and i + 1 < len(lines):
                peek = lines[i + 1]
                if peek.startswith("### ") or peek.startswith("## ") or peek.strip() == "---":
                    i += 1  # skip the blank line
            purged += 1
        else:
            result.append(line)
            i += 1

    return "\n".join(result), purged


def main():
    parser = argparse.ArgumentParser(
        description="Clean up completed issues in code_concerns.md"
    )
    parser.add_argument("--input", default="code_concerns.md", help="Path to code_concerns.md")
    parser.add_argument("--purge", action="store_true",
                        help="Remove completed issues entirely (default: collapse to header-only)")
    args = parser.parse_args()

    filepath = Path(args.input)
    if not filepath.exists():
        print(f"Error: {args.input} not found", file=sys.stderr)
        sys.exit(1)

    text = filepath.read_text(encoding="utf-8")

    if args.purge:
        new_text, count = purge_completed(text)
        mode = "purged"
    else:
        new_text, count = collapse_completed(text)
        mode = "collapsed"

    filepath.write_text(new_text, encoding="utf-8")
    print(f"{count} completed issues {mode} in {args.input}")


if __name__ == "__main__":
    main()

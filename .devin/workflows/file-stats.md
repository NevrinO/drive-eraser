---
description: Count lines for all app files and list top 10 largest (plus any over 800 lines)
---

# File Stats — Line Count Check

## Purpose
Count lines in all application source files and display the largest ones to track file growth over time.

## Scope
- **Directories scanned**: `backend/` and `frontend/` (recursive)
- **File types included**: `*.py`, `*.js`, `*.html`, `*.css`
- **Excludes**: `.git/`, test fixtures, any gitignored files

## Steps

1. **Run the line count command** (PowerShell, Windows dev machine):
   ```powershell
   $files = Get-ChildItem -Path "d:\Projects\Drive-Eraser\backend","d:\Projects\Drive-Eraser\frontend" -Recurse -File -Include *.py,*.js,*.html,*.css; $results = @(); foreach ($f in $files) { $lc = (Get-Content $f.FullName).Count; $results += [PSCustomObject]@{Lines=$lc; File=$f.FullName.Replace('d:\Projects\Drive-Eraser\','')} }; $results | Sort-Object Lines -Descending | ForEach-Object { "$($_.Lines) $($_.File)" }
   ```

2. **Display results**:
   - Show **top 10** files sorted largest to smallest in a table with rank, lines, and relative file path.
   - If any files **beyond the top 10** have **800 or more lines**, include those as additional rows (clearly marked as "beyond top 10").

3. **Diff (conditional)**:
   - If a **previous run exists in the current conversation context** (same agent session), show a side-by-side comparison table with:
     - Previous rank, previous lines
     - Current rank, current lines
     - Delta (lines added/removed)
     - Files that entered or dropped out of the top 10
   - If **no previous run in context**, skip the diff — just show current results.
   - **Do NOT** persist results to a file. Diff is purely in-memory based on what the agent has already seen in the active conversation.

4. **Summary**: Briefly note notable changes (files that grew/shrank significantly, new entries, dropouts) if a diff was shown.

## Known Limitations

- **Primary use case**: This is mainly to keep large files from filling agent context windows before appropriate work can be done. Line count is a size proxy, not a complexity metric — a 1,700-line CSS file is less concerning than a 900-line Python file with high cyclomatic complexity.
- **Hardcoded paths**: PowerShell command and path stripping assume `d:\Projects\Drive-Eraser\`. Won't work if repo is moved.
- **No test/vendored file exclusion**: If minified JS or vendored libraries are added to `frontend/` later, they could skew results.
- **800-line threshold is arbitrary**: If the codebase grows and many files exceed 800 lines, output gets noisy. Threshold is tunable.
- **In-context diff only**: No persistence — diff only works within the same conversation. May need file-based history if workflow changes in the future.
- **Windows/PowerShell only**: Command won't run on the Ubuntu server. Acceptable since this runs from the Windows dev machine, but worth noting if workflow needs to change.

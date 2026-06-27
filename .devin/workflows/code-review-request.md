---
description: How to request deep dive code reviews from the Coding Agent
---

# Code Review Request Workflow

Use this workflow to request systematic code reviews from the Coding Agent for security, performance, and architectural analysis.

## Request Pattern for Code Changes

When you have staged/unstaged changes and want a deep dive:

```
As a [language] principal engineer, do a deep dive on the changes @[working-changes] and determine:
1. Will it fix the problem?
2. Is it the most efficient method?
3. Are there edge cases or security concerns?
Feel free to question any assumptions.
```

## Request Pattern for Existing Code

When you want to audit existing code without changes:

```
As a [language] principal engineer, audit [file/path] for:
- Security vulnerabilities
- Performance issues
- Architectural flaws
- Concurrency problems
Focus on root causes, not surface symptoms.
```

## What the Agent Analyzes

The agent applies these principles systematically:

### 1. Root Cause Analysis
- Traces data flow to find where problems originate
- Prefers minimal upstream fixes over downstream workarounds
- Follows Lesson #25 (Root Cause Investigation Over Surface Fixes)

### 2. Security & Architecture Guardrails
- Checks against `.devin/rules/lessons-learned.md` for known patterns
- Looks for SQL injection, path traversal, race conditions, input validation gaps
- Verifies authentication consistency across endpoints
- Applies language-specific security considerations

### 3. Efficiency Considerations
- Questions whether full re-renders, loops, or operations are necessary
- Looks for targeted updates vs. wholesale replacements
- Considers performance implications (DOM manipulation, database queries, etc.)

### 4. Cross-Language Applicability
- Applies same principles to Python, Go, Rust, JavaScript, etc.
- Adapts language-specific concerns (e.g., regex anchors, concurrency models)

## Example Requests

### JavaScript Frontend
```
As a JavaScript principal engineer, do a deep dive on the changes @[working-changes] and determine if it will fix the checkbox toggle problem efficiently.
```

### Python Backend
```
As a Python principal engineer, audit the changes @[working-changes] in backend/routes/admin_routes.py for SQL injection risks and race conditions.
```

### Go Service
```
As a Go principal engineer, review the goroutine handling in service/worker.go for race conditions and context cancellation propagation.
```

## Automatic Critique Trigger

When implementation is complete and ready for review, create `.agent-signal.json` in the workspace root to automatically trigger a Critic Agent review per the agent-coordination workflow.

## What Makes a Good Request

- **Specific scope**: "Audit the authentication flow" vs. "Review everything"
- **Context**: Explain the problem being solved, not just show the code
- **Open-ended questions**: "Question any assumptions" invites deeper analysis
- **Language expertise**: "As a [language] principal engineer" sets the right context
- **Reference changes**: Use `@[working-changes]` for staged/unstaged changes
- **Reference files**: Use `@[file-path]` for specific file analysis

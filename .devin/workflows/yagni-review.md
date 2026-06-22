---
description: How to request YAGNI (You Aren't Gonna Need It) code reviews
---

# YAGNI Review Workflow

Use this workflow to identify over-engineered features, unnecessary complexity, and code that solves problems you don't actually have.

## Request Pattern

```
Using the YAGNI principle, review @[file/path] and explain potential improvements. Do not code, just explain.
```

## What the Agent Analyzes

The agent systematically identifies:

### 1. Over-Engineered Features
- Features that solve hypothetical problems rather than actual ones
- Defensive coding for edge cases that don't occur in practice
- Configuration options that are never changed
- Hash verification, integrity checks, or validation layers that add complexity without clear benefit

### 2. Unnecessary Abstractions
- Duplicate code that could be consolidated (e.g., two functions with 80% overlap)
- Layers of indirection that don't provide meaningful separation
- Helper functions for data structures that don't actually exist in your data

### 3. Premature Generalization
- Code written for "future use cases" that may never materialize
- Parameterized functions where hardcoded values would suffice
- Plugin architectures or extension points that aren't used

### 4. Defensive Overkill
- Circular reference detection in data that's guaranteed to be JSON-serializable
- Array summarization for collections that never grow large
- Extensive logging or monitoring for low-risk operations

## What's NOT a YAGNI Violation

- Security features (HTML escaping, input validation, authentication)
- Core business logic that's actively used
- Required compliance or regulatory features
- Performance optimizations for actual bottlenecks
- Error handling for realistic failure modes

## Example Request

```
Using the YAGNI principle, review backend/certificates.py and explain potential improvements. Do not code, just explain.
```

## What Makes a Good Request

- **Specific file**: Target a single file or focused module
- **No code changes**: Explicitly state "do not code" to get analysis only
- **Context**: If you know certain features are actually needed, mention them so the agent doesn't flag them

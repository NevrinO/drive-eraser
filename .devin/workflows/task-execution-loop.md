---
description: Standard workflow for implementing a task with separate Coding Agent and Critic Agent roles
---

# Task Execution Loop Workflow

This workflow defines the standard process for implementing a task with iterative code-review cycles using separate Coding Agent and Critic Agent roles (per critic-actor-protocol.md).

## Process

1. **Create Feature Branch**
   - Create branch: `git checkout -b feature/task-X.Y-short-name`
   - Verify branch created successfully

2. **Implement Task (Coding Agent)**
   - Read TASKS.md to understand scope and dependencies
   - Implement all required changes (code, UI, API, etc.)
   - Follow lessons-learned.md guardrails
   - **DO NOT COMMIT** - commits are only allowed after Critic Agent approval
   - **Notify user**: "Implementation complete. Ready for Critic Agent review."

3. **Critic Agent Review (Triggered by User)**
   - User triggers Critic Agent in separate window
   - Critic Agent reviews changes against:
     - Reviewer Rules specified in TASKS.md
     - lessons-learned.md guardrails
     - Security best practices
     - Code patterns in existing codebase
   - If issues found:
     - Generate CRITIQUE.md with structured format
     - Add to lessons-learned.md if new patterns discovered
   - If no issues found:
     - Notify user: "No issues found. Ready to commit."

4. **Fix Issues (Coding Agent)**
   - If CRITIQUE.md exists with issues:
     - Read CRITIQUE.md
     - Fix all issues listed
     - Update CRITIQUE.md Resolution Log
     - **DO NOT DELETE CRITIQUE.md** - keep it for critic verification
     - **Notify user**: "Issues fixed. Ready for Critic Agent re-review."
     - Return to step 3 (Critic Agent re-review)
   - If no CRITIQUE.md (approved by Critic Agent):
     - Proceed to step 5

5. **Commit Changes**
   - **CRITICAL PRE-CHECK**: Verify CRITIQUE.md does not exist in workspace root
   - If CRITIQUE.md exists, STOP and return to step 4 (Fix Issues)
   - **Delete CRITIQUE.md** (critic has approved, safe to remove)
   - Stage all changes: `git add <files>`
   - Commit with descriptive message
   - Include task number and summary

6. **Merge to Base Branch**
   - Switch to base branch: `git checkout clean-up-artifacts`
   - Merge feature branch: `git merge feature/task-X.Y-short-name`
   - Delete feature branch: `git branch -d feature/task-X.Y-short-name`

7. **Update TASKS.md**
   - Mark task as completed
   - Update progress summary
   - Update completion date and agent

8. **Move to Next Task**
   - Read TASKS.md for next unstarted task
   - Repeat from step 1

## Key Principles

- **Separation of Concerns**: Coding Agent implements, Critic Agent reviews. No self-review.
- **Iterative Loop**: Don't commit until Critic Agent approves with no issues.
- **User Coordination**: User triggers Critic Agent reviews between implementation cycles.
- **CRITICAL**: Coding Agent MUST NOT commit any changes until after Critic Agent explicitly approves with "No issues found. Ready to commit."
- **No Partial Merges**: Only merge when task is fully complete and Critic Agent approved.
- **Consistent Branching**: Always branch from clean-up-artifacts, merge back to clean-up-artifacts.
- **Documentation**: Update TASKS.md immediately after merge.

## Example Usage

```
User: "Implement Task 3.4"
Coding Agent:
1. Creates branch feature/task-3.4-template-preview
2. Implements visual preview system
3. "Implementation complete. Ready for Critic Agent review."

User: Triggers Critic Agent to review

Critic Agent:
1. Reviews changes against lessons-learned.md rules
2. Finds issue: missing null check
3. Generates CRITIQUE.md with issue details
4. "Found 1 critical issue. See CRITIQUE.md."

Coding Agent:
1. Reads CRITIQUE.md
2. Fixes missing null check
3. Updates CRITIQUE.md Resolution Log
4. "Issues fixed. Ready for Critic Agent re-review."

User: Triggers Critic Agent to re-review

Critic Agent:
1. Re-reviews changes
2. No issues found
3. "No issues found. Ready to commit."

Coding Agent:
1. Commits changes
2. Merges to clean-up-artifacts
3. Updates TASKS.md
4. "Task 3.4 complete. Ready for next task."
```

---
description: Automatically spin up feature branches and agent assignments for parallel task execution
---
This workflow automates the creation of feature branches and agent assignments for parallel task execution.

## Steps

1. **Check current state**
   - Read TASKS.md to identify completed tasks and available next tasks
   - Verify current branch is clean-up-artifacts

2. **Identify available tasks**
   - Find tasks with "Status: ❌ Not Started" that have all dependencies marked as "✅ Completed"
   - Limit to 4-5 tasks for parallel execution based on agent capacity

3. **Create feature branches**
   - For each available task, create a branch: `feature/task-X.Y-short-name`
   - All branches branch off clean-up-artifacts
   - Run: `git checkout -b feature/task-X.Y-short-name` for each task

4. **Update TASKS.md**
   - Update "Current Session Assignments" section with new branch assignments
   - Update "High-priority parallel branches" section with new branches and their target files
   - Switch back to clean-up-artifacts branch after all branches created

5. **Generate agent assignment prompts**
   - For each task, output a structured prompt including:
     - Branch name
     - Scope (files to modify)
     - Reviewer rules from TASKS.md
     - Dependencies (if any)

## Example Output

After running this workflow, you'll have:
- 4-5 new feature branches created
- TASKS.md updated with branch assignments
- Ready-to-use agent prompts for each task

## Notes

- This workflow should be run from the clean-up-artifacts branch
- Ensure no uncommitted changes before running
- The workflow does NOT implement the tasks - it only sets up the branches and assignments

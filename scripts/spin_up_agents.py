#!/usr/bin/env python3
"""
Automated script to spin up feature branches and agent assignments for parallel task execution.
Reads TASKS.md to identify available tasks and creates branches accordingly.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Tuple


def run_command(cmd: List[str], cwd: Path) -> Tuple[bool, str]:
    """Run a shell command and return success status and output."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr


def read_tasks_md(tasks_path: Path) -> str:
    """Read TASKS.md file."""
    with open(tasks_path, 'r', encoding='utf-8') as f:
        return f.read()


def parse_tasks(content: str) -> List[Dict]:
    """Parse TASKS.md to extract task information."""
    tasks = []
    current_feature = None
    
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Detect feature sections
        if line.startswith('## FEATURE'):
            current_feature = line.split(':')[1].strip()
        
        # Detect task sections
        if line.startswith('### Task'):
            task_match = re.match(r'### Task (\d+\.\d+): (.+)', line)
            if task_match:
                task_id = task_match.group(1)
                task_name = task_match.group(2)
                
                # Parse task details
                status = "Not Started"
                branch = ""
                dependencies = []
                scope = []
                reviewer_rules = []
                
                # Look ahead for task details
                j = i + 1
                while j < len(lines) and not lines[j].startswith('###') and not lines[j].startswith('##'):
                    detail_line = lines[j]
                    
                    if detail_line.startswith('**Status:**'):
                        if '✅ Completed' in detail_line:
                            status = "Completed"
                        elif '❌ Not Started' in detail_line:
                            status = "Not Started"
                    
                    elif detail_line.startswith('**Branch:**'):
                        branch = detail_line.split(':', 1)[1].strip()
                    
                    elif detail_line.startswith('**Dependencies:**'):
                        deps_str = detail_line.split(':', 1)[1].strip()
                        if deps_str != "None":
                            # Clean up dependency format (remove "** Task " prefix, asterisks)
                            dependencies = []
                            for d in deps_str.split(','):
                                d_clean = d.strip()
                                # Remove "** Task " prefix if present
                                d_clean = d_clean.replace('** Task ', '').replace('*', '').strip()
                                if d_clean:
                                    dependencies.append(d_clean)
                    
                    elif detail_line.startswith('**Scope:**'):
                        # Collect scope lines (indented)
                        scope_lines = []
                        k = j + 1
                        while k < len(lines) and (lines[k].startswith('    -') or lines[k].startswith('        ')):
                            scope_lines.append(lines[k].strip())
                            k += 1
                        scope = scope_lines
                        j = k - 1  # Adjust for outer loop increment
                    
                    elif detail_line.startswith('**Reviewer Rules:**'):
                        rules_str = detail_line.split(':', 1)[1].strip()
                        reviewer_rules = [r.strip() for r in rules_str.split(',')]
                    
                    j += 1
                
                tasks.append({
                    'id': task_id,
                    'name': task_name,
                    'feature': current_feature,
                    'status': status,
                    'branch': branch,
                    'dependencies': dependencies,
                    'scope': scope,
                    'reviewer_rules': reviewer_rules
                })
        
        i += 1
    
    return tasks


def get_available_tasks(tasks: List[Dict], completed_tasks: set) -> List[Dict]:
    """Get tasks that are not started and have all dependencies met."""
    available = []
    for task in tasks:
        if task['status'] == "Not Started":
            # Normalize dependency IDs (remove "Task " prefix if present)
            normalized_deps = []
            for dep in task['dependencies']:
                if dep.startswith("Task "):
                    normalized_deps.append(dep.replace("Task ", ""))
                else:
                    normalized_deps.append(dep)
            
            deps_met = all(dep in completed_tasks for dep in normalized_deps)
            if deps_met:
                available.append(task)
    return available


def create_branch(repo_path: Path, branch_name: str, base_branch: str = "clean-up-artifacts") -> Tuple[bool, str]:
    """Create a new feature branch if it doesn't exist."""
    # Check if branch already exists
    success, output = run_command(['git', 'branch', '--list', branch_name], repo_path)
    if success and output.strip():
        return True, f"Branch {branch_name} already exists"
    
    # Switch to base branch first
    success, output = run_command(['git', 'checkout', base_branch], repo_path)
    if not success:
        return False, f"Failed to checkout {base_branch}: {output}"
    
    # Create and checkout new branch
    success, output = run_command(['git', 'checkout', '-b', branch_name], repo_path)
    if not success:
        return False, f"Failed to create branch {branch_name}: {output}"
    
    return True, f"Created branch {branch_name}"


def update_tasks_md(tasks_path: Path, assignments: List[Dict]) -> bool:
    """Update TASKS.md with new branch assignments."""
    with open(tasks_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Update Current Session Assignments
    assignments_section = "### Current Session Assignments\n"
    for i, task in enumerate(assignments, 1):
        assignments_section += f"- **Agent {i}:** {task['branch']} (d:\\\\Projects\\\\Drive-Eraser)\n"
    
    # Update High-priority parallel branches
    branches_section = "High-priority parallel branches:\n"
    for task in assignments:
        scope_str = ", ".join(task['scope']) if task['scope'] else "TBD"
        branches_section += f"- `{task['branch']}` -> {scope_str}\n"
    
    # Replace sections using raw strings for regex
    content = re.sub(
        r'### Current Session Assignments.*?(?=\n### Branch Strategy)',
        assignments_section,
        content,
        flags=re.DOTALL
    )
    
    content = re.sub(
        r'High-priority parallel branches:.*?(?=\nRemaining tasks:)',
        branches_section,
        content,
        flags=re.DOTALL
    )
    
    with open(tasks_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return True


def main():
    repo_path = Path(r"d:\Projects\Drive-Eraser")
    tasks_path = repo_path / "TASKS.md"
    
    if not tasks_path.exists():
        print(f"Error: TASKS.md not found at {tasks_path}")
        sys.exit(1)
    
    print("Reading TASKS.md...")
    content = read_tasks_md(tasks_path)
    
    print("Parsing tasks...")
    tasks = parse_tasks(content)
    
    # Get completed task IDs
    completed_tasks = {task['id'] for task in tasks if task['status'] == "Completed"}
    
    print(f"Found {len(tasks)} total tasks, {len(completed_tasks)} completed")
    
    # Get available tasks
    available = get_available_tasks(tasks, completed_tasks)
    print(f"Found {len(available)} available tasks with dependencies met")
    
    if not available:
        print("No available tasks to process")
        sys.exit(0)
    
    # Limit to 4 tasks for parallel execution
    max_tasks = 4
    to_process = available[:max_tasks]
    print(f"Processing {len(to_process)} tasks (limited to {max_tasks} for parallel execution)")
    
    # Ensure we're on clean-up-artifacts
    print("Switching to clean-up-artifacts branch...")
    success, output = run_command(['git', 'checkout', 'clean-up-artifacts'], repo_path)
    if not success:
        print(f"Warning: {output}")
    
    # Create branches
    assignments = []
    for task in to_process:
        branch_name = task['branch'] if task['branch'] else f"feature/task-{task['id'].replace('.', '-')}-{task['name'].lower().replace(' ', '-')}"
        
        # Clean branch name (remove any markdown formatting)
        branch_name = branch_name.replace('**', '').strip()
        
        print(f"Creating branch: {branch_name}")
        success, output = create_branch(repo_path, branch_name)
        if success:
            print(f"  [OK] {output}")
            assignments.append({
                'branch': branch_name,
                'scope': task['scope']
            })
        else:
            print(f"  [FAIL] {output}")
    
    # Switch back to clean-up-artifacts
    print("Switching back to clean-up-artifacts...")
    run_command(['git', 'checkout', 'clean-up-artifacts'], repo_path)
    
    # Update TASKS.md
    if assignments:
        print("Updating TASKS.md with branch assignments...")
        if update_tasks_md(tasks_path, assignments):
            print("  [OK] TASKS.md updated")
        else:
            print("  [FAIL] Failed to update TASKS.md")
    
    # Print agent assignment prompts
    print("\n" + "="*80)
    print("AGENT ASSIGNMENT PROMPTS")
    print("="*80 + "\n")
    
    for i, task in enumerate(to_process[:len(assignments)], 1):
        branch_name = assignments[i-1]['branch']
        print(f"### Agent {i}: Task {task['id']} - {task['name']}")
        print(f"**Branch:** {branch_name}")
        print(f"**Scope:** {', '.join(task['scope']) if task['scope'] else 'See TASKS.md'}")
        print(f"**Reviewer Rules:** {', '.join(task['reviewer_rules'])}")
        print()
    
    print("="*80)
    print(f"Created {len(assignments)} branches successfully")
    print("="*80)


if __name__ == "__main__":
    main()

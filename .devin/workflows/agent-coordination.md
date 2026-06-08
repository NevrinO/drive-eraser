---
description: Automatic agent coordination and signaling protocol
---

# Agent Coordination Workflow

This workflow defines the automatic signaling protocol for Coding Agent and Critic Agent coordination without manual user intervention.

## Signal Protocol

### Signal File: `.agent-signal.json`
Located in workspace root. Used by agents to request actions from other agents or the system.

**Format:**
```json
{
  "type": "request_review|escalate|acknowledge",
  "from_agent": "coding|critic",
  "to_agent": "critic|coding|user",
  "timestamp": "ISO-8601",
  "context": {
    "changes_summary": "Brief description of changes",
    "files_modified": ["file1.js", "file2.py"],
    "task_reference": "Task X.Y from TASKS.md",
    "decision_reference": "Entry in docs/SECURITY_DEVIATIONS.md (if applicable)"
  }
}
```

**Validation:**
- Required fields: `type`, `from_agent`, `to_agent`, `timestamp`
- `type` must be one of: `request_review`, `escalate`, `acknowledge`
- `from_agent` must be one of: `coding`, `critic`
- `to_agent` must be one of: `critic`, `coding`, `user`
- `timestamp` must be valid ISO-8601 format
- `context` is optional but recommended for `request_review` and `escalate` types

### Response File: `.agent-response.json`
Located in workspace root. Used by agents to respond to signals.

**Format:**
```json
{
  "type": "review_complete|acknowledged|escalation_response",
  "from_agent": "critic|coding|user",
  "to_agent": "coding|critic",
  "timestamp": "ISO-8601",
  "result": {
    "status": "approved|needs_fixes|escalated",
    "critique_file": "CRITIQUE.md (if created)",
    "message": "Additional context or instructions"
  }
}
```

**Validation:**
- Required fields: `type`, `from_agent`, `to_agent`, `timestamp`, `result`
- `type` must be one of: `review_complete`, `acknowledged`, `escalation_response`
- `from_agent` must be one of: `critic`, `coding`, `user`
- `to_agent` must be one of: `coding`, `critic`
- `timestamp` must be valid ISO-8601 format
- `result.status` must be one of: `approved`, `needs_fixes`, `escalated`
- `result.critique_file` is required if status is `needs_fixes`
- `result.message` is optional

## Coding Agent Protocol

### Requesting Automatic Critique
When the Coding Agent completes implementation and wants a Critic Agent review:

1. **Check for existing CRITIQUE.md** - If exists, must resolve before requesting new review
2. **Create signal file**:
   ```json
   {
     "type": "request_review",
     "from_agent": "coding",
     "to_agent": "critic",
     "timestamp": "2026-06-06T00:00:00Z",
     "context": {
       "changes_summary": "Implemented feature X with Y changes",
       "files_modified": ["frontend/component.js", "backend/api.py"],
       "task_reference": "Task 3.4 from TASKS.md"
     }
   }
   ```
3. **Notify user**: "Implementation complete. Requesting automatic Critic Agent review via .agent-signal.json"
4. **Wait for response** - Poll for `.agent-response.json` every 30 seconds with a 30-minute timeout:
   - If response appears: proceed to handling logic
   - If timeout reached: notify user "No response from Critic Agent within 30 minutes. Please check if Critic Agent was triggered or manually review the changes." Delete `.agent-signal.json`

### Handling Critic Response
When `.agent-response.json` appears:

1. **Read response file**
2. **If status is "approved"**:
   - Delete `.agent-signal.json` and `.agent-response.json`
   - Verify commit conditions (see below)
   - Proceed to commit changes
3. **If status is "needs_fixes"**:
   - Read CRITIQUE.md
   - Fix all issues
   - Update CRITIQUE.md Resolution Log
   - Delete `.agent-response.json`
   - Create new signal file to request re-review
4. **If status is "escalated"**:
   - Read escalation context
   - Wait for user decision before proceeding

### Commit Conditions
The Coding Agent MUST NOT commit changes until ALL of the following conditions are met:

1. **CRITIQUE.md does not exist** in workspace root
   - If CRITIQUE.md exists, STOP and return to fix issues
   - CRITIQUE.md must be deleted after Critic Agent approval

2. **Critic Agent has approved** via `.agent-response.json`
   - Response status must be "approved"
   - If status is "needs_fixes" or "escalated", do not commit

3. **Signal files are cleaned up**
   - Delete `.agent-signal.json` and `.agent-response.json` before committing
   - A pre-commit hook will block commits if these files are staged
   - Signal files are NOT in .gitignore (agents need to read/write them), so pre-commit hook provides protection

4. **All issues resolved**
   - If CRITIQUE.md existed previously, all issues must be fixed and Resolution Log updated
   - User has confirmed all issues are fixed (if manual approval was required)

## Critic Agent Protocol

### Responding to Review Request
When `.agent-signal.json` exists with type "request_review":

1. **Read signal file** to understand context
2. **Check docs/SECURITY_DEVIATIONS.md** for documented decisions
3. **Perform review** per critic-actor-protocol.md
4. **Create response file**:
   ```json
   {
     "type": "review_complete",
     "from_agent": "critic",
     "to_agent": "coding",
     "timestamp": "2026-06-06T00:00:00Z",
     "result": {
       "status": "approved|needs_fixes|escalated",
       "critique_file": "CRITIQUE.md (if created)",
       "message": "Optional additional context"
     }
   }
   ```
5. **Delete signal file** - `.agent-signal.json`
6. **Notify user**: "Review complete. Response sent via .agent-response.json"

### Escalation to User
When Critic Agent disagrees with a documented decision:

1. **Create escalation signal**:
   ```json
   {
     "type": "escalate",
     "from_agent": "critic",
     "to_agent": "user",
     "timestamp": "2026-06-06T00:00:00Z",
     "context": {
       "decision_reference": "Entry in docs/SECURITY_DEVIATIONS.md",
       "issue": "Description of why the decision appears problematic",
       "question": "Should this be reconsidered or is the current approach still correct?"
     }
   }
   ```
2. **Wait for user response** in `.agent-response.json`
3. **Proceed based on user decision**

## System Coordination

### Automatic Agent Triggering (Requires System Support)
If the system supports file watching or periodic monitoring:

1. **Monitor for signal files** - Check `.agent-signal.json` periodically
2. **Trigger appropriate agent**:
   - If signal type is "request_review" and to_agent is "critic", spin up Critic Agent
   - If signal type is "escalate" and to_agent is "user", notify user
3. **Clean up stale signals** - Remove signal files older than 1 hour

### Manual Fallback (If System Support Unavailable)
If automatic triggering is not available:

1. **Coding Agent** creates `.agent-signal.json` and notifies user: "Implementation complete. Signal file created. Please manually trigger Critic Agent via @[/review] or by opening a new agent window."
2. **User** sees signal file and manually triggers Critic Agent
3. **Critic Agent** checks for `.agent-signal.json` on startup and processes it if present
4. **Critic Agent** creates `.agent-response.json` after review
5. **Coding Agent** detects response via polling and proceeds

### User Override
User can always:
- Delete signal files to cancel pending requests
- Manually trigger agents via slash commands
- Edit signal files to modify requests

## Error Handling

- **Signal file corruption**: If signal file is invalid JSON or missing required fields, delete it and notify user with specific validation error (e.g., "Missing required field: type")
- **Response file corruption**: If response file is invalid JSON or missing required fields, delete it and notify user with specific validation error
- **SECURITY_DEVIATIONS.md corruption**: If docs/SECURITY_DEVIATIONS.md exists but is malformed, notify user and proceed without using it (do not crash)
- **Missing response timeout**: If no response within 30 minutes, notify user and delete signal
- **Concurrent signals**: Only one active signal per agent pair allowed; new signals replace old ones

## Example Flow

```
Coding Agent:
1. Implements feature
2. Creates .agent-signal.json (request_review)
3. "Implementation complete. Requesting automatic Critic Agent review"

System:
1. Detects .agent-signal.json
2. Spins up Critic Agent

Critic Agent:
1. Reads .agent-signal.json
2. Performs review
3. Creates .agent-response.json (needs_fixes)
4. Deletes .agent-signal.json
5. "Review complete. Response sent via .agent-response.json"

Coding Agent:
1. Detects .agent-response.json
2. Reads CRITIQUE.md
3. Fixes issues
4. Updates CRITIQUE.md Resolution Log
5. Deletes .agent-response.json
6. Creates new .agent-signal.json (request_review)
7. "Issues fixed. Requesting re-review"

[Repeat until approved]
```

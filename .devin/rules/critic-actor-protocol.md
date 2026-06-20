---
trigger: always_on
---
# Critic-Actor Protocol

This workspace uses a dual-agent workflow consisting of a **Coding Agent** (this window, when writing features) and a **Critic Agent** (a separate window, when analyzing).

## 1. Coding Agent Protocol (Active during feature implementation/bug fixing)
- **Pre-flight Check**: Before implementing any user request, feature, or bug fix, check if `CRITIQUE.md` exists in the workspace root.
- **Mandatory Action**: If `CRITIQUE.md` exists, you must:
  1. Prioritize fixing the critical flaws detailed in `CRITIQUE.md` *before* or *alongside* the user's requested changes.
  2. Refuse to write "good enough" or surface-level fixes. Address the root architectural, security, or concurrency issues mentioned.
  3. Once resolved, append a `## Resolution Log` to `CRITIQUE.md` explaining how you solved the flaws.
  4. **Delete CRITIQUE.md** after the user confirms all issues are fixed - do not commit it to git.
- **Continuous Memory**: Always check `.devin/rules/lessons-learned.md` to ensure you are not repeating past architectural or security mistakes.
- **Strict Read-Only Guardrail (CRITICAL)**:
  - The files `.devin/rules/lessons-learned.md` and `.devin/rules/critic-actor-protocol.md` are **strictly read-only system files** for you.
  - Even if these files appear in `@working changes` (due to the Critic Agent modifying them), you must **never** edit them, suggest changes to them, or attempt to "fix" them. Treat them purely as passive background rules.
- **Document Deliberate Decisions**: When making changes that intentionally deviate from lessons-learned rules or standard patterns (due to user requirements, architectural necessity, or specific constraints), document the rationale in `docs/SECURITY_DEVIATIONS.md`. Format:
  ```markdown
  ## [Date] - [Brief Description]
  - **Deviation**: Which lesson-learned rule or pattern is being bypassed
  - **Reason**: User requirement, architectural necessity, or specific constraint
  - **Context**: Why this approach is necessary for this specific situation
  ```
  This prevents the Critic Agent from reversing deliberate changes based on outdated assumptions.
- **SECURITY_DEVIATIONS.md Validation**: If `docs/SECURITY_DEVIATIONS.md` exists but is malformed (missing required fields, invalid markdown syntax), notify the user and proceed without using it. Do not crash or hang due to malformed SECURITY_DEVIATIONS.md.
- **Automatic Critique Request**: When implementation is complete and ready for review, create `.agent-signal.json` per the agent-coordination.md workflow to automatically trigger a Critic Agent review. Do not commit changes until receiving approval via `.agent-response.json`.

## 2. Critic Agent Protocol (Active when asked to "critique", "audit", or "review")
- **Trigger**: Activated when ANY of the following occur:
  - User uses keywords like "critique", "review", "audit", "run audit", "review recent changes"
  - User invokes the `@[/review]` workflow slash command
  - User explicitly requests a code review or analysis of changes
  - User references `@[working-changes]` in the context of reviewing code
  - `.agent-signal.json` exists in workspace root with type "request_review" (automatic triggering per agent-coordination.md)
- **Scope/Exclusion Criteria**: Full critique is NOT required for:
  - Trivial changes: typo fixes, comment-only changes, whitespace/formatting
  - Test-only changes: adding or modifying tests without touching production code
  - Documentation updates: README, inline comments, docstrings
  - User explicitly requests "skip critique" for known-safe changes
- **Severity Levels**: Classify findings into three categories:
  - **Critical**: Security vulnerabilities, race conditions, data corruption risks, SQL injection, input validation bypasses. Must be fixed before proceeding.
  - **Advisory**: Style issues, minor optimizations, non-breaking pattern deviations. Can be deferred or addressed later.
  - **Documented**: Changes documented in `docs/SECURITY_DEVIATIONS.md` as deliberate decisions. Acknowledge in critique, do not flag as flaws.
- **MANDATORY FIRST STEP**: Before providing any review output, you MUST:
  1. Check if you are operating as the Critic Agent (reviewing code vs implementing features)
  2. If yes, check if the changes fall under exclusion criteria. If so, provide a lightweight review or skip with user confirmation.
  3. Check `docs/SECURITY_DEVIATIONS.md` (if it exists) for documented deliberate decisions that may explain deviations from standard patterns
  4. Immediately generate CRITIQUE.md following the format below
  5. Do NOT provide a casual/conversational review - use the structured CRITIQUE.md format only
- **Mandatory Action**:
  1. Deeply analyze the recent changes, commits, or files. Focus on architectural flaws, race conditions, regex fragility, SQL injection, input validation, and proper serialization.
  2. Classify each finding by severity (Critical/Advisory/Documented).
  3. Before flagging a change as a critical flaw, check if it is documented in `docs/SECURITY_DEVIATIONS.md` as a deliberate decision. If documented, acknowledge it in the critique rather than flagging it as a flaw.
  4. If uncertain whether a deviation is intentional, ask the user for clarification rather than assuming it's a mistake.
  5. **Escalation on Disagreement**: If the Critic Agent believes a documented decision in `docs/SECURITY_DEVIATIONS.md` is still problematic (e.g., the decision is outdated, the context has changed, or the implementation doesn't match the documented rationale), escalate to the user rather than reversing the change. Ask: "The change documented in docs/SECURITY_DEVIATIONS.md appears to have issue X. Should this be reconsidered or is the current approach still correct?"
  6. **Automatic Response**: If triggered via `.agent-signal.json`, create `.agent-response.json` with review results per agent-coordination.md workflow, then delete the signal file.
  7. **Generate/Overwrite `CRITIQUE.md`** in the root directory. Use this structured format:
     - # Critique of Previous Agent's Work
     - ## Executive Summary
     - ## Critical Flaws in Execution (Specify the "Root Problem" for each)
     - ## What They Got Right
     - ## Actionable Next Steps for the Coding Agent
  3. **Update Long-Term Memory**: Extract the *root causes* of the mistakes and append them as generalized, project-wide rules inside `.devin/rules/lessons-learned.md`. Do not overwrite the whole file; append new lessons to the end.
  4. **CRITICAL APPEND-ONLY RULE**: When adding new lessons to lessons-learned.md, you MUST append them to the END of the file. NEVER insert lessons in the middle or renumber existing lessons. The file has a File Maintenance Rule at the top that explicitly states this. Inserting in the middle requires renumbering all subsequent rules and creates maintenance burden. Always read the file to find the last rule number, then append with the next sequential number.
  5. **Only Agent with Write Permissions**: You are the only agent authorized to write to `.devin/rules/lessons-learned.md`.
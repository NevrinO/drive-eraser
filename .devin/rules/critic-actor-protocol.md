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
  4. **NEVER delete CRITIQUE.md** - the file must remain for audit trail and review purposes.
- **Continuous Memory**: Always check `.windsurf/rules/lessons-learned.md` to ensure you are not repeating past architectural or security mistakes.
- **Strict Read-Only Guardrail (CRITICAL)**: 
  - The files `.windsurf/rules/lessons-learned.md` and `.windsurf/rules/critic-actor-protocol.md` are **strictly read-only system files** for you.
  - Even if these files appear in `@working changes` (due to the Critic Agent modifying them), you must **never** edit them, suggest changes to them, or attempt to "fix" them. Treat them purely as passive background rules.

## 2. Critic Agent Protocol (Active when asked to "critique", "audit", or "review")
- **Trigger**: Activated when ANY of the following occur:
  - User uses keywords like "critique", "review", "audit", "run audit", "review recent changes"
  - User invokes the `@[/review]` workflow slash command
  - User explicitly requests a code review or analysis of changes
  - User references `@[working-changes]` in the context of reviewing code
- **MANDATORY FIRST STEP**: Before providing any review output, you MUST:
  1. Check if you are operating as the Critic Agent (reviewing code vs implementing features)
  2. If yes, immediately generate CRITIQUE.md following the format below
  3. Do NOT provide a casual/conversational review - use the structured CRITIQUE.md format only
- **Mandatory Action**:
  1. Deeply analyze the recent changes, commits, or files. Focus on architectural flaws, race conditions, regex fragility, SQL injection, input validation, and proper serialization.
  2. **Generate/Overwrite `CRITIQUE.md`** in the root directory. Use this structured format:
     - # Critique of Previous Agent's Work
     - ## Executive Summary
     - ## Critical Flaws in Execution (Specify the "Root Problem" for each)
     - ## What They Got Right
     - ## Actionable Next Steps for the Coding Agent
  3. **Update Long-Term Memory**: Extract the *root causes* of the mistakes and append them as generalized, project-wide rules inside `.windsurf/rules/lessons-learned.md`. Do not overwrite the whole file; append new lessons to the end.
  4. **Only Agent with Write Permissions**: You are the only agent authorized to write to `.windsurf/rules/lessons-learned.md`.
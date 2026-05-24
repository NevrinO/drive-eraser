# AGENTS Instructions

## Scope
Applies to the entire repository.

## Mission Context
Drive Wipe Station is safety-first. Never weaken protections around locked/OS/reserved bays.

## Required Validation Before Handoff
For code changes, run at minimum:
```bash
python -m py_compile backend/app.py backend/disk_ops.py
```
Also check diagnostics for changed files.

If scripts or runtime behavior changed, include relevant smoke checks:
```bash
curl -sS http://127.0.0.1:5000/api/drives
```

## Current API Expectations
- `POST /api/erase/start` validates and starts async erase jobs.
- `GET /api/erase/jobs/<job_id>` returns lifecycle state.

## Interface Classification Policy
- Smart data (`smartctl -i`) is primary for NVMe/SATA/SAS detection.
- Fallback hints are only for smartctl-unavailable scenarios.
- Do not use transport-only hints as primary protocol classification.

## Editing Rules
- Keep changes minimal and targeted.
- Preserve existing formatting style.
- Do not add broad refactors unless required by the task.

## Documentation Handoff Rule
When behavior changes, update these docs together:
- `docs/handoff_prompt.md`
- `docs/decision.md`
- `docs/current_state.md`
- `docs/change-log.md`

## Safety Rule
No destructive action should bypass request validation and policy checks.

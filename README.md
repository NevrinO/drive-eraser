# Drive Eraser

Local wipe-station software for enterprise SATA, SAS, and U.2 NVMe drives.

## Current Scope
- Local KVM-accessed web UI
- Protected OS and reserved bays
- Method-aware wipe workflow
- Verification-aware erase logic
- Certificate generation
- Rebuildable Ubuntu deployment

## Repo Structure
- `backend/` application backend
- `frontend/` UI assets
- `config/` bay mapping and policy
- `scripts/` install/update/start scripts
- `systemd/` service file
- `docs/` project documentation

## First Setup
Run:

```bash
bash scripts/install.sh

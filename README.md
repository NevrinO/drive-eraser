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
```

## Getting Started

After installation, access the web UI by opening your browser to:
```
http://<server-ip>:5000
```

The default port is 5000 (configurable during install). If accessing from a remote network, you'll need to enter the LAN passphrase configured during installation.

### First-Time Configuration
1. **Configure Bay Mapping**: Navigate to the "System Administration" tab (Tab 3) to map physical drive bays to their device paths. Use the "Auto-Detect" feature or manually assign bay mappings.
2. **Verify Setup**: Insert a test drive and confirm it appears in the "Active Workbench" tab with correct identification.
3. **Perform Test Wipe**: Follow the standard workflow to perform your first test wipe and verify certificate generation.

### Documentation
For detailed operational guidance, see:
- **[Technician SOP](docs/SOP_technician_guide.md)** - Step-by-step workflow for health checking and erasing drives
- **[Troubleshooting](docs/troubleshooting.md)** - Common issues and solutions
- **[Runbook](docs/runbook.md)** - Operational commands and procedures

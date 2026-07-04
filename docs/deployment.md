# Deployment Guide

This guide covers all deployment scenarios: fresh installation, GitHub release creation, post-deploy validation, updating existing installations, and rollback procedures.

**Related docs**:
- [operations.md](operations.md) — Service operations and troubleshooting
- [admin-guide.md](admin-guide.md) — Admin panel configuration
- [enclosure-mapping-guide.md](enclosure-mapping-guide.md) — Bay mapping setup

---

## 1. Fresh Install

Estimated time: 15-20 minutes.

### Step 1: Install Ubuntu

Install Ubuntu Server or Desktop (minimal install is fine). Ensure the machine has internet access during setup.

### Step 2: Install Git

```bash
sudo apt update
sudo apt install -y git
```

### Step 3: Clone the Repository

```bash
git clone https://github.com/NevrinO/drive-eraser.git
cd drive-eraser
```

### Step 4: Run the Install Script

```bash
sudo bash scripts/install.sh
```

The script will:
- Install all system dependencies (smartmontools, nvme-cli, sg3-utils, hdparm, etc.)
- Create the `wipestation` application user
- Set up Python virtual environment with all dependencies
- Configure sudo rules for controlled disk command access
- Generate `config/policy.json` with interactive prompts (station ID, port, passphrase, Slack webhook)
- Create default `config/bay_map.json` and `config/layout_templates.json`
- Install and start the systemd service with dynamic resource limits (memory, CPU, file descriptors)

### Step 5: Configure Bay Mapping

After install, configure the bay map to match this server's physical layout. The system supports flexible bay configurations (1-128 bays) through the web UI or by editing the configuration file directly.

**Option A: Use the Web UI (Recommended)**
1. Navigate to http://localhost:5000
2. Go to the System Administration tab (Tab 4)
3. Use the Enclosure Management section to create enclosures and map slots
4. Or use the Interactive Bay Mapping section to auto-detect drives or manually map bays
5. Click Save Mapping Configuration to apply changes

**Option B: Edit Configuration File Manually**

```bash
sudo nano /opt/drive-eraser/config/bay_map.json
```

For each bay, set the correct `by_path` value. To find the correct paths:

```bash
ls -la /dev/disk/by-path/
```

Match each path to its physical bay slot. See [enclosure-mapping-guide.md](enclosure-mapping-guide.md) for detailed instructions.

### Step 6: Verify

Open the browser and navigate to http://localhost:5000. You should see the Drive Wipe Station dashboard.

### Step 7: Check Service Status

```bash
systemctl status drive-eraser
```

If there are issues:

```bash
journalctl -u drive-eraser -f
```

### Important Files

| File | Purpose |
|------|---------|
| `config/bay_map.json` | Maps bays to physical drives |
| `config/policy.json` | Wipe behavior and operational policy |
| `config/layout_templates.json` | Physical bay layout templates |
| `config/command_paths.json` | Resolved paths to system utilities |
| `data/wipes.db` | Wipe history database |
| `data/certs/` | Generated certificates |
| `data/logs/` | Application logs |

### Notes

- The system supports flexible bay configurations from 1 to 128 bays
- After a fresh OS install, `bay_map.json` must be reconfigured because `/dev/disk/by-path/` values may differ between servers
- Bay mapping can be configured through the web UI under the System Administration tab

---

## 2. GitHub Releases

### Prerequisites

- GitHub CLI (`gh`) installed on your development machine
- Authenticated with GitHub: `gh auth login`
- Git repository pushed to GitHub

### Build the Production Artifact

Run the build script from your project root (Windows/PowerShell):

```powershell
.\scripts\build-release.ps1 -Version "v1.0.0"
```

This creates `drive-eraser-v1.0.0.zip` in the project root, excluding:
- Development tools (.devin, .windsurf)
- Git repository (.git)
- Test files (tests/)
- Development-only documentation
- .gitkeep placeholder files

### Create the GitHub Release

```powershell
gh release create v1.0.0 drive-eraser-v1.0.0.zip --notes "Production release v1.0.0"
```

Replace the notes with your actual release notes describing changes.

### Deploy to Production Server

On your Ubuntu server:

```bash
# Download the release
wget https://github.com/YOUR_USERNAME/drive-eraser/releases/download/v1.0.0/drive-eraser-v1.0.0.zip

# Extract
unzip drive-eraser-v1.0.0.zip -d /opt/drive-eraser/

# Run installation
cd /opt/drive-eraser
sudo bash scripts/install.sh

# Start the service
sudo systemctl start drive-eraser
sudo systemctl enable drive-eraser
```

### Versioning

Use semantic versioning:
- `v1.0.0` — Initial production release
- `v1.0.1` — Bug fixes
- `v1.1.0` — New features (backward compatible)
- `v2.0.0` — Breaking changes

### Updating .productionignore

If you need to add or remove exclusions, edit `.productionignore` in the project root. Patterns use the same syntax as `.gitignore`:
- `folder/` — excludes a directory
- `*.log` — excludes all .log files
- `# comment` — comments are ignored

---

## 3. Validation Checklist

### Pre-Update

- Confirm maintenance window or operator readiness
- Confirm recent config backup exists
- Record current service status and running version context
- Confirm target repo branch/commit to deploy

### Post-Deploy Smoke Tests

1. Discovery API:
```bash
curl -sS http://127.0.0.1:5000/api/drives
```

2. Frontend load check in browser.

3. Erase validation + job creation check:
```bash
curl -sS -X POST http://127.0.0.1:5000/api/erase/start \
  -H 'Content-Type: application/json' \
  -d '{"technician":"release","ticket_number":"REL-1","bays":["bay2"],"confirmation_text":"erase BAY 2"}'
```

4. Job status check:
```bash
curl -sS http://127.0.0.1:5000/api/erase/jobs/<job_id>
```

### Protocol Validation Targets

- SATA behavior confirmed
- SAS behavior confirmed
- NVMe behavior confirmed when hardware available

### Sign-Off

- [ ] Service healthy
- [ ] API smoke checks pass
- [ ] Frontend smoke checks pass
- [ ] One erase job lifecycle validated
- [ ] Logs reviewed for critical errors

---

## 4. Updating

To pull the latest version on an existing installation:

```bash
cd /path/to/drive-eraser
sudo bash scripts/update.sh
```

The update script:
- Backs up config files (`bay_map.json`, `policy.json`, `command_paths.json`) to `backups/<timestamp>/`
- Syncs application files via rsync (preserves config, data, venv, logs)
- Re-resolves system command paths
- Updates Python dependencies
- Refreshes sudo rules
- Restarts the service

Your config files and wipe history will be preserved.

### Update Options

```bash
# Skip service restart (apply files only)
sudo bash scripts/update.sh --no-restart

# Dry run (print actions without making changes)
sudo bash scripts/update.sh --dry-run
```

---

## 5. Rollback

### Rollback Triggers

Rollback if any are true:
- Service fails to start reliably
- Discovery payload is invalid/unusable
- Erase jobs fail due to systemic command/permission issues
- Frontend cannot track jobs

### Rollback from GitHub Release

If a release has issues, deploy the previous version:

```bash
wget https://github.com/YOUR_USERNAME/drive-eraser/releases/download/v1.0.0/drive-eraser-v1.0.0.zip
unzip drive-eraser-v1.0.0.zip -d /opt/drive-eraser/
sudo systemctl restart drive-eraser
```

### Rollback from Git Update

1. Stop the service:
```bash
sudo systemctl stop drive-eraser
```

2. Restore previous known-good code snapshot (e.g., checkout previous commit):
```bash
cd ~/drive-eraser
git checkout <previous-commit>
sudo bash scripts/update.sh
```

3. Restore config backup if changed:
```bash
sudo cp /opt/drive-eraser/backups/<timestamp>/bay_map.json /opt/drive-eraser/config/
sudo cp /opt/drive-eraser/backups/<timestamp>/policy.json /opt/drive-eraser/config/
```

4. Start service and re-run smoke tests:
```bash
sudo systemctl start drive-eraser
curl -sS http://127.0.0.1:5000/api/drives
```

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

### From GitHub Release (Production)

Download the latest release from [GitHub Releases](https://github.com/NevrinO/drive-eraser/releases):

```bash
wget https://github.com/NevrinO/drive-eraser/releases/download/v1.1.0/drive-eraser-v1.1.0.zip
unzip drive-eraser-v1.1.0.zip -d /tmp/drive-eraser-v1.1.0
cd /tmp/drive-eraser-v1.1.0
sudo bash scripts/install.sh
rm -rf /tmp/drive-eraser-v1.1.0
```

### From Git Clone (Development/Testing)

```bash
git clone https://github.com/NevrinO/drive-eraser.git
cd drive-eraser
sudo bash scripts/install.sh
```

## Getting Started

After installation, access the web UI by opening your browser to:
```
http://<server-ip>:5000
```

The default port is 5000 (configurable during install). If accessing from a remote network, you'll need to enter the LAN passphrase configured during installation.

### First-Time Configuration
1. **Configure Bay Mapping**: Navigate to the "System Administration" tab (Tab 4) to map physical drive bays to their device paths. Use the "Auto-Detect" feature or manually assign bay mappings.
2. **Verify Setup**: Insert a test drive and confirm it appears in the "Active Workbench" tab with correct identification.
3. **Perform Test Wipe**: Follow the standard workflow to perform your first test wipe and verify certificate generation.

### Documentation
For detailed operational guidance, see:
- **[Technician SOP](docs/SOP_technician_guide.md)** - Step-by-step workflow for health checking and erasing drives
- **[Operations Guide](docs/operations.md)** - Service operations and troubleshooting
- **[Deployment Guide](docs/deployment.md)** - Installation, releases, validation, and rollback
- **[Admin Guide](docs/admin-guide.md)** - System Administration features guide
- **[Enclosure Mapping Guide](docs/enclosure-mapping-guide.md)** - Enclosure setup and slot configuration
- **[API Contract](docs/api-contract.md)** - Complete API endpoint documentation
- **[Architecture](docs/ARCHITECTURE.md)** - Architectural decisions and design rationale
- **[Lifecycle Documentation](docs/lifecycle.md)** - Job state transitions and workflow
- **[Change Log](docs/change-log.md)** - Version history and changes
- **[Test Plan](docs/test-plan.md)** - Manual testing procedures

## Testing

### Running Automated Tests

The project includes automated unit tests for critical safety functions:

1. **Install test dependencies:**
   ```bash
   bash scripts/tests-install.sh
   ```

   Note: If you encounter permission errors, you may need to make the scripts executable first:
   ```bash
   chmod +x scripts/tests-install.sh scripts/tests-run.sh
   ```

2. **Run the test suite:**
   ```bash
   bash scripts/tests-run.sh
   ```

The test suite includes 30+ Python test modules and 4 JavaScript test modules covering:
- SQL injection prevention (`test_database.py`)
- Device path validation security (`test_disk_utils.py`)
- OS drive detection and discovery (`test_disk_ops.py`, `test_device_discovery.py`)
- Erase command preparation safety (`test_job_management.py`)
- API endpoint integration (`test_api_routes.py`, `test_drive_routes.py`)
- SMART health scoring and parsing (`test_smart_parsing.py`, `test_smart_health_scoring.py`)
- SMART self-test runner (`test_smart_test_runner.py`)
- Crypto verification and zero-check (`test_crypto_verification.py`, `test_zero_check_manager.py`)
- Enclosure slot mapping (`test_enclosure_slot_mappings.py`, `test_physical_slot_mapping.py`)
- Certificate routes and bulk cert (`test_certificate_routes.py`, `test_bulk_cert.py`)
- Layout templates (`test_layout_templates.py`, `test_template_routes.py`)
- End-to-end workflows (`test_e2e_workflows.py`)
- Frontend discovery validation (`test_discovery_validation.js`, `test_discovery_mapping.js`)
- Frontend SMART deep dive (`test_smart_deep_dive.js`)

Test coverage reports are generated in the `htmlcov/` directory after running the test suite.

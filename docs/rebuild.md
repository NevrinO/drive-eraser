# Rebuild Instructions

This guide allows anyone to rebuild the Drive Wipe Station from scratch.
Estimated time: 15-20 minutes.

---

## Step 1 - Install Ubuntu

Install Ubuntu Server or Desktop (minimal install is fine).
Ensure the machine has internet access during setup.

---

## Step 2 - Install Git

Open a terminal and run:

    sudo apt update
    sudo apt install -y git

---

## Step 3 - Clone the Repository

    git clone https://github.com/NevrinO/drive-eraser.git
    cd drive-eraser

---

## Step 4 - Run the Install Script

    sudo bash scripts/install.sh

The script will:
- install all system dependencies
- create the application user
- set up Python environment
- configure sudo rules for disk commands
- install and start the systemd service

---

## Step 5 - Configure Bay Mapping

After install, edit the bay map to match this server's physical layout:

    sudo nano /opt/drive-eraser/config/bay_map.json

For each bay, set the correct by_path value.
To find the correct paths, run:

    ls -la /dev/disk/by-path/

Match each path to its physical bay slot.

---

## Step 6 - Verify

Open the browser and navigate to:

    http://localhost:5000

You should see the Drive Wipe Station dashboard.

---

## Step 7 - Check Service Status

    systemctl status drive-eraser

If there are issues:

    journalctl -u drive-eraser -f

---

## Updating the Software

To pull the latest version at any time:

    cd /path/to/drive-eraser
    sudo bash scripts/update.sh

Your config files and wipe history will be preserved.

---

## Important Files

| File | Purpose |
| :--- | :--- |
| config/bay_map.json | Maps bays to physical drives |
| config/policy.json | Wipe behavior and defaults |
| data/wipes.db | Wipe history database |
| data/certs/ | Generated certificates |
| logs/ | Application logs |

---

## Notes

- Bay 1 is always the OS drive and is locked from wiping.
- Bay 2 is reserved and locked.
- After a fresh OS install, bay_map.json must be reconfigured
  because /dev/disk/by-path/ values may differ between servers.

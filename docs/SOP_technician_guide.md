# 🛑 Drive Wipe Station: Technician SOP

This guide covers the standard process for health checking and securely erasing drives using the Wipe Station.

---

## 🛠️ Quick Status Guide (What the colors mean)

| Color | Dashboard Status | Action Needed |
| :--- | :--- | :--- |
| **BLUE** | `VIEW ONLY` (e.g., Bay 1) | **OS Drive.** Do not attempt to wipe. |
| **WHITE** | `EMPTY` | Ready for drive insertion. |
| **GREY** | `IDENTIFIED / READY` | Drive is safe. Review health and wipe method. |
| **YELLOW** | `REJECTED / WARNING` | **High Risk.** Station recommends physical destruction. |
| **GREEN** | `COMPLETE / VERIFIED` | **Success.** Drive is wiped, verified, and certified. |
| **RED** | `FAILED` | **Error.** Erase command or verification failed. |

---

## 📋 Standard Workflow

### 1. Insertion & Identification
- Insert the drive into an available hot-swap bay.
- The station will automatically detect the drive and display its **Serial Number** and **Interface Type** (SATA, SAS, or NVMe).
- **Check the Serial:** Ensure the serial on the screen matches the physical label on the drive.

### 2. Inspection & Health Check
- Click the drive tile to view the detailed health panel.
- Review the SMART metrics (Temperature, Sector counts, Wear level).
- **If the status is Yellow/Rejected:** The station recommends against software wiping. Pull the drive for physical destruction unless you have a specific reason to override.

### 3. Setup the Wipe
- **Single Drive:** Click the drive tile to view details and configure wipe settings.
- **Batch Wiping:** Enable "Sanitize Mode" toggle to select multiple bays simultaneously, then click "Configure Sanitization".
- The station will **auto-select** the best wipe method (e.g., Crypto Erase for NVMe).
- **Method Override:** Use the dropdown to choose a different method only if the primary one is unavailable or required by a specific project.
- **Pre-Wipe Check:** The station will perform a quick read check to confirm data is present before starting (Default: ON).

### 4. Confirmation
- Enter your **Technician Name**.
- Enter the **Ticket Number**.
- Type the confirmation text shown (e.g., "erase 1 drive" or "erase 3 drives") into the confirmation box.
- Click **Validate & Execute Sanitization**.

### 5. Erasing & Monitoring
- Do not remove the drive while the status is `ERASING`.
- Monitor the progress bar or status messages.
- If the wipe command fails (Red), the drive may have a hardware fault.

### 6. Verification
- Once the erase finishes, the station will automatically **Verify** the results.
- **Crypto Erase:** The system checks controller sanitize logs and performs the configured post-erase probe; it does not claim zero-filled media.
- **Block/SATA Erase:** The system samples sectors to ensure they are blank (zeros).
- **Note:** If a Crypto Erase fails verification, the station will prompt you to **"Retry with Block Erase."**

### 7. Marking & Completion
- After verification passes, a small supplemental **Erase Marker** is written to the start of the disk. This allows future technicians to see that *this* station wiped the drive safely.
- The status will turn **GREEN** when finished.
- Download or print the **Data Destruction Certificate** from the "Compliance Audit Vault" tab (Tab 2).

---

## ⚠️ Safety & Override Policies

- **Protected Bays:** You cannot click or wipe the OS drive or any bay marked as locked/reserved.
- **Manual Overrides:** You may override a "Recommended Destruction" (Soft Stop) if the drive is stable enough for a wipe, but this will be noted in the audit log.
- **Marker Errors:** The "Erase Marker" is supplemental station evidence, not a NIST/DoD requirement. If marker writing fails but wipe verification passed, the certificate may still complete with a marker warning.

## 📚 Getting Help

- **In-App Help:** Click the **Help** button in the header for quick access to documentation and common tasks.
- **Bay Mapping:** Configure physical bay mappings via the **System Administration** tab (Tab 3) - use "Auto-Detect" or manual assignment.
- **Full Documentation:** Access detailed guides via the Help modal or directly in the `/docs/` folder.

---

## 🛑 Failure Handling

- **Verification Failed (Red):** Do not trust the wipe. Retry with a different method (like Block Overwrite) or destroy the drive physically.
- **Drive Disappeared:** If the drive disconnects mid-wipe, the task will be marked as `FAILED`. Reseat the drive and start the process over.
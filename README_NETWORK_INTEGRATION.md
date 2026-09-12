# WALVOIL — IoT Viscosity System: Network Share & NAS Integration Guide
**Document Purpose:** Blueprint and step-by-step instructions for automatically backing up generated Excel & PDF reports to a Company Network Shared Folder / NAS / Server.

---

## 1. Architecture Overview

```
   +-----------------------------------------------------------+
   |   Walvoil IoT Viscometer Workstation (Windows / Linux)    |
   |                                                           |
   |   1. Operator submits test -> app.py                      |
   |   2. Generate Excel (.xlsx) & PDF (.pdf)                  |
   |   3. Save to Local Hierarchical Archive (Always 1st)      |
   |   4. Async / Safe Copy to Central Network Server / NAS    |
   +-----------------------------+-----------------------------+
                                 |
                                 | (LAN / Wi-Fi / VPN)
                                 v
   +-----------------------------------------------------------+
   |        Company Central Storage / Quality NAS Server       |
   |                                                           |
   |   UNC: \\192.168.1.X\Walvoil_Quality_Reports              |
   |   Mount: /mnt/walvoil_nas/Quality_Reports                 |
   |   Accessible by: Quality Assurance, Plant Head, Auditing  |
   +-----------------------------------------------------------+
```

---

## 2. Configuration Options

Create or update a `.env` file or configuration dictionary in `app.py`:

```python
# Network Storage Configuration
ENABLE_NETWORK_BACKUP = True

# Windows UNC Path (or Mapped Drive Z:\...)
NETWORK_BACKUP_PATH_WINDOWS = r"\\192.168.1.50\Walvoil_Quality_Reports"

# Linux Mounted Path (CIFS/NFS mounted volume)
NETWORK_BACKUP_PATH_LINUX = "/mnt/walvoil_nas/Quality_Reports"
```

---

## 3. Ready-to-Use Python Code for `app.py`

Add this utility function into `app.py` and invoke it right after local reports are saved:

```python
import shutil
import platform
import threading

def copy_to_network_share_async(local_excel_path: str, local_pdf_path: str, relative_subpath: str):
    """
    Safely copies generated Excel and PDF reports to the central network share
    in a background thread so it never delays the local operator UI.
    """
    def _worker():
        try:
            # 1. Resolve network target root
            if platform.system().lower() == "windows":
                network_root = r"\\192.168.1.50\Walvoil_Quality_Reports"
            else:
                network_root = "/mnt/walvoil_nas/Quality_Reports"

            # Check if network destination is reachable
            if not os.path.exists(network_root):
                print(f"[NETWORK BACKUP] Network path unreachable: {network_root}")
                return

            # 2. Mirror identical directory hierarchy on network drive
            dest_folder = os.path.join(network_root, relative_subpath)
            excel_dest_dir = os.path.join(dest_folder, "Excel Report")
            pdf_dest_dir   = os.path.join(dest_folder, "Pdf Report")

            os.makedirs(excel_dest_dir, exist_ok=True)
            os.makedirs(pdf_dest_dir, exist_ok=True)

            # 3. Copy Excel (.xlsx)
            if os.path.exists(local_excel_path):
                dest_excel = os.path.join(excel_dest_dir, os.path.basename(local_excel_path))
                shutil.copy2(local_excel_path, dest_excel)

            # 4. Copy PDF (.pdf)
            if os.path.exists(local_pdf_path):
                dest_pdf = os.path.join(pdf_dest_dir, os.path.basename(local_pdf_path))
                shutil.copy2(local_pdf_path, dest_pdf)

            print(f"[NETWORK BACKUP SUCCESS] Synced to: {dest_folder}")

        except Exception as err:
            # Never block or crash local testing on network failure
            print(f"[NETWORK BACKUP ERROR] Failed to sync to network share: {err}")

    # Run in background daemon thread
    threading.Thread(target=_worker, daemon=True, name="network-backup-worker").start()
```

### Hooking into `generate_and_save_report()`:
In `app.py`, right after `wb.save(excel_path)` and PDF generation:
```python
    # Calculate relative folder structure:
    # e.g.: Reports 2026/09_September/12_09_2026/Primer Coat/Test Pass/
    rel_subpath = os.path.join(year_folder, month_folder, day_folder, template_folder, status_folder)

    # Trigger background backup to network share
    copy_to_network_share_async(excel_path, pdf_path, rel_subpath)
```

---

## 4. Operating System Setup Instructions

### A. Windows Workstation Setup

1. **Option 1: Direct UNC Path (Recommended)**
   - No drive letter mapping required.
   - The Windows account running the service must have Read/Write permissions to `\\192.168.1.50\Walvoil_Quality_Reports`.

2. **Option 2: Map Network Drive (Persistent)**
   - Open Command Prompt:
     ```cmd
     net use Z: \\192.168.1.50\Walvoil_Quality_Reports /persistent:yes /user:DOMAIN\Username Password
     ```
   - In `app.py`, set:
     ```python
     NETWORK_BACKUP_PATH_WINDOWS = r"Z:\"
     ```

---

### B. Linux (Ubuntu / Debian / Raspberry Pi) Setup

On Linux, network shares are mounted as standard file directories using CIFS/Samba.

1. **Install CIFS Utilities**:
   ```bash
   sudo apt update
   sudo apt install -y cifs-utils
   ```

2. **Create Local Mount Point**:
   ```bash
   sudo mkdir -p /mnt/walvoil_nas/Quality_Reports
   ```

3. **Store Credentials Securely**:
   Create `/etc/walvoil-nas-credentials`:
   ```ini
   username=nas_quality_user
   password=YourStrongPassword
   domain=WALVOIL
   ```
   Secure permissions:
   ```bash
   sudo chmod 600 /etc/walvoil-nas-credentials
   ```

4. **Add to `/etc/fstab` for Auto-Mount on Boot**:
   ```ini
   //192.168.1.50/Walvoil_Quality_Reports /mnt/walvoil_nas/Quality_Reports cifs credentials=/etc/walvoil-nas-credentials,iocharset=utf8,file_mode=0777,dir_mode=0777,nofail 0 0
   ```

5. **Mount & Verify**:
   ```bash
   sudo mount -a
   ls -la /mnt/walvoil_nas/Quality_Reports
   ```

---

## 5. Resilience & Quality Best Practices

1. **Local-First Architecture:**
   - Reports are **always** written to the local disk and SQLite database first.
   - If the corporate network or NAS goes down, testing continues on the shop floor without interruption.

2. **Non-Blocking Background Thread:**
   - Network copying is performed asynchronously in a daemon thread. Operators never experience UI lag or timeouts waiting for network latency.

3. **Hierarchy Mirroring:**
   - The network folder retains the exact same structure (`Reports <Year>/<Month>/<Day>/<Process>/<Status>/...`) as the local workstation for seamless ISO/audit traceability.

4. **Retry Queue (Optional Extension):**
   - If the network is offline during a test, an offline sync routine can check for un-synced tests once the connection is restored.

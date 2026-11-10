# 🐧 Walvoil IoT Viscosity System — Linux Server Setup Guide (Bare Metal / VM)

This guide provides end-to-end instructions for deploying the **Walvoil IoT Viscosity Measurement & Reporting System** directly onto a Linux Server (Ubuntu 20.04/22.04/24.04 LTS, Debian 11/12, or equivalent) without Docker.

---

## 🏗️ Architecture Overview on Linux

When deployed on a Linux server:

1. **WSGI Server**: **Gunicorn** runs with `1 Worker` and `8 Threads` (`-w 1 --threads 8`).
2. **Telemetry Receiver**: A background thread binds **UDP port 63363** to auto-detect incoming ESP32 station packets.
3. **PDF Generator**: Headless **LibreOffice Calc** converts populated Excel templates (`.xlsx`) to pixel-perfect PDFs (`.pdf`) using system fonts (`fonts-liberation`, `fonts-dejavu-core`).
4. **Audit Trail**: High-concurrency **SQLite in WAL (Write-Ahead Logging) mode** stores telemetry logs and test reports with 0 lock contention.
5. **Daemon Management**: Managed 24/7 by **systemd** with automatic restart on reboot or error.

---

## ⚡ Option 1: 1-Click Automated Setup (Recommended)

We provide a self-contained automation script that installs all packages, sets up Python, creates the systemd service, configures the firewall, and starts the system immediately.

```bash
# Navigate to the project root directory on the server
cd /path/to/viscocity_walvoil

# Run the installer with sudo
sudo bash linux_server_setup/install.sh
```

The script will:
- Install Python, LibreOffice, Liberation fonts, and network utilities.
- Create persistent `Reports/` and `logs/` directories with proper permissions.
- Create a Python virtual environment (`venv`) and install `requirements.txt`.
- Generate and register `/etc/systemd/system/walvoil-viscosity.service`.
- Enable and start the service automatically.
- Open firewall ports `5001/tcp` and `63363/udp`.

---

## 🛠️ Option 2: Manual Step-by-Step Installation

If you prefer installing step-by-step or need custom settings:

### Step 1: Install System Packages
```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv libreoffice-calc fonts-liberation fonts-dejavu-core psmisc curl ufw
```

> [!NOTE]
> - `libreoffice-calc`: Required for headless Excel-to-PDF report generation.
> - `fonts-liberation` & `fonts-dejavu-core`: Ensures the generated PDF matches Excel column widths and styling pixel-for-pixel.
> - `psmisc`: Provides `fuser`, used by `app.py` to auto-clean orphaned UDP port locks.

### Step 2: Set Up Python Virtual Environment & Dependencies
```bash
# Navigate to project folder
cd /home/ubuntu/viscocity_walvoil

# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip and install pinned dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

*(On Linux, `requirements.txt` automatically installs `gunicorn` and skips Windows-only `pywin32`)*.

### Step 3: Run Interactively with Gunicorn (Testing)
```bash
gunicorn -w 1 --threads 8 -b 0.0.0.0:5001 app:app
```

> [!IMPORTANT]
> **Why `-w 1` (1 Worker)?**
> Because UDP port `63363` is bound directly by the application to listen for ESP32 hardware packets, only **one operating system process** can hold this socket at a time.
> Using `-w 1 --threads 8` provides **8 concurrent worker threads** to handle web dashboard traffic while maintaining a single, stable UDP receiver.

---

## 🔄 24/7 Background Service Setup (systemd)

To make the service start automatically on boot and restart if crashed:

### 1. Create the systemd Service File
Copy the provided unit template to systemd:
```bash
sudo cp linux_server_setup/walvoil-viscosity.service /etc/systemd/system/walvoil-viscosity.service
```

Or edit `/etc/systemd/system/walvoil-viscosity.service`:
```ini
[Unit]
Description=Walvoil IoT Viscosity Measurement & Reporting Service
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/viscocity_walvoil
Environment="PATH=/home/ubuntu/viscocity_walvoil/venv/bin:/usr/local/bin:/usr/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="FLASK_DEBUG=0"
ExecStart=/home/ubuntu/viscocity_walvoil/venv/bin/gunicorn -w 1 --threads 8 -b 0.0.0.0:5001 --access-logfile - --error-logfile - app:app
Restart=always
RestartSec=5
KillSignal=SIGQUIT
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

*(Replace `/home/ubuntu/viscocity_walvoil` and `User=ubuntu` with your actual server path and username)*.

### 2. Enable and Start the Service
```bash
sudo systemctl daemon-reload
sudo systemctl enable walvoil-viscosity
sudo systemctl start walvoil-viscosity
```

### 3. Verify Status
```bash
sudo systemctl status walvoil-viscosity --no-pager
```

---

## 🛡️ Firewall (UFW) Configuration

Allow inbound traffic for the web interface and the ESP32 viscometer station:

```bash
# Allow web dashboard (TCP)
sudo ufw allow 5001/tcp comment "Walvoil Viscosity Web Dashboard"

# Allow IoT viscometer telemetry stream (UDP)
sudo ufw allow 63363/udp comment "Walvoil IoT Viscometer UDP Telemetry"

# Reload and check status
sudo ufw status verbose
```
*(Or simply run: `sudo bash linux_server_setup/ufw_firewall.sh`)*.

---

## 📁 Storage & Directory Hierarchy

Generated reports are permanently saved in the project's `Reports` directory on the server disk:

```
viscocity_walvoil/
├── Reports/
│   └── Reports 2026/
│       └── November/
│           └── 10_November_2026/
│               ├── Primer Coat/
│               │   ├── Test Pass/
│               │   │   ├── Excel Report/
│               │   │   │   └── 1021F0001_BATCHA_BATCHB.xlsx
│               │   │   └── Pdf Report/
│               │   │       └── 1021F0001_BATCHA_BATCHB.pdf
│               │   └── Test Fail/
│               └── Paint Top Coat/
│                   ├── Test Pass/
│                   └── Test Fail/
├── logs/
│   └── app.log                  # Industrial rotating log (10MB x 5 backups)
└── device_data.db               # SQLite audit database (WAL mode)
```

Ensure permissions remain writable by the service user:
```bash
sudo chown -R ubuntu:ubuntu Reports/ logs/ device_data.db
chmod -R 775 Reports/ logs/
```

---

## 🕹️ Service Management Helper Scripts

The `linux_server_setup/` folder contains handy shortcuts for managing the system:

| Helper Script | Command | Action |
| :--- | :--- | :--- |
| **Check Status** | `bash linux_server_setup/status.sh` | Checks systemd service status, port 5001, port 63363, and active Gunicorn process |
| **View Live Logs** | `bash linux_server_setup/view_logs.sh` | Streams real-time systemd and application logs (`journalctl -f`) |
| **Restart Service** | `bash linux_server_setup/restart.sh` | Restarts the application (run after code changes or updates) |
| **Stop Service** | `bash linux_server_setup/stop.sh` | Gracefully stops the service |
| **Start Service** | `bash linux_server_setup/start.sh` | Starts the service |
| **Configure Firewall** | `bash linux_server_setup/ufw_firewall.sh` | Adds required firewall rules |

---

## 🔄 Updating Code on the Server

When you push new changes or code updates:

```bash
cd /home/ubuntu/viscocity_walvoil

# Pull latest code
git pull origin main

# If requirements.txt changed, update venv
./venv/bin/pip install -r requirements.txt

# Restart service
bash linux_server_setup/restart.sh
```

---

## ❓ Troubleshooting FAQ

### 1. Dashboard shows "STATION OFFLINE"?
- Run `sudo bash linux_server_setup/status.sh` to confirm port `63363 (UDP)` is active.
- Verify the ESP32 firmware has `SERVER_IP` configured to your Linux server's IP address.
- Verify your network or cloud firewall (e.g. AWS Security Group / Azure NSG / DigitalOcean firewall) allows inbound **UDP port 63363**.

### 2. PDF report font looks misaligned?
- Verify Liberation and DejaVu fonts are installed:
  ```bash
  sudo apt install -y fonts-liberation fonts-dejavu-core
  fc-cache -f -v
  ```

### 3. Port 63363 Address Already in Use?
- `app.py` has an automated process cleanup function that frees port 63363.
- If manually stuck:
  ```bash
  sudo fuser -k 63363/udp
  sudo bash linux_server_setup/restart.sh
  ```


# 🐧 Option 2: Manual Linux Server Setup Guide (Bare Metal / VM)

This document provides the exact step-by-step commands to manually set up, run, and configure the **Walvoil IoT Viscosity Measurement & Reporting System** as a 24/7 background systemd service on an Ubuntu or Debian Linux server.

---

## 📋 Step 1: Install System Packages

Update the package index and install required system utilities, Python tools, and LibreOffice for headless PDF report rendering:

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv libreoffice-calc fonts-liberation fonts-dejavu-core psmisc curl ufw
```

### Why these packages are required:
- `python3-venv` & `python3-pip`: Manages isolated Python dependencies.
- `libreoffice-calc`: Generates pixel-perfect PDF reports from populated `.xlsx` templates on Linux without Microsoft Excel.
- `fonts-liberation` & `fonts-dejavu-core`: Ensures crisp typography and table column alignment in exported PDFs.
- `psmisc`: Provides network and process diagnostic tools (`fuser`, `killall`).
- `curl` & `ufw`: Used for API health checks and firewall management.

---

## 🐍 Step 2: Set Up Python Virtual Environment

Navigate to the project root directory, create a virtual environment, activate it, and install all pinned application packages:

```bash
# Navigate to the project root directory
cd /home/ubuntu/viscocity_walvoil

# Create an isolated virtual environment named 'venv'
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Upgrade pip to the latest version
pip install --upgrade pip

# Install project dependencies (Flask, Gunicorn, openpyxl, Pillow, etc.)
pip install -r requirements.txt
```

> [!NOTE]
> On Linux, `requirements.txt` automatically installs `gunicorn==23.0.0` as the production WSGI server and skips Windows-only `pywin32` and `waitress`.

---

## 🧪 Step 3: Test with Gunicorn (Interactive Verification)

Before running the app as a permanent background service, verify that the application and UDP listener start cleanly:

```bash
gunicorn -w 1 --threads 8 -b 0.0.0.0:5001 app:app
```

### Expected Output:
```
[INFO] WALVOIL UDP TELEMETRY SERVER BOUND ON 0.0.0.0:63363
[INFO] [STARTUP] Background UDP listener and offline watchdog initialized.
[INFO] Listening at: http://0.0.0.0:5001
[INFO] Using worker: gthread
[INFO] Booting worker with pid: 12345
```

> [!IMPORTANT]
> **Why `-w 1 --threads 8` (1 Worker, 8 Threads)?**
> Because **UDP port 63363** is bound by the server to listen for ESP32 viscometer packets, only **one operating system process** can hold this socket at a time.
> - Setting `-w 1` guarantees that exactly one process binds the UDP port, avoiding socket collisions (`Address already in use`).
> - Setting `--threads 8` provides **8 concurrent worker threads** to handle web dashboard traffic, report generation, and data downloads in parallel.

Press `Ctrl+C` to stop the interactive test once confirmed.

---

## ⚙️ Step 4: Register & Start Systemd Service (24/7 Background Operation)

To have the application start automatically on server boot, restart on failure, and run continuously in the background:

### Option 4A: Run the Auto-Registration Script
```bash
sudo bash linux_server_setup/register_service.sh
```

### Option 4B: Run the Commands Manually
1. Copy the systemd service unit to the system directory:
   ```bash
   sudo cp linux_server_setup/walvoil-viscosity.service /etc/systemd/system/walvoil-viscosity.service
   ```

2. *(Optional)* Verify the user and working directory in the service file:
   ```bash
   sudo nano /etc/systemd/system/walvoil-viscosity.service
   ```
   *Make sure `User=ubuntu` and `WorkingDirectory=/home/ubuntu/viscocity_walvoil` match your server's path.*

3. Reload the systemd daemon:
   ```bash
   sudo systemctl daemon-reload
   ```

4. Enable the service to launch on server boot:
   ```bash
   sudo systemctl enable walvoil-viscosity
   ```

5. Start the service:
   ```bash
   sudo systemctl start walvoil-viscosity
   ```

---

## 🔍 Verification & Health Check

Verify that the service is running and ports are active:

```bash
# Check service status
sudo systemctl status walvoil-viscosity --no-pager

# Check listening ports (5001 TCP for web, 63363 UDP for telemetry)
ss -tulpn | grep -E "5001|63363"

# Stream live application logs
sudo journalctl -u walvoil-viscosity -f
```

---

## 🛡️ Configure Firewall (Optional)

If `ufw` firewall is active, open the required ports:

```bash
sudo ufw allow 5001/tcp comment "Walvoil Viscosity Web Dashboard"
sudo ufw allow 63363/udp comment "Walvoil IoT Viscometer UDP Telemetry"
sudo ufw reload
```


# 🚀 RheoSync — Application Startup Guide (`startup_.md`)

This guide walks you through setting up, configuring, and starting the **Walvoil IoT Viscosity Testing & Report Generator** application.

---

## 📋 System Requirements & Network Ports

| Service | Protocol | Default Port | Description |
| :--- | :--- | :--- | :--- |
| **Web Dashboard** | TCP | `5001` | Flask Web UI, Reports Archive, Session Sync |
| **IoT Telemetry** | UDP | `63363` | Real-time ESP32 packet receiver & heartbeat monitor |
| **Python Version** | Python 3.10+ | — | Recommended environment |

---

## 🛠️ Step 1: Environment Setup & Dependencies

Open PowerShell or Command Prompt in the project folder:

```powershell
cd c:\Users\vakav\virtualpy\viscocity_walvoil
```

### 1. (Optional) Activate Virtual Environment
If you are using a Python virtual environment:
```powershell
# Windows PowerShell
.\venv\Scripts\Activate.ps1

# Or Command Prompt (cmd)
venv\Scripts\activate.bat
```

### 2. Install Required Python Packages
```powershell
pip install -r requirements.txt
```

> [!NOTE]
> For generating PDF reports:
> - **Windows**: Requires Microsoft Excel (via `win32com`) or [LibreOffice](https://www.libreoffice.org/) installed.
> - **Linux / Docker**: Uses headless `libreoffice` automatically.

---

## ▶️ Step 2: Start the Application

### Standard Startup (Development / Local Workstation)
Run the Flask server:
```powershell
python app.py
```

### Console Output Confirmation
When running successfully, the console will display:
```
======================================================================
  WALVOIL VISCOSITY UDP SERVER ACTIVE — PORT 63363 | 0.0.0.0
  Auto-detecting incoming Viscometer device packets...
======================================================================
 * Serving Flask app 'app'
 * Running on all addresses (0.0.0.0)
 * Running on http://127.0.0.1:5001
 * Running on http://<YOUR_LOCAL_IP>:5001
```

---

## 🌐 Step 3: Access the Web Dashboard

Open any web browser (Chrome, Edge, Firefox) and navigate to:

- **Local PC**: [`http://localhost:5001`](http://localhost:5001)
- **Same Network / Mobile Phone**: `http://<SERVER_IP>:5001` *(e.g. `http://192.168.0.152:5001`)*

### Key Pages:
- **Test & Generator**: `http://localhost:5001/`
- **Reports Archive**: `http://localhost:5001/reports`
- **System Benefits & Specs**: `http://localhost:5001/benefits`

---

## 📡 Step 4: Connecting the ESP32 Hardware

1. Open Arduino IDE or VS Code with PlatformIO.
2. Open either:
   - **Static IP Firmware**: [`Hardwarecodes/main.ino`](Hardwarecodes/main.ino)
   - **WiFiManager Portal Firmware**: [`Hardwarecodes/testing.ino`](Hardwarecodes/testing.ino)
3. Check network constants at the top of the sketch:
   ```cpp
   const char* SERVER_IP = "192.168.0.152"; // Your PC / Server running app.py
   const int   UDP_PORT  = 63363;           // Must match port 63363
   ```
4. Flash the code to the ESP32.
5. As soon as the ESP32 connects to Wi-Fi and sends its first heartbeat (`"120,PING"`), the dashboard banner automatically turns **Green (`STATION ONLINE`)**.

---

## 🧪 Step 5: Testing Without Hardware (Simulator)

You can simulate flow tests and live telemetry without physical hardware using the built-in simulator:

```powershell
# Continuous flow cycle simulator:
python debug/debug.py --host 127.0.0.1 --port 63363

# Interactive manual test (Press ENTER to start / stop flow):
python debug/debug.py --interactive --host 127.0.0.1 --port 63363

# Send an instant test measurement (e.g. 21.50 cSt):
python debug/debug.py --val 21.50 --host 127.0.0.1 --port 63363
```

---

## 🐳 Alternative: Running with Docker (Production)

To run the complete system in a container with background headless PDF rendering:

```bash
# Build and start container in background
docker-compose up -d --build

# View container logs
docker-compose logs -f

# Stop container
docker-compose down
```

---

## ❓ Troubleshooting & FAQs

### 1. "Address already in use" or UDP Port 63363 blocked?
`app.py` includes an automatic cleanup mechanism (`kill_port_owner`) that frees port 63363 on startup. If blocked manually:
```powershell
# Find process using UDP 63363
Get-NetUDPEndpoint -LocalPort 63363
```

### 2. Windows Firewall blocking ESP32 packets?
Allow inbound UDP traffic on port 63363:
```powershell
netsh advfirewall firewall add rule name="Walvoil UDP Telemetry" dir=in action=allow protocol=UDP localport=63363
```

### 3. Dashboard shows "STATION OFFLINE"?
- Ensure the ESP32 is on the **same local subnet** (e.g., `192.168.0.x`).
- Verify `SERVER_IP` in the `.ino` firmware points to the machine running `app.py`.
- If using `debug.py`, run with `--host 127.0.0.1 --port 63363`.


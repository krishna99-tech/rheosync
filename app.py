"""
╔══════════════════════════════════════════════════════════════════════╗
║   WALVOIL — IoT VISCOSITY MEASUREMENT & REPORTING SYSTEM             ║
║   Single Station Architecture & Automated Dual Excel/PDF Generator   ║
╠══════════════════════════════════════════════════════════════════════╣
║  FEATURES:                                                           ║
║  [1] Auto-detect UDP sender on port 63363 (Single Station)           ║
║  [2] Null-byte safe stream decode & noise filtering                  ║
║  [3] Real-time Viscosity Live Telemetry & Web Dashboard              ║
║  [4] Viscosity Report Generator with Viscosity report.xlsx template  ║
║  [5] Auto-calculation of Min/Max tolerance & Pass/Fail evaluation    ║
║  [6] Hierarchical Archive Structure (image1.png & image2.png)        ║
║      Reports <Year>/<Date>/<Test Pass | Test Fail>/                  ║
║      ├── Excel Report/<PartNumber>_<BatchA>_<BatchB>.xlsx            ║
║      └── Pdf Report/<PartNumber>_<BatchA>_<BatchB>.pdf               ║
║  [7] Native Excel COM PDF Export for pixel-perfect reports           ║
║  [8] Full SQLite Audit Trail (device_logs & test_reports)            ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import socket
import threading
import subprocess
import platform
import sqlite3
import logging
import time
import io
import csv
import re
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_file, send_from_directory
import openpyxl

try:
    import win32com.client
    import pythoncom
    HAS_WIN32COM = True
except ImportError:
    HAS_WIN32COM = False

# ================================================================
# PORT 63363 PROCESS KILLER (Windows / Linux clean bind)
# ================================================================
def kill_port_owner(port: int):
    own_pid = os.getpid()
    parent_pid = os.getppid() if hasattr(os, 'getppid') else None
    try:
        if platform.system().lower() == "windows":
            result = subprocess.check_output(
                "netstat -ano -p UDP", shell=True
            ).decode(errors="ignore")
            for line in result.strip().splitlines():
                parts = line.strip().split()
                # e.g.: UDP  0.0.0.0:63363  *:*  1234
                if len(parts) >= 4 and f":{port}" in parts[1]:
                    try:
                        pid = int(parts[-1])
                        if pid not in (own_pid, parent_pid, 0):
                            subprocess.call(
                                f"taskkill /F /PID {pid}",
                                shell=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL
                            )
                            print(f"[STARTUP] Cleaned orphaned process PID {pid} holding UDP port {port}")
                    except ValueError:
                        continue
        else:
            result = subprocess.check_output(
                ["fuser", f"{port}/udp"], stderr=subprocess.DEVNULL
            ).decode(errors="ignore")
            for pid_str in result.strip().split():
                try:
                    pid = int(pid_str)
                    if pid not in (own_pid, parent_pid, 0):
                        os.kill(pid, 9)
                        print(f"[STARTUP] Cleaned orphaned process PID {pid} holding port {port}")
                except ValueError:
                    continue
    except Exception:
        pass

# ================================================================
# FLASK APP SETUP
# ================================================================
app = Flask(__name__)
logging.getLogger('werkzeug').setLevel(logging.ERROR)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_raw_db  = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "device_data.db"))
if os.path.isdir(_raw_db):
    # If Docker mounted device_data.db as a directory, place SQLite file inside it
    DB_PATH = os.path.join(_raw_db, "device_data.db")
else:
    DB_PATH = _raw_db
TEMPLATE_EXCEL_PATH = os.path.join(BASE_DIR, "Viscosity report.xlsx")

# ================================================================
# DATABASE INITIALIZATION
# ================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS device_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT,
            sender_ip   TEXT,
            value       TEXT,
            ts          TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS test_reports (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            process_type     TEXT,
            part_number      TEXT,
            operator_id      TEXT,
            shift            TEXT,
            part_a_batch     TEXT,
            part_b_batch     TEXT,
            thinner_batch    TEXT,
            min_viscosity    REAL,
            max_viscosity    REAL,
            actual_viscosity REAL,
            result           TEXT,
            excel_path       TEXT,
            pdf_path         TEXT,
            created_at       TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_to_db(device_name, sender_ip, value, ts):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO device_logs (device_name, sender_ip, value, ts) VALUES (?, ?, ?, ?)",
            (device_name, sender_ip, value, ts)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] {e}")

init_db()

# ================================================================
# SINGLE STATION LIVE TELEMETRY STATE
# ================================================================
STATION_NAME = "Walvoil Viscosity Testing Station"
UDP_IP   = "0.0.0.0"
UDP_PORT = 63363
OFFLINE_TIMEOUT = 30  # Seconds without packets before marking offline

data_lock = threading.Lock()

station_data = {
    "name": STATION_NAME,
    "val": "0.00",
    "time": "--",
    "sender_ip": "Waiting...",
    "last_seen": 0,
    "online": False
}

# ================================================================
# MULTI-DEVICE SESSION SYNC & PRESENCE TRACKING
# ================================================================
active_clients = {}  # {client_id: {"ip": str, "device": str, "last_seen": float}}
clients_lock   = threading.Lock()

shared_session = {
    "operator": "venkat",
    "updated_by": None,
    "updated_at": time.time(),
    "form_data": {}
}
session_lock   = threading.Lock()

def register_client(cid: str, ip: str, device: str):
    now = time.time()
    with clients_lock:
        if cid:
            active_clients[cid] = {"ip": ip, "device": device, "last_seen": now}
        # Prune clients not seen in 8.0 seconds
        stale = [k for k, v in active_clients.items() if now - v["last_seen"] > 8.0]
        for k in stale:
            del active_clients[k]
        
        count = max(len(active_clients), 1)
        devices = [v["device"] for v in active_clients.values()] if active_clients else [device]
        return count, devices

# ================================================================
# PACKET DECODING
# ================================================================
def extract_value(raw_bytes: bytes) -> tuple:
    clean = raw_bytes.decode('ascii', errors='ignore').strip()
    device_id = "Unknown"
    
    # If the payload is in "DEVICE_ID,VALUE" format (e.g., "120,2.68"), grab the value after the comma
    if ',' in clean:
        parts = clean.split(',')
        device_id = parts[0].strip()
        clean = parts[-1].strip()
        
    m = re.search(r'\d+\.?\d*', clean)
    val = m.group() if m else clean.replace('\x00', '').strip()
    return device_id, val

# ================================================================
# UDP LISTENER THREAD (Auto-detects any incoming sender IP)
# ================================================================
def _udp_loop():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)
    sock.bind((UDP_IP, UDP_PORT))

    print("\n" + "="*70)
    print(f"  WALVOIL VISCOSITY UDP SERVER ACTIVE — PORT {UDP_PORT} | {UDP_IP}")
    print("  Auto-detecting incoming Viscometer device packets...")
    print("="*70 + "\n")

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            sender_ip  = addr[0]
            src_port   = addr[1]
            
            # --- DEBUG PRINT FOR UNDERSTANDING ---
            print(f"[UDP DATA IN] Received from {sender_ip}:{src_port} -> Raw: {data} | Decoded: {data.decode('ascii', errors='ignore')}")

            clean_str = data.decode('ascii', errors='ignore').strip()
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Check if this packet is a heartbeat (e.g. "120,PING" or "PING")
            if "PING" in clean_str.upper() or "HEARTBEAT" in clean_str.upper():
                device_id = clean_str.split(',')[0].strip() if ',' in clean_str else "120"
                device_name = f"Device {device_id}"

                with data_lock:
                    station_data["name"]      = device_name
                    station_data["sender_ip"] = sender_ip
                    station_data["last_seen"] = time.time()
                    station_data["online"]    = True
                    if station_data["time"] == "--":
                        station_data["time"] = current_time
            else:
                device_id, value = extract_value(data)
                device_name = f"Device {device_id}" if device_id != "Unknown" else STATION_NAME

                with data_lock:
                    station_data["name"]      = device_name
                    station_data["val"]       = value
                    station_data["time"]      = current_time
                    station_data["sender_ip"] = sender_ip
                    station_data["last_seen"] = time.time()
                    station_data["online"]    = True

                save_to_db(device_name, sender_ip, value, current_time)

        except socket.timeout:
            continue
        except OSError as e:
            print(f"[UDP] Socket error: {e}")
            try:
                sock.close()
            except Exception:
                pass
            raise

def udp_listener():
    while True:
        try:
            _udp_loop()
        except Exception as e:
            print(f"[UDP] Auto-recovering in 3s after error: {e}")
            time.sleep(3)

def offline_monitor():
    while True:
        time.sleep(4)
        now = time.time()
        with data_lock:
            ls = station_data["last_seen"]
            if ls and (now - ls) > OFFLINE_TIMEOUT and station_data["online"]:
                station_data["online"] = False

# ================================================================
# BACKGROUND SERVICES (UDP RECEIVER & WATCHDOG)
# ================================================================
def start_background_services():
    """Starts the background UDP telemetry receiver and offline monitor once."""
    if getattr(app, '_services_started', False):
        return
    app._services_started = True

    print("\n[STARTUP] Cleaning orphaned UDP listeners on port 63363...")
    kill_port_owner(UDP_PORT)
    time.sleep(0.2)
    threading.Thread(target=udp_listener, daemon=True, name="udp-listener").start()
    threading.Thread(target=offline_monitor, daemon=True, name="offline-monitor").start()

# ================================================================
# REPORT GENERATION & HIERARCHICAL STORAGE LOGIC
# ================================================================
def generate_and_save_report(report_data: dict) -> dict:
    """
    Populates Viscosity report.xlsx and saves dual .xlsx and .pdf reports into
    the folder hierarchy defined in image1.png and image2.png:
    Reports <Year>/<Day>_<Month>_<Year>/<Test Pass | Test Fail>/Excel Report/ & Pdf Report/
    """
    if not os.path.exists(TEMPLATE_EXCEL_PATH):
        raise FileNotFoundError(f"Template not found at {TEMPLATE_EXCEL_PATH}")

    process_type = report_data.get("process_type", "Primer ").strip()
    sheet_target = "Paint Coat " if "Paint" in process_type else "Primer "

    part_number = report_data.get("part_number", "1021F0001").strip()
    operator_id = report_data.get("operator_id", "VIKASH").strip()
    shift       = report_data.get("shift", "Shift A").strip()

    part_a = report_data.get("part_a", {})
    part_b = report_data.get("part_b", {})
    thinner = report_data.get("thinner", {})

    min_visc = float(report_data.get("min_viscosity", 18.0))
    max_visc = float(report_data.get("max_viscosity", 26.0))
    actual_visc = float(report_data.get("actual_viscosity", 0.0))

    # Evaluate Pass / Fail
    is_pass = (min_visc <= actual_visc <= max_visc)
    result_str = "Pass" if is_pass else "Fail"

    # Open template with openpyxl
    wb = openpyxl.load_workbook(TEMPLATE_EXCEL_PATH)
    if sheet_target in wb.sheetnames:
        ws = wb[sheet_target]
        wb.active = ws  # Make tested sheet active
    else:
        ws = wb.active

    # Remove the untested sheet so the generated report contains ONLY the tested sheet (no empty second tab)
    for s_name in list(wb.sheetnames):
        if s_name != sheet_target:
            try:
                wb.remove(wb[s_name])
            except Exception:
                pass

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d %H:%M:%S")

    # Header section
    ws["C6"] = "Primer Coat " if sheet_target == "Primer " else "Paint Top  Coat"
    ws["H6"] = result_str
    ws["C7"] = date_str
    ws["H7"] = operator_id
    ws["C8"] = shift
    ws["F8"] = "Part / Model No "
    ws["H8"] = part_number

    # PART A (Row 19 - 22)
    ws["G19"] = part_a.get("batch", "")
    ws["G20"] = part_a.get("mfg", "")
    ws["G21"] = part_a.get("exp", "")
    ws["G22"] = part_a.get("qty", "")

    # PART B (Row 25 - 28)
    ws["G25"] = part_b.get("batch", "")
    ws["G26"] = part_b.get("mfg", "")
    ws["G27"] = part_b.get("exp", "")
    ws["G28"] = part_b.get("qty", "")

    # Thinner (Row 101 - 104)
    ws["G101"] = thinner.get("batch", "")
    ws["G102"] = thinner.get("mfg", "")
    ws["G103"] = thinner.get("exp", "")
    ws["G104"] = thinner.get("qty", "")

    # Viscosity Test Spec & Actual (Row 108)
    ws.cell(row=108, column=7, value=min_visc)
    ws.cell(row=108, column=8, value=max_visc)
    ws.cell(row=108, column=9, value=actual_visc)
    ws.cell(row=108, column=10, value=result_str)

    # Resolve folder hierarchy:
    # Reports/Reports <Year>/<Month>/<Day>_<Month>_<Year>/<Primer Coat | Paint Top Coat>/<Test Pass | Test Fail>/Excel Report & Pdf Report
    report_folder   = f"Reports"
    year_folder     = f"Reports {now.year}"
    month_folder    = now.strftime('%B')
    day_folder      = f"{now.day}_{now.strftime('%B')}_{now.year}"
    template_folder = "Primer Coat" if sheet_target == "Primer " else "Paint Top Coat"
    status_folder   = "Test Pass" if is_pass else "Test Fail"

    excel_dir = os.path.join(BASE_DIR, report_folder, year_folder, month_folder, day_folder, template_folder, status_folder, "Excel Report")
    pdf_dir   = os.path.join(BASE_DIR, report_folder, year_folder, month_folder, day_folder, template_folder, status_folder, "Pdf Report")
    os.makedirs(excel_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)

    # Resolve filename from image2.png:
    # <Part_Number>_<PartA_Batch>_<PartB_Batch>.xlsx / .pdf
    clean_part = re.sub(r'[^A-Za-z0-9_-]', '', part_number) or "1021F0001"
    clean_a    = re.sub(r'[^A-Za-z0-9_-]', '', part_a.get("batch", "")) or "BATCHA"
    clean_b    = re.sub(r'[^A-Za-z0-9_-]', '', part_b.get("batch", "")) or "BATCHB"
    base_name  = f"{clean_part}_{clean_a}_{clean_b}"

    # Ensure unique filename if repeated in the same minute
    test_excel_path = os.path.join(excel_dir, f"{base_name}.xlsx")
    if os.path.exists(test_excel_path):
        base_name = f"{base_name}_{now.strftime('%H%M%S')}"

    excel_filename = f"{base_name}.xlsx"
    pdf_filename   = f"{base_name}.pdf"

    excel_path = os.path.join(excel_dir, excel_filename)
    pdf_path   = os.path.join(pdf_dir, pdf_filename)
    # Ensure official Walvoil logo is always present in worksheet
    try:
        from openpyxl.drawing.image import Image as OpenpyxlImage
        logo_path = os.path.join(BASE_DIR, "static", "logo.png")
        if os.path.exists(logo_path) and hasattr(ws, '_images') and len(ws._images) == 0:
            img = OpenpyxlImage(logo_path)
            img.width = 194
            img.height = 43
            ws.add_image(img, "A1")
    except Exception as img_err:
        pass

    # Save populated Excel workbook
    wb.save(excel_path)
    wb.close()

    # Generate PDF report:
    # 1. Windows: Native Excel COM Automation
    # 2. Linux: Headless LibreOffice CLI export
    pdf_generated = False
    if HAS_WIN32COM and platform.system().lower() == "windows":
        excel_app = None
        try:
            pythoncom.CoInitialize()
            excel_app = win32com.client.Dispatch("Excel.Application")
            excel_app.Visible = False
            excel_app.DisplayAlerts = False
            wb_com = excel_app.Workbooks.Open(os.path.abspath(excel_path))
            ws_com = wb_com.Sheets(sheet_target)

            # Deselect all other sheets; isolate ONLY the target tested sheet
            ws_com.Select(True)

            # Fit neatly on portrait single page (Rows 1 to 108)
            try:
                ws_com.PageSetup.PrintArea = "A1:J108"
                ws_com.PageSetup.Orientation = 1  # xlPortrait
                ws_com.PageSetup.Zoom = False
                ws_com.PageSetup.FitToPagesWide = 1
                ws_com.PageSetup.FitToPagesTall = 1
            except Exception:
                pass

            ws_com.ExportAsFixedFormat(0, os.path.abspath(pdf_path))
            wb_com.Close(False)
            pdf_generated = os.path.exists(pdf_path)
        except Exception as com_err:
            print(f"[PDF COM EXPORT ERROR] {com_err}")
        finally:
            if excel_app:
                try:
                    excel_app.Quit()
                except Exception:
                    pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    elif platform.system().lower() != "windows":
        try:
            # On Linux (Ubuntu/Debian/CentOS), use headless LibreOffice if available
            out_dir = os.path.dirname(os.path.abspath(pdf_path))
            cmd = ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", out_dir, os.path.abspath(excel_path)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
            generated_pdf = os.path.join(out_dir, f"{os.path.splitext(excel_filename)[0]}.pdf")
            if os.path.exists(generated_pdf) and generated_pdf != os.path.abspath(pdf_path):
                os.replace(generated_pdf, os.path.abspath(pdf_path))
            pdf_generated = os.path.exists(pdf_path)
        except Exception as linux_err:
            print(f"[PDF LINUX EXPORT] {linux_err}")

    # Fallback if PDF was not created
    if not pdf_generated:
        # Create lightweight PDF copy or placeholder
        try:
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\nxref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \ntrailer<</Size 3/Root 1 0 R>>\nstartxref\n101\n%%EOF")
        except Exception:
            pass

    # Save record to SQLite test_reports table
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO test_reports (
            process_type, part_number, operator_id, shift,
            part_a_batch, part_b_batch, thinner_batch,
            min_viscosity, max_viscosity, actual_viscosity,
            result, excel_path, pdf_path, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        sheet_target, part_number, operator_id, shift,
        part_a.get("batch", ""), part_b.get("batch", ""), thinner.get("batch", ""),
        min_visc, max_visc, actual_visc,
        result_str, excel_path, pdf_path, date_str
    ))
    report_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": report_id,
        "result": result_str,
        "is_pass": is_pass,
        "excel_path": excel_path,
        "pdf_path": pdf_path,
        "excel_filename": excel_filename,
        "pdf_filename": pdf_filename
    }

# ================================================================
# FLASK HTTP ROUTES
# ================================================================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/reports')
def reports_page():
    return render_template('reports.html')

@app.route('/benefits')
def benefits_page():
    return render_template('benefits.html')

@app.route('/data')
def get_data():
    cid = request.args.get("cid", "")
    dev = request.args.get("dev", "PC")
    client_count, client_devices = register_client(cid, request.remote_addr, dev)

    with data_lock:
        st_data = dict(station_data)

    with session_lock:
        curr_session = {
            "operator": shared_session["operator"],
            "updated_by": shared_session["updated_by"],
            "updated_at": shared_session["updated_at"],
            "form_data": dict(shared_session["form_data"])
        }

    return jsonify({
        "station": st_data,
        STATION_NAME: st_data,
        "clients_count": client_count,
        "clients_devices": client_devices,
        "operator": curr_session.get("operator", "venkat"),
        "session": curr_session
    })

@app.route('/api/session/update', methods=['POST'])
def api_session_update():
    try:
        payload = request.get_json(force=True)
        cid = payload.get("cid", "")
        dev = payload.get("dev", "PC")
        client_count, client_devices = register_client(cid, request.remote_addr, dev)

        with session_lock:
            if "operator" in payload and payload["operator"]:
                shared_session["operator"] = payload["operator"].strip()
            
            if "form_data" in payload and isinstance(payload["form_data"], dict):
                shared_session["form_data"].update(payload["form_data"])
                # Automatically sync session operator if operator_id input is edited
                if "operator_id" in payload["form_data"] and payload["form_data"]["operator_id"]:
                    shared_session["operator"] = payload["form_data"]["operator_id"].strip()
            
            shared_session["updated_by"] = cid
            shared_session["updated_at"] = time.time()
            curr = {
                "operator": shared_session["operator"],
                "updated_by": shared_session["updated_by"],
                "updated_at": shared_session["updated_at"],
                "form_data": dict(shared_session["form_data"])
            }

        return jsonify({
            "success": True,
            "session": curr,
            "clients_count": client_count,
            "clients_devices": client_devices
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/ping_status')
def ping_status():
    with data_lock:
        return jsonify({
            STATION_NAME: {
                "online": station_data["online"],
                "ip": station_data["sender_ip"],
                "last_value": station_data["val"],
                "last_seen": station_data["time"]
            }
        })

@app.route('/api/save_report', methods=['POST'])
def api_save_report():
    try:
        report_data = request.get_json(force=True)
        result = generate_and_save_report(report_data)
        return jsonify({
            "success": True,
            "id": result["id"],
            "result": result["result"],
            "is_pass": result["is_pass"],
            "excel_filename": result["excel_filename"],
            "pdf_filename": result["pdf_filename"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/reports')
def api_reports():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, process_type, part_number, operator_id, shift,
               part_a_batch, part_b_batch, thinner_batch,
               min_viscosity, max_viscosity, actual_viscosity,
               result, created_at
        FROM test_reports
        ORDER BY id DESC LIMIT 50
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/download/<int:report_id>/<file_type>')
def api_download(report_id, file_type):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT excel_path, pdf_path, part_number, result FROM test_reports WHERE id = ?", (report_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return "Report not found", 404

    excel_path, pdf_path, part_num, res = row
    target_path = excel_path if file_type.lower() == "excel" else pdf_path

    if not os.path.exists(target_path):
        return f"File {file_type} not found on disk", 404

    return send_file(target_path, as_attachment=True)

@app.route('/history')
def history():
    limit = int(request.args.get("limit", 2000))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, device_name, sender_ip, value, ts FROM device_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/download_csv')
def download_csv():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.cursor().execute("SELECT device_name, value, ts FROM device_logs ORDER BY ts ASC, id ASC").fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["STATION NAME", "VALUE", "TIMESTAMP"])
    for r in rows:
        writer.writerow(r)

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="Viscosity_Live_Log.csv")



@app.route('/api/download_reports_archive')
def download_reports_archive():
    import zipfile
    start_date = request.args.get("start", "")
    end_date = request.args.get("end", "")
    process_filter = request.args.get("process", "all")
    status_filter = request.args.get("status", "all")
    search_query = request.args.get("search", "")
    
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT pdf_path, excel_path, part_number, created_at FROM test_reports WHERE 1=1"
    params = []
    
    if start_date and end_date:
        query += " AND date(created_at) BETWEEN ? AND ?"
        params.extend([start_date, end_date])
        
    if process_filter == 'primer':
        query += " AND process_type LIKE '%Primer%'"
    elif process_filter == 'paint':
        query += " AND process_type LIKE '%Paint%'"
        
    if status_filter == 'pass':
        query += " AND LOWER(TRIM(result)) = 'pass'"
    elif status_filter == 'fail':
        query += " AND LOWER(TRIM(result)) = 'fail'"
        
    if search_query:
        query += " AND (part_number LIKE ? OR part_a_batch LIKE ? OR part_b_batch LIKE ? OR operator_id LIKE ?)"
        lk = f"%{search_query}%"
        params.extend([lk, lk, lk, lk])
    
    rows = conn.cursor().execute(query, params).fetchall()
    conn.close()

    mem = io.BytesIO()
    seen_paths = set()
    
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_STORED) as zf:
        for pdf_path, excel_path, part_num, created_at in rows:
            for file_path in [pdf_path, excel_path]:
                if file_path and os.path.exists(file_path):
                    # Preserve the exact folder hierarchy starting from 'Reports'
                    try:
                        arcname = os.path.relpath(file_path, BASE_DIR)
                    except ValueError:
                        arcname = os.path.basename(file_path)
                    
                    # Normalize Windows slashes to standard zip slashes
                    arcname = arcname.replace('\\', '/')
                    
                    # Only add the file if we haven't already added it to the zip
                    if arcname not in seen_paths:
                        zf.write(file_path, arcname)
                        seen_paths.add(arcname)

    mem.seek(0)
    filename = f"Archived_Reports_{start_date}_to_{end_date}.zip" if start_date and end_date else "Archived_Reports_All.zip"
    return send_file(mem, mimetype="application/zip", as_attachment=True, download_name=filename)

# Automatically start background UDP receiver & watchdog when imported by WSGI (e.g. Gunicorn)
if not getattr(app, '_services_started', False) and os.environ.get("WERKZEUG_RUN_MAIN") is None and __name__ != '__main__':
    start_background_services()

# ================================================================
# ENTRY POINT
# ================================================================
if __name__ == '__main__':
    # Start background UDP receiver ONLY in the active Flask worker process,
    # never in the reloader parent process, otherwise the parent steals the UDP packets!
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_background_services()

    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=True)

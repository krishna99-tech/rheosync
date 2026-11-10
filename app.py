"""
╔══════════════════════════════════════════════════════════════════════╗
║   WALVOIL — IoT VISCOSITY MEASUREMENT & REPORTING SYSTEM             ║
║   Single Station Architecture & Automated Dual Excel/PDF Generator   ║
╠══════════════════════════════════════════════════════════════════════╣
║  PRODUCTION ARCHITECTURE:                                            ║
║  [1] High-concurrency SQLite with WAL mode & 30s busy timeout        ║
║  [2] Thread-safe Windows Excel COM export with STA lock & cleanup    ║
║  [3] Non-blocking UDP Telemetry Listener on port 63363               ║
║  [4] Industrial Rotating File Logger (logs/app.log)                  ║
║  [5] Multi-threaded Production WSGI (Waitress on Windows)            ║
║  [6] Input validation with HTTP 400 guards & path traversal defense  ║
║  [7] Hierarchical Archive Structure (Excel Report & Pdf Report)      ║
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
from logging.handlers import RotatingFileHandler
import time
import io
import csv
import re
import signal
import atexit
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_file, abort
import openpyxl

try:
    import win32com.client
    import pythoncom
    HAS_WIN32COM = True
except ImportError:
    HAS_WIN32COM = False

# ================================================================
# ENVIRONMENT CONFIGURATION
# ================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

DEBUG = os.environ.get("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5001"))
UDP_IP = os.environ.get("UDP_IP", "0.0.0.0")
UDP_PORT = int(os.environ.get("UDP_PORT", "63363"))
OFFLINE_TIMEOUT = int(os.environ.get("OFFLINE_TIMEOUT", "30"))
STATION_NAME = os.environ.get("STATION_NAME", "Walvoil Viscosity Testing Station")

_raw_db = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "device_data.db"))
if os.path.isdir(_raw_db):
    DB_PATH = os.path.join(_raw_db, "device_data.db")
else:
    DB_PATH = _raw_db

TEMPLATE_EXCEL_PATH = os.path.join(BASE_DIR, "Viscosity report.xlsx")

# ================================================================
# INDUSTRIAL LOGGING SYSTEM (Rotating File + Console)
# ================================================================
logger = logging.getLogger("walvoil")
logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)

if not logger.handlers:
    # 1. Rotating File Handler (10MB max, keep 5 backups)
    file_handler = RotatingFileHandler(
        os.path.join(LOGS_DIR, "app.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    logger.addHandler(file_handler)

    # 2. Console Handler (Clean output for terminal)
    console_handler = logging.StreamHandler(sys.stdout)
    console_formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('waitress').setLevel(logging.INFO)

# ================================================================
# GRACEFUL SOCKET LIFECYCLE & PROCESS SUPERVISION
# ================================================================
_udp_sock = None
_shutdown_event = threading.Event()

def close_udp_socket():
    """Cleanly releases the UDP socket resource without orphaned locks."""
    global _udp_sock
    _shutdown_event.set()
    if _udp_sock:
        try:
            _udp_sock.close()
            logger.info(f"[SOCKET] Cleanly released UDP telemetry socket on port {UDP_PORT}")
        except Exception as e:
            logger.debug(f"[SOCKET] Notice during socket close: {e}")
        finally:
            _udp_sock = None

def graceful_shutdown(signum=None, frame=None):
    """Gracefully shuts down background threads and sockets on SIGINT / SIGTERM."""
    sig_name = f"Signal {signum}" if signum is not None else "Application exit"
    logger.info(f"[SHUTDOWN] {sig_name} received. Releasing resources...")
    _shutdown_event.set()
    close_udp_socket()
    if signum in (signal.SIGTERM, signal.SIGINT):
        sys.exit(0)

# Register exit handlers for clean socket release on termination
atexit.register(close_udp_socket)
try:
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
except (ValueError, AttributeError):
    # Signals are not permitted outside main thread in some multi-threaded runners
    pass

def diagnose_port_status(port: int):
    """Non-destructive diagnostic check. Warns if a port appears already occupied."""
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        test_sock.bind((UDP_IP, port))
        test_sock.close()
    except OSError:
        logger.warning(
            f"[PORT CHECK] UDP port {port} appears to be in use. "
            f"If listener fails to bind, check for another running instance."
        )

# ================================================================
# FLASK APP SETUP
# ================================================================
app = Flask(__name__)

# ================================================================
# DATABASE CONCURRENCY (WAL Mode & Robust Connection Management)
# ================================================================
def get_db_connection(row_factory: bool = False) -> sqlite3.Connection:
    """Returns a SQLite connection configured for high-concurrency multi-threaded access."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
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
    # Performance indices for fast querying even over millions of records
    c.execute("CREATE INDEX IF NOT EXISTS idx_device_logs_ts ON device_logs(ts);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_test_reports_created ON test_reports(created_at);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_test_reports_part ON test_reports(part_number);")
    conn.commit()
    conn.close()
    logger.info("[DATABASE] Initialized SQLite tables and indices with WAL mode.")

def save_to_db(device_name: str, sender_ip: str, value: str, ts: str):
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO device_logs (device_name, sender_ip, value, ts) VALUES (?, ?, ?, ?)",
            (device_name, sender_ip, value, ts)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[DB ERROR] Failed to save device log: {e}")

init_db()

# ================================================================
# SHARED STATE & SYNCHRONIZATION LOCKS
# ================================================================
data_lock      = threading.Lock()
clients_lock   = threading.Lock()
session_lock   = threading.Lock()
excel_com_lock = threading.Lock()   # Protects Single-Threaded Apartment (STA) COM Automation

station_data = {
    "name": STATION_NAME,
    "val": "0.00",
    "time": "--",
    "sender_ip": "Waiting...",
    "last_seen": 0,
    "online": False
}

active_clients = {}  # {client_id: {"ip": str, "device": str, "last_seen": float}}

shared_session = {
    "operator": "venkat",
    "updated_by": None,
    "updated_at": time.time(),
    "form_data": {}
}

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
# PACKET DECODING & HELPERS
# ================================================================
def extract_value(raw_bytes: bytes) -> tuple:
    clean = raw_bytes.decode('ascii', errors='ignore').strip()
    device_id = "Unknown"
    
    # If the payload is in "DEVICE_ID,VALUE" format (e.g., "120,2.68"), grab value after comma
    if ',' in clean:
        parts = clean.split(',')
        device_id = parts[0].strip()
        clean = parts[-1].strip()
        
    m = re.search(r'\d+\.?\d*', clean)
    val = m.group() if m else clean.replace('\x00', '').strip()
    return device_id, val

def safe_float(val, default: float = 0.0) -> float:
    """Safely converts an arbitrary value to float without raising ValueError."""
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (ValueError, TypeError):
        return default

def is_safe_path(target_path: str, base_dir: str = BASE_DIR) -> bool:
    """Verifies that target_path strictly resolves within base_dir to avoid directory traversal."""
    try:
        target_abs = os.path.abspath(target_path)
        base_abs = os.path.abspath(base_dir)
        return os.path.commonpath([target_abs, base_abs]) == base_abs
    except Exception:
        return False

# ================================================================
# UDP LISTENER THREAD (Non-blocking, Auto-recovering)
# ================================================================
def _udp_loop():
    global _udp_sock
    close_udp_socket()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.settimeout(1.0)

    try:
        sock.bind((UDP_IP, UDP_PORT))
        _udp_sock = sock
        logger.info(f"WALVOIL UDP TELEMETRY SERVER BOUND ON {UDP_IP}:{UDP_PORT}")
    except OSError as bind_err:
        logger.critical(
            f"[UDP BIND ERROR] Cannot bind UDP port {UDP_PORT} on {UDP_IP}: {bind_err}. "
            f"Port is already in use by another instance or service."
        )
        sock.close()
        raise

    last_heartbeat_log = 0.0

    while not _shutdown_event.is_set():
        try:
            data, addr = sock.recvfrom(1024)
            sender_ip = addr[0]
            src_port  = addr[1]
            
            clean_str = data.decode('ascii', errors='ignore').strip()
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Check if this packet is a heartbeat (e.g. "120,PING" or "PING")
            if "PING" in clean_str.upper() or "HEARTBEAT" in clean_str.upper():
                device_id = clean_str.split(',')[0].strip() if ',' in clean_str else "120"
                device_name = f"Device {device_id}"

                with data_lock:
                    was_offline = not station_data["online"]
                    station_data["name"]      = device_name
                    station_data["sender_ip"] = sender_ip
                    station_data["last_seen"] = time.time()
                    station_data["online"]    = True
                    if station_data["time"] == "--":
                        station_data["time"] = current_time

                now = time.time()
                if was_offline or (now - last_heartbeat_log > 60.0):
                    logger.info(f"[UDP HEARTBEAT] Station online from {sender_ip}:{src_port} ({device_name})")
                    last_heartbeat_log = now
            else:
                device_id, value = extract_value(data)
                device_name = f"Device {device_id}" if device_id != "Unknown" else STATION_NAME

                with data_lock:
                    was_offline = not station_data["online"]
                    station_data["name"]      = device_name
                    station_data["val"]       = value
                    station_data["time"]      = current_time
                    station_data["sender_ip"] = sender_ip
                    station_data["last_seen"] = time.time()
                    station_data["online"]    = True

                if was_offline:
                    logger.info(f"[UDP CONNECT] Telemetry stream active from {sender_ip}:{src_port}")

                logger.debug(f"[UDP DATA] {sender_ip} -> {device_name}: {value} sec")
                save_to_db(device_name, sender_ip, value, current_time)

        except socket.timeout:
            continue
        except OSError as e:
            if _shutdown_event.is_set() or getattr(e, 'winerror', None) in (10038, 10004) or getattr(e, 'errno', None) in (10038, 10004, 9):
                break
            logger.error(f"[UDP SOCKET ERROR] {e}")
            close_udp_socket()
            raise

    close_udp_socket()

def udp_listener():
    while not _shutdown_event.is_set():
        try:
            _udp_loop()
        except Exception as e:
            if _shutdown_event.is_set():
                break
            logger.warning(f"[UDP RECOVERY] Re-initializing UDP listener in 3s after error: {e}")
            time.sleep(3)

def offline_monitor():
    while not _shutdown_event.is_set():
        time.sleep(4)
        now = time.time()
        with data_lock:
            ls = station_data["last_seen"]
            if ls and (now - ls) > OFFLINE_TIMEOUT and station_data["online"]:
                station_data["online"] = False
                logger.warning(f"[WATCHDOG] Station marked OFFLINE (no packet for >{OFFLINE_TIMEOUT}s)")

# ================================================================
# BACKGROUND SERVICES LIFECYCLE (Singleton Guarantee)
# ================================================================
_services_lock = threading.Lock()
_services_started = False

def start_background_services():
    """Starts the background UDP telemetry receiver and offline monitor once in a thread-safe manner."""
    global _services_started
    with _services_lock:
        if _services_started:
            return
        _services_started = True

        diagnose_port_status(UDP_PORT)
        threading.Thread(target=udp_listener, daemon=True, name="udp-listener").start()
        threading.Thread(target=offline_monitor, daemon=True, name="offline-monitor").start()
        logger.info("[STARTUP] Background UDP listener and offline watchdog initialized.")

# ================================================================
# REPORT GENERATION & HIERARCHICAL STORAGE LOGIC
# ================================================================
def generate_and_save_report(report_data: dict) -> dict:
    """
    Populates Viscosity report.xlsx and saves dual .xlsx and .pdf reports into
    the folder hierarchy:
    Reports/Reports <Year>/<Month>/<Day>_<Month>_<Year>/<Primer Coat | Paint Top Coat>/<Test Pass | Test Fail>/
    ├── Excel Report/<PartNumber>_<BatchA>_<BatchB>.xlsx
    └── Pdf Report/<PartNumber>_<BatchA>_<BatchB>.pdf
    """
    if not os.path.exists(TEMPLATE_EXCEL_PATH):
        raise FileNotFoundError(f"Template not found at {TEMPLATE_EXCEL_PATH}")

    process_type = str(report_data.get("process_type", "Primer ")).strip()
    sheet_target = "Paint Coat " if "Paint" in process_type else "Primer "

    part_number = str(report_data.get("part_number", "1021F0001")).strip()
    operator_id = str(report_data.get("operator_id", "VIKASH")).strip()
    shift       = str(report_data.get("shift", "Shift A")).strip()

    part_a = report_data.get("part_a") or {}
    part_b = report_data.get("part_b") or {}
    thinner = report_data.get("thinner") or {}

    min_visc = safe_float(report_data.get("min_viscosity"), 18.0)
    max_visc = safe_float(report_data.get("max_viscosity"), 26.0)
    actual_visc = safe_float(report_data.get("actual_viscosity"), 0.0)

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
    ws["G19"] = str(part_a.get("batch", ""))
    ws["G20"] = str(part_a.get("mfg", ""))
    ws["G21"] = str(part_a.get("exp", ""))
    ws["G22"] = str(part_a.get("qty", ""))

    # PART B (Row 25 - 28)
    ws["G25"] = str(part_b.get("batch", ""))
    ws["G26"] = str(part_b.get("mfg", ""))
    ws["G27"] = str(part_b.get("exp", ""))
    ws["G28"] = str(part_b.get("qty", ""))

    # Thinner (Row 101 - 104)
    ws["G101"] = str(thinner.get("batch", ""))
    ws["G102"] = str(thinner.get("mfg", ""))
    ws["G103"] = str(thinner.get("exp", ""))
    ws["G104"] = str(thinner.get("qty", ""))

    # Viscosity Test Spec & Actual (Row 108)
    ws.cell(row=108, column=7, value=min_visc)
    ws.cell(row=108, column=8, value=max_visc)
    ws.cell(row=108, column=9, value=actual_visc)
    ws.cell(row=108, column=10, value=result_str)

    # Resolve folder hierarchy
    report_folder   = "Reports"
    year_folder     = f"Reports {now.year}"
    month_folder    = now.strftime('%B')
    day_folder      = f"{now.day}_{now.strftime('%B')}_{now.year}"
    template_folder = "Primer Coat" if sheet_target == "Primer " else "Paint Top Coat"
    status_folder   = "Test Pass" if is_pass else "Test Fail"

    excel_dir = os.path.join(BASE_DIR, report_folder, year_folder, month_folder, day_folder, template_folder, status_folder, "Excel Report")
    pdf_dir   = os.path.join(BASE_DIR, report_folder, year_folder, month_folder, day_folder, template_folder, status_folder, "Pdf Report")
    os.makedirs(excel_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)

    # Resolve filename: <Part_Number>_<PartA_Batch>_<PartB_Batch>.xlsx / .pdf
    clean_part = re.sub(r'[^A-Za-z0-9_-]', '', part_number) or "1021F0001"
    clean_a    = re.sub(r'[^A-Za-z0-9_-]', '', str(part_a.get("batch", ""))) or "BATCHA"
    clean_b    = re.sub(r'[^A-Za-z0-9_-]', '', str(part_b.get("batch", ""))) or "BATCHB"
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
        logger.debug(f"[LOGO INSERT WARNING] {img_err}")

    # Save populated Excel workbook
    wb.save(excel_path)
    wb.close()

    # Generate PDF report:
    # 1. Windows: Thread-Safe Native Excel COM Automation
    # 2. Linux: Headless LibreOffice CLI export
    pdf_generated = False
    if HAS_WIN32COM and platform.system().lower() == "windows":
        with excel_com_lock:
            excel_app = None
            wb_com = None
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
                pdf_generated = os.path.exists(pdf_path)
            except Exception as com_err:
                logger.error(f"[PDF COM EXPORT ERROR] {com_err}")
            finally:
                if wb_com:
                    try:
                        wb_com.Close(False)
                    except Exception:
                        pass
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
            out_dir = os.path.dirname(os.path.abspath(pdf_path))
            cmd = ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", out_dir, os.path.abspath(excel_path)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
            generated_pdf = os.path.join(out_dir, f"{os.path.splitext(excel_filename)[0]}.pdf")
            if os.path.exists(generated_pdf) and generated_pdf != os.path.abspath(pdf_path):
                os.replace(generated_pdf, os.path.abspath(pdf_path))
            pdf_generated = os.path.exists(pdf_path)
        except Exception as linux_err:
            logger.error(f"[PDF LINUX EXPORT ERROR] {linux_err}")

    # Fallback if PDF was not created
    if not pdf_generated:
        try:
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\nxref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \ntrailer<</Size 3/Root 1 0 R>>\nstartxref\n101\n%%EOF")
            logger.warning(f"[PDF FALLBACK] Created placeholder PDF at {pdf_path}")
        except Exception as e:
            logger.error(f"[PDF FALLBACK ERROR] {e}")

    # Save record to SQLite test_reports table
    conn = get_db_connection()
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
        str(part_a.get("batch", "")), str(part_b.get("batch", "")), str(thinner.get("batch", "")),
        min_visc, max_visc, actual_visc,
        result_str, excel_path, pdf_path, date_str
    ))
    report_id = cur.lastrowid
    conn.commit()
    conn.close()

    logger.info(f"[REPORT SAVED] ID #{report_id} | Part: {part_number} | Result: {result_str} | Operator: {operator_id}")

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
        if not payload or not isinstance(payload, dict):
            return jsonify({"success": False, "error": "Invalid JSON payload"}), 400

        cid = payload.get("cid", "")
        dev = payload.get("dev", "PC")
        client_count, client_devices = register_client(cid, request.remote_addr, dev)

        with session_lock:
            if "operator" in payload and payload["operator"]:
                shared_session["operator"] = str(payload["operator"]).strip()
            
            if "form_data" in payload and isinstance(payload["form_data"], dict):
                shared_session["form_data"].update(payload["form_data"])
                # Automatically sync session operator if operator_id input is edited
                if "operator_id" in payload["form_data"] and payload["form_data"]["operator_id"]:
                    shared_session["operator"] = str(payload["form_data"]["operator_id"]).strip()
            
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
        logger.error(f"[SESSION UPDATE ERROR] {e}")
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
        if not report_data or not isinstance(report_data, dict):
            return jsonify({"success": False, "error": "Invalid request body; JSON object expected."}), 400

        # Validate mandatory field presence
        part_number = str(report_data.get("part_number", "")).strip()
        if not part_number:
            return jsonify({"success": False, "error": "Part / Model Number is required."}), 400

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
        logger.error(f"[SAVE REPORT ERROR] {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/reports')
def api_reports():
    conn = get_db_connection(row_factory=True)
    rows = conn.execute("""
        SELECT id, process_type, part_number, operator_id, shift,
               part_a_batch, part_b_batch, thinner_batch,
               min_viscosity, max_viscosity, actual_viscosity,
               result, created_at
        FROM test_reports
        ORDER BY id DESC LIMIT 100
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/download/<int:report_id>/<file_type>')
def api_download(report_id: int, file_type: str):
    if file_type.lower() not in ("excel", "pdf"):
        return abort(400, "Invalid file type requested. Expected 'excel' or 'pdf'.")

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT excel_path, pdf_path FROM test_reports WHERE id = ?", (report_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return abort(404, "Report not found in database.")

    excel_path, pdf_path = row
    target_path = excel_path if file_type.lower() == "excel" else pdf_path

    if not target_path or not os.path.exists(target_path):
        return abort(404, f"Requested {file_type} file does not exist on disk.")

    # Guard against arbitrary path traversal
    if not is_safe_path(target_path, BASE_DIR):
        logger.warning(f"[SECURITY WARNING] Path traversal blocked: {target_path}")
        return abort(403, "Access to the requested file path is forbidden.")

    return send_file(target_path, as_attachment=True)

@app.route('/history')
def history():
    limit = min(max(int(request.args.get("limit", 1000)), 1), 5000)
    conn = get_db_connection(row_factory=True)
    rows = conn.execute(
        "SELECT id, device_name, sender_ip, value, ts FROM device_logs ORDER BY id DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/download_csv')
def download_csv():
    conn = get_db_connection()
    rows = conn.cursor().execute(
        "SELECT device_name, value, ts FROM device_logs ORDER BY ts ASC, id ASC LIMIT 50000"
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["STATION NAME", "VALUE (sec)", "TIMESTAMP"])
    for r in rows:
        writer.writerow(r)

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="Viscosity_Live_Log.csv")

@app.route('/api/download_reports_archive')
def download_reports_archive():
    import zipfile
    start_date = request.args.get("start", "").strip()
    end_date = request.args.get("end", "").strip()
    process_filter = request.args.get("process", "all").strip().lower()
    status_filter = request.args.get("status", "all").strip().lower()
    search_query = request.args.get("search", "").strip()
    
    conn = get_db_connection()
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
    
    query += " ORDER BY id DESC LIMIT 500"  # Guard against memory exhaustion
    rows = conn.cursor().execute(query, params).fetchall()
    conn.close()

    mem = io.BytesIO()
    seen_paths = set()
    
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as zf:
        for pdf_path, excel_path, part_num, created_at in rows:
            for file_path in [pdf_path, excel_path]:
                if file_path and os.path.exists(file_path) and is_safe_path(file_path, BASE_DIR):
                    try:
                        arcname = os.path.relpath(file_path, BASE_DIR)
                    except ValueError:
                        arcname = os.path.basename(file_path)
                    
                    arcname = arcname.replace('\\', '/')
                    if arcname not in seen_paths:
                        zf.write(file_path, arcname)
                        seen_paths.add(arcname)

    mem.seek(0)
    filename = f"Archived_Reports_{start_date}_to_{end_date}.zip" if start_date and end_date else "Archived_Reports_All.zip"
    return send_file(mem, mimetype="application/zip", as_attachment=True, download_name=filename)

# Always initialize background telemetry threads when imported by WSGI (Waitress/Gunicorn)
if not _services_started and __name__ != '__main__':
    start_background_services()

# ================================================================
# PRODUCTION ENTRY POINT
# ================================================================
if __name__ == '__main__':
    start_background_services()

    if DEBUG:
        logger.warning(f"Starting Flask in DEVELOPMENT/DEBUG mode on http://{HOST}:{PORT}")
        app.run(host=HOST, port=PORT, debug=True, use_reloader=False)
    else:
        if platform.system().lower() == "windows":
            try:
                import waitress
                logger.info("=" * 65)
                logger.info("  WALVOIL VISCOSITY SYSTEM — PRODUCTION SERVER (WAITRESS WSGI)")
                logger.info(f"  Listening on: http://{HOST}:{PORT} (8 worker threads)")
                logger.info(f"  Database WAL Mode: Enabled | UDP Port: {UDP_PORT}")
                logger.info(f"  Logging to: {os.path.join(LOGS_DIR, 'app.log')}")
                logger.info("=" * 65)
                waitress.serve(app, host=HOST, port=PORT, threads=8)
            except ImportError:
                logger.warning("Waitress not installed; starting built-in server with debug=False")
                app.run(host=HOST, port=PORT, debug=False)
        else:
            logger.info(f"Starting production server on http://{HOST}:{PORT}")
            app.run(host=HOST, port=PORT, debug=False)

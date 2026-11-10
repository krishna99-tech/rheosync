"""
================================================================================
  WALVOIL VISCOMETER HARDWARE SIMULATOR (debug.py)
  Faithful Python Simulator for Hardwarecodes/main.ino (ESP32 / Arduino)
================================================================================
  Mirrors exact hardware behavior from main.ino:
  - IR Flow Sensor: detects flow start -> flow stop -> measures elapsed seconds
  - Hardware LED Logic:
      * value < 13.0 s           -> 🔴 RED LED (Too thin / Under-viscous)
      * 16.0 s <= value <= 18.0 s -> 🟢 GREEN LED (Target In-Spec)
      * value > 18.0 s           -> 🔴 RED LED (Too thick / Out-of-spec)
  - Modbus Register: computes uint16_t (elapsedSeconds * 100) -> Slave 1, Reg 0
  - UDP Packet: sends formatted "%.2f" to UDP_IP:63363 with 3-attempt retry loop
  - Serial Heartbeat: periodic status report mirroring sendHeartbeat()

Usage:
  python debug.py                          # Continuous hardware flow cycles
  python debug.py --interactive            # Press ENTER to Start / Stop liquid flow!
  python debug.py --val 17.25              # Send exact hardware reading
  python debug.py --preset pass            # Generate in-spec readings (16.0 - 18.0 s)
  python debug.py --preset thin            # Generate under-viscous readings (< 13.0 s)
  python debug.py --preset thick           # Generate thick readings (> 18.0 s)
  python debug.py --host 127.0.0.1         # Destination IP (default: 127.0.0.1)
  python debug.py --port 63363             # Destination Port (default: 63363)
"""

import socket
import time
import random
import argparse
import sys
from datetime import datetime

# Ensure Windows PowerShell/cmd console handles emojis/unicode without charmap errors
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Default settings matching main.ino
# (main.ino uses 192.168.0.150 for server; default to 127.0.0.1 for local testing)
DEFAULT_HOST = "192.168.0.159"
DEFAULT_PORT = 63363
MAX_RETRIES  = 3

def get_led_status(elapsed_seconds: float) -> tuple:
    """
    Mirrors startLED() in main.ino:
      if (value < 13.0) -> RED LED (<13)
      else if (value > 16.0 && value <= 18.0) -> GREEN LED (16-18)
      else -> RED LED (out of range)
    """
    if elapsed_seconds < 13.0:
        return "RED", "[RED LED ON] (<13s - Too Thin / Under-viscous)", "FAIL"
    elif 16.0 <= elapsed_seconds <= 18.0:
        return "GREEN", "[GREEN LED ON] (16.0s - 18.0s In-Spec Target)", "PASS"
    else:
        return "RED", f"[RED LED ON] ({elapsed_seconds:.2f}s Out-of-Range)", "FAIL"

def generate_hardware_reading(preset: str) -> float:
    """Simulates physical cup flow duration based on preset."""
    if preset == "pass":
        # Target in-spec specification defined in main.ino line 106
        return round(random.uniform(16.10, 17.90), 2)
    elif preset == "thin":
        # Too thin condition defined in main.ino line 103
        return round(random.uniform(9.50, 12.80), 2)
    elif preset == "thick":
        # Over-specification condition defined in main.ino line 109
        return round(random.uniform(19.20, 24.50), 2)
    elif preset == "primer":
        # Primer coat range (20.0 - 25.0 cSt)
        return round(random.uniform(20.50, 24.50), 2)
    else:
        # Realistic sensor jitter around target 17.0s (+/- 2.5s)
        return round(random.uniform(14.50, 19.50), 2)

def send_udp_with_retries(sock: socket.socket, host: str, port: int, value: float) -> bool:
    """
    Mirrors sendUDP() in main.ino lines 184-225:
      char msg[20];
      snprintf(msg, sizeof(msg), "%.2f", value);
      udp.beginPacket(UDP_IP, UDP_PORT);
      udp.write((uint8_t*)msg, strlen(msg));
      udp.endPacket();
    """
    msg = f"{value:.2f}"
    payload = msg.encode('ascii')

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"  [UDP] Attempt {attempt}/{MAX_RETRIES} -> '{msg}'")
        try:
            bytes_sent = sock.sendto(payload, (host, port))
            print(f"        bytes: {bytes_sent}")
            print(f"        [OK] UDP packet delivered to {host}:{port}")
            return True
        except Exception as e:
            print(f"        [ERR] Send attempt failed: {e}")
            time.sleep(0.2)  # delay(200) matching main.ino line 220

    print("  [ERR] UDP failed after all retries")
    return False

def print_heartbeat(elapsed_seconds: float, led_status: str):
    """Mirrors sendHeartbeat() in main.ino lines 230-244."""
    print("\n  ===== HEARTBEAT (main.ino) =====")
    print("  WiFi    : UP")
    print("  udpReady: yes")
    print("  flow    : complete")
    print("  sent    : yes")
    print(f"  elapsed : {elapsed_seconds:.2f} s")
    print(f"  LED     : {led_status}")
    print("  ================================\n")

def run_simulation_cycle(sock: socket.socket, host: str, port: int, value: float, cycle_num: int):
    """
    Simulates one full measurement cycle from main.ino:
      1. Flow Start (IR Sensor LOW)
      2. Flow Measurement
      3. Flow Stop (IR Sensor HIGH)
      4. Modbus RTU Write
      5. LED State Update
      6. UDP Transmission
      7. Heartbeat
    """
    print("\n" + "-" * 65)
    print(f" [CYCLE #{cycle_num:03d}] -- ESP32 VISCOMETER HARDWARE CYCLE")
    print("-" * 65)

    # 1. Flow Start
    print("  [*] FLOW START  (IR Sensor pin 27: LOW)")
    time.sleep(0.3)

    # 2. Flow duration
    print(f"  [*] Timing liquid stream... (Simulated Flow Time: {value:.2f}s)")
    time.sleep(0.5)

    # 3. Flow Stop
    print("  [*] FLOW STOP   (IR Sensor pin 27: HIGH - Liquid stream break)")
    print(f"  [*] Time Measured: {value:.2f} s")

    # 4. Modbus RTU Write (line 376 of main.ino)
    modbus_val = int(value * 100)
    print(f"  [Modbus] Writing Holding Register: {modbus_val} (Slave: 1, Reg: 0)")
    print("           Modbus Write Confirmed")

    # 5. Hardware LED evaluation (lines 101-115 of main.ino)
    led_color, led_msg, pass_fail = get_led_status(value)
    print(f"  [LED]    {led_msg}")

    # 6. UDP Send (lines 184-225 of main.ino)
    send_udp_with_retries(sock, host, port, value)

    # 7. Heartbeat
    print_heartbeat(value, led_color)
    print("  Cycle complete -- ready for next measurement\n")

def interactive_mode(sock: socket.socket, host: str, port: int):
    """Interactive mode where operator presses ENTER to start and stop flow."""
    print("\n" + "=" * 65)
    print("   ESP32 HARDWARE INTERACTIVE MODE (IR SENSOR SIMULATOR)")
    print("=" * 65)
    print(" Instructions:")
    print("  1. Press [ENTER] when liquid starts flowing through the cup.")
    print("  2. Press [ENTER] again when liquid stream breaks (flow stops).")
    print("  3. The simulator will calculate real elapsed seconds and transmit!")
    print("  Type 'q' and press [ENTER] to exit.")
    print("=" * 65 + "\n")

    cycle = 0
    while True:
        cycle += 1
        val = input(f" [Cycle #{cycle}] Press [ENTER] to START flow (or 'q' to quit): ")
        if val.strip().lower() == 'q':
            break

        start_time = time.time()
        print("  💧 FLOW START  (IR Sensor triggered: LOW)... Timing liquid flow...")

        input("  ⏱ Flow in progress... Press [ENTER] when liquid stream BREAKS: ")
        stop_time = time.time()
        elapsed = round(stop_time - start_time, 2)

        # Ensure minimum realistic value
        if elapsed < 0.5:
            elapsed = round(random.uniform(16.0, 18.0), 2)
            print(f"  [Notice: Instant click detected -> generated realistic {elapsed}s]")

        run_simulation_cycle(sock, host, port, elapsed, cycle)

def main():
    parser = argparse.ArgumentParser(
        description="ESP32 IoT Viscometer Hardware Simulator (based on Hardwarecodes/main.ino)"
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Destination IP (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Destination UDP Port (default: {DEFAULT_PORT})")
    parser.add_argument("--val", type=float, default=None, help="Send a specific viscosity flow reading in seconds")
    parser.add_argument("--preset", choices=["pass", "thin", "thick", "primer", "random"], default="random",
                        help="Hardware preset: pass (16-18s), thin (<13s), thick (>18s), primer (20-25s), random")
    parser.add_argument("--interval", type=float, default=3.0, help="Interval between cycles in seconds (default: 3.0)")
    parser.add_argument("--count", type=int, default=0, help="Number of cycles to run (0 = continuous)")
    parser.add_argument("--interactive", action="store_true", help="Interactive stopwatch mode using ENTER key")

    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print("=" * 68)
    print("   WALVOIL IoT VISCOMETER HARDWARE SIMULATOR (debug.py)")
    print("   Built from: Hardwarecodes/main.ino")
    print("=" * 68)
    print(f"  Target Server : {args.host}:{args.port}")
    print(f"  Target LEDs   : RED (<13s) | GREEN (16-18s) | RED (>18s)")
    print(f"  Modbus Reg    : Slave ID 1, Holding Reg 0 (value * 100)")
    print(f"  Packet Format : '%.2f' via UDP Port {args.port}")
    print("=" * 68)

    if args.interactive:
        try:
            interactive_mode(sock, args.host, args.port)
        finally:
            sock.close()
        return

    if args.val is not None:
        run_simulation_cycle(sock, args.host, args.port, args.val, 1)
        sock.close()
        return

    print("  Starting automated hardware simulation cycles...")
    print("  Press Ctrl+C to stop.\n")

    cycle_num = 0
    try:
        while True:
            cycle_num += 1
            reading = generate_hardware_reading(args.preset)
            run_simulation_cycle(sock, args.host, args.port, reading, cycle_num)

            if args.count > 0 and cycle_num >= args.count:
                print(f"[+] Completed {args.count} simulation cycle(s).")
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n[!] Simulator stopped by user (Ctrl+C).")
    finally:
        sock.close()

if __name__ == "__main__":
    main()

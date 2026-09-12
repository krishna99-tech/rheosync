# RheoSync — IoT Viscosity Measurement & Reporting System

Automated IoT quality station for hydraulic component viscosity measurement, real-time telemetry monitoring, dual Excel/PDF report generation, and multi-device workstation management.

---

## Key Features

1. **IoT Hardware Integration:**
   - Single station auto-detect listener on UDP Port `63363`.
   - Modbus Holding Register 0 mirroring (100x multiplier).
   - Real-time stream decoding, noise filtering, and status monitoring.

2. **Dual Excel & PDF Report Automation:**
   - Auto-populates official template `Viscosity report.xlsx` (`openpyxl`).
   - Native pixel-perfect PDF export via Microsoft Excel COM (Windows) or Headless LibreOffice (Linux).
   - Hierarchical year/month/day/process/pass-fail storage structure.

3. **Multi-Device Live Sync:**
   - Real-time mirroring between Desktop PC and Mobile phones.
   - Live Operator ID synchronization across all devices.
   - Shift schedule tracking (`Shift A`, `Shift B`, `Shift C`).

4. **Hardware Simulator:**
   - `debug.py`: Faithful Python simulator mirroring `Hardwarecodes/main.ino` for offline testing.

---

## Documentation & Guides

- **[Dockerfile](Dockerfile)**: Containerized production build with headless LibreOffice PDF rendering.
- **[docker-compose.yml](docker-compose.yml)**: Multi-port (TCP 5000 + UDP 63363) orchestration with volume mounts.
- **[requirements.txt](requirements.txt)**: Cross-platform Python dependencies (Windows & Linux).
- **[README_NETWORK_INTEGRATION.md](README_NETWORK_INTEGRATION.md)**: Blueprint and Python code for automatic backup to Company Network Shared Folders / NAS.

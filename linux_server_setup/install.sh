#!/bin/bash
# ==============================================================================
# WALVOIL — IoT VISCOSITY SYSTEM
# 1-Click Automated Linux Bare Metal / VM Setup & Service Installer
# Supports: Ubuntu 20.04/22.04/24.04 LTS, Debian 11/12
# ==============================================================================

set -e

# ANSI Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "\n${BLUE}=================================================================${NC}"
echo -e "${GREEN}  WALVOIL VISCOSITY SYSTEM — LINUX SERVER PRODUCTION INSTALLER   ${NC}"
echo -e "${BLUE}=================================================================${NC}\n"

# 1. Resolve project directory and user
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CURRENT_USER="$(logname 2>/dev/null || echo "${SUDO_USER:-$USER}")"
USER_HOME="$(eval echo ~${CURRENT_USER})"

echo -e "${BLUE}[*] Project Directory:${NC} ${PROJECT_DIR}"
echo -e "${BLUE}[*] Operating User:${NC}    ${CURRENT_USER}"

# Check for sudo/root permissions
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] Please run this script with sudo:${NC}"
    echo -e "  sudo bash linux_server_setup/install.sh"
    exit 1
fi

# 2. Update package repositories and install system dependencies
echo -e "\n${BLUE}[1/5] Installing Linux system packages...${NC}"
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    libreoffice-calc \
    fonts-liberation \
    fonts-dejavu-core \
    psmisc \
    curl \
    ufw

echo -e "${GREEN}[✓] System packages installed successfully.${NC}"

# 3. Create persistent directories and set ownership
echo -e "\n${BLUE}[2/5] Creating persistent directories (Reports, logs)...${NC}"
mkdir -p "${PROJECT_DIR}/Reports"
mkdir -p "${PROJECT_DIR}/logs"
chown -R "${CURRENT_USER}:${CURRENT_USER}" "${PROJECT_DIR}"
chmod -R 775 "${PROJECT_DIR}/Reports"
chmod -R 775 "${PROJECT_DIR}/logs"
echo -e "${GREEN}[✓] Directories created and ownership assigned to ${CURRENT_USER}.${NC}"

# 4. Set up Python virtual environment and install requirements
echo -e "\n${BLUE}[3/5] Setting up Python virtual environment...${NC}"
VENV_DIR="${PROJECT_DIR}/venv"

if [ ! -d "${VENV_DIR}" ]; then
    sudo -u "${CURRENT_USER}" python3 -m venv "${VENV_DIR}"
fi

echo -e "${BLUE}[*] Upgrading pip and installing Python packages...${NC}"
sudo -u "${CURRENT_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip
sudo -u "${CURRENT_USER}" "${VENV_DIR}/bin/pip" install -r "${PROJECT_DIR}/requirements.txt"

echo -e "${GREEN}[✓] Python dependencies installed successfully.${NC}"

# 5. Generate and install systemd unit service
echo -e "\n${BLUE}[4/5] Configuring systemd background service (walvoil-viscosity)...${NC}"
SERVICE_FILE="/etc/systemd/system/walvoil-viscosity.service"

cat <<EOF > "${SERVICE_FILE}"
[Unit]
Description=Walvoil IoT Viscosity Measurement & Reporting Service
After=network.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="FLASK_DEBUG=0"
ExecStart=${VENV_DIR}/bin/gunicorn -w 1 --threads 8 -b 0.0.0.0:5001 --access-logfile - --error-logfile - app:app
Restart=always
RestartSec=5
KillSignal=SIGQUIT
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "${SERVICE_FILE}"
systemctl daemon-reload
systemctl enable walvoil-viscosity
systemctl restart walvoil-viscosity

echo -e "${GREEN}[✓] Systemd service enabled and started.${NC}"

# 6. Configure UFW Firewall rules
echo -e "\n${BLUE}[5/5] Configuring Firewall (UFW) rules...${NC}"
if command -v ufw >/dev/null 2>&1; then
    ufw allow 5001/tcp comment "Walvoil Viscosity Web Dashboard" >/dev/null 2>&1 || true
    ufw allow 63363/udp comment "Walvoil IoT Viscometer UDP Stream" >/dev/null 2>&1 || true
    echo -e "${GREEN}[✓] Firewall rules added: 5001/tcp and 63363/udp.${NC}"
fi

# 7. Print summary and status
sleep 2
SERVER_IP="$(hostname -I | awk '{print $1}')"

echo -e "\n${GREEN}=================================================================${NC}"
echo -e "${GREEN}  INSTALLATION COMPLETE — SERVICE IS LIVE & RUNNING!             ${NC}"
echo -e "${GREEN}=================================================================${NC}\n"
echo -e "  🌐 Web Dashboard:         ${YELLOW}http://${SERVER_IP}:5001${NC}"
echo -e "  🔍 Diagnostic API:        ${YELLOW}http://${SERVER_IP}:5001/ping_status${NC}"
echo -e "  📡 IoT UDP Receiver:      Port ${YELLOW}63363 (UDP)${NC}"
echo -e "  📁 Reports Archive:       ${PROJECT_DIR}/Reports"
echo -e "  📋 Application Log:       ${PROJECT_DIR}/logs/app.log"
echo -e "\n  Management Commands:"
echo -e "  - View Live Logs:         ${BLUE}sudo bash linux_server_setup/view_logs.sh${NC}"
echo -e "  - Check Service Status:   ${BLUE}sudo bash linux_server_setup/status.sh${NC}"
echo -e "  - Restart Service:        ${BLUE}sudo bash linux_server_setup/restart.sh${NC}"
echo -e "  - Stop Service:           ${BLUE}sudo bash linux_server_setup/stop.sh${NC}"
echo -e "${GREEN}=================================================================${NC}\n"


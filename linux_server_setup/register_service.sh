#!/bin/bash
# ==============================================================================
# WALVOIL — IoT VISCOSITY SYSTEM
# Helper script to Register & Start the Systemd 24/7 Background Service
# ==============================================================================

set -e

# ANSI Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] Please run this script with sudo:${NC}"
    echo "  sudo bash linux_server_setup/register_service.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CURRENT_USER="$(logname 2>/dev/null || echo "${SUDO_USER:-$USER}")"
VENV_DIR="${PROJECT_DIR}/venv"
SERVICE_DEST="/etc/systemd/system/walvoil-viscosity.service"

echo -e "\n${BLUE}=================================================================${NC}"
echo -e "${BLUE}  REGISTERING & STARTING WALVOIL SYSTEMD SERVICE                 ${NC}"
echo -e "${BLUE}=================================================================${NC}\n"
echo -e "  Project Directory: ${GREEN}${PROJECT_DIR}${NC}"
echo -e "  Service User:      ${GREEN}${CURRENT_USER}${NC}"
echo -e "  Virtualenv Path:   ${GREEN}${VENV_DIR}${NC}"

# Check virtualenv exists
if [ ! -f "${VENV_DIR}/bin/gunicorn" ]; then
    echo -e "${RED}[ERROR] Gunicorn not found in ${VENV_DIR}/bin/gunicorn!${NC}"
    echo "Please complete Step 2 first (create venv and run pip install -r requirements.txt)."
    exit 1
fi

# Ensure Reports and logs directories exist with correct permissions
mkdir -p "${PROJECT_DIR}/Reports" "${PROJECT_DIR}/logs"
chown -R "${CURRENT_USER}:${CURRENT_USER}" "${PROJECT_DIR}/Reports" "${PROJECT_DIR}/logs"
chmod -R 775 "${PROJECT_DIR}/Reports" "${PROJECT_DIR}/logs"

# Write service file with resolved user and paths
echo -e "\n${BLUE}[1/4] Writing ${SERVICE_DEST}...${NC}"
cat <<EOF > "${SERVICE_DEST}"
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

chmod 644 "${SERVICE_DEST}"
echo -e "${GREEN}[✓] Service file installed.${NC}"

# Reload systemd daemon
echo -e "\n${BLUE}[2/4] Reloading systemd daemon (systemctl daemon-reload)...${NC}"
systemctl daemon-reload
echo -e "${GREEN}[✓] Daemon reloaded.${NC}"

# Enable service on boot
echo -e "\n${BLUE}[3/4] Enabling service on boot (systemctl enable walvoil-viscosity)...${NC}"
systemctl enable walvoil-viscosity
echo -e "${GREEN}[✓] Service enabled on boot.${NC}"

# Start service
echo -e "\n${BLUE}[4/4] Starting service (systemctl start walvoil-viscosity)...${NC}"
systemctl restart walvoil-viscosity
echo -e "${GREEN}[✓] Service started.${NC}"

# Display status
sleep 1
echo -e "\n${BLUE}=================================================================${NC}"
echo -e "${GREEN}  WALVOIL VISCOSITY SERVICE STATUS                               ${NC}"
echo -e "${BLUE}=================================================================${NC}\n"
systemctl status walvoil-viscosity --no-pager


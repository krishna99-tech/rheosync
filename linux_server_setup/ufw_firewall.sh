#!/bin/bash
# Configure UFW firewall for Walvoil Viscosity System
echo "Configuring UFW Firewall for Ports 5001 (TCP) and 63363 (UDP)..."

sudo ufw allow 5001/tcp comment "Walvoil Viscosity Web Dashboard"
sudo ufw allow 63363/udp comment "Walvoil IoT Viscometer UDP Telemetry"

echo -e "\nCurrent Firewall Status:"
sudo ufw status verbose

